# Build out-of-tree do plugin bpa. Use build/Dockerfile.dev, que ja traz
# o vpp-dev na versao fixada.
cmake_minimum_required(VERSION 3.13)

# OPTIMISED UNLESS SOMEBODY ASKS OTHERWISE (#490). With CMAKE_BUILD_TYPE empty
# CMake passes no -O at all, VPP's add_vpp_plugin adds none either, and every
# release up to 0.140.2 shipped the plugin at -O0: every local spilled to the
# stack. Measured with 20 M 64-byte packets through one CGNAT session, same
# code, same machine: bpa-in2out 408 clocks/packet at -O0, 114 at -O2, 124 at
# -O3; the whole VPP path 708 cycles/packet at -O0 and 358 at -O2. -O3 buys
# nothing over -O2, and -g keeps a box's core dump readable. FORCE, because a
# build directory configured before this line holds the empty value in its
# cache, and it is the directory the labs and tools/build.sh reuse.
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "Build type" FORCE)
endif()
# The build's project name keeps the old spelling after the rename to Prisma
# Routing (#202): it is an identifier the build tree is keyed on, not a
# string anybody reads.
project(prisma-routing VERSION 0.1 LANGUAGES C)

find_package(VPP REQUIRED)

include_directories(${CMAKE_CURRENT_SOURCE_DIR}/src/plugins/bpa)
# O vppapigen gera os headers em ${CMAKE_BINARY_DIR}/src/plugins/bpa/, e o
# codigo os inclui como <bpa/bpa.api_enum.h>: o diretorio pai e que entra.
include_directories(${CMAKE_BINARY_DIR}/src/plugins)
add_subdirectory(src/plugins/bpa)
