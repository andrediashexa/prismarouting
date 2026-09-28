#!/bin/sh
# plugin-optimised.sh <file.so> - refuse a plugin that was not built optimised
# (#490).
#
# Every release up to 0.140.2 shipped bpa_plugin.so at -O0, because
# CMakeLists.txt never set CMAKE_BUILD_TYPE and CMake passes no -O without
# one. Nothing noticed for 140 releases: the plugin worked, it was only 3.6x
# dearer per packet than it had to be (408 against 114 clocks in bpa-in2out).
# CMakeLists.txt defaults the type now; this is the reader that keeps it so,
# run on the very file build-iso.sh and build-upgrade.sh are about to ship.
#
# What it reads is what the compiler wrote into the file: the DW_AT_producer of
# each compilation unit carries the flags it was given ("GNU C17 12.2.0 ... -g
# -O2 ..."). A file with no debug info, or a unit with no -O1/-O2/-O3/-Os/-Ofast
# on its producer line, is refused - a plugin built without -g cannot say how
# it was built, and a box's core dump could not be read either.
set -u
SO=${1:?usage: plugin-optimised.sh <file.so>}
[ -f "$SO" ] || { echo "plugin-optimised: no such file: $SO" >&2; exit 2; }
PRODUCERS=$(readelf --debug-dump=info "$SO" 2>/dev/null \
  | sed -n 's/.*DW_AT_producer *: *\(([^)]*): *\)\{0,1\}//p')
if [ -z "$PRODUCERS" ]; then
  echo "plugin-optimised: $SO carries no compiler producer (built without -g?)" >&2
  echo "  it cannot say how it was built; CMakeLists.txt builds RelWithDebInfo" >&2
  exit 1
fi
UNOPT=$(printf '%s\n' "$PRODUCERS" | grep -v -E -e ' -O([1-3s]|fast)( |$)' | sort -u)
if [ -n "$UNOPT" ]; then
  echo "plugin-optimised: $SO has code compiled without optimisation:" >&2
  printf '%s\n' "$UNOPT" | sed 's/^/  /' | head -5 >&2
  echo "  the CGNAT at -O0 costs 3.6x the clocks a packet (#490)" >&2
  exit 1
fi
echo "plugin-optimised: $(printf '%s\n' "$PRODUCERS" | grep -o -E ' -O([1-3s]|fast)' | sort | uniq -c | tr -s ' ' | tr '\n' ';')"
exit 0
