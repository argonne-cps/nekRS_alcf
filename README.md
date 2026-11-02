# nekRS for ALCF Aurora (`aurora-next-patches`)

This branch is nekRS `next` at commit `5e2455cf` (the same commit as `main` in this repository), plus two small fixes
needed to run the `rbc` example on Aurora (Intel Data Center GPU Max / PVC, DPC++ backend). The original upstream
README follows the horizontal rule below.

- Upstream: <https://github.com/Nek5000/nekRS> (branch `next`)
- Tested on: 1 Aurora node, oneAPI 2026.1.0, intel-compute-runtime 25.18.33578.77, mpich 5.0.0.aurora_test,
  libfabric 2.3.1, gcc 14.3.0, cmake 3.31.11, DPC++ backend, one MPI rank per PVC GPU tile (12 ranks per node)

## Changes relative to upstream

Only 4 tracked source files change (16 insertions, 8 deletions), plus the new `alcf/` directory.

### 1. Schwarz weights were used before they were created (`src/core/elliptic/MG/ellipticMultiGridSchwarz.cpp`)

- **Symptom:** every rank aborts while building the pressure multigrid preconditioner (`BUILDING pMG7`) with
  `Segmentation fault from GPU at 0x90000 ... access: 0 (Read)`.
- **Cause:** `pMGLevel::setupSmootherSchwarz()` calls `autoOverlap()`, which times `smoothSchwarz()`. On the ASM path
  `smoothSchwarz()` passes `o_wts` to `postFDMKernel`, but `o_wts` is only allocated in `generateSchwarzWeights()`,
  which ran after `autoOverlap()`.
- **Triggers when:** the ASM smoother is used (the default for pressure), there is more than one rank, the backend is not
  Serial, and `ENABLE GS COMM OVERLAP` is on (the default).
- **Change:** call `generateSchwarzWeights()` before `autoOverlap()`.
- **Patch:** `alcf/patches/01-nekrs-schwarz-weights-before-autooverlap.patch`

### 2. Atomics on `@shared` arrays used the global address space (OCCA DPC++ translator)

Files: `3rd_party/occa/src/occa/internal/lang/expr/dpcppAtomicNode.cpp`, `dpcppAtomicNode.hpp`, and
`3rd_party/occa/src/occa/internal/lang/modes/dpcpp.cpp`.

- **Symptom:** setup completes, then every rank aborts at step 0 with
  `Segmentation fault from GPU at 0x0 ... access: 2 (Atomic)`, in the `hist` UDF kernel of `examples/rbc`
  (`@atomic cnt_s[idx] += w[id]` on a `@shared` array).
- **Cause:** `dpcppAtomicNode::print()` emits `local_space` only if the atomic's type has the `shared` attribute, but OCCA
  attaches `@shared` to the variable, not its type. Every atomic therefore got `sycl::access::address_space::global_space`,
  which is undefined behaviour on local memory.
- **Affects:** DPC++ only, and only user kernels with `@atomic` on `@shared` arrays. No nekRS core kernel does this.
- **Change:** `transformAtomicBasicExpressionStatement` passes `atomic_var.hasAttribute("shared")` into
  `dpcppAtomicNode`, and `print()` emits `local_space` when it is set.
- **Patches:** `alcf/patches/02-nekrs-occa-dpcpp-shared-atomic.patch` (paths for this tree) and
  `alcf/patches/02-occa-upstream-dpcpp-shared-atomic.patch` (paths relative to the OCCA repository, for upstream).

After changing the OCCA translator, delete the case's `.cache` directory, because OCCA can reuse stale kernels.
`alcf/patches/README.md` and `alcf/patches/nekrs-aurora-fixes.html` have the full write-up with backtraces.

### Known issue, not fixed here

The histogram loop in `examples/rbc/hist.okl` checks `id < nBin`; it should check `id < N`. As written each rank counts only
its first 256 points, so the values in `pdf_scalar00.dat` are far too small.

### Added: `alcf/`

- `alcf/patches/`: the patch files above and the write-up.
- `alcf/scripts/`: the PBS scripts used for the runs below. They use project `Catalyst`, queue `debug`, and hard-coded paths
  under `/lus/flare/projects/catalyst/world_shared/sspatel/scheel` (`TOP=`); change these for your own account and
  directories.
  - `build_nekrs.pbs`: configure, build and install on a compute node (`cmake -S . -B build`, then
    `cmake --build build --target install -j 32`).
  - `run_rbc.pbs`, `run_tgv.pbs`, `run_hit_ranks.pbs`, `run_pb146_ranks.pbs`, `run_kershaw_ranks.pbs`,
    `run_ethier.pbs`, `run_1rank.pbs`: run a case. Each first
    JIT-precompiles kernels with `--build-only`, then runs with `--backend dpcpp`. They bind each rank to one GPU tile
    with `ZE_AFFINITY_MASK` and a per-rank CPU list. Settings follow `nekRS_HPCsupport/Aurora/nrsqsub`.
  - `diag_rbc.pbs`, `compare_tgv.py`: diagnostics for `rbc`, and a comparison of `tgv` energy and enstrophy against
    `examples/tgv/ref/data`.

Run settings used by the scripts: `NEKRS_GPU_MPI=0`, `MPICH_GPU_SUPPORT_ENABLED=0`, `NEKRS_CACHE_BCAST=1`,
`FI_CXI_RX_MATCH_MODE=hybrid` (needed by parRSB), `UR_L0_USE_COPY_ENGINE=0` (runtime workaround), and
`OCCA_DPCPP_COMPILER_FLAGS="-O3 -fsycl -fsycl-targets=intel_gpu_pvc -ftarget-register-alloc-mode=pvc:auto -fma"`.

To apply the fixes to a fresh `next` checkout:

```
git apply alcf/patches/01-nekrs-schwarz-weights-before-autooverlap.patch
git apply alcf/patches/02-nekrs-occa-dpcpp-shared-atomic.patch
```

## Verified runs on Aurora

All runs used 1 node and the DPC++ backend with both patches applied. "Completed" means the run reached its end time or
step count and exited with code 0. Solution accuracy was not validated against reference data for these runs, except for `ethier`, which was checked
against its exact solution (see the notes below).

| Case | Ranks | PBS job | Run | Init (s) | Solve (s) | Result |
|------|-------|---------|-----|----------|-----------|--------|
| `rbc` | 12 | 8991170 | 2840 steps, t = 100.03 | 38.9 | 77.9 | completed |
| `rbc` (`rbc_1rank`) | 1 | 8992110 | 2889 steps, t = 100.04 | 51.5 | 130.1 | completed |
| `tgv` | 12 | 8991225 | 6667 steps, t = 20.00 | 53.8 | 268.1 | completed |
| `tgv` (`tgv_1rank`) | 1 | 8992202 | 3334 steps, t = 10.00 (`endTime = 10`) | 177.8 | 705.7 | completed |
| `hit` | 1 | 8992580 | 2000 steps, t = 40 | 27.7 | 36.4 | completed |
| `hit` | 2 | 8992580 | 2000 steps, t = 40 | 26.3 | 41.8 | completed |
| `hit` | 6 | 8992580 | 2000 steps, t = 40 | 24.3 | 36.7 | completed |
| `hit` | 12 | 8992580 | 2000 steps, t = 40 | 24.8 | 35.7 | completed |
| `pb146` | 1 | 9032766 | 100 steps, t = 0.2 (capped) | 379.6 | 125.3 | completed |
| `pb146` | 2 | 9032766 | 100 steps, t = 0.2 (capped) | 246.3 | 93.0 | completed |
| `pb146` | 12 | 9032766 | 100 steps, t = 0.2 (capped) | 77.0 | 24.4 | completed |
| `kershaw` | 1 | 9032995 | benchmark, `numSteps = 0` | 48.6 | n/a | completed |
| `kershaw` | 12 | 9032995 | benchmark, `numSteps = 0` | 33.0 | n/a | completed |
| `ethier` | 12 | 9033715 | 100 steps, t = 0.2 | 34.2 | 2.7 | completed, error vs exact solution checked |
| `ethier` (`--cimode 2`) | 12 | 9033715 | 30 steps, t = 0.06 | 37.8 | 1.2 | all CI checks passed |
| `ethier` (`--cimode 1`, with particles) | 12 | 9033715 | 30 steps, t = 0.06 | 30.2 | 3.9 | all CI checks passed |

Notes:

- `rbc` needs both fixes. Without them it fails (see the table below).
- `pb146` was capped at 100 steps with `numSteps = 100` in each run directory's `.par`; the stock case runs 10000 steps
  (`endTime = 20`, `dt = 2e-3`). Its mesh (62,132 elements) comes from `examples/pb146/pb.re2.tgz`, extracted before the
  run. `run_pb146_ranks.pbs` expects it in `run/pb146_src`. The solve time is for the 100 steps. Rank counts agree on
  the final CFL (2.267), and the 1-rank run fit on one GPU tile.
- `hit` reaches a different final CFL at each rank count (1.72, 1.70, 1.61 and 1.83 for 1, 2, 6 and 12 ranks). HIT is
  chaotic, and I did not compare the results.
- `tgv_1rank` was run to t = 10 instead of 20 to fit the time limit.
- `kershaw` is a solver benchmark with `numSteps = 0`, so there is no time stepping and no solution to validate;
  "completed" means the weighted dot product and the BPS5, BP5 and BP6 benchmarks all ran and the job exited with code 0.
  It uses the stock mesh (1000 elements, refined to 8000 with `hrefine = 2`), so the 1-rank run has 8000 elements on the
  GPU tile, as in the example's README, and the 12-rank run has about 670 per rank (strong scaling). `run_kershaw_ranks.pbs`
  runs 1 and 12 ranks.

  | Benchmark | 1 rank: throughput, (DOF x iter)/s/rank | 1 rank: flops/rank | 12 ranks: throughput | 12 ranks: flops/rank |
  |-----------|------------------------------|--------------------|----------------------|----------------------|
  | BPS5 (32 / 51 iterations) | 4.42e8 | 9.24e11 | 4.26e7 | 8.88e10 |
  | BP5 (1000 iterations) | 3.94e9 | 7.53e11 | 8.76e8 | 1.68e11 |
  | BP6 (500 iterations) | 4.97e9 | 9.46e11 | 1.49e9 | 2.84e11 |

- `ethier` is the Ethier-Steinman exact-solution case, so it is the accuracy check for this build. The velocity and
  pressure Dirichlet/Neumann conditions and the two scalars (`VELX_DIR`, `VELX_NEU`) come from the exact solution. It ran on
  12 ranks in three ways, in `run_ethier.pbs`: the stock `ethier.par` (100 steps, dt = 2e-3, polynomial order 9), and CI
  modes 2 and 1 (`--cimode N`). All three exited with code 0. Errors below are L2 norms against the exact solution at the
  last step.

  | Run | Velocity (u_x) | Pressure | `VELX_DIR` | `VELX_NEU` |
  |-----|----------------|----------|------------|------------|
  | stock par, t = 0.2 | 3.6e-10 | 6.6e-10 | 1.1e-11 | 1.4e-11 |
  | `--cimode 2`, t = 0.06 | 2.78e-10 | 7.0e-10 | 6.7e-12 | 7.5e-12 |
  | `--cimode 1`, t = 0.06 | 2.78e-10 | 7.1e-10 | 5.4e-12 | 6.3e-12 |

  In CI modes 1 and 2 the velocity, pressure, both scalars and the iteration-count checks passed (within the example's
  30% tolerance on the reference errors; for example the velocity error is 2.78e-10 against a reference of 2.77e-10). Mode 1 also tracks particles and compares them with the exact trajectories; the
  position error was 2.9e-11, 4.5e-11 and 7.1e-11 in x, y and z. Mode 3 is excluded for DPC++ in the example's CMake
  test list, and I did not run the other modes.

  For comparison, the example's README lists, at 8000 elements per GPU, BPS5 3.90e8 and BP5 3.87e9 for an NVIDIA A100,
  and BPS5 8.03e8 and BP5 8.86e9 for a GH200. The 12-rank throughput per rank is much lower because the same
  8000-element mesh is split over 12 tiles, leaving too little work per rank; it is not a like-for-like comparison.

Runs of `rbc` used to find the fixes (12 ranks):

| PBS job | Patches | Result |
|---------|---------|--------|
| 8991073 | none | GPU read fault in pressure pMG setup |
| 8991119 | fix 1 | GPU atomic fault in `hist` at step 0 |
| 8991170 | fix 1 + 2 | completed: 2840 steps to t = 100, solve time 77.9 s |

Init is the "initialization took" line of the nekRS log, and solve is the "solve" line of its runtime statistics, in
seconds. Init includes mesh setup and loading kernels that were precompiled with `--build-only`.

---

```
███    ██ ███████ ██   ██ ██████  ███████
████   ██ ██      ██  ██  ██   ██ ██     
██ ██  ██ █████   █████   ██████  ███████
██  ██ ██ ██      ██  ██  ██   ██      ██
██   ████ ███████ ██   ██ ██   ██ ███████ 
(c) 2019-2026 UCHICAGO ARGONNE, LLC
```

[![Build Status](https://travis-ci.com/Nek5000/nekRS.svg?branch=master)](https://travis-ci.com/Nek5000/nekRS)
[![License](https://img.shields.io/badge/License-BSD%203--Clause-orange.svg)](https://opensource.org/licenses/BSD-3-Clause)

**nekRS** is a fast and scalable computational fluid dynamics (CFD) solver targeting HPC applications. The code started as an early fork of [libParanumal](https://github.com/paranumal/libparanumal) in 2019, with the intention of supplanting its precursor, [nek5000](https://github.com/Nek5000/Nek5000).

Capabilities:

* Incompressible and low Mach-number Navier-Stokes + scalar transport 
* High-order curvilinear conformal Hex spectral elements in space 
* Variable time step 2nd/3rd order semi-implicit time integration
* MPI + [OCCA](https://github.com/libocca/occa) supporting CUDA, HIP, DPC++, SERIAL (C++)
* LES and RANS turbulence models
* Arbitrary-Lagrangian-Eulerian moving mesh
* Lagrangian phase model
* Overlapping overset grids
* Conjugate fluid-solid heat transfer
* Various boundary conditions
* VisIt & Paraview for data analysis and visualization including in-situ support through Ascent
* Legacy interface

## Build Instructions

Requirements:
* Linux, Mac OS X (Microsoft WSL and Windows is not supported) 
* GNU/oneAPI/NVHPC/ROCm compilers (C++17/C99 compatible)
* MPI-3.1 or later
* CMake version 3.21 or later 

Download the latest [release](https://github.com/Nek5000/nekRS/releases) available under

```sh
https://github.com/Nek5000/nekRS/archive/refs/tags/v26.1.tar.gz 
```

or clone our GitHub repository:

```sh
https://github.com/Nek5000/nekRS.git
```
The [master](https://github.com/Nek5000/nekRS) branch always points to the latest stable release while [next](https://github.com/Nek5000/nekRS/tree/next) 
provides an early preview of the next upcoming release (do not use in a production environment).

#
If you're on an HPC system, ensure you log in to a compute node. You can find installation instructions and job submission scripts for common HPC systems [here](https://github.com/Nek5000/nekRS_HPCsupport).
Now, just run:

```sh
CC=mpicc CXX=mpic++ FC=mpif77 ./build.sh [-DCMAKE_INSTALL_PREFIX=$HOME/.local/nekrs] [<options>]
```
Adjust the compilers as necessary. Make sure to remove the previous build and installation directory if updating.

## Setting the Environment

Assuming you run `bash` and your install directory is $HOME/.local/nekrs, 
add the following line to your $HOME/.bash_profile:

```sh
export NEKRS_HOME=$HOME/.local/nekrs
export PATH=$NEKRS_HOME/bin:$PATH
```
then type `source $HOME/.bash_profile` in the current terminal window. 

## Run the Code

We try hard not to break userland but the code is evolving quickly so things might change from one version to another without being backward compatible. Please consult `RELEASE.md` *before* using the code.  

```sh
cd <directory outside of installation/source folder>
cp -a $NEKRS_HOME/examples/turbPipePeriodic .
mpirun -np 2 nekrs --setup turbPipe.par
```
For convenience we provide various launch scripts in the `bin` directory.

## Documentation 
For documentation, see [readthedocs page](https://nekrs.readthedocs.io/en/latest/). 
The manual pages for the `par` file and environment variables `env` can be accessed through `nrsman`

## Discussion Group
Please visit [GitHub Discussions](https://github.com/Nek5000/nekRS/discussions). Here we help, find solutions, share ideas, and follow discussions.

## Contributing
Our project is hosted on [GitHub](https://github.com/Nek5000/nekRS). To learn how to contribute, see `CONTRIBUTING.md`.

## Reporting Bugs
All bugs are reported and tracked through [Issues](https://github.com/Nek5000/nekRS/issues). If you are having trouble installing the code or getting your case to run properly, you should first visit our discussion group.

## License
nekRS is released under the BSD 3-clause license (see `LICENSE` file). 
All new contributions must be made under the BSD 3-clause license.

## Citing
If you find our project useful, please cite [NekRS, a GPU-Accelerated Spectral Element Navier-Stokes Solver](https://www.sciencedirect.com/science/article/abs/pii/S0167819122000710) 

## Acknowledgment
This research was supported by the Exascale Computing Project (17-SC-20-SC), 
a joint project of the U.S. Department of Energy's Office of Science and National Nuclear Security 
Administration, responsible for delivering a capable exascale ecosystem, including software, 
applications, and hardware technology, to support the nation's exascale computing imperative.
