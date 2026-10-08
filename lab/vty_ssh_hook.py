#!/usr/bin/env python3
"""
prismash, with the sshd writer of before #593 - a lab fixture for
lab/vty-ssh.sh's off run, not the product.

LISTEN=off puts back what 0.152.4 wrote for `ssh listen-address`: one
`ListenAddress`, the first address, and `0.0.0.0` for a box that named
none - and loads a saved lone wildcard as written (no ssh_listen_legacy).
The prompt, the reader and the commit check are this release's, so what the
off run turns red is what the writer and the load do, and nothing else.
"""
import os
import sys

sys.path.insert(0, "/src/src/prismash")
import system  # noqa: E402

if os.environ.get("LISTEN") == "off":
    def _one_address(value):
        words = system.listen_addresses(value)
        return [f"ListenAddress {words[0] if words else '0.0.0.0'}"]
    system.ssh_listen_lines = _one_address
import prismash  # noqa: E402

if os.environ.get("LISTEN") == "off":
    prismash.ssh_listen_legacy = lambda saved, running: saved
sys.exit(prismash.main())
