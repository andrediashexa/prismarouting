# The pair of readers that refuse a message no box printed, and a line every
# box printed. Sourced by the suite that arranges the boxes.
#
# WHY A HOLDER AND NOT A THIRD COPY OF THE READER. test/test_selftest.sh grew
# this pair for appliance/selftest after a hand sweep found seven branches
# nothing in this repository had ever executed - one of them the VTY dying at
# start, which is the defect of 0.2.0. #364 measured the same gap on
# appliance/hexarouting-install: nearly every message it can print had been
# produced by none of the four suites that drive it, and the ones that had not
# run are the screens an operator meets after mistyping a letter at a prompt.
# appliance/bind-dataplane-nic and appliance/apply-tuning are the same shape
# again. Writing the reader a second time inside test/test_installer.sh and a
# third time beside it is how a rule ends up with three spellings and two of
# them wrong, which is the argument #311 makes about verdict readers. So the
# reader lives here, once, and a suite brings the boxes.
#
# WHAT A SUITE HAS TO DO:
#
#   . "$(dirname "$0")/say-coverage.sh"
#   say_init <program>          before the first box
#   <output> | say_box          for every box
#   say_report <label>          at the end
#
# say_report calls the suite's own ok() and bad(), because the verdict is the
# suite's to count - exactly as it is in test_selftest.sh, where this pair sits
# at the bottom of the file that arranged the boxes.
#
# A BOX IS A RUN OF THE PROGRAM, whole or at its library seam, with its output
# kept. The suite decides what a box is; this file only reads what came out.

# THE NEEDLE FLOOR, in literal characters, and the same 8 test_selftest.sh
# uses. A message made almost entirely of interpolated value cannot be
# recognised in a run at all - `printf '  %s) %-14s %6s  %s\n'` is a menu row
# and prints whatever it was handed - and demanding a box for one would be
# this reader reporting a branch as uncovered because it could not look. Those
# are counted and named as unreadable rather than passed over in silence.
SAY_MIN=${SAY_MIN:-8}

# WHICH LANGUAGE THE SOURCE IS IN, because the syntax a value is written in
# differs and the rule about it does not. `say_messages` reads shell;
# `say_messages_c` reads C. Both hand back the same rows, both cut rather than
# glob, and everything below this line - the floor, the most-specific-message
# walk, both halves and the report - is the same code for either. A second
# reader for C would have been a second spelling of the needle rule, which is
# the thing this file exists to stop.
say_init() {   # <program source> [sh|c] [printers, for c]
  SAY_SRC=$1
  SAY_LANG=${2:-sh}
  SAY_PRINTERS=${3:-}
  # WHAT A BOX IS MADE OF. "run" means every line in it came out of the
  # program; "transcript" means the program's output is a few lines among
  # somebody else's. It decides what the second half below can honestly ask,
  # and the argument is there. A suite that boxes transcripts sets it after
  # this call.
  SAY_BOX_IS=run
  SAY_DIR=$(mktemp -d)
  : > "$SAY_DIR/every-line"
  : > "$SAY_DIR/per-box-lines"
  : > "$SAY_DIR/boxes"
}

# One box's output, on stdin.
#
# Kept twice: whole, for the half that asks whether a message was ever
# printed, and collapsed to distinct lines for the half that asks whether a
# line was printed by EVERY box - so a line printed twice on one box does not
# read as two boxes.
say_box() {
  _sb=$(cat)
  # A run that printed nothing is not a box: there is nothing in it to read,
  # and counting it would weaken the "printed by every box" half below to
  # nothing at all - no line can be printed by every box once one of them
  # prints none.
  [ -n "$_sb" ] || return 0
  printf '%s\n' "$_sb" >> "$SAY_DIR/every-line"
  printf '%s\n' "$_sb" | sed 's/^ *//' | sort -u >> "$SAY_DIR/per-box-lines"
  # COUNTED IN A FILE AND NOT IN A VARIABLE, because the natural way to call
  # this is `<run> | say_box`, and the right-hand side of a pipeline is a
  # subshell whose variables die with it. A box counter that silently stayed
  # at zero would take the floor below down with it and report the whole
  # reader as having asked nothing.
  echo box >> "$SAY_DIR/boxes"
  # AND KEPT WHOLE, ONE FILE PER BOX, for the half below that asks what EVERY
  # box has in common. Numbered off the file just written, for the same reason
  # the count is in a file: a counter in a variable dies with the pipeline.
  printf '%s\n' "$_sb" > "$SAY_DIR/box-$(grep -c . "$SAY_DIR/boxes")"
}

# Every printer this program has, DERIVED from it rather than listed here.
#
# echo and printf to start with, and then any function that hands one of its
# own arguments to a printer is itself a printer: that is what say(), die(),
# fatal(), ask(), ask_secret(), _vok() and _vbad() are in the installer, and
# an eighth added tomorrow is found by the same pass. A list typed here
# instead would be a list that goes quietly out of date, and a printer left
# off it is a whole screen this reader reports as covered because it never
# went looking for it - which is #311's defect wearing the check's clothes.
#
# It over-derives, deliberately, and that is inert: ipv4_ok() and letter_at()
# come out as printers because they read $1 and contain a printf, and every
# call to them passes a variable, so there is no literal left to hunt for and
# nothing is demanded. Under-deriving is the failure that matters and this
# errs the other way.
say_printers() {
  _p="echo printf"
  _n=0
  while [ "$_n" -lt 5 ]; do
    _more=$(awk -v known="$_p" '
      BEGIN { split(known, k, " "); for (i in k) is[k[i]] = 1 }
      /^[ \t]*#/ { next }
      # A one-line definition carries its own body; a top-level one runs to a
      # closing brace in the first column, which is the shape this repository
      # writes.
      match($0, /^[ \t]*[A-Za-z_][A-Za-z0-9_]*\(\)/) {
        name = substr($0, RSTART, RLENGTH)
        sub(/^[ \t]*/, "", name); sub(/\(\)/, "", name)
        if ($0 ~ /\{.*\}[ \t]*$/) { check(name, $0); next }
        fn = name; body = $0; next
      }
      fn != "" {
        body = body "\n" $0
        if ($0 ~ /^\}/) { check(fn, body); fn = "" }
      }
      function check(n, b,   w) {
        if (is[n]) return
        for (w in is)
          if (b ~ ("(^|[ \t;|&({])" w "[ \t]") && b ~ /\$\*|\$@|\$[1-9]/) {
            print n; return
          }
      }
    ' "$SAY_SRC")
    [ -n "$_more" ] || break
    _p="$_p $(printf '%s\n' "$_more" | sort -u | tr '\n' ' ')"
    _n=$((_n + 1))
  done
  printf '%s' "$_p"
}

# Every message the program can print, one row of "<source line><tab><needle>"
# per call site.
#
# THE NEEDLE IS THE MESSAGE WITH EVERY VALUE CUT OUT, not globbed over, and
# test_selftest.sh's comment says why: "$nbad file(s) ..." hunted under the
# variable's NAME would be looked for under a string no box prints, and the
# rule would go red on correct work. A check that goes red on correct work is
# one people learn to run around, which is #333.
#
# EVERY literal fragment, in order, and not just the longest. Two call sites
# can share their longest chunk and differ only in what follows: measured on
# this program, "  there is no disk '" is the longest chunk of the refusal in
# the install menu and of the one in the mirror menu, and a reader keyed on
# the longest chunk alone cannot tell those apart.
#
# WHAT IS NOT A PRINTED LINE, each of which this reader would otherwise demand
# a box for and never get one:
#
#   - a comment;
#   - a here-document body, which is the bytes of a file being written;
#   - a brace group redirected to a file. write_network_config() is twenty
#     echos inside `{ ... } > "$_r$MGMT_CONF"`, and that is the management
#     stanza, not a screen - test/test_installer.sh reads that file back by
#     name, in a section of its own;
#   - a line whose own output is piped or redirected to a file, which is how
#     `printf 'label: gpt\n,,R\n' | sfdisk` writes a partition table;
#   - an unquoted argument: `echo 0` inside an arithmetic expansion is a
#     value, not a screen;
#   - the DEFINITION of a printer. `_vok() { echo "  [ ok ] $*"; }` is the
#     printer itself, and its nine literal characters would be a needle that
#     every verify line contains and no line is spent on, so it could never go
#     green. The printers are derived above, so this cuts exactly them.
say_messages() {   # <printers>
  awk -v printers="$1" -v sq="'" '
    BEGIN { split(printers, p, " ")
            for (i in p) if (p[i] != "") {
              pr = (pr == "" ? p[i] : pr "|" p[i]); isp[p[i]] = 1 } }
    /^[ \t]*#/ { next }
    hd != "" { if ($0 ~ ("^[ \t]*" hd "[ \t]*$")) hd = ""; next }
    match($0, "<<-?" sq "?[A-Za-z_][A-Za-z0-9_]*" sq "?") {
      hd = substr($0, RSTART, RLENGTH); gsub("^<<-?", "", hd); gsub(sq, "", hd)
    }
    # A printer being DEFINED, not called.
    match($0, /^[ \t]*[A-Za-z_][A-Za-z0-9_]*\(\)/) {
      dn = substr($0, RSTART, RLENGTH)
      sub(/^[ \t]*/, "", dn); sub(/\(\)/, "", dn)
      if (isp[dn]) next
    }
    # A brace group whose result goes to a file. The close is the line that
    # carries the redirection, so the group is held back until then and
    # dropped if it turns out to have been a file.
    /^[ \t]*\{[ \t]*$|^[ \t]*\{[ \t]+/ { grp = 1; gn = 0 }
    grp && /^[ \t]*\}[ \t]*>/ { grp = 0; gn = 0; next }
    grp && /^[ \t]*\}/ { grp = 0; for (i = 1; i <= gn; i++) print gbuf[i]; gn = 0 }
    {
      s = $0
      if (match($0, "^[ \t]*(" pr ")[ \t]")) {
        cmd = $0; sub(/^[ \t]*/, "", cmd); sub(/[ \t].*$/, "", cmd)
        sub("^[ \t]*(" pr ")[ \t]+", "", s)
      } else if (match($0, "[);|&{][ \t]*(" pr ")[ \t]")) {
        cmd = substr($0, RSTART, RLENGTH); gsub(/[);|&{ \t]/, "", cmd)
        sub(".*[);|&{][ \t]*(" pr ")[ \t]+", "", s)
      } else next
      # The message is a QUOTED argument. Anything else is a value, or a call
      # to a function this pass over-derived as a printer.
      q = substr(s, 1, 1)
      if (q != "\"" && q != "'"'"'") next
      s = substr(s, 2)
      # WHERE THE OUTPUT WENT, read off what follows the message with the
      # remaining arguments taken out, so a path inside one cannot look like a
      # redirection. >&2 is the console; anything else is a file or a pipe.
      _qi = index(s, q)
      _rest = (_qi > 0) ? substr(s, _qi + 1) : ""
      gsub(/"[^"]*"/, "", _rest)
      if (_rest ~ /\|/ || _rest ~ />[ \t]*[^&]/ || _rest ~ />$/) next
      # The values, punched out BEFORE the closing quote is looked for: the
      # first quote on the line can be one inside a $(...), and cutting there
      # truncates the message and leaves shell in it.
      gsub(/\$\(\([^)]*\)\)/, "\001", s)
      while (sub(/\$\([^()]*\)/, "\001", s)) ;
      gsub(/`[^`]*`/,     "\001", s)
      gsub(/\$\{[^}]*\}/, "\001", s)
      gsub(/\$[A-Za-z_][A-Za-z0-9_]*/, "\001", s)
      gsub(/\$[0-9@*?#]/, "\001", s)
      gsub(/\\./, "\001", s)
      # A printf conversion is a value like any other. Only for printf: a "%"
      # in an echo is a per cent sign an operator reads.
      if (cmd == "printf") gsub(/%[-# +0-9.]*[a-zA-Z]/, "\001", s)
      sub(q ".*$", "", s)
      out = ""; n = split(s, part, "\001")
      for (i = 1; i <= n; i++) if (length(part[i]) > 0)
        out = (out == "" ? part[i] : out "\001" part[i])
      row = FNR "\t" out
      if (grp) { gbuf[++gn] = row } else print row
    }
    END { for (i = 1; i <= gn; i++) print gbuf[i] }
  ' "$SAY_SRC"
}

# The same rows, read out of C instead of out of shell.
#
# WHY A SECOND EXTRACTION AND NOT A SECOND READER. Everything that makes the
# rule a rule is above and below this function: the needle is cut and not
# globbed, every literal fragment is kept in order, the floor is SAY_MIN, and
# the report walks each printed line onto the most specific message it
# carries. None of that is repeated here. What differs is only what a VALUE
# looks like - `$nbad` in a shell script and `%u` in a format string - and a
# file that knew one syntax would have had the whole rule written twice to
# learn the other, which is #311's defect and the reason this holder exists.
#
# THE PRINTERS ARE NAMED BY THE CALLER, not derived as say_printers() derives
# them. The shell derivation works because a shell function that hands `$1` to
# echo is visibly a printer; C has no such shape - `vlib_cli_output` is
# declared in a VPP header this repository does not own, and a derivation over
# the plugin alone would find nothing at all. So the caller states it and the
# floor in say_report catches a name that finds nothing, which is the failure
# that matters.
#
# WHAT IT READS, exactly: the FORMAT STRING of each call, which is the first
# argument that is a string literal, with adjacent literals concatenated -
# clang-format splits one sentence over four source lines and each piece on
# its own is not a message anybody printed.
#
# WHAT IT DOES NOT READ, said out loud because the count below is only honest
# against it:
#
#   - a literal passed as a VALUE rather than as the format. `"   BELOW the
#     floor " rfc " asks for"` is a whole sentence handed to a `%s`, and so is
#     `" - PINNED by a forward, so no hold-down will take it"`. They reach the
#     screen and they are not needles, because the needle rule is written on
#     the format string and a value is what the rule cuts out;
#   - a message built with format() into a vector that some later call prints
#     with "%U" or "%v". `show bpa pool` and `show bpa config` are written
#     that way in their entirety. Those are a separate body of text and giving
#     them a box is a separate measurement - naming them here would move the
#     denominator without moving the evidence.
#
# A comment is not a call site, and a string inside one is not a message: the
# whole file is blanked of comments and character constants first, in place,
# so that every line number below is still the line number in the source.
#
# NOT THE SAME RULE AS test/test_wiki_screens.py, which also reads this C, and
# deliberately not. That one takes every adjacent-literal run in the file,
# whatever printed it, and GLOBS each conversion into a hole, because it is
# matching a published wiki page against a template and the page carries a
# value where the hole is. This hunts a needle inside a captured run, where
# the value is whatever that box happened to produce - so it cuts, for the
# reason test_selftest.sh gives about "$nbad file(s)". Two questions, two
# rules; what must not happen is a third spelling of either.
say_messages_c() {   # <printer name(s)>
  awk -v printers="$1" -v sq="'" '
    BEGIN { NP = split(printers, P, " "); SQ = sq }
    { SRC[FNR] = $0 }
    END {
      N = FNR
      strip()
      for (l = 1; l <= N; l++) {
        s = SRC[l]; off = 0
        while (1) {
          hit = 0
          for (k = 1; k <= NP; k++) {
            p = index(substr(s, off + 1), P[k])
            if (p == 0) continue
            if (hit == 0 || off + p < at) { at = off + p; ln = length(P[k]); hit = 1 }
          }
          if (!hit) break
          off = at
          c = (at == 1) ? " " : substr(s, at - 1, 1)
          if (c ~ /[A-Za-z0-9_]/) continue
          c = substr(s, at + ln, 1)
          if (c ~ /[A-Za-z0-9_]/) continue
          site(l, at + ln)
        }
      }
    }
    # Comments and character constants blanked, byte for byte, so the call
    # sites keep the line numbers a reader will go and look at.
    function strip(   l, s, o, i, L, c, d) {
      INC = 0
      for (l = 1; l <= N; l++) {
        s = SRC[l]; o = ""; i = 1; L = length(s)
        while (i <= L) {
          c = substr(s, i, 1)
          if (INC) {
            if (c == "*" && substr(s, i + 1, 1) == "/") { INC = 0; o = o "  "; i += 2 }
            else { o = o " "; i++ }
            continue
          }
          if (c == "/" && substr(s, i + 1, 1) == "*") { INC = 1; o = o "  "; i += 2; continue }
          if (c == "/" && substr(s, i + 1, 1) == "/") { while (i <= L) { o = o " "; i++ }; continue }
          if (c == "\"") {
            o = o c; i++
            while (i <= L) {
              d = substr(s, i, 1)
              if (d == "\\") { o = o substr(s, i, 2); i += 2; continue }
              o = o d; i++
              if (d == "\"") break
            }
            continue
          }
          if (c == SQ) {
            o = o " "; i++
            while (i <= L) {
              d = substr(s, i, 1)
              if (d == "\\") { o = o "  "; i += 2; continue }
              o = o " "; i++
              if (d == SQ) break
            }
            continue
          }
          o = o c; i++
        }
        SRC[l] = o
      }
    }
    # One call, from the open paren to the one that closes it, however many
    # lines that takes. Parentheses inside a string do not count, which is why
    # this walks characters rather than matching a regexp per line.
    function site(l, i,   depth, started, cl, ci, cs, CL, c, d, txt) {
      depth = 0; started = 0; cl = l; ci = i; txt = ""
      while (cl <= N) {
        cs = SRC[cl]; CL = length(cs)
        while (ci <= CL) {
          c = substr(cs, ci, 1)
          if (c == "\"") {
            txt = txt c; ci++
            while (ci <= CL) {
              d = substr(cs, ci, 1)
              if (d == "\\") { txt = txt substr(cs, ci, 2); ci += 2; continue }
              txt = txt d; ci++
              if (d == "\"") break
            }
            continue
          }
          if (c == "(") { depth++; started = 1 }
          else if (c == ")") depth--
          txt = txt c; ci++
          if (started && depth == 0) { emit(l, txt); return }
        }
        txt = txt " "
        cl++; ci = 1
      }
    }
    function emit(line, call,   i, L, c, e, d, na, cur, args, fmt, res) {
      sub(/^[ \t]*\(/, "", call)
      sub(/\)[ \t]*$/, "", call)
      na = 0; cur = ""; d = 0; L = length(call); i = 1
      while (i <= L) {
        c = substr(call, i, 1)
        if (c == "\"") {
          cur = cur c; i++
          while (i <= L) {
            e = substr(call, i, 1)
            if (e == "\\") { cur = cur substr(call, i, 2); i += 2; continue }
            cur = cur e; i++
            if (e == "\"") break
          }
          continue
        }
        if (c == "(" || c == "[") d++
        else if (c == ")" || c == "]") d--
        else if (c == "," && d == 0) { args[++na] = cur; cur = ""; i++; continue }
        cur = cur c; i++
      }
      args[++na] = cur
      fmt = (na >= 2) ? args[2] : ""
      sub(/^[ \t]+/, "", fmt); sub(/[ \t]+$/, "", fmt)
      # A call whose format is a variable is STILL A ROW, with an empty
      # needle, so it is counted and named as unreadable rather than
      # disappearing from the denominator. A site that vanished would be a
      # branch this reader reported as covered by never having looked at it.
      if (substr(fmt, 1, 1) != "\"") { printf "%d\t\n", line; return }
      res = ""; i = 1; L = length(fmt)
      while (i <= L) {
        c = substr(fmt, i, 1)
        if (c == "\"") {
          i++
          while (i <= L) {
            e = substr(fmt, i, 1)
            if (e == "\\") { res = res substr(fmt, i, 2); i += 2; continue }
            i++
            if (e == "\"") break
            res = res e
          }
          continue
        }
        i++
      }
      printf "%d\t%s\n", line, needle(res)
    }
    # THE VALUES PUNCHED OUT, exactly as the shell pass above punches out
    # $var and $(...): a conversion is a value and the box prints whatever it
    # was handed, so hunting for one under its own spelling would hunt for a
    # string no box prints.
    #
    # The length modifier goes with the conversion and not before it. VPP
    # writes "%Lu" for a u64 and "%llu" beside it; cutting "%L" alone leaves a
    # stray "u" glued to the front of the sentence after it, and "u extensions
    # placed where it says" is a needle no run will ever contain.
    #
    # An ESCAPE cuts too. "\n" is a line break, so a needle that spanned one
    # would be looked for on a line that cannot hold it.
    function needle(s,   out, i, L, c, j, m, k, np, parts, res) {
      out = ""; i = 1; L = length(s)
      while (i <= L) {
        c = substr(s, i, 1)
        if (c == "\\") { out = out "\001"; i += 2; continue }
        if (c == "%") {
          j = i + 1
          while (j <= L && substr(s, j, 1) ~ /[-+ #0-9.*]/) j++
          m = substr(s, j, 2)
          if (m == "hh" || m == "ll") j += 2
          else if (substr(s, j, 1) ~ /[hlLqjzt]/) j++
          if (j <= L) j++
          out = out "\001"; i = j; continue
        }
        out = out c; i++
      }
      np = split(out, parts, "\001"); res = ""
      for (k = 1; k <= np; k++) if (length(parts[k]) > 0)
        res = (res == "" ? parts[k] : res "\001" parts[k])
      return res
    }
  ' "$SAY_SRC"
}

# Which messages a body of captured output carries, one per line printed.
#
# PULLED OUT SO THAT BOTH HALVES USE IT. The half that asks "did any box print
# this" runs it over every box's output at once; the half that asks "did EVERY
# box print this" runs it over one box at a time. Written twice they would be
# two spellings of the same walk, and the walk is the delicate part.
say_match() {   # <messages file> <captured output file>
awk -F'\t' '
  function literal(frags,   i, m, tot) {
    tot = 0; m = split(frags, f, "\001")
    for (i = 1; i <= m; i++) tot += length(f[i])
    return tot
  }
  # In ORDER, and all of them. A line carrying the fragments of the more
  # specific message carries the less specific one as well, so the order
  # walk is what separates them and the total literal length is what decides
  # which message the line is spent on.
  function ordered(line, frags,   i, m, at, p, first) {
    m = split(frags, f, "\001"); at = 1; first = 0
    for (i = 1; i <= m; i++) {
      p = index(substr(line, at), f[i])
      if (p == 0) return 0
      if (first == 0) first = at + p - 1
      at = at + p + length(f[i]) - 1
    }
    SPAN_A = first; SPAN_B = at - 1
    return 1
  }
  NR == FNR { if (literal($2) >= min) need[$2] = 1 ; next }
  # ONE LINE CAN CARRY MORE THAN ONE MESSAGE, and wherever a program asks a
  # question it does: a prompt is a printf with no newline, so what an
  # operator sees on one row of the console is
  #
  #   Type 'yes' to install: error: not confirmed (got 'no')
  #
  # - the question and the refusal together. Spending the line on its single
  # most specific message loses the other, and "not confirmed" then reads as
  # a branch nothing has run while the suite that arranged it is asserting
  # on the very same output. So the best match is taken, its span is CUT
  # OUT, and what is left is read again. The loop ends because the line only
  # ever gets shorter.
  {
    line = $0
    while (1) {
      bestlen = 0; best = ""
      for (nd in need)
        if (ordered(line, nd) && literal(nd) > bestlen) {
          bestlen = literal(nd); best = nd; ba = SPAN_A; bb = SPAN_B
        }
      if (best == "") break
      print best
      line = substr(line, 1, ba - 1) substr(line, bb + 1)
    }
  }
' min="$SAY_MIN" "$1" "$2"
}

# The two halves, and the floor under both of them.
say_report() {   # <what to call the program in the report>
  _what=${1:-$SAY_SRC}

  # THE FLOOR, first, because everything below it reads green on an empty
  # file. A reader that arranged no box, or whose extraction found nothing,
  # has asked nothing at all - which is the shape test_selftest.sh and
  # test_console_width.py both guard against.
  SAY_BOXES=$(grep -c . "$SAY_DIR/boxes" 2>/dev/null || echo 0)
  if [ "$SAY_BOXES" = 0 ] || [ ! -s "$SAY_DIR/every-line" ]; then
    bad "no box's output was kept, so the message readers asked nothing"
    return
  fi
  case ${SAY_LANG:-sh} in
    c) say_messages_c "$SAY_PRINTERS" > "$SAY_DIR/messages" ;;
    *) say_messages "$(say_printers)" > "$SAY_DIR/messages" ;;
  esac
  if [ ! -s "$SAY_DIR/messages" ]; then
    bad "no message could be read out of $_what, so this reader would report \
every branch of it as covered"
    return
  fi

  echo
  echo "=== no message of $_what may have gone unseen: every one has a box ==="
  # READ FROM THE PRODUCT'S SOURCE, against the runs. There is no list here to
  # keep up to date: the call sites are found in the program itself, so a
  # message added there without a box added here goes red on the next run.
  #
  # BY MESSAGE AND NOT BY LINE. The installer writes "no root password given;
  # refusing to install a box nobody can log into" at three call sites and
  # "  there is no disk '...' in that list." at two, and nothing reading what
  # came out can tell
  # which of them printed: they are one message, and a box that produces it
  # has run one of them. Keyed by line instead, two of the three could never
  # go green and the rule would have to carry an exception - which is the one
  # thing test_selftest.sh says three times it must not.
  say_match "$SAY_DIR/messages" "$SAY_DIR/every-line" \
    | sort -u > "$SAY_DIR/seen"

  # One row per distinct message: "<line>[,<line>...]<tab><needle>", and the
  # under-floor call sites counted separately.
  awk -F'\t' '
      { lit = $2; gsub(/\001/, "", lit)
        if (length(lit) < min) { print "-" > un ; next }
        seen[$2] = seen[$2] (seen[$2] == "" ? "" : ",") $1 }
      END { for (n in seen) print seen[n] "\t" n }
    ' min="$SAY_MIN" un="$SAY_DIR/unreadable" "$SAY_DIR/messages" \
    | sort -n > "$SAY_DIR/distinct"
  _unreadable=$(grep -c . "$SAY_DIR/unreadable" 2>/dev/null || echo 0)
  _msgs=$(grep -c . "$SAY_DIR/distinct")
  _prog=$(printf '%s' "$_what" | sed 's|.*/||')
  : > "$SAY_DIR/unseen"
  while IFS="$(printf '\t')" read -r _lines _needle; do
    grep -qxF -- "$_needle" "$SAY_DIR/seen" || \
      printf '    %s:%s  %s\n' "$_prog" "$_lines" \
        "$(printf '%s' "$_needle" | tr '\001' '~')" >> "$SAY_DIR/unseen"
  done < "$SAY_DIR/distinct"
  _unseen=$(cat "$SAY_DIR/unseen")

  if [ -n "$_unseen" ]; then
    bad "$_what can print these and no box here ever made it do so, which is \
a branch that has never run anywhere - add a box, do not add an \
exception:
$_unseen"
  else
    ok "$_msgs message(s) $_what can print, over $SAY_BOXES boxes, and a box \
for every one"
  fi
  # SAID OUT LOUD ON THE GREEN RUN TOO. These are the call sites whose text is
  # so nearly all interpolated value that nothing could recognise them in a
  # run - menu rows, and `printf '%s\n' "$_a"`. They are not excused messages;
  # they are lines with no words of their own, and the count is here so that a
  # message quietly losing its words shows up as this number moving.
  [ "$_unreadable" = 0 ] || \
    ok "and $_unreadable call site(s) carry fewer than $SAY_MIN literal \
characters: those are values, not messages, and no run can recognise them"

  echo
  # WHAT A BOX IS MADE OF, which decides what this half can honestly ask.
  #
  #   run        every line in the box came out of the program, because the
  #              box IS the program run once. A line on every box is then a
  #              branch of the program whose other form nothing has seen, and
  #              that is what found the three stubs in test_selftest.sh.
  #
  #   transcript the program's output is a few lines among somebody else's.
  #              A lab transcript is eight hundred lines of container build,
  #              vppctl and packet counts with a dozen `show bpa` screens in
  #              it, and "a line every box printed" there is "LAB PASSED" and
  #              the verdict banner - true, and a statement about the lab
  #              harness rather than about the program. Asked of the program
  #              the question is the same one a word later: is there a MESSAGE
  #              every box printed, which is a branch whose absence nothing
  #              has seen.
  #
  # Not a weakening: the transcript form is stricter about what it looks at
  # and identical in what it refuses. A rule that went red on every all-green
  # lab suite would be a rule people run around, which is #333.
  case ${SAY_BOX_IS:-run} in
  transcript)
    : > "$SAY_DIR/per-box-msgs"
    _b=1
    while [ "$_b" -le "$SAY_BOXES" ]; do
      say_match "$SAY_DIR/messages" "$SAY_DIR/box-$_b" | sort -u \
        >> "$SAY_DIR/per-box-msgs"
      _b=$((_b + 1))
    done
    echo "=== and no message of $_what may have been printed by every box ==="
    _always=$(sort "$SAY_DIR/per-box-msgs" | uniq -c \
              | awk -v n="$SAY_BOXES" '$1 == n' | sed 's/^ *[0-9]*  *//' \
              | tr '\001' '~' | grep . || true)
    if [ -n "$_always" ]; then
      bad "these message(s) were printed by all $SAY_BOXES boxes, so nothing \
here has ever seen the program not print them:"
      printf '%s\n' "$_always" | sed 's/^/    /'
    else
      ok "$SAY_BOXES boxes, and every message has a box that does not print it"
    fi
    return
    ;;
  esac

  echo "=== and no line of $_what may have been printed by every box ==="
  # THE OTHER HALF, and they are not the same thing. The half above refuses a
  # message printed by NO box, which is a branch nothing has run. This one
  # refuses a line printed by EVERY box, which is a branch whose other form
  # nothing has seen: whatever decided it answered the same way every time.
  #
  # In test_selftest.sh this found three stubs that answered a constant, one
  # of them making "binary API NOT registered" unreachable on a box that
  # routes and answers and cannot be configured at all. The same shape here is
  # a rig every box shares - one disk table, one answer file, one fake root -
  # which is a suite measuring its fixture rather than the program.
  #
  # MEASURED FROM THE RUNS, not from the source. A fixture that answers a
  # constant looks like any other fixture; what gives it away is every box
  # being told the same thing.
  #
  # The rule has no exceptions in it, deliberately. When it goes red the
  # repair is to arrange a box where that line does NOT appear, not to excuse
  # the line: an excused line is one nobody re-examines.
  _always=$(sort "$SAY_DIR/per-box-lines" | uniq -c \
            | awk -v n="$SAY_BOXES" '$1 == n' | sed 's/^ *[0-9]*  *//' \
            | grep . || true)
  if [ -n "$_always" ]; then
    bad "these line(s) were printed by all $SAY_BOXES boxes, so nothing here \
has ever seen the program not print them:"
    printf '%s\n' "$_always" | sed 's/^/    /'
  else
    ok "$SAY_BOXES boxes, and every line has a box that does not print it"
  fi
}
