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

say_init() {   # <program source>
  SAY_SRC=$1
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
  _printers=$(say_printers)
  say_messages "$_printers" > "$SAY_DIR/messages"
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
  ' min="$SAY_MIN" "$SAY_DIR/messages" "$SAY_DIR/every-line" \
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
