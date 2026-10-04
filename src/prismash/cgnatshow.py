"""
The CGNAT's `show` screens as fields (#558): what `show cgnat translation`,
`top`, `drops`, `blocks`, `identity`, `history verify`, `alg` and `forward`
print, read back into the dicts OperationalService's typed rpcs answer with.

THE SCREEN IS THE SOURCE, and that is the design rather than a shortcut.
These eight are the dataplane's own commands, forwarded word for word by the
VTY (Shell.show_or_pass); the rpc asks the VTY for the same screen through
the same Shell and reads it here, so a typed answer and the screen an
operator reads cannot disagree - there is one reading of the dataplane, not
two. test/test_cgnatshow.py holds every pattern below to the format string
in src/plugins/bpa that prints it, so a change of wording there turns a test
red instead of a field reading 0.

Each parser takes the screen's text and returns a dict; none of them raises
on a screen it does not recognise - the fields stay at their zero value and
the rpc carries the text beside them.
"""
import re

#: A pool's name as the dataplane prints it: one word.
_POOL = r"[^\s,]+"

#: How the dataplane refuses a CLI command: the command's path, a colon, the
#: reason - "show bpa translation: 9.9.9.9 is not a public address of any
#: pool" - or VPP's own parser's "unknown input".
_REFUSAL = re.compile(r"^(show bpa [a-z ]+: \S.*|unknown input\b.*)$")


def screen_error(text):
    """
    The dataplane's refusal, when the screen is one; "" otherwise.

    NOT the VTY's VPP_ERROR, which takes any first line shaped `words: text`
    for an error and so counts "no identity table: every record says
    'unknown'" and "configuration trail: /var/log/..." as failures - measured
    in lab/api-grpc.sh, where GetIdentity and VerifyHistory answered
    UNAVAILABLE on two good screens. A refusal here is the command's own path
    before the colon.
    """
    first = next((l for l in text.splitlines() if l.strip()), "")
    return first.strip() if _REFUSAL.match(first.strip()) else ""


#: TranslationSource's names, by what the screen says.
SOURCE_FORMULA = "formula"
SOURCE_RESERVE = "reserve"
SOURCE_DYNAMIC = "dynamic"
SOURCE_OUTSIDE = "outside_range"


# ------------------------------------------------- show cgnat translation
def parse_translation(text):
    """
    `show bpa translation <public> <port>`: who is behind it, from where the
    answer comes, and whether it holds for any instant or only for now.
    """
    out = {"public_address": "", "port": 0, "subscriber": "", "pool": "",
           "block": 0, "source": "", "nat64": False, "now_only": False}
    m = re.search(r"^(\S+):(\d+) belongs to pool (" + _POOL + r") but to no block",
                  text, re.M)
    if m:
        out.update(public_address=m.group(1), port=int(m.group(2)),
                   pool=m.group(3), source=SOURCE_OUTSIDE)
        return out
    m = re.search(r"^(\S+):(\d+)  ->  (.+?)\s*$", text, re.M)
    if not m:
        return out
    out.update(public_address=m.group(1), port=int(m.group(2)))
    who = m.group(3)
    out["subscriber"] = "" if who.startswith("nobody") else who
    m = re.search(r"^  pool (" + _POOL + r"), (reserve block|block) (\d+), "
                  r"(DETERMINISTIC NAT64|DETERMINISTIC RESERVE|DETERMINISTIC|DYNAMIC)",
                  text, re.M)
    if m:
        out["pool"], out["block"] = m.group(1), int(m.group(3))
        kind = m.group(4)
        out["source"] = {"DETERMINISTIC NAT64": SOURCE_FORMULA,
                         "DETERMINISTIC": SOURCE_FORMULA,
                         "DETERMINISTIC RESERVE": SOURCE_RESERVE,
                         "DYNAMIC": SOURCE_DYNAMIC}[kind]
        out["nat64"] = kind == "DETERMINISTIC NAT64" or ":" in out["subscriber"]
        out["now_only"] = out["source"] != SOURCE_FORMULA
    return out


# --------------------------------------------------------- show cgnat top
_TOP_ROW = re.compile(r"^(\S+)\s+(\S+)\s+(\d+)/(\d+)\s+(\d+)%\s+(\d+)\s+(\d+)\s*$")


def parse_top(text):
    """`show bpa top [<n>]`: the rows, most nearly full first, and the count."""
    rows = []
    for line in text.splitlines():
        m = _TOP_ROW.match(line)
        if m:
            rows.append({"subscriber": m.group(1), "pool": m.group(2),
                         "ports_used": int(m.group(3)),
                         "ports_total": int(m.group(4)),
                         "percent_full": int(m.group(5)),
                         "blocks": int(m.group(6)),
                         "refusals": int(m.group(7))})
    m = re.search(r"^(\d+) subscriber\(s\) holding blocks", text, re.M)
    return {"rows": rows, "holding": int(m.group(1)) if m else 0}


# ------------------------------------------------------- show cgnat drops
#: The dataplane's node names, as the screen heads them, and the API's.
DROP_NODES = {"bpa-in2out": "in2out", "bpa-out2in": "out2in"}


def parse_drops(text):
    """
    `show bpa drops`: per node, each counter that is not zero, with whether
    it counts packets that did not get through. The node is there with no
    counter when it counted nothing.
    """
    nodes, node, dropped = [], None, False
    for line in text.splitlines():
        if line.strip() in DROP_NODES and not line.startswith(" "):
            node = {"node": DROP_NODES[line.strip()], "counters": []}
            nodes.append(node)
            dropped = False
            continue
        if node is None:
            continue
        s = line.strip()
        if s == "packets that did not get through":
            dropped = True
        elif s == "everything else it counted":
            dropped = False
        else:
            m = re.match(r"^\s+(\d+)  (\S.*?)\s*$", line)
            if m:
                node["counters"].append({"reason": m.group(2),
                                         "count": int(m.group(1)),
                                         "dropped": dropped})
    return {"nodes": nodes}


# ------------------------------------------------------ show cgnat blocks
_THREAD = re.compile(
    r"^  thread (\d+): (\d+)/(\d+) blocks in use, (\d+) allocations "
    r"\((\d+) extensions\), (\d+) releases, (\d+) refusals, (\d+) pinned")
_TOTAL = re.compile(
    r"^  total: (\d+)/(\d+) in use, (\d+) allocations \((\d+) extensions\), "
    r"(\d+) releases, (\d+) refusals, (\d+) pinned")


def _counts(m, first):
    """The six counters a thread row and a total row share, from group `first`."""
    g = [int(m.group(first + i)) for i in range(7)]
    return {"in_use": g[0], "blocks": g[1], "allocations": g[2],
            "extensions": g[3], "releases": g[4], "refusals": g[5],
            "pinned": g[6]}


def parse_blocks(text):
    """
    `show bpa blocks`: every pool that allocates blocks - a dynamic one, or a
    deterministic one with a reserve - per worker and in total.
    """
    pools, p, thread = [], None, None
    for line in text.splitlines():
        m = re.match(r"^pool (" + _POOL + r") \(hold-down (\d+)s\)$", line)
        if m:
            p = {"pool": m.group(1), "reserve": False, "holddown_seconds": int(m.group(2)),
                 "reserve_per_address": 0, "reserve_blocks": 0, "threads": [],
                 "total": None, "stride": 0, "stride_placed": 0,
                 "stride_fell_back": 0, "paired": 0, "paired_of": 0,
                 "paired_shown": False, "reserve_exhausted": 0}
            pools.append(p)
            continue
        m = re.match(r"^pool (" + _POOL + r") \(deterministic reserve, (\d+) block\(s\) "
                     r"per public address, (\d+) in all, hold-down (\d+)s\)$", line)
        if m:
            p = {"pool": m.group(1), "reserve": True, "holddown_seconds": int(m.group(4)),
                 "reserve_per_address": int(m.group(2)),
                 "reserve_blocks": int(m.group(3)), "threads": [], "total": None,
                 "stride": 0, "stride_placed": 0, "stride_fell_back": 0,
                 "paired": 0, "paired_of": 0, "paired_shown": False,
                 "reserve_exhausted": 0}
            pools.append(p)
            continue
        if p is None:
            continue
        m = _THREAD.match(line)
        if m:
            thread = dict(_counts(m, 2), thread=int(m.group(1)), lent=0)
            p["threads"].append(thread)
            continue
        m = re.match(r"^\s+(\d+) lent to another thread", line)
        if m and thread is not None:
            thread["lent"] = int(m.group(1))
            continue
        m = _TOTAL.match(line)
        if m:
            p["total"] = _counts(m, 1)
            continue
        m = re.match(r"^  stride (\d+): (\d+) extensions placed where it says, "
                     r"(\d+) fell back", line)
        if m:
            p["stride"], p["stride_placed"], p["stride_fell_back"] = (
                int(m.group(1)), int(m.group(2)), int(m.group(3)))
            continue
        m = re.match(r"^  RFC 4787 REQ-2 paired: (\d+) of (\d+) extension", line)
        if m:
            p["paired"], p["paired_of"] = int(m.group(1)), int(m.group(2))
            p["paired_shown"] = True
            continue
        m = re.match(r"^  reserve exhausted (\d+) time", line)
        if m:
            p["reserve_exhausted"] = int(m.group(1))
    return {"pools": pools}


# ---------------------------------------------------- show cgnat identity
def parse_identity(text):
    """
    `show bpa identity`: how many identities and when they were read, and
    RADIUS accounting's counters while it listens. Never an entry: the screen
    prints none, and neither does this (#21).
    """
    out = {"loaded": False, "entries": 0, "read_at": "", "radius": False,
           "radius_port": 0, "received": 0, "applied": 0, "answered": 0,
           "bad_authenticator": 0, "malformed": 0, "truncated_names": 0,
           "name_limit": 0}
    m = re.search(r"^identity table: (\d+) entry\(s\), read (.+?)\s*$", text, re.M)
    if m:
        out.update(loaded=True, entries=int(m.group(1)), read_at=m.group(2))
    m = re.search(r"^radius accounting: listening on port (\d+)", text, re.M)
    if m:
        out.update(radius=True, radius_port=int(m.group(1)))
    m = re.search(r"^  packets    (\d+) received, (\d+) applied, (\d+) answered",
                  text, re.M)
    if m:
        out.update(received=int(m.group(1)), applied=int(m.group(2)),
                   answered=int(m.group(3)))
    m = re.search(r"^  refused    (\d+) bad authenticator, (\d+) malformed", text, re.M)
    if m:
        out.update(bad_authenticator=int(m.group(1)), malformed=int(m.group(2)))
    m = re.search(r"^  (\d+) name\(s\) were longer than (\d+) characters", text, re.M)
    if m:
        out.update(truncated_names=int(m.group(1)), name_limit=int(m.group(2)))
    return out


# ---------------------------------------------- show cgnat history verify
#: HistoryVerdict's names, by what the screen says.
VERDICTS = ("intact", "no_trail", "no_chained_entries", "altered", "removed",
            "truncated_mid_entry", "truncated_end")


def parse_history_verify(text):
    """
    `show bpa pool history verify`: the trail's path, the verdict, and the
    entry or the numbers the verdict names. The verdict is the worst thing
    the screen says, in the order the dataplane checks.
    """
    out = {"path": "", "verdict": "", "checked": 0, "unchained": 0,
           "unwritten": 0, "entry": 0, "entry_text": "", "removed": 0,
           "head_seq": 0, "head_sha256": "", "file_ends_at": 0,
           "dataplane_wrote_to": 0}
    m = re.search(r"^configuration trail: (.+?)\s*$", text, re.M)
    if m:
        out["path"] = m.group(1)
    if re.search(r"^no trail at (.+?)\s*$", text, re.M):
        out["path"] = out["path"] or re.search(r"^no trail at (.+?)\s*$",
                                               text, re.M).group(1)
        out["verdict"] = "no_trail"
        return out
    m = re.search(r"^  (\d+) entr(?:y|ies) carr(?:ies|y) no digest", text, re.M)
    if m:
        out["unchained"] = int(m.group(1))
    m = re.search(r"^  INCOMPLETE: (\d+) entr", text, re.M)
    if m:
        out["unwritten"] = int(m.group(1))
    m = re.search(r"^  (\d+) entr(?:y|ies) checked, chain intact", text, re.M)
    if m:
        out["checked"] = int(m.group(1))
        out["verdict"] = "intact"
    m = re.search(r"^  head  seq (\d+) sha256 ([0-9a-f]+)", text, re.M)
    if m:
        out["head_seq"], out["head_sha256"] = int(m.group(1)), m.group(2)
    if re.search(r"^  no chained entries: nothing here is covered", text, re.M):
        out["verdict"] = "no_chained_entries"
    m = re.search(r"^  TRUNCATED: the file ends at entry (\d+) and this dataplane "
                  r"wrote up to (\d+) - (\d+) entr", text, re.M)
    if m:
        out.update(verdict="truncated_end", file_ends_at=int(m.group(1)),
                   dataplane_wrote_to=int(m.group(2)), removed=int(m.group(3)))
    m = re.search(r"^  TRUNCATED: the file ends mid-entry\n    (.*)$", text, re.M)
    if m:
        out.update(verdict="truncated_mid_entry", entry_text=m.group(1))
    m = re.search(r"^  BROKEN before entry (\d+)\n    the sequence jumps from (\d+) "
                  r"to (\d+): (\d+) entr", text, re.M)
    if m:
        out.update(verdict="removed", entry=int(m.group(1)), removed=int(m.group(4)))
    m = re.search(r"^  BROKEN at entry (\d+)\n    (.*)$", text, re.M)
    if m:
        out.update(verdict="altered", entry=int(m.group(1)), entry_text=m.group(2))
    return out


# --------------------------------------------------------- show cgnat alg
def parse_alg(text):
    """`show bpa alg`: each pool with each ALG on or off, as the dataplane has it."""
    pools, p = [], None
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line.startswith(" "):
            if line.strip() == "no pools configured":
                break
            p = {"pool": line.strip(), "algs": []}
            pools.append(p)
            continue
        # `%-3s` pads "on" to three, then two spaces: the description is
        # whatever follows the run of spaces.
        m = re.match(r"^  (\S+)\s+(on|off)\s+(.*?)\s*$", line)
        if m and p is not None:
            p["algs"].append({"alg": m.group(1), "on": m.group(2) == "on",
                              "description": m.group(3)})
    return {"pools": pools}


# ----------------------------------------------------- show cgnat forward
_FORWARD = re.compile(r"^  (\S+) (\S+):(\d+) -> (\S+):(\d+)\s*$")


def parse_forward(text):
    """
    `show bpa forward [<pool>]`: every port forward the dataplane holds. The
    inside host of a NAT64 pool's forward is IPv6, written with its port after
    the last colon, which is where it is split.
    """
    out, pool = [], ""
    for line in text.splitlines():
        m = re.match(r"^pool (" + _POOL + r")\s*$", line)
        if m:
            pool = m.group(1)
            continue
        m = _FORWARD.match(line)
        if m and pool:
            out.append({"pool": pool, "protocol": m.group(1),
                        "public_address": m.group(2),
                        "public_port": int(m.group(3)),
                        "private_address": m.group(4),
                        "private_port": int(m.group(5))})
    return {"forwards": out}
