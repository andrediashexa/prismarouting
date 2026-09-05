#!/usr/bin/env python3
"""
Testes da completacao e do "?" da VTY.

Exercitam a arvore de comandos sem terminal: o que o readline faria ao
completar ou ao pedir ajuda vem de _options(), e e isso que se confere aqui.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import hexash

fail = 0

def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FALHA: {msg}")

def opts(mode, line, ending=None):
    sh = hexash.Shell()
    sh.mode = mode
    if ending is None:
        ending = bool(line) and not line.endswith(" ")
    o, hint, prefix = sh._options(line, ending)
    return set(o), hint, prefix

print("=== operational mode: '?' on an empty line ===")
o, _, _ = opts("operational", "")
check({"configure", "show", "rollback", "save", "exit"} <= o, f"missing: {o}")
print(f"  {sorted(o)}")

print("\n=== 'show ' mostra os sub-comandos ===")
o, _, _ = opts("operational", "show ")
check("cgnat" in o and "running-config" in o, f"esperado cgnat e running-config: {o}")
print(f"  {sorted(o)}")

print("\n=== 'show cgnat ' desce mais um nivel ===")
o, _, _ = opts("operational", "show cgnat ")
check(o == {"pool", "session", "blocks", "logging", "history"}, f"got {o}")
print(f"  {sorted(o)}")

print("\n=== prefixo parcial completa ===")
o, _, prefix = opts("operational", "conf")
check(o == {"configure"}, f"'conf' deveria dar so 'configure': {o}")
check(prefix == "conf", f"prefixo {prefix}")
o, _, _ = opts("operational", "show cgnat po")
check(o == {"pool"}, f"'po' deveria dar so 'pool': {o}")
print("  'conf' -> configure ; 'show cgnat po' -> pool")

print("\n=== modo pool: opcoes proprias ===")
o, _, _ = opts("pool", "")
check({"inside", "outside", "block-size", "policy", "mode", "exit"} <= o,
      f"faltam opcoes de pool: {o}")
o, _, _ = opts("pool", "policy ")
check(o == {"interleaved", "blocked"}, f"policy deveria ter duas opcoes: {o}")
o, _, _ = opts("pool", "mode ")
check(o == {"deterministic", "dynamic"}, f"mode deveria ter duas opcoes: {o}")
print(f"  policy -> {sorted(o)} ; mode tem duas opcoes")

print("\n=== argumento livre vira dica, nao lista vazia ===")
o, hint, _ = opts("pool", "inside ")
check(hint == hexash.ARG_PREFIX, f"esperado dica de prefixo, obtido {hint!r}")
check(not o, f"nao deveria listar opcoes apos 'inside': {o}")
o, hint, _ = opts("config", "cgnat block-holddown ")
check(hint == hexash.ARG_NUM, f"esperado dica numerica, obtido {hint!r}")
print(f"  'inside ' -> {hexash.ARG_PREFIX} ; 'block-holddown ' -> {hexash.ARG_NUM}")

print("\n=== config: CGNAT e roteamento no mesmo nivel ===")
o, _, _ = opts("config", "")
check({"cgnat", "router", "interface", "no", "commit", "rollback", "exit"} <= o,
      f"got {o}")
o, _, _ = opts("config", "cgnat ")
check({"pool", "interface", "timeout", "logging", "block-holddown"} <= o, f"obtido {o}")
o, _, _ = opts("config", "router ")
check({"bgp", "ospf", "isis"} <= o, f"obtido {o}")
print("  cgnat, router, interface, no, exit")

print("\n=== commit sub-commands, Juniper style ===")
o, _, _ = opts("config", "commit ")
check({"check", "confirmed", "comment"} <= o, f"got {o}")
print(f"  commit -> {sorted(o)}")

o, hint, _ = opts("config", "rollback ")
check(hint == hexash.ARG_REV, f"rollback should hint a revision, got {hint!r}")
print(f"  rollback -> {hexash.ARG_REV}")

print("\n=== comando desconhecido nao inventa opcao ===")
o, hint, _ = opts("config", "algo-que-nao-existe ")
check(not o and hint is None,
      f"deveria admitir que nao sabe, obtido opcoes={o} hint={hint!r}")
print("  admite que dali em diante quem sabe e o FRR")

print(f"\n{'TODOS OS TESTES PASSARAM' if not fail else str(fail) + ' FALHAS'}")
sys.exit(1 if fail else 0)
