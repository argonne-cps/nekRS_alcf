#!/bin/bash
set -e -a

module -q purge
module -q restore
module -q swap mpich/opt/4.3.0rc3 mpich/opt/develop-git.7dd7b89 
module -q unload oneapi/eng-compiler/2024.07.30.002
module -q use /opt/aurora/24.180.3/spack/unified/0.8.0/install/modulefiles/oneapi/2024.07.30.002
module -q use /soft/preview/pe/24.347.0-RC2/modulefiles
module -q add oneapi/release/2025.0.5 
module -q unload intel_compute_runtime 
module -q load cmake
module list

CC=mpicc
CXX=mpic++
FC=mpif77

BUILD_DIR=$PWD/build
INSTALL_DIR=$HOME/.local/nekrs

if [ -d ${BUILD_DIR} ]; then
  rm -r ${BUILD_DIR}
fi

if [ -d ${INSTALL_DIR} ]; then
  rm -r ${INSTALL_DIR}
fi

cmake -S . -B ${BUILD_DIR} -DCMAKE_INSTALL_PREFIX=${INSTALL_DIR}  -Wfatal-errors && \
cmake --build ${BUILD_DIR} --parallel 8 && \
cmake --install ${BUILD_DIR}
