# Build out-of-tree do plugin bpa. Use build/Dockerfile.dev, que ja traz
# o vpp-dev na versao fixada.
cmake_minimum_required(VERSION 3.13)
# The build's project name keeps the old spelling after the rename to Prisma
# Routing (#202): it is an identifier the build tree is keyed on, not a
# string anybody reads.
project(hexarouting VERSION 0.1 LANGUAGES C)

find_package(VPP REQUIRED)

include_directories(${CMAKE_CURRENT_SOURCE_DIR}/src/plugins/bpa)
# O vppapigen gera os headers em ${CMAKE_BINARY_DIR}/src/plugins/bpa/, e o
# codigo os inclui como <bpa/bpa.api_enum.h>: o diretorio pai e que entra.
include_directories(${CMAKE_BINARY_DIR}/src/plugins)
add_subdirectory(src/plugins/bpa)
