#!/usr/bin/env python3
"""
Testes do hexarouting-tune com topologias sinteticas.

A maquina de desenvolvimento nao tem SMT nem multiplos nos NUMA, que e
justamente onde as decisoes de tuning importam. Aqui as topologias sao
montadas em disco e conferidas.
"""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TUNE = os.path.join(HERE, "hexarouting-tune")
fail = 0

def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FALHA: {msg}")

def w(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(str(content) + "\n")

def build(root, sockets, cores_per_socket, threads, nic_node=None):
    """
    Numeracao tipica do Linux: primeiro todos os primeiros threads, depois
    todos os segundos. Com 2 sockets x 8 cores x 2 threads, a irma da cpu0
    e a cpu16.
    """
    per_thread = sockets * cores_per_socket
    node_cpus = {s: [] for s in range(sockets)}
    for t in range(threads):
        for s in range(sockets):
            for c in range(cores_per_socket):
                n = t * per_thread + s * cores_per_socket + c
                sibs = [k * per_thread + s * cores_per_socket + c
                        for k in range(threads)]
                base = f"{root}/sys/devices/system/cpu/cpu{n}/topology"
                w(f"{base}/physical_package_id", s)
                w(f"{base}/core_id", c)
                w(f"{base}/thread_siblings_list", ",".join(map(str, sibs)))
                node_cpus[s].append(n)
    for s, cpus in node_cpus.items():
        w(f"{root}/sys/devices/system/node/node{s}/cpulist",
          ",".join(map(str, sorted(cpus))))
    if nic_node is not None:
        w(f"{root}/sys/class/net/eth0/device/numa_node", nic_node)
    return node_cpus

def run(root, *args):
    p = subprocess.run([sys.executable, TUNE, "--sysfs-root", root] + list(args),
                       capture_output=True, text=True)
    return p.stdout

def field(out, prefix):
    for line in out.split("\n"):
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""

def expand(spec):
    out = []
    for part in spec.split(","):
        if not part: continue
        if "-" in part:
            a, b = part.split("-"); out += list(range(int(a), int(b)+1))
        else:
            out.append(int(part))
    return sorted(out)

# ---------------------------------------------------------------- casos

print("=== 2 sockets x 8 cores x 2 threads (32 CPUs), NIC no no 1 ===")
with tempfile.TemporaryDirectory() as root:
    nodes = build(root, sockets=2, cores_per_socket=8, threads=2, nic_node=1)
    out = run(root, "--interface", "eth0")
    workers = expand(field(out, "# workers"). split(": ")[-1])
    isolated = expand(field(out, "# isolados:"))
    oscpus = expand(field(out, "# sistema operacional e main do VPP:"))
    print(f"  workers={len(workers)} isolados={len(isolated)} os={oscpus}")

    check(all(c in nodes[1] for c in workers),
          f"workers fora do no da NIC: {[c for c in workers if c not in nodes[1]]}")
    check(len(workers) == 7, f"esperado 7 workers (8 cores - 1 do SO), obtido {len(workers)}")
    check(len(isolated) == 14, f"esperado 14 CPUs isoladas (7 cores x 2 threads), obtido {len(isolated)}")
    check(not set(workers) & set(oscpus), "worker sobre CPU do sistema operacional")
    check(len(oscpus) == 2, f"o SO deveria ficar com o core inteiro (2 threads), obtido {oscpus}")
    # nenhuma irma de worker pode ficar de fora do isolamento
    for c in workers:
        sib = c + 16 if c < 16 else c - 16
        check(sib in isolated, f"irma SMT {sib} do worker {c} nao foi isolada")

print("\n=== 1 socket x 4 cores, sem SMT ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0)
    out = run(root, "--interface", "eth0")
    workers = expand(field(out, "# workers").split(": ")[-1])
    isolated = expand(field(out, "# isolados:"))
    print(f"  workers={workers} isolados={isolated}")
    check(workers == [1, 2, 3], f"esperado [1,2,3], obtido {workers}")
    check(isolated == [1, 2, 3], f"sem SMT, isolados devem ser os workers; obtido {isolated}")

print("\n=== maquina minima: 1 socket x 1 core, sem SMT ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=1, threads=1, nic_node=0)
    out = run(root, "--interface", "eth0")
    print("  " + field(out, "# workers"))
    check("(nenhum)" in out or "sem workers" in out,
          "maquina de 1 core deveria terminar sem workers, nao com isolcpus vazio")
    check("isolcpus=" not in out.split("### /etc/vpp")[0].split("GRUB")[-1],
          "nao pode emitir isolcpus numa maquina sem core sobrando")

print("\n=== NIC sem afinidade NUMA declarada (-1) ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=2, cores_per_socket=4, threads=1, nic_node=-1)
    out = run(root, "--interface", "eth0")
    check("sem afinidade NUMA declarada" in out,
          "deveria avisar que a NIC nao declarou no NUMA")
    workers = expand(field(out, "# workers").split(": ")[-1])
    check(all(c < 4 for c in workers),
          f"sem afinidade, devia cair no no 0; workers={workers}")
    print(f"  caiu no no 0: workers={workers}")

print("\n=== limite de workers respeitado ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=2, nic_node=0)
    out = run(root, "--interface", "eth0", "--max-workers", "3")
    workers = expand(field(out, "# workers").split(": ")[-1])
    isolated = expand(field(out, "# isolados:"))
    check(len(workers) == 3, f"esperado 3 workers, obtido {len(workers)}")
    check(len(isolated) == 6, f"3 cores x 2 threads = 6 isoladas, obtido {len(isolated)}")
    print(f"  workers={workers} isolados={isolated}")

print("\n=== hugepages crescem com o numero de assinantes ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0)
    small = int(field(run(root, "--interface", "eth0", "--subscribers", "10000"),
                      "# hugepages de 1G:").split()[0])
    big = int(field(run(root, "--interface", "eth0", "--subscribers", "500000"),
                    "# hugepages de 1G:").split()[0])
    check(big > small, f"500k assinantes ({big}G) deveria pedir mais que 10k ({small}G)")
    print(f"  10k assinantes -> {small}G ; 500k -> {big}G")

print(f"\n{'TODOS OS TESTES PASSARAM' if not fail else str(fail) + ' FALHAS'}")
sys.exit(1 if fail else 0)
