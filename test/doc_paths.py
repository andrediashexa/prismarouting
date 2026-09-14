#!/usr/bin/env python3
"""
Which backticked strings in a document are paths into THIS repository.

ONE HOLDER, because two readers ask it. `test/test_doc_commands.py` checks
every path the documents send a reader to, and `test/test_changelog_landing.py`
checks the ones in the section being written. The first version of those two
had the rule written out twice and they immediately disagreed: the changelog
one had no idea that `vnet/error.h` is VPP's header rather than ours, and went
red on a changelog entry whose whole subject was that distinction.

THE RULE, and it replaced an allowlist rather than being a shortcut past one.
A path counts only when its first component is one of this repository's own
top-level directories. Measured before it was written: across `docs/` alone,
280 mentions of a repo-relative-looking path gave 4 that do not exist, and
THREE OF THE FOUR were VPP's own headers - `vnet/error.h`,
`vnet/ethernet/node.c`, `vnet/ipfix-export/ipfix_info_elements.h` - which are
real files on a box and not files here. The fourth was a wiki page named
relative to `wiki/` instead of the root. With the rule: 330 mentions across the
tree, 0 missing. An allowlist of three is an allowlist somebody grows.

Absolute paths never match: `/etc/hexarouting/vpp-base.conf` is a file on the
appliance, and asking this tree for it is asking the wrong machine.
"""
import re

#: This repository's own top-level directories. Anything else is somebody
#: else's tree, or a name relative to a directory this cannot know.
OWN_DIRS = ("src", "appliance", "tools", "test", "lab", "wiki", "docs", "ref")

#: The extensions a document uses when it is naming a file to go and read.
_SUFFIXES = "sh|py|c|h|md|conf|env|list|service|json|yml"

_PATH_RE = re.compile(
    r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:" + _SUFFIXES + r"))`")


def repo_paths(text):
    """Every path into this repository that `text` sends a reader to.

    A list rather than a set: two mentions of one path are two places a reader
    can be sent, and the count is what tells a caller whether it is reading
    anything at all.
    """
    return [m.group(1) for m in _PATH_RE.finditer(text)
            if m.group(1).split("/")[0] in OWN_DIRS]
