#!/usr/bin/env python3
"""
prismash, with the box's default MTU of before #592 - a lab fixture for
lab/default-mtu.sh's MODE=off, not the product.

Before 0.152.1 an interface with no `mtu` statement had VPP's own 9000:
config.startup_default_mtu() answered VPP_DEFAULT_MTU for a startup.conf with
no `ethernet { default-mtu }`, and the tuner wrote none (the lab's off run
renders that stanza with the tuner's BOX_DEFAULT_MTU set to 9000, and runs no
pin, since that build had none). This puts the old answer back; nothing else
about the VTY changes.
"""
import re
import sys

sys.path.insert(0, "/src/src/prismash")
import config  # noqa: E402
import prismash  # noqa: E402


def _vpp_default(text):
    m = re.search(r"\bethernet\s*\{[^}]*?\bdefault-mtu\s+(\d+)", text or "",
                  re.S)
    if m and config.MTU_MIN <= int(m.group(1)) <= config.MTU_MAX:
        return int(m.group(1))
    return config.VPP_DEFAULT_MTU


prismash.startup_default_mtu = _vpp_default
sys.exit(prismash.main())
