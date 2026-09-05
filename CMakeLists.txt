# Build out-of-tree do plugin bpa. Use build/Dockerfile.dev, que ja traz
# o vpp-dev na versao fixada.
cmake_minimum_required(VERSION 3.13)
project(hexarouting VERSION 0.1 LANGUAGES C)

find_package(VPP REQUIRED)

include_directories(${CMAKE_CURRENT_SOURCE_DIR}/src/plugins/bpa)
add_subdirectory(src/plugins/bpa)
