#!/usr/bin/env python3
"""The runtime images, read off their Dockerfiles rather than started.

WHY A FILE-READING CHECK AT ALL, when `sh tools/build-images.sh check` starts
both images and asks them what they hold. Because that one needs docker, a
network and four minutes, and `make test` has none of the three. What it
cannot answer and this one can is whether the pins in the two Dockerfiles are
still the pins the MEDIUM installs: those drift with an edit, silently, and
the image that carries the other version starts, runs, and is a second
product.

The class is #396's, and it is the same argument appliance/build-upgrade.sh
makes about the artifact: the version a box runs is a fact about this
repository, written in one place and read everywhere else. Three readers of
that fact now - the medium, the artifact and the images.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
fails = 0


def ok(msg):
    print(f"  [ ok ] {msg}")


def bad(msg):
    global fails
    fails += 1
    print(f"  FAIL: {msg}")


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def iso_pin(name):
    """The version appliance/build-iso.sh installs into the medium."""
    m = re.search(rf"^{name}=\$\{{{name}:-([^}}]+)\}}",
                  read("appliance", "build-iso.sh"), re.M)
    return m.group(1) if m else None


def arg_pin(dockerfile, name):
    """The default a Dockerfile's ARG carries, which is what an unargued
    `docker build` uses - and `docker build` is run unargued by hand far more
    often than through the tool."""
    m = re.search(rf"^ARG {name}=(.+)$", read("build", dockerfile), re.M)
    return m.group(1).strip() if m else None


print("=== the images pin what the medium installs ===")
for dockerfile, name in (("Dockerfile.vpp", "VPP_VERSION"),
                         ("Dockerfile.frr", "FRR_VERSION")):
    want, got = iso_pin(name), arg_pin(dockerfile, name)
    if want is None:
        bad(f"appliance/build-iso.sh no longer pins {name}, so nothing here "
            f"knows what the medium installs")
    elif got is None:
        bad(f"build/{dockerfile} carries no ARG {name}, so an unargued "
            f"`docker build` takes whatever the repository holds today")
    elif want != got:
        bad(f"build/{dockerfile} pins {name}={got} and the medium installs "
            f"{want}: a box upgraded to the images would change its {name} "
            f"without a release saying so")
    else:
        ok(f"{dockerfile}: {name}={got}, the same the medium installs")

print("\n=== and the tool reads the pin rather than repeating it ===")
# A THIRD COPY IS WHAT THIS PREVENTS. The ARG defaults above are for a human
# typing `docker build`; the tool passes --build-arg, and if it carried its
# own literal there would be two numbers to keep equal instead of one.
tool = read("tools", "build-images.sh")
if "build-iso.sh" in tool and "--build-arg" in tool:
    ok("tools/build-images.sh takes both pins out of appliance/build-iso.sh")
else:
    bad("tools/build-images.sh no longer reads the pins out of "
        "appliance/build-iso.sh, so the images and the medium have two "
        "sources for one fact")
for name in ("26.06", "10.7.1"):
    if re.search(rf"^[^#]*{re.escape(name)}", tool, re.M):
        bad(f"tools/build-images.sh writes {name} in its own code: the pin "
            f"belongs in appliance/build-iso.sh, and a copy here is a copy "
            f"that can be right while the other is not")
ok("and it writes neither version in its own code")

print("\n=== the runtime images are not the build image ===")
# build/Dockerfile.dev carries gcc, cmake, ninja, gdb and the -dev packages
# because its whole job is to compile the plugin. An appliance image that
# carries them ships a build environment to every customer, and `check`'s
# equivalent assertion can only run where docker can.
vpp_df = read("build", "Dockerfile.vpp")
frr_df = read("build", "Dockerfile.frr")
for what, df in (("Dockerfile.vpp", vpp_df), ("Dockerfile.frr", frr_df)):
    for pkg in ("vpp-dev", "libvppinfra-dev", "build-essential", "cmake",
                "ninja-build", "gdb"):
        if re.search(rf"^\s*{re.escape(pkg)}[=\s\\]", df, re.M):
            bad(f"build/{what} installs {pkg}: that is build/Dockerfile.dev's "
                f"job, and an appliance image carrying a compiler ships one "
                f"to every customer")
    ok(f"{what} installs nothing that builds")

print("\n=== the plugin is copied in, never compiled there ===")
# A plugin compiled against one VPP and loaded by another does not load, and
# that is the entire reason this product pins a VPP version. Building it
# inside the image would be a second compile against a second apt resolution,
# on a different day.
if "COPY vpp_plugins/" not in vpp_df:
    bad("build/Dockerfile.vpp does not COPY the plugin in, so either the "
        "image has no CGNAT or it compiles its own - and a plugin built "
        "against a different VPP than the one loading it does not load")
elif re.search(r"^RUN.*(cmake|ninja|make)\b", vpp_df, re.M):
    bad("build/Dockerfile.vpp compiles inside the image: the plugin this "
        "repository built is the one that must be in it")
else:
    ok("the dataplane image takes the plugin this repository compiled")


# THE COPY INSTRUCTION, not the string. A Dockerfile mentions that file in
# its comment too, so `"vpp-stop-drain.conf" in text` is satisfied by a
# COPY that has been commented out - measured: that mutation passed.
def copies(df, name):
    """Is there an uncommented COPY whose text names `name`?"""
    joined, out = "", []
    for line in df.splitlines():
        if line.lstrip().startswith("#"):
            continue
        joined += line
        if joined.rstrip().endswith("\\"):
            joined = joined.rstrip()[:-1]
            continue
        out.append(joined)
        joined = ""
    return any(l.strip().startswith("COPY") and name in l for l in out)


print("\n=== the dataplane image runs the wrapper, and carries its drain ===")
# The ordering itself is test/test_vpp_entrypoint.sh's - it drives the
# wrapper with stubs and requires the drain's line first. What is read here
# is that the image will actually RUN it: an image whose entrypoint is vpp
# would take SIGTERM straight to the graph, and the release record for every
# block it held would be written into a dataplane that has stopped.
if not any(l.strip().startswith("ENTRYPOINT") and "vpp-entrypoint" in l
           for l in vpp_df.splitlines() if not l.lstrip().startswith("#")) \
        or not copies(vpp_df, "appliance/vpp-entrypoint"):
    bad("build/Dockerfile.vpp does not make appliance/vpp-entrypoint its "
        "ENTRYPOINT, so `docker stop` signals VPP with no drain in front of "
        "it and a stopped box looks exactly like one that fell over (#217)")
else:
    ok("PID 1 is the wrapper")
if not copies(vpp_df, "vpp-stop-drain.conf"):
    bad("build/Dockerfile.vpp does not copy the drain's drop-in in, and the "
        "wrapper reads the command out of it: it would start VPP and drain "
        "nothing on the way down")
else:
    ok("and the drop-in it reads the drain out of is in the image")
# NOT RETYPED. The whole argument for copying that file is that there is one
# statement of what stopping this dataplane means; a wrapper with the command
# written into it is a second one, which can be right while the drop-in is
# wrong.
entry = read("appliance", "vpp-entrypoint")
if "bpa stop-drain" in entry:
    bad("appliance/vpp-entrypoint writes the drain command in its own code: "
        "it is read out of the drop-in so that systemd and the container "
        "cannot come to disagree about what stopping means")
else:
    ok("and the wrapper reads that command rather than repeating it")

print("\n=== and nothing here has been started, which is the other check ===")
# Said out loud, because a suite that reads a file and reports on an image is
# the shape that lets a broken image pass. `sh tools/build-images.sh check`
# starts both and asks VPP for its plugin list; it is not in make test
# because make test has no docker.
if "check" not in tool:
    bad("tools/build-images.sh has no `check` mode any more, so nothing "
        "starts these images at all and this file is the only reader left")
else:
    ok("tools/build-images.sh check is what starts them")

print("\nALL TESTS PASSED" if not fails else f"\n{fails} FAILURES")
sys.exit(1 if fails else 0)
