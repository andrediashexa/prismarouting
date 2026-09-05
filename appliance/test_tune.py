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

def build(root, sockets, cores_per_socket, threads, nic_node=None,
          hugepages_1g=0, isolated="", nic_pci=None):
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
        if nic_pci:
            # A NIC de verdade e um symlink para o device PCI. Sem reproduzir
            # isso, o caminho que gera o stanza dpdk nunca e exercitado.
            dev = f"{root}/sys/devices/pci0000:00/{nic_pci}"
            w(f"{dev}/numa_node", nic_node)
            os.makedirs(f"{root}/sys/class/net/eth0", exist_ok=True)
            os.symlink(dev, f"{root}/sys/class/net/eth0/device")
        else:
            w(f"{root}/sys/class/net/eth0/device/numa_node", nic_node)
    w(f"{root}/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages",
      hugepages_1g)
    w(f"{root}/sys/devices/system/cpu/isolated", isolated)
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
    isolated = expand(field(out, "# isolated:"))
    oscpus = expand(field(out, "# operating system and VPP main:"))
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
    isolated = expand(field(out, "# isolated:"))
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
    check("declares no NUMA affinity" in out,
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
    isolated = expand(field(out, "# isolated:"))
    check(len(workers) == 3, f"esperado 3 workers, obtido {len(workers)}")
    check(len(isolated) == 6, f"3 cores x 2 threads = 6 isoladas, obtido {len(isolated)}")
    print(f"  workers={workers} isolados={isolated}")

print("\n=== hugepages crescem com o numero de assinantes ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0)
    small = int(field(run(root, "--interface", "eth0", "--subscribers", "10000"),
                      "# 1G hugepages:").split()[0])
    big = int(field(run(root, "--interface", "eth0", "--subscribers", "500000"),
                    "# 1G hugepages:").split()[0])
    check(big > small, f"500k assinantes ({big}G) deveria pedir mais que 10k ({small}G)")
    print(f"  10k assinantes -> {small}G ; 500k -> {big}G")

print("\n=== primeiro boot: kernel ainda sem hugepage nem isolcpus ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_1g=0, isolated="")
    out = run(root, "--interface", "eth0")
    check("FIRST BOOT" in out, "deveria detectar que o kernel nao esta tunado")
    check("FIRST BOOT MODE" in out, "startup.conf deveria sair conservador")
    check("corelist-workers" not in out,
          "nao pode pinar worker sem isolcpus: o VPP tomaria os cores do SO")
    check("default-hugepage" not in out,
          "nao pode exigir hugepage que nao existe: poe o VPP em loop de reinicio")
    check("page-size 4k" in out, "deveria cair para paginas de 4k")
    check("plugin dpdk_plugin.so { disable }" in out,
          "sem hugepage a EAL do DPDK falha e o VPP sai; com Restart=always "
          "isso vira loop de reinicio")
    # e a linha do GRUB continua pedindo o tuning para o boot seguinte
    check("isolcpus=1-7" in out, "o GRUB ainda deve pedir isolcpus para o proximo boot")
    check("hugepages=" in out, "o GRUB ainda deve pedir hugepages para o proximo boot")
    print("  config conservadora agora, GRUB pedindo o tuning para o proximo boot")

print("\n=== boot seguinte: kernel ja tunado ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_1g=6, isolated="1-7")
    out = run(root, "--interface", "eth0")
    check("FIRST BOOT" not in out, "nao deveria mais estar em primeiro boot")
    check("corelist-workers 1-7" in out, "deveria pinar os workers")
    check("default-hugepage" in out, "deveria usar hugepages")
    check("plugin dpdk_plugin.so { disable }" not in out,
          "com hugepage o DPDK tem que voltar")
    check("bpa_plugin.so { enable }" in out, "o plugin do CGNAT tem que estar ligado")
    print("  config completa: pinning e hugepages")

print("\n=== hugepages reservadas mas isolcpus ausente: ainda conservador ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_1g=6, isolated="")
    out = run(root, "--interface", "eth0")
    check("FIRST BOOT" in out,
          "sem isolcpus, pinar worker faz o VPP competir com o SO")
    print("  meio caminho ainda e tratado como nao tunado")

print("\n=== fila de RX por worker e endereco PCI da NIC ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_1g=6, isolated="1-7", nic_pci="0000:3b:00.0")
    out = run(root, "--interface", "eth0")
    workers = expand(field(out, "# workers").split(": ")[-1])
    check("dev 0000:3b:00.0" in out, "o stanza dpdk deve nomear o device PCI")
    check(f"num-rx-queues {len(workers)}" in out,
          f"uma fila por worker: esperava {len(workers)}")
    check("num-tx-queues" in out, "sem fila de TX o DPDK cai no default")
    print(f"  dpdk: {len(workers)} filas em 0000:3b:00.0")

print("\n=== NIC que nao e PCI nao inventa endereco ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=4, threads=1, nic_node=0,
          hugepages_1g=6, isolated="1-3")
    out = run(root, "--interface", "eth0")
    check("dev 0000:" not in out, "nao pode inventar endereco PCI")
    check("dev default" in out, "ainda deve dimensionar a fila no default")
    print("  cai no dev default")

print("\n=== C-states e THP desligados por padrao ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=1, nic_node=0,
          hugepages_1g=6, isolated="1-7")
    out = run(root, "--interface", "eth0")
    check("processor.max_cstate=1" in out, "C-state profundo deve sair da linha")
    check("intel_idle.max_cstate=0" in out, "intel_idle tambem responde por C-state")
    check("transparent_hugepage=never" in out, "khugepaged para a CPU que compacta")
    off = run(root, "--interface", "eth0", "--no-low-latency")
    check("max_cstate" not in off, "--no-low-latency tem que remover mesmo")
    check("isolcpus=1-7" in off, "e nao pode derrubar o resto do tuning junto")
    print("  ligado por padrao, desligavel na flag")

print("\n=== IRQ banida dos cores isolados ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=8, threads=2, nic_node=0,
          hugepages_1g=6, isolated="1-7,9-15")
    out = run(root, "--interface", "eth0")
    banned = field(out, "IRQBALANCE_BANNED_CPULIST=")
    check(expand(banned) == expand(field(out, "# isolated:")),
          f"banidas devem ser exatamente as isoladas; obtido {banned!r}")
    print(f"  banidas: {banned}")

print("\n=== maquina sem core para isolar nao gera lista vazia de banimento ===")
with tempfile.TemporaryDirectory() as root:
    build(root, sockets=1, cores_per_socket=1, threads=1, nic_node=0)
    out = run(root, "--interface", "eth0")
    check("IRQBALANCE_BANNED_CPULIST=" not in out,
          "banir lista vazia desliga o irqbalance inteiro em algumas versoes")
    print("  nao emite banimento")

# ---------------------------------------------------------------- apply-tuning

print("\n=== apply-tuning nao pode travar o boot num sistema live ===")
import subprocess, os as _os, tempfile as _tf, shutil as _sh

APPLY = os.path.join(HERE, "apply-tuning")
fakebin = _tf.mkdtemp()
# update-grub falso que demora: se for chamado, o teste percebe
with open(os.path.join(fakebin, "update-grub"), "w") as f:
    f.write("#!/bin/sh\necho CHAMOU-UPDATE-GRUB\nsleep 5\n")
_os.chmod(os.path.join(fakebin, "update-grub"), 0o755)
with open(os.path.join(fakebin, "hexarouting-tune"), "w") as f:
    f.write("#!/bin/sh\nexit 0\n")
_os.chmod(os.path.join(fakebin, "hexarouting-tune"), 0o755)

env = dict(_os.environ, PATH=fakebin + ":" + _os.environ["PATH"],
           HEXA_DATAPLANE_IF="", HEXA_SUBSCRIBERS="1000")
r = subprocess.run(["sh", APPLY], capture_output=True, text=True, env=env,
                   timeout=30)
out = r.stdout + r.stderr
check("CHAMOU-UPDATE-GRUB" in out or "tuning unchanged" in out,
      "num sistema normal o update-grub deveria ser considerado")
# O ajuste de governor roda antes de tudo: se ele abortar por nao achar
# cpufreq, o resto do apply-tuning nunca acontece.
check("governor" in out, f"apply-tuning deveria relatar o governor; saiu {out!r}")
check(r.returncode == 0, f"apply-tuning nao pode falhar aqui (rc={r.returncode})")
print("  sistema normal: comportamento preservado, governor relatado")
_sh.rmtree(fakebin, ignore_errors=True)

print(f"\n{'TODOS OS TESTES PASSARAM' if not fail else str(fail) + ' FALHAS'}")
sys.exit(1 if fail else 0)
