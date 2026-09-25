"""
Fixtures written in the model's flat CGNAT statements, as the `nat` > `cgnat`
block a saved file has held since #446.

Suites that exercise what the parser does with a pool's lines build their
files flat, because that is the shape the statement under test is easiest to
read in. This turns them into the file format through the product's own
writer, config.nest_cgnat, so the fixture and the box cannot disagree about
the format. A suite asserting that the one-line form is REFUSED calls
Config.parse directly and does not come through here.
"""
import re

import config

_ONE = re.compile(r"^(nat )?cgnat \S")


def in_nat_block(text):
    """`text` with its flat CGNAT statements rewritten as the block."""
    out, flat, in_pool = [], [], False

    def flush():
        if flat:
            out.extend(config.nest_cgnat(flat))
            flat.clear()

    for line in text.split("\n"):
        if _ONE.match(line):
            stmt = line[4:] if line.startswith("nat ") else line
            flat.append(stmt)
            in_pool = stmt.startswith("cgnat pool ")
            continue
        if in_pool and line.startswith(" "):
            flat.append(line)
            if line.strip() == "exit":
                in_pool = False
            continue
        in_pool = False
        flush()
        out.append(line)
    flush()
    return "\n".join(out)


def parse_nested(text):
    """Config.parse of a flat fixture, in the file format (#446)."""
    return config.Config.parse(in_nat_block(text))
