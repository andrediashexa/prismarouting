# Rodar o Prisma Routing numa máquina virtual

O que uma máquina virtual precisa ter para a caixa encaminhar, e o que muda nela em relação a um servidor físico. Para quem vai avaliar o produto num hipervisor, ou pô-lo em produção num.

# Memória e processador

| Recurso | O que pedir | O que acontece se faltar |
|---------|-------------|--------------------------|
| RAM     | **6 GB no mínimo, 8 GB recomendado** | com 4 GB a caixa sobe no perfil conservador, sem hugepages, e o dataplane não recebe placa nenhuma: `show interface` só mostra `local0` e nada é encaminhado |
| Instruções do processador | **SSE4.2** (AVX2 é aproveitado quando existe) | sem SSE4.2 o dataplane morre na partida, em laço; o autoteste de boot confere o processador **antes** de olhar para o dataplane e diz o que falta |
| Páginas de 1 GB (`pdpe1gb`) | desejável, não obrigatório | a caixa cai para páginas de 2 MB sozinha; veja *Modelo de processador sem páginas de 1 GB* abaixo |

Medido numa VM com 8 núcleos, depois do segundo boot, que é o que aplica o ajuste:

| RAM da VM | O que a caixa faz |
|-----------|-------------------|
| 4 GB      | sobe, não encaminha |
| 6 GB      | encaminha, com 1 GB de heap: cabem umas 2,7 milhões de sessões |
| 8 GB      | encaminha, com 2 GB de heap |


:::warning
**O primeiro boot ainda não é o boot definitivo.** Hugepages e isolamento de núcleos vêm da linha de comando do kernel, então só valem a partir do boot seguinte. Uma VM avaliada no primeiro boot está no perfil conservador e não diz nada sobre o que a caixa faz.

:::

O modelo de processador conta. O modelo genérico `qemu64` do QEMU não tem SSE4.2: dê à VM o processador do hospedeiro, ou um modelo que tenha SSE4.2.

## Modelo de processador sem páginas de 1 GB

Muitos modelos de processador virtual não expõem páginas de 1 GB — é o caso do modelo padrão de uma VM no Proxmox. A caixa lê isso no boot e:

| O que ela ajusta | Por quê |
|------------------|---------|
| reserva as hugepages em páginas de **2 MB** em vez de 1 GB | pedir páginas de 1 GB a um processador que não as tem não reserva nada, em silêncio |
| mantém o número de páginas que calculou, mesmo contra o ajuste que o pacote do dataplane traz de fábrica | aquele ajuste, numa caixa de 2 MB, encolheria a reserva depois do boot e o dataplane não subiria |
| endereça a placa por endereço físico | numa caixa de 2 MB é o único modo em que o dataplane consegue usar a placa |

Não há nada a configurar: desde a versão 0.120 uma VM sem páginas de 1 GB sobe e encaminha. Antes disso ela subia sem dataplane.

# Placas de rede virtuais

| Situação | O que a caixa faz |
|----------|-------------------|
| placa **virtio** | é o caso comum e o mais testado; entregue ao dataplane como qualquer placa PCI |
| placa **virtio legada** (a que o QEMU dá a uma VM sem IOMMU) | também é entregue, desde a versão 0.121; antes disso o dataplane subia e não pegava a porta, sem dizer por quê |
| interface **sem endereço PCI** (bridge, bond, veth, `lo`) | não pode ser entregue ao dataplane; a caixa sobe, roteia e não encaminha, e o autoteste de boot diz isso |

## VM sem IOMMU

A maioria das VMs não tem IOMMU virtual. A caixa percebe isso no boot e liga a placa ao dataplane pelo driver `uio_pci_generic` em vez de `vfio-pci`.

|     | Com IOMMU | Sem IOMMU |
|-----|-----------|-----------|
| driver usado | `vfio-pci` | `uio_pci_generic` |
| o que você ganha ou perde | a placa só alcança a memória que recebeu, então um defeito no dataplane não escreve fora dela | encaminha igual, sem essa proteção |
| onde conferir | `show hardware` diz, por placa ainda não entregue, para qual driver a caixa vai movê-la | o mesmo   |

## Placa acrescentada com a VM ligada

Uma placa posta na VM pelo hipervisor com a caixa rodando encaminha **na hora**, sem reiniciar nada:

| Etapa | O que acontece |
|-------|----------------|
| a placa aparece | a caixa só a pega se a regra do boot também pegaria; uma placa com endereço IPv4 ou com a rota padrão fica com o Linux |
| a caixa a pega | ela entra no dataplane pelo **AF_XDP**, com o nome que o Linux deu e o MAC da própria placa |
| você configura | como qualquer outra: `interface <nome>`, endereço, `no shutdown`, `commit` |
| `show interface` | avisa, abaixo da contagem, que ela foi acrescentada com a caixa rodando e encaminha por AF_XDP até o dataplane reiniciar |
| `system dataplane restart` | passa a placa para o modo definitivo (DPDK), na janela que você escolher. Um reboot faz o mesmo |

O AF_XDP custa velocidade, não correção: a placa é lida pelo driver do kernel. Numa placa virtio de uma fila só, o próprio kernel avisa que o envio fica num modo mais lento.


:::warning
`system dataplane restart` para o encaminhamento até o dataplane voltar e **descarta o bloco de portas e as sessões de todos os assinantes**. O dataplane volta com a configuração **salva**: o que foi aplicado com `commit` e não foi salvo não volta. A tela diz o custo antes de agir. Medido numa VM: 18 segundos sem encaminhar.

:::

## A MTU que a placa virtual carrega

Toda placa virtual que o hipervisor configura anuncia um máximo — em geral 1500. O dataplane, por conta própria, começa toda interface em 9000, e uma placa que não aceita esse valor recusa a porta: ela fica em erro e não encaminha nada.

| O que a caixa faz | Consequência |
|-------------------|--------------|
| lê no boot o máximo de cada placa que vai pegar | o número vem do hipervisor, não de uma suposição |
| faz **todas** as interfaces começarem no **menor** máximo entre as placas pegas | numa VM com uma placa de 1500 ao lado de uma de 9000, a de 9000 também começa em 1500 |
| aceita `mtu <n>` no bloco da interface depois | é assim que se sobe a porta que carrega mais |
| não escreve nada quando todas as placas carregam 9000 ou mais | fica igual a uma caixa física |

Para carregar quadros maiores que 1500 numa VM, a MTU tem de ser aumentada primeiro na placa virtual, no hipervisor; a caixa lê o novo máximo no boot seguinte.

## Várias filas na placa virtual (multiqueue)

A caixa pergunta à placa quantas filas ela tem, e só esse número de workers recebe pacotes dela. Uma placa virtual com **uma** fila numa VM de oito núcleos faz um worker receber tudo — o relatório de boot diz isso numa linha por placa, com o limite, em vez de deixar a caixa lenta sem explicação.

| Filas na placa virtual | Workers que recebem pacotes |
|------------------------|-----------------------------|
| 1                      | 1, qualquer que seja o número de núcleos |
| igual ou maior que o número de workers | todos                       |

Para aproveitar os núcleos da VM, ligue no hipervisor várias filas na placa virtual (a opção costuma se chamar *multiqueue*) com um número igual ao de workers. A caixa relê as filas a cada boot.

# Desligar a VM

O *Shutdown* do hipervisor envia o botão de energia (ACPI), e a caixa desliga limpa: fecha os blocos no log de retenção e para os serviços antes de desligar. Medido num Proxmox: 41 segundos, dentro do tempo que o hipervisor espera antes de forçar.


:::warning
**Até a versão 0.139.0 a caixa ignorava esse botão.** Numa caixa nessa versão ou anterior, o *Shutdown* do hipervisor não faz nada e o desligamento forçado que vem depois é o mesmo que tirar o cabo de força: os blocos que o dataplane está segurando não são fechados no log de retenção. Atualize a caixa, ou desligue pela VTY.

:::

Desligue ou reinicie pela VTY, que diz o custo antes de agir e fecha os blocos no caminho:

```
system shutdown
```

```
system reboot
```

# A caixa sugere a própria afinação, e a do hipervisor

Numa VM, cada vCPU é uma thread do hipervisor, e um worker do dataplane nunca solta a CPU dele. Se dois vCPUs de workers caem no mesmo núcleo físico (dois irmãos de HT), ou se outra VM divide a thread com um deles, esse worker gira mais devagar que os outros, e o tempo que o hipervisor tira da VM aparece como *steal*. Foi o que aconteceu numa caixa de laboratório: com tráfego quase zero, os cinco workers giravam entre 2,41 e 3,29 milhões de voltas por segundo, com 7,7% de steal. Fixando cada vCPU numa thread do host, no nó NUMA da placa, um núcleo físico por worker, eles passaram a 3,21–3,30 milhões, e o steal ficou só no plano de controle.

A caixa diz isso sozinha:

```
prisma> show system tuning advice
prisma> show system tuning advice seconds 10
```

Ela mostra o que tem (CPUs, se é VM e de quem, memória e hugepages, cada placa do dataplane com suas filas e o worker de cada uma, o layout, MTU e `tcp-mss`), mede uma janela de alguns segundos (5 por padrão) sem zerar nenhum contador, e diz o que está errado, sempre com o número medido, o limite e o porquê: workers desiguais (um worker mais de 15% abaixo da mediana, sem tráfego), steal acima de 1% nas CPUs do dataplane, filas que não batem com os workers, worker fora do nó NUMA da placa, hugepages a menos do que a caixa pediu, thread principal dedicada numa caixa ociosa ou compartilhada com um plano de controle ocupado. Nada é alterado: aplicar é com você.

O que fazer vem em duas partes. **Na caixa**, as linhas exatas da VTY e se cada uma pede reinício do dataplane ou reboot. **No hipervisor**, quando é uma VM: o princípio, um *hookscript* do Proxmox (`/var/lib/vz/snippets/prisma-pin.sh`), as linhas `qm set` e um bloco `<cputune>` do libvirt, gerados a partir do papel de cada vCPU desta caixa. Os números das CPUs do host vêm como `HOST_*` — o script se recusa a rodar enquanto sobrar um — a menos que você cole a topologia do host:

```
prisma> show system tuning advice host-topology
```

Cole a saída de `lscpu -e=CPU,NODE,SOCKET,CORE` do host e, numa linha à parte, `nic-node <n>` com o nó NUMA da placa (`cat /sys/bus/pci/devices/<pci>/numa_node`); termine com uma linha vazia. A caixa então põe a thread principal no primeiro núcleo físico do nó da placa, o plano de controle no irmão de HT desse núcleo (a thread principal fica quase sempre parada, então é o lugar mais quieto do nó), cada worker num núcleo só dele, as outras threads do QEMU nos últimos irmãos que sobram, e as outras VMs no outro nó.

Fixar não basta para manter o resto longe dessas CPUs, então vem também a **linha de comando do kernel do host**: `isolcpus`, `nohz_full` e `rcu_nocbs` em todas as CPUs do host que a VM recebe. Com `isolcpus`, o escalonador do host nunca põe ali outra VM, um serviço do host ou uma thread do kernel, a não ser que esteja fixado ali, então não há mais nada a configurar; os outros dois tiram o tick do timer e os callbacks de RCU desses núcleos. Exige reiniciar o host e é estático. (Uma partição de cpuset do cgroup faz o mesmo em tempo de execução, mas não é gerida pelo Proxmox.)

A mesma linha reserva a memória da VM em páginas de 1 GB no nó da placa (`hugepagesz=1G hugepages=<nó>:<GB>`), e as linhas `qm set` amarram a memória ali: `numa0: cpus=0-<n-1>,hostnodes=<nó>,memory=<MB>,policy=bind` com `hugepages: 1024`. Os buffers do dataplane e o DMA da placa vivem nessa memória, e memória no outro soquete custa latência e vazão em cada pacote; `numa: 1` sozinho não amarra nada. Na caixa de laboratório, a VM tinha 16,1 GB no nó 1 e nada no nó 0 — local por sorte do primeiro toque, não por amarração. Cole também a saída de `numastat -p <pid do qemu da VM>` depois da topologia e a caixa diz onde a memória está agora.

No fim vem uma tabela de dimensionamento: para VMs de 4 a 32 vCPUs, quantos ficam com o plano de controle, se a thread principal é dedicada, quantos workers isso dá — calculada pelo próprio ajuste da caixa — e quantas threads do host reservar. É o número para decidir quantos vCPUs dar a uma VM nova.

# Medir desempenho numa VM

Um número de vazão medido numa VM mede o **caminho do switch virtual do hipervisor**, não a caixa. O custo do dataplane é por pacote, e numa VM o que acaba primeiro é a quantidade de pacotes por segundo que o hipervisor entrega à placa virtual.

| O que se mede | O que isso diz |
|---------------|----------------|
| pacotes por segundo | o limite real do caminho: é ele que acaba primeiro |
| bits por segundo com quadros pequenos | pouco: o mesmo limite de pacotes, com poucos bits em cada |
| bits por segundo com quadros grandes | mais bits no mesmo número de pacotes — por isso uma medição com quadros grandes parece melhor, sem que a caixa tenha mudado |

A caixa numa VM serve para avaliar funcionalidade, configuração e comportamento do CGNAT. Vazão de produção se mede numa placa física que o dataplane acesse diretamente.

# Ver também

* [Dimensionamento e requisitos](/doc/dimensionamento-e-requisitos-f42dC4k45s) — quanto de máquina e de endereços o seu caso exige
* [Instalação da appliance](/doc/instalacao-da-appliance-06NmxvBDb5) — a instalação em disco, passo a passo
* [Configuração de interface](/doc/configuracao-de-interface-j1q58jKWTQ) — `mtu`, endereços e o bloco `interface`
* [Operação e diagnóstico](/doc/operacao-e-diagnostico-A4fcKDVwZt) — o autoteste de boot e o que ele diz
