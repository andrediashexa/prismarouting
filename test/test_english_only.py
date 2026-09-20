#!/usr/bin/env python3
"""
The rule that documentation, source comments and VTY output are in English.

`CONTRIBUTING.md` section 1 rule 1 states it and names those three as part of
it. [#1](https://github.com/andrediashexa/hexarouting/issues/1) closed on it.
Nothing held it, so the rule decayed exactly the way an unheld rule does: a
sweep for MUST statements found seven Portuguese strings, and measuring
properly found **twenty-one**, in eleven files added or edited since. Two of
the seven were section headers in a lab transcript, which is what a reader
uses to find where a failure happened, and `CONTRIBUTING.md` names `lab/*.sh`
as the safe place to quote the product's strings from when writing the wiki -
so a Portuguese heading is a string somebody may copy onto a public page
(#246).

WHAT THIS IS NOT. Not a spell checker and not a language detector. The false
positive cost of either, on a tree full of protocol identifiers, is higher than
the defect: a check that goes red on work that is right is the worst kind
there is. This is a fixed list of words that cannot be English and cannot be a
protocol identifier, measured against this tree rather than taken from a
dictionary, and one of them on a line is enough.

`com` is deliberately not in the list. It is Portuguese, and it is also the
end of every `.com` hostname and of every `github.com` issue link in the tree,
which the URL strip below does not catch inside a quoted string. A word that
cannot be told from an identifier is a word that costs more than it finds.

WHERE IT LOOKS. The files this project writes and ships behaviour in. `docs/`
and `test/` are deliberately out: `docs/wiki-audit.md` quotes the titles of a
Brazilian-Portuguese wiki and `test/test_doc_commands.py` carries its page
slugs, and both are identifiers of somebody else's document.
`PADRAO-DOCUMENTACAO.md` and `PROMPT-DOCUMENTAR-WIKI.md` are written in
Portuguese on purpose, for the people who write that wiki.
"""
import os
import re
import sys

fail = 0


def bad(msg):
    global fail
    fail += 1
    print(f"  FAIL: {msg}")


def ok(msg):
    print(f"  [ ok ] {msg}")


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCOPE = ("src", "lab", "appliance", "tools", "ref", "docs")

#: The one file in SCOPE that is allowed to carry Portuguese, and why.
#:
#: `docs/` used to be out of scope ENTIRELY for this one file's sake, and that
#: is how three sections of docs/routing.md came to be written in Portuguese
#: without anything noticing: a directory excused to accommodate one file is a
#: directory nobody measures. Measured when it was brought back in: 33 lines in
#: docs/routing.md, every one of them real, and 14 in the file below, every one
#: of them a quoted wiki title.
EXCUSED = {
    "docs/wiki-audit.md": "it quotes the titles and section headings of a "
                          "Brazilian-Portuguese wiki, which are identifiers "
                          "of somebody else's document",
    "PADRAO-DOCUMENTACAO.md": "the standard for writing the operator wiki, "
                              "written in Portuguese on purpose for the "
                              "people who write it",
    "PROMPT-DOCUMENTAR-WIKI.md": "the brief that goes with that standard, "
                                 "and read by the same people",
    "docs/brand/README.md": "the brand kit's own index, written in "
                            "Portuguese by the designer who produced it, for "
                            "the Brazilian team that uses it. It describes "
                            "files rather than the product, and nothing in "
                            "the product reads it",
    "CHANGELOG.md": "it QUOTES Portuguese strings the product used to carry - "
                    "a VTY help line, a lab's old needle - inside sections "
                    "that have been tagged. This file's own rule is that a "
                    "tagged section describes a medium somebody is running "
                    "and MUST NOT be edited, so those quotations cannot be "
                    "corrected even in principle",
}

# MARKDOWN AT THE REPOSITORY ROOT IS READ TOO, and it was not.
#
# SCOPE walks directories, so `CONTRIBUTING.md`, `README.md` and `TESTING.md`
# sat outside every one of them - while CONTRIBUTING.md's own rule 1 binds
# "every `*.md` added to this repository" (#323). Measured when they were
# brought in: 54 markdown files in the tree and 11 of them checked. Of the six
# at the root, three are clean, two are Portuguese on purpose, and one quotes
# Portuguese it is not allowed to edit.

WORDS = set("""nao não que pela pelo pelos pelas uma umas uns dos das nas aos
este esta isso isto quando entao então porque sempre tambem também ja já sao
são foi sera será seja onde quais muito mesmo mesma outra outro ainda apenas
cada mais menos para nunca derruba gerado arquivo arquivos usuario usuário
senha endereco endereço enderecos endereços configuracao configuração
operacoes operações removido verificacao verificação mensagem tamanho
quantidade preenchido blocos saida saída entrada leitura escrita caminho salvo
criando recebido apos após possiveis possíveis proposital borda binario
binário binaria binária faltando carrega dirige absorve devolvidos crua
transito trânsito estaticas estáticas autoteste passou existe abre morria
embarcou exercita assinantes coletor recebeu roteamento memoria memória
disco""".split())

# Portuguese that is an IDENTIFIER rather than prose, each with the reason it
# stays. Removed from the line before the words are looked for, so a line that
# carries one and nothing else is clean.
ALLOWED = {
    "TRANSITO": "a BGP peer-group name in the lab fixtures, and the VTY must "
                "carry it back verbatim",
    "NAS": "Network Access Server, RFC 2865's own word for the box that "
           "sends accounting - and 'nas' is also a Portuguese preposition, "
           "which is why only the RFC's own spelling, in capitals, is "
           "removed here: 'nas' in prose still fails",
    "radius_nas.py": "the file name of the NAS the labs point at the box, "
                     "and a file name is an identifier rather than prose",
    "America/Sao_Paulo": "an IANA timezone identifier",
    "label: dos": "the sfdisk name for an MBR partition table",
    "uplink para o transito": "a deliberate Portuguese interface description: "
                              "the product must store and read back what a "
                              "Brazilian operator actually types",
    "AS-NAO-EXISTE": "an AS-path list named for what it is: one that does "
                     "not exist. The lab asserts the box refuses it",
    "PADRAO-DOCUMENTACAO.md": "the filename of the wiki writing standard, "
                              "which is written in Portuguese on purpose for "
                              "the people who write that wiki",
    "ASSINANTES PON-3": "an interface description in a docs/routing.md "
                        "transcript, and the same case as the uplink above: "
                        "what a Brazilian operator types is what the box must "
                        "store and read back",
    "transito-primario": "a BGP neighbour description in a docs/routing.md "
                         "transcript. Same case as TRANSITO above: the box "
                         "must carry it back verbatim",
    "PL-MEUS-BLOCOS": "a prefix-list name in a docs/routing.md transcript. A "
                      "name an operator chose, which this product stores and "
                      "reads back and does not translate",
}


# And the half a word list cannot have: Portuguese ENDINGS.
#
# The list above finds only the Portuguese somebody thought of, and #265
# proved it on this very check - `aguardando`, `varredura`, `expiracao`,
# `tres`, `chegarem` and `intermitente` were none of them in WORDS, and two of
# those lines are ECHOED, so they were in every lab capture the suite collected
# while this file reported the tree clean.
#
# Measured over this tree: 16 distinct words match one of these endings and
# 15 of them are Portuguese. The single false positive was `param`, at five
# letters, so the minimum length is six and it costs nothing else. A rule that
# generalises beats a list that has to be remembered, which is the same
# argument NOT_LABS and the credential scope were rewritten on.
SUFFIXES = ("cao", "coes", "ando", "endo", "mente", "agem", "dade", "arem",
            "aram", "ente", "eram", "iram", "ismo", "inho", "inha", "eiro",
            "eira")


def scanned():
    """Every file this rule binds, root markdown and SCOPE together.

    ONE LIST, because the "is this reading anything" guard below used to do its
    own walk over SCOPE alone. Two walks of one set is two answers to one
    question, and the guard's answer was the one that did not include the root
    markdown it was supposed to be guarding.
    """
    paths = [os.path.join(REPO, f) for f in sorted(os.listdir(REPO))
             if f.endswith(".md")]
    for root in SCOPE:
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, root)):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "__pycache__", ".build-vpp")]
            paths += [os.path.join(dirpath, n) for n in sorted(filenames)]
    return paths


def offenders():
    """Every line this rule binds that is not in English.

    The paths are gathered first and walked once, because the set has two
    parts: the directories in SCOPE, and the markdown at the repository ROOT.
    The root half was missing and CONTRIBUTING.md's rule 1 binds "every `*.md`
    added to this repository" - so `CONTRIBUTING.md`, `README.md` and
    `TESTING.md` sat outside every directory the check walked (#323).
    """
    out = []
    skipped = []
    for path in scanned():
        rel = os.path.relpath(path, REPO)
        # By NAME, not by directory. Excusing the directory is what let three
        # sections of docs/routing.md be written in Portuguese with nothing
        # noticing.
        if rel in EXCUSED:
            continue
        try:
            raw = open(path, "rb").read()
        except OSError:
            continue
        # A LANGUAGE CHECK OVER AN IMAGE MEANS NOTHING, and this read every
        # byte of every file in scope as if it were prose. `errors="ignore"`
        # turned a PNG into a string, and three of them in docs/brand matched
        # Portuguese words out of compressed pixel data. A check that fires on
        # a file it cannot read is a check somebody switches off.
        #
        # BY CONTENT AND NOT BY EXTENSION. A list of extensions is a second
        # list to keep in step with whatever gets added next; a NUL byte in
        # the first block is what every tool on this box uses to tell a binary
        # from text, and it costs one read.
        if b"\x00" in raw[:8192]:
            skipped.append(rel)
            continue
        text = raw.decode("utf-8", errors="ignore")
        for n, line in enumerate(text.split("\n"), 1):
            # A URL is not prose. github.com would otherwise match `com` on
            # every issue link in the tree.
            probe = re.sub(r"https?://\S+", "", line)
            for allowed in ALLOWED:
                probe = probe.replace(allowed, " ")
            words = set(re.findall(r"[a-zà-ú]+", probe.lower()))
            hit = sorted(words & WORDS)
            hit += sorted(w for w in words
                          if len(w) >= 6 and w.endswith(SUFFIXES)
                          and w not in WORDS)
            if hit:
                out.append((rel, n, line.strip()[:90], hit[:3]))
    return out, skipped


print("=== the product's own files are written in English ===")
found, not_text = offenders()
if found:
    for rel, n, line, hit in found[:20]:
        bad(f"{rel}:{n} is not in English ({', '.join(hit)}): {line!r}")
    if len(found) > 20:
        bad(f"... and {len(found) - 20} more line(s)")
else:
    ok(f"{len(WORDS)} words checked over {len(SCOPE)} directories, none found")
# SAID, NOT SILENT. A file this check stopped reading is a file it no longer
# holds, and a skip nobody prints is how a rule quietly stops applying to a
# whole directory.
print(f"  [info] {len(not_text)} file(s) are not text and were not read")

print("\n=== what a word list cannot find, measured rather than described ===")
# THE LIMIT OF THIS CHECK, driven instead of claimed (#420). The rule is a
# vocabulary, so it finds Portuguese only when the Portuguese uses one of the
# words below. The words most likely to slip are the SHORT ones that overlap
# English - `no`, `da`, `do`, `se`, `a`, `os` - and those are what a short
# comment is made of.
#
# Two real comments lived in appliance/test_tune.py for the life of that file
# and this check was green the whole time: "NIC no no 1" and "workers fora do
# no da NIC". They are in English now, and this box is what stops the next
# pair being found by somebody reading rather than by the suite.
#
# It does NOT fail. A check that refused its own known limit would be a check
# nobody could keep green, and the three ways out are a decision on #420
# rather than a line here. What it does is say the number out loud on every
# run, so the limit is a measurement.
_UNFINDABLE = [
    "NIC no no 1",
    "workers fora do no da NIC",
    "o disco e a placa",
    "se o valor nao vier",
]
_missed = [line for line in _UNFINDABLE
           if not (set(line.lower().split()) & WORDS)]
print(f"    {len(_missed)} of {len(_UNFINDABLE)} real Portuguese lines carry "
      f"no word this check knows:")
for _line in _missed:
    print(f"      {_line!r}")
if not _missed:
    bad("every sample line was caught, which would mean this box is measuring "
        "nothing - widen the samples rather than deleting the box")
print(f"  a floor of {len(WORDS)} words, and what it cannot reach is counted")

print("\n=== and the check is reading something ===")
# Without this the file passes on a scope that resolved to nothing, which is
# the shape #151 spent an issue on and #235 found again in test/ itself.
# Counted OUT OF WHAT THE WALK RETURNED, not out of a second listing of the
# directory. The first version asked os.listdir() how many root markdown files
# exist, which is true whether or not the walk read any of them: with the root
# half deleted from scanned(), it still said six.
_scanned = scanned()
seen = len(_scanned)
_root_md = [p for p in _scanned
            if os.path.dirname(p) == REPO and p.endswith(".md")]
if seen >= 100 and len(WORDS) >= 50 and len(_root_md) >= 4:
    ok(f"{seen} file(s) in scope, {len(_root_md)} of them markdown at the root")
else:
    bad(f"only {seen} file(s) and {len(WORDS)} word(s), so this is asserting "
        f"almost nothing")

print("\n=== every allowed identifier is still somewhere in the tree ===")
# An allowance nobody needs is an allowance nobody re-reads, and the next
# Portuguese string that happens to contain it walks straight through.
whole = ""
for root in SCOPE:
    for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, root)):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "__pycache__", ".build-vpp")]
        for name in filenames:
            try:
                whole += open(os.path.join(dirpath, name), encoding="utf-8",
                              errors="ignore").read()
            except OSError:
                pass
stale = [a for a in ALLOWED if a not in whole]
if stale:
    for a in stale:
        bad(f"{a!r} is allowed and no longer appears anywhere: an allowance "
            f"nobody needs is one nobody re-reads")
else:
    ok(f"all {len(ALLOWED)} of them")

if fail == 0:
    print()
    print("ALL TESTS PASSED")
    sys.exit(0)
print()
print(f"{fail} FAILURES")
sys.exit(1)
