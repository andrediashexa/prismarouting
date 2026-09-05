# Como testar o HexaRouting

Quatro niveis, do mais barato ao mais caro. Cada um pega uma classe
diferente de defeito, e nenhum substitui o seguinte.

## 1. Nucleo (segundos, sem nada instalado)

```sh
make test      # mapeamento BPA, alocador de portas e tuning de boot
make model     # valida o proprio modelo de referencia em Python
```

Nao precisa de root, de VPP nem de rede. O C e conferido contra
`ref/bpa_model.py`, que e a especificacao executavel: divergencia quebra o
build.

Pega: erro de aritmetica no mapeamento, colisao de bloco, alocador de portas,
calculo de isolcpus e hugepages.

## 2. Labs de dataplane (minutos, dentro do container)

```sh
sh tools/lab.sh                # lista os labs com uma linha de descricao
sh tools/lab.sh datapath       # traducao com pacote real, 1 worker
sh tools/lab.sh handoff        # sharding entre 2 workers
sh tools/lab.sh dynamic        # modo dinamico e hold-down de bloco
sh tools/lab.sh frag           # fragmento IP
sh tools/lab.sh icmp-tcp       # erro ICMP (PMTUD) e estado TCP
sh tools/lab.sh routing        # BGP, OSPF e IS-IS ate a FIB do VPP
sh tools/lab.sh ipfix          # export RFC 8158 com coletor decodificando
sh tools/lab.sh ipfix-tcp      # transporte TCP, com o coletor derrubado no meio
sh tools/lab.sh vty            # a VTY configurando roteamento e CGNAT
sh tools/lab.sh api            # API binaria
sh tools/lab.sh audit          # trilha de configuracao datada
```

Usam o packet-generator do VPP: nao precisam de NIC nem de trafego externo.
Cada um imprime o **esperado** antes do **obtido**, e varios conferem o
pacote capturado em vez de acreditar no contador.

Pega: comportamento do dataplane, ordem de features, checksum, sharding.

## 3. Hardware real (duas VMs, internet de verdade)

Topologia em `lab/hw-lab.sh`. Precisa das duas VMs e de acesso root ao peer
por chave.

```sh
sh tools/lab.sh hw-lab                       # /31, assinantes, FORWARD
sg docker -c 'docker rm -f hexa-vpp; docker run -d --name hexa-vpp \
    --privileged --network=host -v $PWD:/src -w /src \
    hexarouting-dev:26.06 sleep infinity'
sg docker -c 'docker exec hexa-vpp sh /src/lab/hw-vpp.sh'
```

Depois, no peer (10.95.200.197):

```sh
ip route replace 1.1.1.1/32 via 10.95.172.128 dev ens19
ping -c4 -I 100.64.0.5 1.1.1.1
curl --interface 100.64.0.5 https://1.1.1.1/
python3 /tmp/manyconn.py 100.64.0.5 600      # esgotamento de bloco
```

Esperado, conferido contra `ref/bpa_model.py`:

```
100.64.0.5 -> 198.19.24.5    portas 1024-1535
100.64.0.6 -> 198.19.24.6
100.64.0.7 -> 198.19.24.7
```

Para ver no fio:

```sh
sg docker -c 'docker exec hexa-vpp tcpdump -nni vppwan -c 10 icmp'
sg docker -c 'docker exec hexa-vpp vppctl show bpa session'
sg docker -c 'docker exec hexa-vpp vppctl show errors'
```

Pega: o que so aparece com trafego de verdade - TCP completo, TLS,
concorrencia, limites do outro lado da internet.

## Acessando a VTY

Com o lab de pe:

```sh
sh tools/vty.sh                     # sessao interativa
sh tools/vty.sh -c "show cgnat pool"  # um comando e sai
```

Dentro dela, roteamento e CGNAT na mesma sessao:

```
hexarouting# configure terminal
hexarouting(config)# cgnat pool provedor-a
hexarouting(config-cgnat-pool)# inside 100.64.0.0/22
hexarouting(config-cgnat-pool)# outside 198.19.24.0/24
hexarouting(config-cgnat-pool)# block-size 512
hexarouting(config-cgnat-pool)# exit
hexarouting(config)# router bgp 65000
hexarouting(config)# neighbor 10.95.172.129 remote-as 65001
hexarouting(config)# exit
hexarouting# show running-config
hexarouting# write memory
```

Comandos de CGNAT vao para o plugin no VPP; qualquer outra coisa e repassada
ao `vtysh` do FRR com o contexto preservado. O `show running-config` le a
configuracao do **dataplane**, nao da memoria da shell - cada invocacao e um
processo novo.

Tab completa, e `?` lista as opcoes com descricao no ponto em que voce esta:

```
hexarouting# show cgnat ?
  blocks   blocos alocados no modo dinamico
  logging  estado da exportacao IPFIX
  pool     pools de CGNAT configurados
  session  sessoes ativas por thread

hexarouting(config-cgnat-pool)# inside ?
  <A.B.C.D/M>
```

A arvore cobre os comandos de CGNAT inteiros e os pontos de entrada do
roteamento. Depois de um comando do FRR ela admite que nao sabe, em vez de
fingir uma lista que ficaria desatualizada a cada versao do FRR.

## 4. Appliance (ISO)

```sh
sh appliance/build-iso.sh      # ~10 min
sh tools/lab.sh boot-iso       # segundos com KVM, minutos sem
make test-appliance            # gera o startup.conf e sobe o VPP com ele
```

O `boot-iso` falha com codigo diferente de zero se o autoteste nao reportar
`HexaRouting pronto`, e despeja o console em `boot-console.log` quando falha.

Requisitos verificados: **4 GB de RAM** e CPU com **SSE4.2** - abaixo disso o
VPP e morto por OOM ou por SIGILL, e o autoteste avisa antes.

Pega: ordem de servicos, tuning de boot, tudo o que so existe depois de
instalar.

## O que nao esta testado

- **HA / failover**: analisado em [docs/ha.md](docs/ha.md), nao implementado.
- **Throughput**: os labs medem correcao, nao desempenho. Numero de
  throughput em virtio nao diz nada sobre 100 Gbps; isso exige E810 ou CX6
  em ferro.
