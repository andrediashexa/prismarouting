# HexaRouting

Roteador em software com CGNAT BPA (Bloco de Portas Alocadas), entregue como
appliance Debian.

- **Dataplane**: VPP sobre DPDK, com o plugin `bpa` fazendo o CGNAT
- **Plano de controle**: FRR (BGP, OSPF, IS-IS), colado ao VPP por
  `linux_cp` / `linux_nl` — ver [docs/routing.md](docs/routing.md)
- **Configuracao**: `hexash`, VTY unica para roteamento e CGNAT
- **Alvo**: 100 Gbps+, 200k+ assinantes

## Layout

```
docs/architecture.md      decisoes de projeto, sharding, estado
docs/routing.md           plano de FRR, VTY e as armadilhas de punt
docs/logging.md           exportacao de log BPA via IPFIX (RFC 8158)
ref/                      modelo de referencia do BPA, em Python
src/plugins/bpa/          plugin VPP do CGNAT BPA
src/hexash/               VTY: CGNAT nativo, roteamento via vtysh
test/                     testes do nucleo, sem VPP e sem root
lab/                      topologias de teste com packet-generator
appliance/                ISO, tuning de kernel, systemd
```

## Testes

```sh
make test     # nucleo BPA: mapeamento e alocador de portas
make model    # valida o proprio modelo de referencia
```

Nao precisa de root, de compilador de VPP nem de NIC. O plugin em C deve
reproduzir o modelo Python bit a bit; qualquer mudanca no mapeamento passa
por `ref/` primeiro.

## Build e lab

```sh
sh tools/build.sh image     # container de build, VPP fixado
sh tools/build.sh plugin    # compila o plugin

# dentro do container, com o repo montado em /src:
sh lab/datapath.sh          # traducao com pacote real, 1 worker
sh lab/handoff.sh           # sharding entre 2 workers
sh lab/routing.sh           # BGP, OSPF e IS-IS ate a FIB do VPP
sh lab/dynamic.sh           # modo dinamico e hold-down de bloco
sh lab/ipfix.sh             # export RFC 8158 com coletor decodificando
sh lab/vty.sh               # a VTY configurando os dois mundos
```
