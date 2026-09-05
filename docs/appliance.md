# HexaRouting como appliance

Instalar VPP e FRR e a parte facil. O que decide se a caixa entrega os
100 Gbps ou metade disso e o tuning de boot, e ele nao falha de forma
visivel: a caixa sobe, encaminha, e rende menos.

## `hexarouting-tune`

Calcula isolamento de CPU, hugepages e pinning a partir do hardware, em vez
de valores copiados de um blog. Regras aplicadas, com o motivo:

| Regra | Por que |
|-------|---------|
| CPU 0 e sua irma SMT ficam para o SO e a main do VPP | isolar tudo deixa o kernel sem onde rodar |
| workers so no no NUMA da NIC | atravessar NUMA no caminho de pacote custa mais do que o worker extra rende |
| um worker por core fisico, irma isolada junto | senao carga do sistema compete pelo mesmo core fisico |
| `nohz_full` e `rcu_nocbs` acompanham o `isolcpus` | isolar sem tirar o tick e o callback de RCU deixa jitter, que aparece como perda em rajada |
| hugepages de 1G dimensionadas pelo alvo de assinantes | orcamento de sessao e bitmap vem de [architecture.md](architecture.md) |

Testado com topologias sinteticas em `appliance/test_tune.py`, porque a
maquina de desenvolvimento nao tem SMT nem multiplos nos NUMA - e e
exatamente ali que as decisoes importam. Um dos casos monta 2 sockets x 8
cores x 2 threads com a NIC no no 1 e confere que nenhum worker caiu no no 0
e que nenhuma irma SMT ficou fora do isolamento.

## Primeiro boot: o tuning ainda nao vale

Encontrado ao bootar a ISO em QEMU, e nao antes: `isolcpus` e hugepages vem
da linha de comando do kernel, entao **so entram em vigor no boot seguinte**.
A primeira versao gerava um `startup.conf` exigindo `default-hugepage` que
ainda nao existia, e o VPP entrava em loop de reinicio - o que quebraria toda
primeira inicializacao depois de instalar.

Agora o `hexarouting-tune` le o estado real do kernel:

```
/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages
/sys/devices/system/cpu/isolated
```

Enquanto os dois nao estiverem no lugar, o `startup.conf` sai conservador:
sem pinning, paginas de 4k, heap menor. Funciona em qualquer lugar e nao
rende - mas sobe. A linha do GRUB continua pedindo o tuning completo para o
boot seguinte, e ai o mesmo comando passa a gerar a config completa.

Meio caminho tambem conta como nao tunado: hugepages reservadas sem
`isolcpus` ainda sai conservador, porque pinar worker sem isolamento faz o
VPP competir com o proprio sistema.

## O startup.conf do VPP nao tem include

Encontrado ao investigar por que o VPP reiniciava na ISO: o
`/etc/vpp/startup.conf` do pacote nao tem diretiva de include, e o VPP nao
suporta nenhuma. A primeira versao gravava o tuning em
`/etc/vpp/startup.conf.d/tuning.conf`, que **nunca foi lido**.

O efeito e o pior possivel para um produto: nao ha erro, nao ha aviso, a
caixa sobe e encaminha. Ela so rende metade, e ninguem tem como saber por que.

Agora o `hexarouting-tune` gera o arquivo que o VPP realmente le,
concatenando `/etc/hexarouting/vpp-base.conf` - editavel pelo operador, com
`unix`, `plugins`, `linux-cp` - com as stanzas `cpu`, `buffers` e `memory`
que ele calcula. A base nao deve conter essas tres, para nao duplicar.

`appliance/test_startup_conf.sh` fecha o buraco de teste que permitiu isso:
gera a configuracao, **sobe o VPP com ela**, confere que os plugins
carregaram, que o processo continua vivo e que da para criar pool. Roda com
`make test-appliance`.

## Servicos

```
hexarouting-tune.service   Before=vpp.service    calcula e grava o tuning
vpp.service                                      dataplane
frr.service                                      plano de controle
hexarouting.service        After=vpp,frr         aplica a config de CGNAT
```

Duas decisoes que valem explicar:

- **FRR e `Wants`, nao `Requires`.** Sem roteamento a caixa ainda encaminha o
  que houver em rota estatica; derrubar o CGNAT junto so aumentaria o estrago.
- **Sem `ExecStop`.** A configuracao vive no VPP. Parar o servico nao pode
  apagar pool de producao; quem remove pool e o operador, pela VTY.

O `hexarouting.service` espera o VPP aceitar comandos antes de aplicar: o
systemd considera o `vpp.service` iniciado quando o processo sobe, mas o
socket de CLI e os plugins levam mais um instante, e aplicar antes disso
falha de forma intermitente.

O tuning roda em **todo** boot, sem sentinela, para respeitar mudanca em
`tune.env` ou troca de NIC. Quem evita o custo do `update-grub` e o
`apply-tuning`, que compara antes de regenerar.

## Instalacao

```sh
sh appliance/install.sh              # sobre um Debian 12 existente
DESTDIR=/mnt sh appliance/install.sh # dentro de um chroot, para a ISO
sh appliance/build-iso.sh            # ISO hibrida BIOS/UEFI
```

Depois de instalar, ajustar `/etc/hexarouting/tune.env` (NIC do dataplane e
alvo de assinantes) e reiniciar: `isolcpus` e hugepages so valem no boot
seguinte.

## O que ainda falta

- HA: hoje nao ha failover. Duas caixas com o mesmo pool deterministico
  traduzem igual, o que ajuda, mas a tabela de sessao nao e sincronizada
- transporte TCP para o log IPFIX (ver [logging.md](logging.md))
- trilha de configuracao datada, que e o "log" do modo deterministico
