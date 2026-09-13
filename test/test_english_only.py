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
SCOPE = ("src", "lab", "appliance", "tools", "ref")

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


def offenders():
    out = []
    for root in SCOPE:
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, root)):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "__pycache__", ".build-vpp")]
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                try:
                    text = open(path, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                for n, line in enumerate(text.split("\n"), 1):
                    # A URL is not prose. github.com would otherwise match
                    # `com` on every issue link in the tree.
                    probe = re.sub(r"https?://\S+", "", line)
                    for allowed in ALLOWED:
                        probe = probe.replace(allowed, " ")
                    words = set(re.findall(r"[a-zà-ú]+", probe.lower()))
                    hit = sorted(words & WORDS)
                    hit += sorted(w for w in words
                                  if len(w) >= 6 and w.endswith(SUFFIXES)
                                  and w not in WORDS)
                    if hit:
                        rel = os.path.relpath(path, REPO)
                        out.append((rel, n, line.strip()[:90], hit[:3]))
    return out


print("=== the product's own files are written in English ===")
found = offenders()
if found:
    for rel, n, line, hit in found[:20]:
        bad(f"{rel}:{n} is not in English ({', '.join(hit)}): {line!r}")
    if len(found) > 20:
        bad(f"... and {len(found) - 20} more line(s)")
else:
    ok(f"{len(WORDS)} words checked over {len(SCOPE)} directories, none found")

print("\n=== and the check is reading something ===")
# Without this the file passes on a scope that resolved to nothing, which is
# the shape #151 spent an issue on and #235 found again in test/ itself.
seen = 0
for root in SCOPE:
    for _, _, filenames in os.walk(os.path.join(REPO, root)):
        seen += len(filenames)
if seen >= 100 and len(WORDS) >= 50:
    ok(f"{seen} file(s) in scope")
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
