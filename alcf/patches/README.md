# nekRS `next` on Aurora: two fixes needed for `examples/rbc`

Tested with nekRS `next` @ 5e2455cf on Aurora (Intel PVC) with the DPC++ backend: oneAPI 2026.1.0,
intel-compute-runtime 25.18.33578.77, mpich 5.0.0.aurora_test, 1 node with 12 ranks (one rank per GPU tile).
The full write-up, with logs and backtraces, is in `nekrs-aurora-fixes.html`.

## Fix 1: Schwarz weights used before they exist (nekRS)

- **Symptom:** every rank aborts during setup, while the pressure solver's pMG preconditioner is being built
  (`BUILDING pMG7`):
  `Segmentation fault from GPU at 0x90000 ... access: 0 (Read)`. The fault address is different on each rank and
  equals `Nelements*Np*sizeof(pfloat)`.
- **Cause:** `pMGLevel::setupSmootherSchwarz()` calls `autoOverlap()`, which times `smoothSchwarz()`. On the ASM
  path, `smoothSchwarz()` passes `o_wts` to `postFDMKernel`. But `o_wts` is only allocated in
  `generateSchwarzWeights()`, which ran after `autoOverlap()`.
- **When it triggers:** the ASM smoother (the default for pressure), more than one rank, a non-Serial backend, and
  `ENABLE GS COMM OVERLAP` on (the default).
- **Change:** call `generateSchwarzWeights()` before `autoOverlap()`. It only needs `ogs` and `ogsExt`, which are
  already set up at that point.
- **Patch:** `01-nekrs-schwarz-weights-before-autooverlap.patch`

## Fix 2: atomics on `@shared` arrays compiled with the global address space (OCCA DPC++ translator)

- **Symptom:** setup finishes, then every rank aborts at step 0 in `udfExecuteStep`:
  `Segmentation fault from GPU at 0x0 ... access: 2 (Atomic)`. The faulting kernel is `hist` in `rbc/hist.okl`,
  which does `@atomic cnt_s[idx] += w[id]` on a `@shared` array.
- **Cause:** `dpcppAtomicNode::print()` emits `local_space` only if `atomic_type.hasAttribute("shared")`. But OCCA
  attaches `@shared` to the variable, not to its type (`setSharedQualifiers()` checks `var.hasAttribute("shared")`).
  As a result, every atomic gets `sycl::access::address_space::global_space`, and using that on local memory is
  undefined behavior.
- **Who is affected:** DPC++ only (CUDA and HIP are not), and only kernels that use `@atomic` on `@shared` arrays.
  No nekRS core kernel does this.
- **Change:** `transformAtomicBasicExpressionStatement` now passes `atomic_var.hasAttribute("shared")` into
  `dpcppAtomicNode`, and `print()` emits `local_space` when that flag is set.
- **Patches:** `02-nekrs-occa-dpcpp-shared-atomic.patch` (paths under `3rd_party/occa/`, for the nekRS tree) and
  `02-occa-upstream-dpcpp-shared-atomic.patch` (the same change with paths relative to the OCCA repo, for upstream).

## Applying

```
cd nekRS
git apply ../patches/01-nekrs-schwarz-weights-before-autooverlap.patch
git apply ../patches/02-nekrs-occa-dpcpp-shared-atomic.patch
cmake --build build --target install -j 32
rm -rf <case>/.cache    # OCCA may not notice a translator change and reuse old kernels
```

## Runs

| PBS job | Patches | Result |
|---------|---------|--------|
| 8991073 | none | GPU read fault in pressure pMG setup |
| 8991119 | fix 1 | GPU atomic fault in `hist` at step 0 |
| 8991170 | fix 1 + 2 | completed: 2840 steps to t = 100, solve time 77.9 s, exit 0 |

## Related bug, not fixed

The loop in `examples/rbc/hist.okl` that adds each point to the histogram checks `id < nBin`; it should check
`id < N`. As written, each rank only counts its first 256 points, so the values in `pdf_scalar00.dat` are far too
small.
