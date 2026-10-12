#!/bin/sh
# tools/publish-release.sh against a host that is a directory (#439).
#
# rsync, ssh and curl are stand-ins on PATH: rsync copies into $REMOTE, ssh
# runs its command with $REMOTE as the host's filesystem, and curl answers a
# URL under $URL out of $REMOTE - so "read back from where an operator reads"
# is read back from the copy, and a copy that went wrong is seen as wrong.
# No network, no root.
#
# Mutations shown red, one at a time: the forward-only test on `latest`
# removed (the old-version case moves it back); the read-back of the package
# removed (the corrupted copy is published and latest moves); the target
# check removed (the unconfigured run reaches rsync); the medium not carried
# forward (a patch release after a Y leaves latest/ with no ISO, #604); the
# medium's own forward-only test removed (an older ISO filled in later takes
# latest/ back); the scan for a medium when latest/ offers none removed (the
# old symlink, converted, loses the ISO); the read-back through latest/
# removed (a link that lands on nothing is called published).
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fail=0
ok()  { echo "  [ ok ] $1"; }
bad() { echo "  FAIL: $1"; fail=$((fail + 1)); }

REMOTE=$T/host/srv/prisma-releases
mkdir -p "$REMOTE" "$T/bin" "$T/src"
# A REAL HTTP SERVER over the host's directory, because the package is read
# back by the product's own downloader and not by curl (#456) - and it
# refuses Python's default agent the way the public server's front does,
# measured: 403 for Python-urllib, 200 for anything else. A read-back that
# does not say who it is fails here the way `system upgrade` failed on every
# box.
cat > "$T/serve.py" <<EOF
import http.server, sys
class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("User-Agent", "").startswith("Python-urllib"):
            self.send_error(403); return
        super().do_GET()
    def log_message(self, *a): pass
s = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
    lambda *a, **k: H(*a, directory="$T/host/srv", **k))
open("$T/port", "w").write(str(s.server_address[1]))
s.serve_forever()
EOF
python3 "$T/serve.py" & SERVER=$!
trap 'kill $SERVER 2>/dev/null; rm -rf "$T"' EXIT
_n=0; while [ ! -s "$T/port" ] && [ $_n -lt 50 ]; do sleep 0.1; _n=$((_n + 1)); done
URL=http://127.0.0.1:$(cat "$T/port")/prisma-releases
cat > "$T/bin/rsync" <<EOF
#!/bin/sh
# the last two arguments are the source and user@host:/path
for a; do :; done; dst=\$a
src=\$(for a; do echo "\$a"; done | tail -2 | head -1)
echo "\$src -> \$dst" >> "$T/rsync.log"
[ -n "\${CORRUPT:-}" ] && { cp -r "\$src" "$REMOTE/"; echo x >> "$REMOTE/\$(basename "\$src")/\$CORRUPT"; exit 0; }
cp -r "\$src" "$REMOTE/"
EOF
cat > "$T/bin/ssh" <<EOF
#!/bin/sh
for a; do :; done
cmd=\$(printf '%s' "\$a" | sed "s#/srv/prisma-releases#$REMOTE#g")
sh -c "\$cmd"
EOF
cat > "$T/bin/curl" <<EOF
#!/bin/sh
head=0; for a; do case "\$a" in -*I*) head=1 ;; http*) u=\$a ;; esac; done
f=$REMOTE/\${u#$URL/}
[ -f "\$f" ] || exit 22
if [ "\$head" = 1 ]; then printf 'HTTP/2 200\r\ncontent-length: %s\r\n' "\$(wc -c < "\$f" | tr -d ' ')"; else cat "\$f"; fi
EOF
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH" RELEASES_URL=$URL HOME=$T
TARGET="prisma-releases@host:/srv/prisma-releases"

mk() {  # <version> [iso]: what the gate leaves behind
  rm -rf "$T/src"; mkdir -p "$T/src"
  echo "package $1" > "$T/src/prisma-routing-$1-upgrade.tar.gz"
  [ "${2:-}" = iso ] && echo "medium $1" > "$T/src/prisma-routing-$1.iso"
  return 0
}

# What latest/ offers, as one line: the names in it, sorted.
offers() { ls "$REMOTE/latest" 2>/dev/null | sort | tr '\n' ' '; }
# latest/ offers exactly the upgrade package of $1 and the ISO of $2, each
# beside a sha256 that checks THROUGH the link, and is a directory.
offers_ok() {
  want="prisma-routing-$1-upgrade.tar.gz prisma-routing-$1-upgrade.tar.gz.sha256 "
  [ -n "${2:-}" ] && want="${want}prisma-routing-$2.iso prisma-routing-$2.iso.sha256 "
  want=$(printf '%s' "$want" | tr ' ' '\n' | sed '/^$/d' | sort | tr '\n' ' ')
  [ -d "$REMOTE/latest" ] && [ ! -L "$REMOTE/latest" ] \
    && [ "$(offers)" = "$want" ] \
    && (cd "$REMOTE/latest" && sha256sum -c --quiet ./*.sha256) >/dev/null 2>&1
}

echo "=== refused before anything is uploaded ==="
mk 1.2.3
out=$(sh "$REPO/tools/publish-release.sh" v1.2.3 "$T/src" 2>&1); rc=$?
[ "$rc" = 2 ] && case "$out" in *"NOTHING was published"*) true ;; *) false ;; esac \
  && [ ! -f "$T/rsync.log" ] \
  && ok "no upload target: refused, and rsync was never run" \
  || bad "no target: rc=$rc, rsync ran=$([ -f "$T/rsync.log" ] && echo yes || echo no): $out"
out=$(RELEASES_TARGET=$TARGET sh "$REPO/tools/publish-release.sh" v9.9.9 "$T/src" 2>&1); rc=$?
[ "$rc" = 2 ] && [ ! -f "$T/rsync.log" ] \
  && ok "a version whose package is not there is refused, not half published" \
  || bad "missing package: rc=$rc: $out"
out=$(RELEASES_TARGET=$TARGET sh "$REPO/tools/publish-release.sh" 1.2.3 "$T/src" 2>&1); rc=$?
[ "$rc" = 2 ] && ok "a tag without its v is refused" || bad "bare version: rc=$rc"

echo "=== a release goes up, reads back, and moves latest ==="
mk 1.2.3 iso
out=$(RELEASES_TARGET=$TARGET sh "$REPO/tools/publish-release.sh" v1.2.3 "$T/src" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "published: rc 0" || bad "publish failed: rc=$rc: $out"
(cd "$REMOTE/1.2.3" && sha256sum -c --quiet prisma-routing-1.2.3-upgrade.tar.gz.sha256 \
   prisma-routing-1.2.3.iso.sha256) >/dev/null 2>&1 \
  && ok "the package and the ISO are there, each beside a sha256 that checks" \
  || bad "files or sums wrong on the host: $(ls "$REMOTE/1.2.3")"
offers_ok 1.2.3 1.2.3 && ok "latest/ offers its package and its ISO, each with a sha256 that checks" \
  || bad "latest/ offers '$(offers)'"
case "$out" in *"downloads and verifies"*) ok "and it says it read the package back" ;;
  *) bad "no read-back reported: $out" ;; esac

echo "=== latest only moves forward, and keeps the medium (#604) ==="
# 1.2.10 is a PATCH release: an upgrade package and no ISO. latest/ moves to
# its package and keeps 1.2.3's ISO, which is the newest medium there is -
# before #604 it moved wholesale to 1.2.10/ and offered no ISO at all.
mk 1.2.10
out=$(RELEASES_TARGET=$TARGET sh "$REPO/tools/publish-release.sh" v1.2.10 "$T/src" 2>&1)
offers_ok 1.2.10 1.2.3 \
  && ok "1.2.10 is newer than 1.2.3, as numbers and not as strings" \
  || bad "latest/ after 1.2.10 offers '$(offers)': $out"
offers_ok 1.2.10 1.2.3 \
  && ok "a patch release after a Y keeps the Y's ISO in latest/, with its sha256" \
  || bad "a patch release left latest/ with no medium: '$(offers)'"
# And read the way a box reads it: `system upgrade` takes the newest
# version off latest/'s listing, over HTTP, through the links.
got=$(python3 -c '
import sys; sys.path.insert(0, sys.argv[1]); import prismash
print(prismash.latest_release(base=sys.argv[2]))' "$REPO/src/prismash" "$URL" 2>&1)
[ "$got" = "('1.2.10', '')" ] \
  && ok "the box's own reader finds 1.2.10 in latest/'s listing" \
  || bad "latest_release() over latest/ says $got"
mk 1.2.9
out=$(RELEASES_TARGET=$TARGET sh "$REPO/tools/publish-release.sh" v1.2.9 "$T/src" 2>&1)
[ -f "$REMOTE/1.2.9/prisma-routing-1.2.9-upgrade.tar.gz" ] && offers_ok 1.2.10 1.2.3 \
  && ok "an older version filled in later is published, and latest stays put" \
  || bad "older version: latest/ offers '$(offers)': $out"

echo "=== a copy that does not read back is not called published ==="
mk 1.3.0
out=$(CORRUPT=prisma-routing-1.3.0-upgrade.tar.gz RELEASES_TARGET=$TARGET \
      sh "$REPO/tools/publish-release.sh" v1.3.0 "$T/src" 2>&1); rc=$?
[ "$rc" = 1 ] && offers_ok 1.2.10 1.2.3 \
  && ok "a package that changed on the way: rc 1, and latest was NOT moved to it" \
  || bad "corrupt copy: rc=$rc latest/ offers '$(offers)': $out"

echo "=== the target can live in a file, like the wiki token ==="
mk 1.4.0
printf '%s\n' "$TARGET" > "$T/.releases-target"
out=$(sh "$REPO/tools/publish-release.sh" v1.4.0 "$T/src" 2>&1); rc=$?
[ "$rc" = 0 ] && [ -f "$REMOTE/1.4.0/prisma-routing-1.4.0-upgrade.tar.gz" ] \
  && ok "~/.releases-target is read when RELEASES_TARGET is not set" \
  || bad "target file: rc=$rc: $out"

echo "=== the old symlink is converted, and keeps a medium (#604) ==="
# The site as 0.153.1 left it: `latest` a link to a patch release's
# directory, which has no ISO. The next publish makes it a directory and
# finds the newest medium among the version directories - 1.2.3 here, the
# only one with an ISO.
rm -rf "$REMOTE/latest"; ln -s 1.4.0 "$REMOTE/latest"
mk 1.4.1
out=$(RELEASES_TARGET=$TARGET sh "$REPO/tools/publish-release.sh" v1.4.1 "$T/src" 2>&1); rc=$?
[ "$rc" = 0 ] && offers_ok 1.4.1 1.2.3 && [ -d "$REMOTE/1.4.0" ] \
  && ok "the link became a directory offering 1.4.1's package and 1.2.3's ISO" \
  || bad "old symlink: rc=$rc latest/ offers '$(offers)': $out"

echo "=== a new medium moves the ISO forward, an older one does not ==="
mk 1.5.0 iso
RELEASES_TARGET=$TARGET sh "$REPO/tools/publish-release.sh" v1.5.0 "$T/src" >/dev/null 2>&1
offers_ok 1.5.0 1.5.0 && ok "a Y release: its package and its ISO" \
  || bad "after 1.5.0 latest/ offers '$(offers)'"
mk 1.4.5 iso
RELEASES_TARGET=$TARGET sh "$REPO/tools/publish-release.sh" v1.4.5 "$T/src" >/dev/null 2>&1
[ -f "$REMOTE/1.4.5/prisma-routing-1.4.5.iso" ] && offers_ok 1.5.0 1.5.0 \
  && ok "an older medium filled in later is published, and latest/ keeps 1.5.0's" \
  || bad "after 1.4.5 latest/ offers '$(offers)'"
mk 1.5.1
RELEASES_TARGET=$TARGET sh "$REPO/tools/publish-release.sh" v1.5.1 "$T/src" >/dev/null 2>&1
offers_ok 1.5.1 1.5.0 && ok "and the patch after it: 1.5.1's package, 1.5.0's ISO" \
  || bad "after 1.5.1 latest/ offers '$(offers)'"
[ ! -e "$REMOTE/latest.new" ] && ok "nothing is left beside latest/" \
  || bad "latest.new was left on the host"

echo "=== latest/ is read back through its links ==="
# The medium's sum gone from its own directory, so the link latest/ carries
# for it lands on nothing: said, and rc 1, not "published".
mv "$REMOTE/1.5.0/prisma-routing-1.5.0.iso.sha256" "$T/held.sha256"
mk 1.5.2
out=$(RELEASES_TARGET=$TARGET sh "$REPO/tools/publish-release.sh" v1.5.2 "$T/src" 2>&1); rc=$?
mv "$T/held.sha256" "$REMOTE/1.5.0/prisma-routing-1.5.0.iso.sha256"
[ "$rc" = 1 ] && case "$out" in *"latest/ does not read back"*) true ;; *) false ;; esac \
  && ok "a link in latest/ that does not read back: rc 1, and said" \
  || bad "dangling link in latest/: rc=$rc: $out"

echo
[ "$fail" = 0 ] && echo "ALL TESTS PASSED" || echo "$fail FAILURES"
[ "$fail" = 0 ]
