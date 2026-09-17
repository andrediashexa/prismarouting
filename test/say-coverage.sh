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

# THE CONSOLE THESE PROGRAMS PRINT TO, in columns, and it is 79 and not 80 for
# the reason appliance/selftest's say_list already folds to 79: a terminal that
# writes the 80th column of an 80-column line has nowhere to put the newline,
# and a serial console then either wraps or swallows it depending on the
# firmware. The same budget src/hexash/config.py declares for the VTY, because
# it is the same screen an operator reads at.
SAY_COLUMNS=${SAY_COLUMNS:-79}

# KEEPING THE ROWS, and OFF unless somebody asks for them.
#
# say_box below already holds every line every box printed, in a mktemp -d that
# dies with the suite. That is right for a check - a check states a verdict and
# owes nobody its working - and it is no good at all for a MEASUREMENT: #367
# was opened off a corpus of 2,143 rows that could only be taken by patching
# say_box in a scratch tree, which is a measurement nobody else can repeat and
# which stops being true the moment the scratch tree is thrown away.
#
# So the rows can be kept, by naming a directory, and nothing is kept when
# nobody names one. Off by default is the whole point: a suite that writes a
# file outside its own tempdir on every run is a suite that writes into
# somebody's repository, and `make test` would start leaving litter behind it.
#
# ONE FILE PER PROGRAM, named after the program, beside a `.src` naming the
# source those rows came out of - so a corpus taken against a scratch tree
# says which tree it was, and two suites pointed at one directory do not
# overwrite each other.
#
#   SAY_CORPUS=/var/tmp/rows sh test/test_installer.sh
#
# AND A `<program>.runs/` DIRECTORY BESIDE THEM, written by say_capture below,
# for the suites that run a program with its output redirected into a file
# rather than through this reader's own pipeline. One knob, two shapes of
# evidence: the pile of rows, and the runs those rows came out of.
#
# It is the same knob tools/run-all-labs.sh grew for #366 - `LAB_OUTPUT=<dir>`,
# named rather than a flag because whoever wants the corpus wants to say where
# it goes - and it keeps that rule: the absence of the variable is the off
# switch, and a default path would be a switch that is always on. The two are
# the same measurement one layer apart, and the C half of this file is read
# from a corpus kept exactly that way.
SAY_CORPUS=${SAY_CORPUS:-}

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
  : > "$SAY_DIR/segments"
  : > "$SAY_DIR/unattributed"
  SAY_KEEP=
  if [ -n "$SAY_CORPUS" ]; then
    # Loud on failure rather than quiet: somebody who asked for the corpus and
    # got no file would read the absence as "this suite prints nothing", which
    # is the reading that starts a measurement off wrong.
    mkdir -p "$SAY_CORPUS" || { echo "SAY_CORPUS=$SAY_CORPUS cannot be made" \
                                     "- no rows will be kept" >&2; return 0; }
    SAY_KEEP=$SAY_CORPUS/$(basename "$SAY_SRC").rows
    : > "$SAY_KEEP"
    printf '%s\n' "$SAY_SRC" > "$SAY_CORPUS/$(basename "$SAY_SRC").src"
  fi
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
  # WHOLE AND UNSORTED, and not the collapsed copy: a corpus is what came out,
  # in the order it came out, and a row that a hundred boxes printed is a fact
  # about this rig that `sort -u` would throw away.
  [ -z "${SAY_KEEP:-}" ] || printf '%s\n' "$_sb" >> "$SAY_KEEP"
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

# A RUN WHOSE OUTPUT WAS CAPTURED INTO A FILE, boxed and kept without changing
# how it was captured (#371).
#
# WHY A THIRD CALLER AND NOT A THIRD KNOB. SAY_CORPUS above keeps the rows of a
# program this file DRIVES, one line at a time through say_box (#367), and
# tools/run-all-labs.sh keeps a lab's whole transcript under LAB_OUTPUT (#366).
# appliance/install.sh is neither shape. Its suites run it with everything it
# prints redirected into a file - `sh "$TREE/appliance/install.sh" > "$2" 2>&1`
# - so NOTHING it prints reaches the suite's own stdout, and a corpus built out
# of suite stdout sees almost none of it. The first measurement taken for #371
# was built exactly that way and reported 71 of 75 messages covered; taken off
# the files the program really wrote, 44 were covered by nothing at all. That
# is the same mistake #366 records against the lab summary logs, which is why
# this is the same variable a third time rather than a third name: whoever
# wants a corpus of what this product says wants one directory and one knob,
# and a second name here is a second thing to remember and get wrong.
#
# AND WHY IT KEEPS THE FILE RATHER THAN TEEING THE PIPELINE. The redirection is
# not an accident to work around. test_install_says.sh keeps stdout and stderr
# in two separate files on purpose, and its comment says why: "a runner that
# merges them cannot ask the question: a warning on stderr followed by exit 0
# and a refusal on stderr followed by exit 1 are the same text in one file".
# #301 is a defect about exactly that pair, so a tee that merged the two
# streams would buy this corpus by taking away the question the suite exists to
# ask. Handed the file the suite already wrote, after the run, this copies it
# and the suite is untouched.
#
# ONE FILE PER RUN, under a directory named after the program and a name made
# of the suite and the run, because which run printed a row is half the corpus:
# `.rows` beside it is every row in one pile, and two suites pointed at one
# directory must not overwrite each other.
#
# AND IT BOXES THE RUN TOO, when the suite that called it has a reader open on
# the same program. A suite with a reader would otherwise write the file, keep
# the file, and then have to hand it to say_box in a second line beside this
# one - two call sites per run, one of which somebody eventually forgets, and a
# run missing from the boxes is a message this reader reports as covered by
# nothing having looked at it. A suite with no reader (test/test_manifest.sh
# runs the installer twice and reads the MANIFEST, not the screen) passes the
# test below and only keeps.
say_capture() {   # <program source> <capture file> <tag for this run>
  # A run that printed nothing is not a box - say_box says why - and it is not
  # a corpus either. test_install_says.sh asserts that two of its runs print
  # NOTHING on stdout, because a refusal that narrated work first is a refusal
  # that came too late, and an empty file kept beside the others would read as
  # a run whose output went missing.
  [ -s "$2" ] || return 0
  [ -z "${SAY_DIR:-}" ] || [ "${SAY_SRC:-}" != "$1" ] || say_box < "$2"
  [ -n "$SAY_CORPUS" ] || return 0
  # Loud on failure rather than quiet, for the reason say_init gives: somebody
  # who asked for the corpus and got no file would read the absence as "this
  # suite prints nothing", which is the reading that starts a measurement off
  # wrong.
  _cr=$SAY_CORPUS/$(basename "$1").runs
  mkdir -p "$_cr" || { echo "SAY_CORPUS=$SAY_CORPUS cannot be made - no runs" \
                            "of $1 will be kept" >&2; return 0; }
  printf '%s\n' "$1" > "$SAY_CORPUS/$(basename "$1").src"
  cp "$2" "$_cr/$(basename "$0" .sh).$3.out"
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

# The printers that do NOT end the row, DERIVED the same way the printers
# themselves are.
#
# WHY ANYTHING HERE CARES. A console row is what an operator reads across, and
# what a box captures is not the same thing: `ask` writes its prompt with
# printf and no newline, so the prompt and whatever is printed next arrive in
# one captured line. `Type yes to install: error: not confirmed` is two rows on
# the box and one row here, and a width measured on it is a width nothing ever
# printed - which is most of what looked over budget when #367 was opened.
#
# A function that hands its argument to a printf whose format does not end in a
# newline is such a printer: that is ask() and ask_secret() in the installer,
# and an `ask_yes_no` added tomorrow is found by the same pass rather than by
# somebody remembering to add it here. Everything else ends its row, because
# echo does.
#
# SHELL ONLY. A C printer is named by its caller rather than derived (there is
# no shape in C that says "this one prints"), and the plugin CLI has no
# prompts: vlib_cli_output writes whole rows. say_report asks for this on the
# shell path and nowhere else.
say_row_enders() {   # <printers>; the ones that do NOT end a row
  awk -v printers="$1" -v sq="'" '
    BEGIN { split(printers, p, " "); for (i in p) is[p[i]] = 1 }
    /^[ \t]*#/ { next }
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
    # A printf in the body that is handed one of the arguments and whose
    # format does not end in a newline. The format is read off the source
    # rather than off what was printed, because a prompt is recognised by what
    # it does NOT write and there is nothing in the output to see.
    function check(n, b,   i, m, parts, f, t, q) {
      if (!is[n]) return
      m = split(b, parts, "\n")
      for (i = 1; i <= m; i++)
        # The FIRST argument, and not any argument: a prompt is the thing a
        # printer is handed to write before it waits. `$4` inside an embedded
        # awk program is not one, and nic_table carries exactly that.
        if (parts[i] ~ /printf/ && parts[i] ~ /\$\*|\$@|\$1([^0-9]|$)/ &&
            parts[i] !~ ("\\\\n[\"" sq "]")) {
          # AND WHAT IT WRITES AFTER THE QUESTION, which is where the row it
          # left open actually ends. ask writes `%s [%s]: ` or `%s: `, so what
          # follows the last value is `]: ` or `: `, and the message printed
          # next begins after that - with its own prefix, which is the seven
          # columns of `error: ` that #362 found the VTY measuring short.
          # Read off the format rather than written here: an installer that
          # ends its questions with `? ` tomorrow is read correctly then.
          f = parts[i]
          sub(/^.*printf[ \t]+/, "", f)
          q = substr(f, 1, 1)
          if (q != "\"" && q != sq) { print n; continue }
          f = substr(f, 2); sub(q ".*$", "", f)
          t = f; sub(/^.*%[-# +0-9.]*[a-zA-Z]/, "", t)
          printf "%s\t%s\n", n, t
        }
    }
  ' "$SAY_SRC"
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
say_messages() {   # <printers> <printers that do not end a row>
  awk -v printers="$1" -v noend="$2" -v sq="'" \
      -v unseen_out="${SAY_DIR:-}/unseen-printers" '
    BEGIN { split(printers, p, " ")
            for (i in p) if (p[i] != "") {
              pr = (pr == "" ? p[i] : pr "|" p[i]); isp[p[i]] = 1 }
            split(noend, ne, " ")
            for (i in ne) if (ne[i] != "") isne[ne[i]] = 1 }
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
      # THE LAST PRINTER ON THE LINE, and the one at the start of it only when
      # there is no other. `ipv4_ok "$_ns" || die "the DNS server ... is not an
      # address"` opens with a printer and PRINTS from the second one: keyed on
      # the first, the message this reader finds is a bare value, the refusal
      # an operator reads is found nowhere, and the row it lands on is spent on
      # whatever else was captured beside it.
      #
      # A PRINTER INSIDE A COMMAND SUBSTITUTION counts, which is how every
      # question in the installer is asked: `_a=$(ask "Install onto which
      # disk" "a")`. Without it a prompt is not a message, and #367 cannot
      # tell the row an operator reads from the refusal captured beside it.
      if (match($0, "[();|&{][ \t]*(" pr ")[ \t]")) {
        cmd = substr($0, RSTART, RLENGTH); gsub(/[();|&{ \t]/, "", cmd)
        sub(".*[();|&{][ \t]*(" pr ")[ \t]+", "", s)
      } else if (match($0, "^[ \t]*(" pr ")[ \t]")) {
        cmd = $0; sub(/^[ \t]*/, "", cmd); sub(/[ \t].*$/, "", cmd)
        sub("^[ \t]*(" pr ")[ \t]+", "", s)
      } else {
        # A PRINTER THIS RULE CANNOT SEE, counted rather than passed over in
        # silence. The two arms above take a printer at the start of a line or
        # after one of `( ) ; | & {`. A shell has other places to put one:
        #
        #   for f in $FILES; do echo "# yours: $f"; done
        #   if [ -n "$d" ]; then printf "%s [%s]: " "$1" "$d"; fi
        #
        # `do`, `then` and `else` are words, not punctuation, so neither arm
        # matches and the message is not looked for at all.
        #
        # Measured over the five programs these readers cover: FOUR such
        # sites, and not one of them hides a screen today. Two are inside
        # ask(), which say_row_enders finds by scanning the function body for
        # a printf regardless of this rule; one is a printer DEFINITION, which
        # this pass excludes deliberately; and one is inside a brace group
        # redirected into a file, which is not a console row either.
        #
        # So it is a limit and not a defect, and the number is printed for the
        # same reason the skip lists are: the day a fifth appears somewhere
        # that does print, somebody sees it rather than the reader quietly
        # reporting full coverage of a program with a screen it never looked
        # for.
        if (match($0, "(^|[ \t])(do|then|else)[ \t]+(" pr ")[ \t]+[\"'"'"']"))
          SAY_UNSEEN_PRINTER[FILENAME ":" FNR] = $0
        next
      }
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
      # THE SHAPE, third, and it is the same string with the empty fragments
      # LEFT IN. The needle drops them because a needle is hunted for and an
      # empty fragment matches everywhere; the width reader below needs them,
      # because a message that opens with a value and one that opens with a
      # word are different rows and the needle cannot tell them apart. One
      # extraction, two readings of it: a second pass over the source for the
      # sake of the shape would be a second answer to "what is a message
      # here", and the day they disagreed the widths would be measured
      # against words the box does not print.
      #
      # WHETHER THE ROW ENDS HERE, fourth. printf is asked of the line it is
      # on, because one printf ends a row and the next does not and the
      # difference is the two characters before the closing quote; every other
      # printer is asked of its name.
      ends = 1
      if (cmd == "printf") ends = ($0 ~ ("\\\\n[\"" sq "]")) ? 1 : 0
      else if (isne[cmd]) ends = 0
      row = FNR "\t" out "\t" s "\t" ends
      if (grp) { gbuf[++gn] = row } else print row
    }
    END {
      for (i = 1; i <= gn; i++) print gbuf[i]
      # To a file and not to stdout, so a caller reading the message rows is
      # not handed a line that is not one, and not to stderr either: say_report
      # holds this to a ceiling, and a notice nothing asserts on is a notice
      # that grows.
      if (unseen_out != "")
        for (k in SAY_UNSEEN_PRINTER)
          print k "\t" SAY_UNSEEN_PRINTER[k] > unseen_out
    }
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
# WHAT IT READS, exactly, and it is TWO POSITIONS in a call and not one:
#
#   - the FORMAT STRING of each call, which is the argument after the sink,
#     with adjacent literals concatenated - clang-format splits one sentence
#     over four source lines and each piece on its own is not a message
#     anybody printed;
#   - every literal passed as an ARGUMENT to that call, one message per
#     branch of a conditional. `cond ? " - PINNED by a forward, so no
#     hold-down will take it" : ""` is a whole sentence handed to a `%s`, and
#     so is `"   BELOW the floor " rfc " asks for"` - the RFC-floor warning
#     that exists to stop somebody setting a timeout that breaks the protocol.
#     They reach the screen. #366 could not see them because the needle rule
#     was written on the format string alone and a value is what that rule
#     cuts out, so every one of them was a branch reported as covered by
#     nothing ever having looked at it.
#
# WHICH ARGUMENTS ARE SENTENCES, and the rule is the FLOOR and not a second
# predicate beside it. An argument branch is kept when its literals, run
# through the same cut the format string gets, carry SAY_MIN characters of
# their own. That is already what the floor says - text that is nearly all
# interpolated value cannot be recognised in a run - and a format fragment is
# only the limiting case of it: `"%s%s"` cuts to nothing, `" "` to one
# character, `", "` to two, and none of the three can be hunted for. Writing a
# separate "is this a format fragment" test would have been a second spelling
# of the floor, and two spellings of one rule is what this whole file exists
# to stop. They are not passed over in silence either: a branch that carries a
# literal and falls under the floor is still a row, counted and named as
# unreadable exactly as an under-floor format string is.
#
# ONE MESSAGE PER BRANCH, because a conditional is where the sentences are.
# `a ? "  (pinned by a forward)" : (i ? "  (extension)" : "")` puts three
# different answers on one line of source and no run ever carries two of them
# at once; joined into one needle they would be a string no box prints, which
# is the defect test_selftest.sh records about hunting a value under its own
# name. The CONDITION is walked as a branch too. That over-reads - a literal
# inside a condition is compared, not printed - and it is the same deliberate
# direction say_printers() takes below: over-reading names something extra,
# under-reading clears a screen nobody looked at.
#
# WHAT IT STILL DOES NOT READ, said out loud because the count is only honest
# against it:
#
#   - a literal assigned to a VARIABLE and printed later. `mode = "strict"` in
#     show bpa urpf, and the node names handed to bpa_show_drops_node(), are
#     sentences this reader cannot reach: the shell pass below derives a
#     printer from a function that hands `$1` to echo, and C has no such shape
#     to derive from;
#   - a message built with format() into a vector that some later call prints
#     with "%U" or "%v", unless `format` is among the printers the caller
#     names. `show bpa pool` and `show bpa config` are written that way in
#     their entirety, and tools/plugin-says.sh names it - with the corpus that
#     pays for it, because a denominator that moved without the evidence
#     moving is how a measurement stops meaning anything.
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
    function emit(line, call,   i, L, c, e, d, na, cur, args, fmt) {
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
      #
      # Its ARGUMENTS are still walked. `vlib_cli_output (vm, fmt, s)` with a
      # sentence in `s` is exactly the shape this half exists to read, and
      # returning here would lose it to the one call whose format nobody can
      # hunt for.
      if (substr(fmt, 1, 1) != "\"") printf "%d\t\n", line
      else printf "%d\t%s\n", line, needle(literals(fmt))
      # THE SECOND BODY. Argument 1 is the sink - `vm`, or the vector format()
      # is appending to - and argument 2 is the format, read above; everything
      # from the third on is a value, and a value that is a literal is a
      # sentence somebody reads off the screen.
      for (i = 3; i <= na; i++) branches(line, args[i])
    }
    # Every conditional branch of one argument, each as its own row. Split at
    # the "?" and ":" that are not inside a nested call, a subscript or a
    # string, because those are the only ones that choose between sentences.
    function branches(line, a,   i, L, c, d, d2, cur) {
      d = 0; cur = ""; i = 1; L = length(a)
      while (i <= L) {
        c = substr(a, i, 1)
        if (c == "\"") {
          cur = cur c; i++
          while (i <= L) {
            d2 = substr(a, i, 1)
            if (d2 == "\\") { cur = cur substr(a, i, 2); i += 2; continue }
            cur = cur d2; i++
            if (d2 == "\"") break
          }
          continue
        }
        if (c == "(" || c == "[") d++
        else if (c == ")" || c == "]") d--
        else if (d == 0 && (c == "?" || c == ":")) {
          branch(line, cur); cur = ""; i++; continue
        }
        cur = cur c; i++
      }
      branch(line, cur)
    }
    # One branch. It is a message only if it has a literal in it at all: an
    # argument that is `p->name` or `sub->n_refused` is a value with no words
    # of its own, and emitting a row for it would put every variable in the
    # plugin into the denominator as something nobody could ever find a box
    # for. An EMPTY literal is not text either - `cond ? "" : "..."` prints
    # nothing down one side, and nothing is not a branch anybody can see.
    function branch(line, b,   res) {
      res = literals(b)
      if (res == "") return
      printf "%d\t%s\n", line, needle(res)
    }
    # The string literals of an expression, in order, with a value marker
    # wherever something that is not a literal sits between two of them.
    #
    # ADJACENT literals join with nothing between them, which is the C rule
    # and the reason a sentence clang-format broke over four lines comes back
    # whole. Anything else between them is a value: `"   BELOW the floor " rfc
    # " asks for"` reaches the screen with the name of the RFC interpolated in
    # the middle, and a needle that ran straight through the gap would be
    # hunted for under a string no box prints.
    function literals(e,   i, L, c, d, res, seen, gap) {
      res = ""; seen = 0; gap = 0; i = 1; L = length(e)
      while (i <= L) {
        c = substr(e, i, 1)
        if (c == "\"") {
          if (seen && gap) res = res "\002"
          gap = 0; seen = 1; i++
          while (i <= L) {
            d = substr(e, i, 1)
            if (d == "\\") { res = res substr(e, i, 2); i += 2; continue }
            i++
            if (d == "\"") break
            res = res d
          }
          continue
        }
        if (c != " " && c != "\t") gap = 1
        i++
      }
      return res
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
    #
    # And so does the marker literals() leaves where two literals of one
    # argument had an interpolated value between them, which is the same thing
    # a conversion is and is cut the same way.
    function needle(s,   out, i, L, c, j, m, k, np, parts, res) {
      out = ""; i = 1; L = length(s)
      while (i <= L) {
        c = substr(s, i, 1)
        if (c == "\002") { out = out "\001"; i++; continue }
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
#
# AND SO THAT THE WIDTH READER USES IT TOO (#367). Measuring a row needs the
# same walk and one more answer from it: WHICH PIECE of the captured line each
# message owns. Given a file to write them to it emits those pieces, and given
# none it behaves exactly as it did - the width half is a second question of
# one walk, not a second walk. A third spelling of `ordered` is how a rule ends
# up with two of them wrong, which is the argument this whole file makes.
say_match() {   # <messages> <captured> [segments] [unattributed] [prompt tails]
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
  #
  # AND WHERE EACH FRAGMENT LANDED, not only where the message started and
  # stopped. Between two fragments of a message is a VALUE the message was
  # handed, and the distinction is the whole of #369: the branch literal in
  #
  #   "    the sequence jumps from %llu to %llu: %llu entr%s removed from
  #    the middle", ..., n == 1 ? "y was" : "ies were"
  #
  # reaches the screen inside the row its own format string printed. The
  # format owns the words around the hole; it does not own what was poured
  # into it, and a span that runs from the first fragment to the last owns
  # both.
  function ordered(line, frags,   i, m, at, p, first) {
    m = split(frags, f, "\001"); at = 1; first = 0; SPAN_N = 0
    for (i = 1; i <= m; i++) {
      p = index(substr(line, at), f[i])
      if (p == 0) return 0
      if (first == 0) first = at + p - 1
      SPAN_N++
      SPAN_FA[SPAN_N] = at + p - 1
      SPAN_FB[SPAN_N] = at + p + length(f[i]) - 2
      at = at + p + length(f[i]) - 1
    }
    SPAN_A = first; SPAN_B = at - 1
    return 1
  }
  # A run of the one byte no needle can contain, to stand where the words of
  # a message were. \001 is what the extraction joins fragments with, so
  # split() has already made it impossible for a fragment to hold one: a
  # fragment can never be found inside a struck-out region, and no match after
  # the first can be manufactured out of the text of the message itself.
  function struck(n,   s, i) {
    s = ""
    for (i = 1; i <= n; i++) s = s "\001"
    return s
  }
  NR == FNR { if (literal($2) >= min) {
                need[$2] = 1
                # WHETHER THIS MESSAGE ENDS ITS ROW, defaulting to yes. The C
                # extraction hands back two fields and every one of its rows
                # ends: vlib_cli_output writes whole lines and the plugin CLI
                # asks nothing. Only the shell extraction knows about a printf
                # that leaves the cursor where it was, so an absent field is a
                # row-ender rather than a gap - a default the other way would
                # cut every C row at its first match.
                e = ($4 == "") ? 1 : $4
                # A needle printed from two call sites is one message, and it
                # does not end its row if either of them does not.
                if (e == 0 || !($2 in ends)) ends[$2] = e }
              next }
  # ONE LINE CAN CARRY MORE THAN ONE MESSAGE, and wherever a program asks a
  # question it does: a prompt is a printf with no newline, so what an
  # operator sees on one row of the console is
  #
  #   Type 'yes' to install: error: not confirmed (got 'no')
  #
  # - the question and the refusal together. Spending the line on its single
  # most specific message loses the other, and "not confirmed" then reads as
  # a branch nothing has run while the suite that arranged it is asserting
  # on the very same output. So the best match is taken, ITS OWN WORDS are
  # struck out, and what is left is read again. The loop ends because every
  # round strikes out at least `min` characters and nothing struck out can be
  # matched again.
  #
  # ITS OWN WORDS AND NOT ITS SPAN, which is #369 and is the whole of it. The
  # span of a format string runs from its first literal fragment to its last
  # and swallows every value interpolated between them - and one of those
  # values is `n == 1 ? "y was" : "ies were"`, a sentence of this program in
  # its own right and the only place that branch can ever be seen. Struck out
  # with the format that carried it, `ies were` was reported as a branch
  # nothing had run while two labs were printing it. Both sites in
  # bpa_audit.c, 560 and 647, were named unseen off a corpus that holds them.
  #
  # AND IT CANNOT MAKE A FALSE ATTRIBUTION, which is the property the whole
  # reader rests on. Striking out leaves the row the length it was, so every
  # position `ordered` reports is a position on the row the box printed, and
  # a fragment found after a strike is found in text the box really wrote
  # there. The cut this replaces did not have that property: it glued the
  # text before a match to the text after it, and a needle could match across
  # the seam - two halves of a word the console never showed side by side.
  # Striking out is strictly the stricter of the two. What it ADMITS is only
  # this: text the box printed and the winning message never claimed.
  {
    line = $0
    # No map from the read line back to the printed row, because they are the
    # same row: a strike is length-preserving and a cut was not. #367 rebuilt
    # the positions after every cut to measure a row where the console put it;
    # there is nothing left to rebuild.
    nmatch = 0
    while (1) {
      bestlen = 0; best = ""
      for (nd in need)
        if (ordered(line, nd) && literal(nd) > bestlen) {
          bestlen = literal(nd); best = nd; ba = SPAN_A; bb = SPAN_B
          bn = SPAN_N
          for (i = 1; i <= bn; i++) { bfa[i] = SPAN_FA[i]; bfb[i] = SPAN_FB[i] }
        }
      if (best == "") break
      print best
      if (seg != "") { at[++nmatch] = ba; atend[nmatch] = bb; hit[nmatch] = best }
      for (i = 1; i <= bn; i++)
        line = substr(line, 1, bfa[i] - 1) struck(bfb[i] - bfa[i] + 1) \
               substr(line, bfb[i] + 1)
    }
    if (seg == "") next
    # A ROW THIS READER COULD NOT KEY ON AT ALL, kept rather than dropped. A
    # message written across two source lines prints two rows and only the
    # first carries words a needle is cut from; a menu row is all values; and
    # the shell writes its own diagnostics into the same capture. None of
    # those can be measured against a widest value, and a reader that simply
    # did not mention them would be reporting a budget it holds over part of
    # the screen as if it held over all of it.
    if (nmatch == 0) { if (unatt != "") print $0 > unatt
                       next }
    # In the order they appear on the row.
    for (i = 1; i <= nmatch; i++)
      for (j = i + 1; j <= nmatch; j++)
        if (at[j] < at[i]) {
          t = at[i]; at[i] = at[j]; at[j] = t
          t = atend[i]; atend[i] = atend[j]; atend[j] = t
          u = hit[i]; hit[i] = hit[j]; hit[j] = u
        }
    #
    # A CONSOLE ROW STARTS where a message follows one that did not end its
    # row, and at column 1. Not at every match: two messages on one captured
    # row are two rows only when the first of them left the cursor where it
    # was, and a wrapper that interpolates a literal - `say_using "1, the
    # shipped default"` prints "  Using 1, the shipped default." - is one row
    # carrying two of these messages. Cutting there would measure two halves
    # of a row the box prints whole.
    #
    # FROM COLUMN 1, because what stands in front of the first message on a
    # row is that row as well: `die` writes "error: " in front of what it was
    # handed, `note` writes "  - ", and a verdict measured without its prefix
    # is measured seven columns short - which is the defect #362 found in the
    # VTY by reading the prefix out of the product rather than writing it down.
    ntail = split(tails, TL, "\001")
    for (i = 1; i <= nmatch; i++) {
      if (i == 1) { start[i] = 1; continue }
      if (ends[hit[i - 1]] == 1) { start[i] = start[i - 1]; continue }
      # WHERE THE PROMPT ENDED, and the new row begins after it. Between the
      # last word of the question and the first word of the message there is
      # what the prompt printer wrote - ` [dhcp]: ` - and what this printer
      # wrote - `error: `. Splitting at the message would measure the refusal
      # seven columns short; splitting at the question would give it the
      # default the prompt offered. The prompt tail is read out of the
      # printer, so the cut is where the box put it.
      start[i] = at[i]
      cut = 0
      for (t = 1; t <= ntail; t++) {
        # An empty tail is found at the first column of everything: some awks
        # answer 1 to index(s, "") and the row would then be cut where the
        # question ended rather than where the prompt did.
        if (TL[t] == "") continue
        p = index(substr($0, atend[i - 1] + 1), TL[t])
        if (p > 0) {
          p = atend[i - 1] + p + length(TL[t])
          if (p <= at[i] && (cut == 0 || p < cut)) cut = p
        }
      }
      if (cut > 0) start[i] = cut
    }
    # The whole row goes to the last message on it, trailing value and all: a
    # message whose source line is continued - `say "..." \` and a second
    # string under it - has words on the row that its own needle never
    # carried, and cutting at the needle would measure the row short.
    for (i = 1; i <= nmatch; i++) {
      stop = length($0) + 1
      for (j = i + 1; j <= nmatch; j++)
        if (start[j] > start[i]) { stop = start[j]; break }
      print hit[i] "\t" substr($0, start[i], stop - start[i]) > seg
    }
  }
' min="$SAY_MIN" seg="${3:-}" unatt="${4:-}" tails="${5:-}" "$1" "$2"
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
  _tails=
  case ${SAY_LANG:-sh} in
    c) say_messages_c "$SAY_PRINTERS" > "$SAY_DIR/messages" ;;
    *) _printers=$(say_printers)
       # Which of them leave the cursor where it was, and what they write
       # after the question. One walk of the source hands back both, joined so
       # that one awk argument can carry a tail with spaces in it. The empty
       # ones are dropped: a printer whose format is bare `%s` ends its row
       # wherever the value ends, and an empty needle is found at every column
       # of every row.
       _enders=$(say_row_enders "$_printers" | sort -u)
       _noend=$(printf '%s\n' "$_enders" | cut -f1 | sort -u | tr '\n' ' ')
       _tails=$(printf '%s\n' "$_enders" | cut -f2- | grep . | sort -u \
                | tr '\n' '\001')
       say_messages "$_printers" "$_noend" > "$SAY_DIR/messages" ;;
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
    "$SAY_DIR/segments" "$SAY_DIR/unattributed" "$_tails" \
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
  # SAID OUT LOUD ON THE GREEN RUN TOO. These are the pieces of text so nearly
  # all interpolated value that nothing could recognise them in a run - menu
  # rows, `printf '%s\n' "$_a"`, and since #368 the separators and the on/off
  # words handed to a conversion as an argument. They are not excused
  # messages; they are text with no words of its own, and the count is here so
  # that a message quietly losing its words shows up as this number moving.
  [ "$_unreadable" = 0 ] || \
    ok "and $_unreadable piece(s) of text carry fewer than $SAY_MIN literal \
characters: those are values, not messages, and no run can recognise them"

  # SAID ON EVERY RUN THAT ASKED FOR IT, with the count, because a corpus file
  # that is there and empty and a corpus file that is there and whole look the
  # same to whoever comes back to it an hour later. Here rather than at the end
  # of this function so that a suite boxing transcripts, which returns below,
  # says it too.
  #
  # The two derived files go beside it: what the program can say, and which
  # message each captured row was spent on. A corpus of rows with no way to ask
  # which site printed one is a pile of strings, and the answer is already
  # computed here.
  [ -z "${SAY_KEEP:-}" ] || {
    ok "$(grep -c . "$SAY_KEEP") row(s) kept in $SAY_KEEP (SAY_CORPUS is set)"
    cp "$SAY_DIR/messages" "$SAY_KEEP.messages"
    cp "$SAY_DIR/segments" "$SAY_KEEP.segments"; }

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

  # THE THIRD HALF, and it is the shell path only (#367).
  #
  # Not because a C program cannot print a row too wide, but because what
  # bounds a value is read out of the source and the syntax is the source: the
  # paths this program names, the collections of words it declares, the tools
  # it checks for. A C reader would need its own, and inventing one over a
  # language nothing here has measured widths in would be a rule that has never
  # been red. The plugin CLI is also boxed as transcripts, which return above.
  case ${SAY_LANG:-sh} in
    sh) say_widths "$_what" ;;
  esac
}

# THE CEILINGS, and the suite that brings the boxes sets them.
#
# Not an allowlist: an allowlist is read once and grown forever, and the whole
# failure this file exists for is a row nobody re-examines. What is left over
# is NAMED on every run, with the program, the line and the column count, and
# the number it may not exceed is written down beside the boxes. Every row
# that gains a fix lowers one of these, and they may only go down - which is
# the idiom test/test_hexash_refusals.py uses for the same measurement on the
# VTY, and test/test_off_run_coverage.sh before it.
#
# They live in the SUITE and not here because they are a statement about one
# program: the installer has forty-odd rows carrying a disk path this reader
# cannot bound and appliance/apply-tuning has one, and a single number here
# would be the loosest of the three for all of them.
SAY_OVER_AT_MOST=${SAY_OVER_AT_MOST:-0}

#: Printers this pass's boundary rule cannot see - one after `do`, `then` or
#: `else`, which are words rather than the punctuation the rule matches on. A
#: ceiling and not a refusal, because the four that exist today hide nothing:
#: two are found by say_row_enders scanning the function body, one is a printer
#: definition, and one is inside a brace group redirected to a file. What must
#: not happen is a FIFTH appearing somewhere that does print, with the reader
#: going on reporting full coverage of a program it never looked at properly.
SAY_UNSEEN_AT_MOST=${SAY_UNSEEN_AT_MOST:-0}
SAY_UNSAFE_AT_MOST=${SAY_UNSAFE_AT_MOST:-0}

# Every row these boxes made the program print, measured against the widest
# value that can reach it.
#
# WHY THIS IS NOT A SWEEP OF THE SOURCE. A row of one of these programs is
# literal words with values interpolated into it, and the width of one is not
# a property of the source: `bind: $ADDR is named in $VPPCONF` is 25 columns
# plus a PCI address plus a path. #359 and #362 put a static rule on the VTY
# and both printed, on every run, the half they did not hold - the 296 rows
# that carry a value. #363 closed that half by DRIVING each site with the
# widest value that can reach it, and this is the same method one component
# over, on programs that print to a boot console instead of a terminal.
#
# THE BOXES ARE ALREADY HERE, which is why this lives beside the coverage
# reader rather than in a suite of its own. The three suites that source this
# file run the installer 128 times, the binder 17 and the tuner 22, against
# fake disk tables, fake roots and fake cards. A second harness would have to
# build every one of those again to read them, and the day after it was
# written the two rigs would differ.
#
# WHAT IT DECIDES, per message, and it is one of three things:
#
#   * BOUNDED. Every value that reaches it comes from somewhere with a widest
#     member, so the row is rebuilt with that member in place of the value
#     and measured. The widest members are READ FROM THE PRODUCT - the paths
#     this program names, the drivers it declares, the tools it checks for -
#     and never invented: a made-up 200-character path proves a fold nobody
#     will ever see, and turns this into a check that fails for the wrong
#     reason.
#   * UNBOUNDED BUT SAFE. The value is a disk somebody named, a card name, an
#     answer somebody typed, and NOTHING FOLLOWS IT on its row. A fold there
#     costs nothing: every word the program wrote is already on the screen and
#     the value is the last thing on it. That is what appliance/selftest has
#     say_list for.
#   * NEITHER, which is refused. An unbounded value with the program's own
#     words behind it pushes those words off an 80-column console exactly when
#     the value is long - and a boot console does not reflow, does not scroll
#     back, and is being read by somebody deciding what to type next.
#
# WHAT IT CANNOT SEE is printed too, on every run, so nobody reads this as
# covering more than it does.
say_widths() {   # <what to call the program in the report>
  _what=${1:-$SAY_SRC}
  _prog=$(printf '%s' "$_what" | sed 's|.*/||')
  echo
  echo "=== no row $_what prints is wider than the console, at the widest ==="
  echo "=== value that can reach it (#367) ==="

  : > "$SAY_DIR/over"
  : > "$SAY_DIR/unsafe"
  awk -F'\t' -v cols="$SAY_COLUMNS" -v min="$SAY_MIN" -v prog="$_prog" \
      -v src="$SAY_SRC" -v msgf="$SAY_DIR/messages" \
      -v overf="$SAY_DIR/over" -v unsafef="$SAY_DIR/unsafe" \
      -v stat="$SAY_DIR/wstat" '
    function rstrip(s) { sub(/[ \t]+$/, "", s); return s }
    function prefixwords(s, take,   a, i, out, m) {
      m = split(s, a, " ")
      if (take <= 0 || take >= m) return s
      out = a[1]
      for (i = 2; i <= take; i++) out = out " " a[i]
      return out
    }
    # The values of one row, recovered from the literal parts around them.
    # Fills V[1..slots]; 0 when the words did not line up, which happens when
    # a message is wrapped by another one or assembled somewhere else.
    function recover(sh, line,   m, i, p, at) {
      m = split(sh, F2, "\001")
      at = 1
      for (i = 1; i <= m; i++) {
        if (F2[i] == "") {
          # A message that ENDS with its value takes the rest of the row,
          # continuation arguments and all. Which is the right answer: it is
          # the widest thing that reached this site, and the prefix walk below
          # gives back the words that were never a value.
          if (i == m) { if (i > 1) { V[i - 1] = substr(line, at); VP[i - 1] = at }
                        return 1 }
          p = at
        } else {
          p = index(substr(line, at), F2[i])
          if (p == 0) return 0
          p = at + p - 1
        }
        if (i > 1) { V[i - 1] = substr(line, at, p - at); VP[i - 1] = at }
        at = p + length(F2[i])
      }
      return 1
    }
    function allre(re,   k) {
      for (k = 1; k <= NVALS; k++) if (VALS[k] !~ re) return 0
      return 1
    }
    # A PATH THIS PROGRAM NAMES, and the widest of them that any value ends
    # with. The suffix is the whole trick: every one of these programs takes a
    # root - HEXA_ROOT, HEXA_INSTALL_SRC, a mount point - so that a suite can
    # point it at a tree shaped like /etc, and what the rig prints is
    # /tmp/tmp.XXXXXX in front of the path the box would print. Measuring what
    # came out would report a width that depends on where mktemp put its
    # directory, which is how three of the five rows #367 names looked twenty
    # to thirty columns wider than a box ever prints them. On a box the root
    # is empty and the literal in the source IS the path.
    function pathwidest(   k, i, best, b, v, L) {
      best = 0
      for (k = 1; k <= NVALS; k++) {
        v = VALS[k]
        if (substr(v, 1, 1) != "/") return 0
        b = 0
        for (i = 1; i <= npath; i++) {
          L = length(PATH[i])
          if (L > b && substr(v, length(v) - L + 1) == PATH[i]) b = L
        }
        if (b > best) best = b
      }
      return best
    }
    # The TIGHTEST set that holds every value. A driver name is in one of
    # these and a tool name in another, and the widest member of the widest
    # set would be a bound on a different set.
    function setmember(   s, k, good, best, bw) {
      best = ""; bw = 0
      for (s = 1; s <= nset; s++) {
        good = 1
        for (k = 1; k <= NVALS; k++)
          if (!((s SUBSEP VALS[k]) in MEM)) { good = 0; break }
        if (good && (best == "" || SETW[s] < bw)) {
          best = SETNAME[s]; bw = SETW[s] }
      }
      return (best == "") ? "" : "one of " best "|" bw
    }
    # A value ACCUMULATED out of a set: `missing="$missing $t"` over the ten
    # tools the installer checks for. Its widest is the whole set, which is
    # the case an operator meets on a medium that was built wrong - exactly
    # when they need to read it.
    function setall(   s, k, a, m, i, good) {
      for (s = 1; s <= nset; s++) {
        good = 1
        for (k = 1; k <= NVALS && good; k++) {
          m = split(VALS[k], a, " ")
          if (m == 0) { good = 0; break }
          for (i = 1; i <= m; i++)
            if (!((s SUBSEP a[i]) in MEM)) { good = 0; break }
        }
        if (good) return "every member of " SETNAME[s] "|" SETALLW[s]
      }
      return ""
    }
    function kindof(   k, e, got) {
      if (NVALS == 0) return ""
      e = 1
      for (k = 1; k <= NVALS; k++) if (VALS[k] != "") e = 0
      if (e) return "nothing at all|0"
      if (allre(RE_PCI)) return "a PCI address|" W_PCI
      if (allre(RE_MAC)) return "a MAC address|" W_MAC
      if (allre(RE_IPP)) return "an IPv4 prefix|" W_IPP
      if (allre(RE_IP))  return "an IPv4 address|" W_IP
      if (allre(RE_UUID)) return "a UUID|" W_UUID
      if (allre("^[0-9]+$")) return "a decimal|" W_DEC
      k = pathwidest()
      if (k > 0) return "a path this program names|" k
      got = setmember(); if (got != "") return got
      got = setall();    if (got != "") return got
      # A LITERAL THIS PROGRAM HANDS IT, and the widest that has reached it.
      #
      # Widest-seen is a lower bound everywhere else in this reader and a
      # BOUND here, and only because of the half above: no message of this
      # program may have gone unseen, so every literal that can arrive at
      # this slot has arrived at it. `say_using` is handed one of three
      # sentences, each of them a message of this program in its own right,
      # and the widest of the three is the widest row it can make. The day a
      # fourth is added without a box, the coverage reader goes red first.
      got = 1
      for (k = 1; k <= NVALS; k++) if (!(VALS[k] in shape)) { got = 0; break }
      if (got) {
        e = 0
        for (k = 1; k <= NVALS; k++) if (length(VALS[k]) > e) e = length(VALS[k])
        return "a literal this program hands it|" e
      }
      # A NAME OUT OF THE FILE AN OPERATOR EDITS. The tuner refuses a setting
      # by name, and the names are the ones this program itself reads - the
      # widest of them is a bound, and one added tomorrow widens it here
      # without being mentioned.
      got = 1
      for (k = 1; k <= NVALS; k++) if (!(VALS[k] in VARNAME)) { got = 0; break }
      if (got) return "a variable name this program reads|" wvar
      return ""
    }
    BEGIN {
      H = "[0-9a-fA-F][0-9a-fA-F]"
      D = "[0-9]+"
      RE_PCI  = "^" H H ":" H ":" H "[.][0-9a-fA-F]$"
      RE_MAC  = "^" H ":" H ":" H ":" H ":" H ":" H "$"
      RE_IP   = "^" D "[.]" D "[.]" D "[.]" D "$"
      RE_IPP  = "^" D "[.]" D "[.]" D "[.]" D "/" D "$"
      RE_UUID = "^" H H H H "-" H H "-" H H "-" H H "-" H H H H H H "$"
      # Computed or read off the kernel, never guessed. A PCI address is what
      # /sys/bus/pci prints; a MAC is what /sys/class/net/<if>/address is; the
      # widest IPv4 prefix is 255.255.255.255/32; a UUID is what blkid
      # answers. A decimal is ten digits, which is where a count this box
      # makes runs out - 4294967295 - and NOT a bound on what somebody can
      # type at a prompt, which is said out loud below.
      W_PCI = length("0000:00:00.0"); W_MAC = length("02:00:00:00:00:01")
      W_IP = length("255.255.255.255"); W_IPP = length("255.255.255.255/32")
      W_UUID = length("abcdef25-1111-2222-3333-444444444444")
      W_DEC = length("4294967295"); W_IFNAME = 15
    }
    # ---------------------------------------------------- the product itself
    FILENAME == src {
      if ($0 ~ /^[ \t]*#/) next
      # A logical line, because the list of tools the installer checks for is
      # written over two of them and a set read off the first half would be a
      # bound five tools short.
      if ($0 ~ /\\$/) { acc = acc substr($0, 1, length($0) - 1); next }
      acc = acc $0
      line = acc; acc = ""
      s = line
      while (match(s, "/[A-Za-z0-9_][A-Za-z0-9_.+-]*(/[A-Za-z0-9_.+-]+)+")) {
        p = substr(s, RSTART, RLENGTH)
        if (!(p in SEENP)) { SEENP[p] = 1; PATH[++npath] = p }
        s = substr(s, RSTART + RLENGTH)
      }
      # The variable names it reads, for a message that refuses one by name.
      s = line
      while (match(s, "[$][{]?[A-Z_][A-Z0-9_]*")) {
        p = substr(s, RSTART, RLENGTH); sub(/^[$][{]?/, "", p)
        VARNAME[p] = 1
        if (length(p) > wvar) wvar = length(p)
        s = substr(s, RSTART + RLENGTH)
      }
      # The collections of words this program declares, which is where its
      # closed sets live: DPDK_DRIVERS, and the tools checked for before
      # anything is written. Derived rather than listed, so a set added to the
      # product bounds its own rows with nothing added here.
      if (match(line, "^[A-Z_][A-Z0-9_]*=\"[A-Za-z0-9_.+ -]+\"[ \t]*$")) {
        nm = line; sub(/=.*$/, "", nm)
        vv = line; sub(/^[^=]*="/, "", vv); sub(/"[ \t]*$/, "", vv)
        addset(nm, vv)
      }
      if (match(line, "for[ \t]+[A-Za-z_][A-Za-z0-9_]*[ \t]+in[ \t]+[^;]+;[ \t]*do")) {
        one = substr(line, RSTART, RLENGTH)
        nm = one; sub(/^for[ \t]+/, "", nm); sub(/[ \t]+in.*$/, "", nm)
        vv = one; sub(/^for[ \t]+[^ \t]+[ \t]+in[ \t]+/, "", vv)
        sub(/;[ \t]*do$/, "", vv)
        if (vv ~ /^[A-Za-z0-9_.+ \t-]+$/) addset("the list `for " nm "` at " prog ":" FNR, vv)
      }
      next
    }
    # ------------------------------------------------ what it can print
    FILENAME == msgf {
      lit = $2; gsub(/\001/, "", lit)
      if (length(lit) < min) next
      if (!($2 in shape)) { shape[$2] = $3; norder[++nmsg] = $2 }
      ln[$2] = ln[$2] (ln[$2] == "" ? "" : ",") $1
      next
    }
    # ------------------------------------------------ and what it printed
    {
      k = index($0, "\t")
      if (k == 0) next
      n = substr($0, 1, k - 1); s = rstrip(substr($0, k + 1))
      c = ++cnt[n]; sg[n, c] = s
    }
    function addset(nm, words,   a, m, i, tot) {
      m = split(words, a, " ")
      if (m < 2) return
      nset++
      SETNAME[nset] = nm; SETW[nset] = 0; tot = 0
      for (i = 1; i <= m; i++) {
        MEM[nset, a[i]] = 1
        if (length(a[i]) > SETW[nset]) SETW[nset] = length(a[i])
        tot += length(a[i]) + 1
      }
      SETALLW[nset] = tot
    }
    # The row as a box would print it, with each value at the widest that can
    # reach it. Right to left, so a substitution does not move the slot after
    # it. Shown rather than the driven row, because a row named at 88 columns
    # beside a driven row of 107 reads as arithmetic nobody can follow.
    function rebuild(line, ns,   j, used, pad, i, out) {
      out = line
      for (j = ns; j >= 1; j--) {
        if (KIND[j] == "" || VP[j] == 0) continue
        used = (TRIM[j] > 0) ? prefixwords(V[j], TRIM[j]) : V[j]
        pad = ""
        for (i = 1; i <= KW[j]; i++) pad = pad "X"
        out = substr(out, 1, VP[j] - 1) pad substr(out, VP[j] + length(used))
      }
      return out
    }
    function note(k) { tally[k]++; if (!(k in kseen)) { kseen[k] = 1; korder[++nk] = k } }
    END {
      for (mi = 1; mi <= nmsg; mi++) {
        n = norder[mi]; sh = shape[n]
        nf = split(sh, F, "\001"); ns = nf - 1
        if (cnt[n] == 0) { unreached[++nun] = ln[n]; continue }
        measured++
        drv = 0; worstrow = ""
        for (c = 1; c <= cnt[n]; c++)
          if (length(sg[n, c]) > drv) { drv = length(sg[n, c]); worstrow = sg[n, c] }
        if (ns == 0) {
          note("no value in it")
          if (drv > cols) print ln[n] "\t" drv "\tno value in it\t" worstrow > overf
          continue
        }
        # Every value that reached every slot, over every row.
        nok = 0
        for (c = 1; c <= cnt[n]; c++) {
          for (j = 1; j <= ns; j++) V[j] = ""
          okc[c] = recover(sh, sg[n, c])
          if (!okc[c]) continue
          nok++
          for (j = 1; j <= ns; j++) SEEN[n, j, ++nseen[n, j]] = V[j]
        }
        if (nok == 0) {
          # The words did not line up with what came out, so the values
          # cannot be told from them. Named rather than dropped, and measured
          # as it was driven - which is a lower bound on the row and not a
          # bound on it.
          note("could not be taken apart")
          if (drv > cols) print ln[n] "\t" drv "\tas driven\t" worstrow > overf
          continue
        }
        # The kind of each slot, and how wide that kind can be.
        # WHAT MAY FOLLOW AN UNBOUNDED VALUE and still cost nothing when the
        # row folds: whitespace, and punctuation that closes what opened
        # before the value - a quote, a bracket, a full stop. Not a WORD. The
        # doctrine is that every word the program wrote is already on the
        # screen and the value is the last thing on it; a closing quote pushed
        # onto the next row takes no meaning with it, and `commit` does.
        tailslot = (F[nf] !~ /[A-Za-z0-9]/) ? ns : 0
        for (j = 1; j <= ns; j++) {
          NVALS = nseen[n, j]
          for (k = 1; k <= NVALS; k++) VALS[k] = SEEN[n, j, k]
          TRIM[j] = 0
          got = kindof()
          if (got == "" && j == tailslot) {
            # THE LONGEST PREFIX OF THE TAIL THAT IS A VALUE. A message whose
            # last word is a value takes the rest of the row when it is
            # recovered, and on a continued source line the rest of the row
            # carries words the program wrote: `apply the command line from
            # $GRUBFILE` is continued with `in your bootloader and reboot`.
            # Longest first, so the management record is read whole and a
            # path is not read as a path followed by a sentence.
            mw = 0
            for (k = 1; k <= NVALS; k++) {
              t = split(SEEN[n, j, k], aa, " ")
              if (t > mw) mw = t
            }
            for (take = mw - 1; take >= 1 && got == ""; take--) {
              for (k = 1; k <= NVALS; k++) VALS[k] = prefixwords(SEEN[n, j, k], take)
              got = kindof()
              if (got != "") TRIM[j] = take
            }
            if (got == "") for (k = 1; k <= NVALS; k++) VALS[k] = SEEN[n, j, k]
          }
          if (got == "") { KIND[j] = ""; KW[j] = 0 }
          else {
            KIND[j] = got; sub(/\|[0-9]*$/, "", KIND[j])
            KW[j] = got; sub(/^.*\|/, "", KW[j]); KW[j] += 0
          }
        }
        nunb = 0; lastunb = 0
        for (j = 1; j <= ns; j++) if (KIND[j] == "") { nunb++; lastunb = j }
        # The row rebuilt with the widest value in every slot, measured over
        # every row this site printed: what came out, plus what each value
        # could have been, minus what it was.
        wide = 0; widerow = ""; upto = 0
        for (c = 1; c <= cnt[n]; c++) {
          if (!okc[c]) continue
          for (j = 1; j <= ns; j++) V[j] = ""
          recover(sh, sg[n, c])
          w = length(sg[n, c]); u = w
          for (j = 1; j <= ns; j++) {
            used = (TRIM[j] > 0) ? prefixwords(V[j], TRIM[j]) : V[j]
            if (KIND[j] == "") { u -= length(V[j]) }
            else { w += KW[j] - length(used); u += KW[j] - length(used) }
          }
          if (w > wide) { wide = w; widerow = rebuild(sg[n, c], ns) }
          if (u > upto) upto = u
        }
        if (nunb == 0) {
          if (wide > cols) {
            note("bounded, and over at its widest")
            why = ""
            for (j = 1; j <= ns; j++)
              if (KIND[j] != "" && KIND[j] != "nothing at all")
                why = why (why == "" ? "" : " and ") KIND[j]
            print ln[n] "\t" wide "\t" why "\t" widerow > overf
          } else note("bounded, and fits at its widest")
          if (wide > widestfit) { widestfit = wide; widestrow = widerow }
          continue
        }
        # An unbounded value is safe only where nothing follows it.
        safe = (nunb == 1 && lastunb == ns && tailslot == ns)
        if (!safe) {
          note("unbounded, with the words of the program behind it")
          why = ""
          for (j = 1; j <= ns; j++)
            why = why (why == "" ? "" : " | ") (KIND[j] == "" ? "no widest" : KIND[j])
          print ln[n] "\t" upto "\t" why "\t" sh > unsafef
          continue
        }
        if (upto > cols) {
          note("the words before the unbounded value are over")
          print ln[n] "\t" upto "\tup to the unbounded value\t" widerow > overf
        } else {
          note("unbounded, and last on its row")
          if (upto > widestfit) { widestfit = upto; widestrow = widerow }
        }
      }
      printf "  %d of the %d message(s) this program can print were printed here\n", \
             measured, nmsg
      for (i = 1; i <= nk; i++) printf "    %4d  %s\n", tally[korder[i]], korder[i]
      wp = 0; wpn = ""
      for (i = 1; i <= npath; i++) if (length(PATH[i]) > wp) { wp = length(PATH[i]); wpn = PATH[i] }
      # THE WIDEST ROW IT MEASURED, said on the green run. A reader that had
      # stopped composing rows would pass with nothing to show for it, and a
      # number that falls to nothing is what says so - the same argument the
      # floors in test/test_console_width.py make.
      printf "  the widest row measured here is %d column(s) of the %d:\n    %s\n", \
             widestfit, cols, substr(widestrow, 1, 100)
      printf "  the widest values, read from the product: a PCI address is %d columns,\n", W_PCI
      printf "  a MAC %d, an IPv4 prefix %d, a UUID %d, a decimal %d; the widest of the\n", \
             W_MAC, W_IPP, W_UUID, W_DEC
      printf "  %d path(s) this program names is %d (%s); a closed set is its own\n", npath, wp, wpn
      printf "  widest member, out of the %d this program declares", nset
      for (i = 1; i <= nset; i++) printf "%s%s", (i == 1 ? " - " : ", "), SETNAME[i]
      printf ".\n"
      print "  NOT MEASURED HERE, and these are the whole of what is not:"
      printf "  %d message(s) no box here printed, so there was no row to measure", nun
      if (nun > 0) { printf ":\n"; for (i = 1; i <= nun; i++) printf "    %s:%s\n", prog, unreached[i] }
      else printf ".\n"
      print "  a decimal is measured at the widest number this box COUNTS, and not at"
      print "  the widest an operator can type: an answer of fifty digits is quoted"
      print "  back whole, and that row is not measured here."
      print "  a prompt is measured as the question, and what somebody types at it is"
      print "  on the same row of the box, and nothing here holds it."
      # THE FLOOR, written where the shell can read it. Everything above this
      # reads green on an empty corpus: no row measured is no row over, and
      # the two ceilings below would both sit at zero and agree with
      # themselves. It is the same floor test_console_width.py and
      # test_hexash_refusals.py each put under their own walk, and for the
      # same reason - the day the segments stop arriving, the recorder is
      # what is broken and not the widths.
      printf "%d %d\n", measured, widestfit > stat
    }
  ' "$SAY_SRC" "$SAY_DIR/messages" "$SAY_DIR/segments"

  # THE ROWS THIS READER COULD NOT KEY ON, counted and the wide ones named.
  #
  # NOT a failure, and it cannot be: the capture holds whatever the box wrote
  # on its stderr, and a rig that puts the program under a tempdir it cannot
  # write to has the SHELL write the complaint - which is a row this program
  # never printed. What it IS is the honest bottom of the measurement, and the
  # second line of a message written across two source lines is in here: it
  # reaches the console and carries no words of its own to key on.
  # Counted with awk and not `grep -c`, which answers 0 and exits 1 on an
  # empty file - so the `|| echo 0` beside it prints a second zero and the
  # arithmetic below is handed two lines.
  _un=$(awk 'END { print NR }' "$SAY_DIR/unattributed")
  _unwide=$(awk -v c="$SAY_COLUMNS" 'length > c' "$SAY_DIR/unattributed" \
            | sort -u)
  echo "  $_un captured row(s) carry no message this reader could key on - the"
  echo "  second line of a message written over two source lines, a menu row that"
  echo "  is all values, a complaint the shell itself wrote into the capture."
  if [ -n "$_unwide" ]; then
    echo "  $(printf '%s\n' "$_unwide" | awk 'END { print NR }') of those are over \
$SAY_COLUMNS columns:"
    printf '%s\n' "$_unwide" | awk '{ printf "    %3d  %s\n", length, substr($0,1,100) }'
  fi

  read -r _measured _widest < "$SAY_DIR/wstat" || { _measured=0; _widest=0; }
  if [ "${_measured:-0}" = 0 ] || [ "${_widest:-0}" = 0 ]; then
    bad "no row of $_what was measured against a widest value, so the two \
ceilings below are agreeing with an empty file: the rows stopped reaching this \
reader, which is the recorder being broken and not the widths"
  fi

  # TESTED FOR RATHER THAN REDIRECTED. This was
  # `awk ... "$SAY_DIR/unseen-printers" 2>/dev/null`, and the file is written
  # only when there IS a missed printer: awk exits 2 on a file that is not
  # there, `2>/dev/null` hides the message and not the status, and a caller
  # with `set -e` dies inside the assignment. test/test_plugin_says.sh does
  # set -e, so it stopped mid-run with NO VERDICT at all - which the suite
  # counter (#338) reported as a suite that crashed rather than one that
  # failed, correctly, and is how this was found. A reader that dies is worse
  # than a reader that is red: the red says something about the product.
  if [ -f "$SAY_DIR/unseen-printers" ]; then
    _unseen_pr=$(awk 'END { print NR + 0 }' "$SAY_DIR/unseen-printers")
  else
    _unseen_pr=0
  fi
  say_ceiling "${_unseen_pr:-0}" "$SAY_UNSEEN_AT_MOST" \
    "printer(s) this pass cannot see, after a do/then/else" \
    "$(sed 's/^/    /' "$SAY_DIR/unseen-printers" 2>/dev/null)" \
    "A printer this pass does not look for is a screen nothing here counts, \
and the coverage above reads as complete while it is not - move the call \
somewhere the rule sees, or widen the rule and say why"

  _over=$(awk 'END { print NR }' "$SAY_DIR/over")
  _unsafe=$(awk 'END { print NR }' "$SAY_DIR/unsafe")
  say_ceiling "$_over" "$SAY_OVER_AT_MOST" \
    "row(s) do not fit the $SAY_COLUMNS-column console at their widest" \
    "$(sort -n "$SAY_DIR/over" | awk -F'\t' -v p="$_prog" \
        '{ printf "    %s:%s  %d columns, %s\n      %s\n", p, $1, $2, $3, substr($4,1,110) }')"
  say_ceiling "$_unsafe" "$SAY_UNSAFE_AT_MOST" \
    "row(s) carry a value with no widest and the program's own words behind it" \
    "$(sort -n "$SAY_DIR/unsafe" | awk -F'\t' -v p="$_prog" \
        '{ w = $4; gsub(/\001/, "~", w)
           printf "    %s:%s  up to %d columns before the value, %s\n      %s\n", \
                  p, $1, $2, $3, substr(w,1,110) }')"
}

# One ceiling, which may only go down.
#
# AND IT NAMES WHAT IT IS HOLDING ON THE GREEN RUN, not only when it breaks.
# A ceiling of 49 whose rows are printed only under `bad` is a number nobody
# can act on: the one way to see the forty-nine is to lower the variable and
# provoke a red run, and nobody lowers a variable whose contents they cannot
# see. That is the opposite of what the rest of this file does - the NOT
# MEASURED HERE block, the unattributed rows above it and the unseen list in
# tools/plugin-says.sh all print what the check deliberately does not hold, on
# every run, where the next person is standing. A ceiling is exactly that: the
# rows this reader has looked at and is not holding.
#
# Guarded on the detail being there at all, because a ceiling of zero has
# nothing to list and an empty heading under it reads as a listing that failed
# to print.
# <found> <allowed> <what> <detail> [advice]
#
# THE ADVICE BELONGS TO THE CALLER. It used to be the width one, written into
# this function: "a row that does not fit is broken mid-word by a console that
# cannot reflow". True of the two width ceilings and false of every other one
# - and a refusal that gives advice about the wrong thing sends somebody to
# fix something that is not the problem. The default is still the width
# sentence, because that is what two of the three callers want and repeating
# it at both would be the same words in two places.
say_ceiling() {
  _advice=${5:-"A row that does not fit is broken mid-word by a console that \
cannot reflow and cannot be scrolled back, in front of somebody deciding what \
to type - move the value off the row, do not raise the number"}
  if [ "$1" -gt "$2" ]; then
    bad "$1 $3, above the ceiling of $2. $_advice:
$4"
  elif [ "$1" -lt "$2" ]; then
    bad "the ceiling says $2 and only $1 $3: lower it, or it stops holding \
anything"
  else
    ok "$1 $3, at the ceiling of $2"
    [ -z "$4" ] || printf '%s\n' "$4"
  fi
}
