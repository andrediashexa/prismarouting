#!/usr/bin/env python3
"""
prismash, with or without `replace pattern` and `rename` - a lab fixture for
lab/replace-rename.sh (#585), not the product.

EDIT=off is the build before #585: the two words are no level's, so a line
starting with either goes where any unknown line went - handed to FRR, which
refuses it - and `?` does not offer them. That is the lab's MODE=off, and
every check that an edit happened, or was refused for its own reason, MUST
go red in it. Nothing else changes.
"""
import os
import sys

sys.path.insert(0, "/src/src/prismash")
import prismash  # noqa: E402

if os.environ.get("EDIT") == "off":
    prismash.EDIT_WORDS = ()
sys.exit(prismash.main())
