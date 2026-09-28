#!/bin/sh
# tools/plugin-optimised.sh refuses a plugin built without optimisation (#490).
#
# Built here, not taken from a release: the same C file as a shared object at
# -O0 -g (what every release to 0.140.2 shipped, but with the -g it lacked),
# at -O2 -g (what CMakeLists.txt builds now), with no -g at all (what 0.140.2
# actually shipped: no build type is no -g either), and one object of each
# linked together - a plugin with one unoptimised unit is not an optimised
# plugin.
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fail=0
ok()  { echo "  [ ok ] $1"; }
bad() { echo "  FAIL: $1"; fail=$((fail + 1)); }

cat > "$T/a.c" <<'EOF'
int bpa_twice (int x) { int y = x; y += x; return y; }
EOF
cat > "$T/b.c" <<'EOF'
int bpa_thrice (int x) { return 3 * x; }
EOF
gcc -shared -fPIC -O0 -g -o "$T/o0.so" "$T/a.c"
gcc -shared -fPIC -O2 -g -o "$T/o2.so" "$T/a.c"
gcc -shared -fPIC -O0 -o "$T/nog.so" "$T/a.c"
gcc -c -fPIC -O2 -g -o "$T/a.o" "$T/a.c"
gcc -c -fPIC -O0 -g -o "$T/b.o" "$T/b.c"
gcc -shared -o "$T/mixed.so" "$T/a.o" "$T/b.o"

echo "=== the check, on plugins built four ways ==="
if out=$(sh "$HERE/tools/plugin-optimised.sh" "$T/o2.so" 2>&1); then
  ok "an -O2 -g plugin is shipped ($out)"
else
  bad "an -O2 -g plugin was refused: $out"
fi
if out=$(sh "$HERE/tools/plugin-optimised.sh" "$T/o0.so" 2>&1); then
  bad "an -O0 plugin was shipped: $out"
else
  case "$out" in
    *"without optimisation"*) ok "an -O0 plugin is refused, and says why" ;;
    *) bad "an -O0 plugin was refused for another reason: $out" ;;
  esac
fi
if out=$(sh "$HERE/tools/plugin-optimised.sh" "$T/nog.so" 2>&1); then
  bad "a plugin with no debug info was shipped: $out"
else
  case "$out" in
    *"no compiler producer"*) ok "a plugin that cannot say how it was built is refused" ;;
    *) bad "a plugin with no debug info was refused for another reason: $out" ;;
  esac
fi
if out=$(sh "$HERE/tools/plugin-optimised.sh" "$T/mixed.so" 2>&1); then
  bad "a plugin with one -O0 unit was shipped: $out"
else
  ok "one unoptimised unit is enough to refuse the plugin"
fi
if sh "$HERE/tools/plugin-optimised.sh" "$T/missing.so" >/dev/null 2>&1; then
  bad "a missing file passed"
else
  ok "a missing file is refused"
fi

echo "=== and both packaging scripts ask it ==="
for f in appliance/build-iso.sh appliance/build-upgrade.sh; do
  if grep -q 'tools/plugin-optimised.sh' "$HERE/$f"; then
    ok "$f runs it on the plugin it ships"
  else
    bad "$f ships a plugin nobody asked how it was built"
  fi
done

echo
[ "$fail" -eq 0 ] && echo "ALL TESTS PASSED" || echo "$fail FAILURES"
exit "$fail"
