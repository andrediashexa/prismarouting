# Rodar o Prisma Routing numa máquina virtual

O que uma máquina virtual precisa ter para a caixa encaminhar, e o que muda nela em relação a um servidor físico. Para quem vai avaliar o produto num hipervisor, ou pô-lo em produção num.

# Memória e processador

| Recurso | O que pedir | O que acontece se faltar |
|---------|-------------|--------------------------|
| RAM | **6 GB no mínimo, 8 GB recomendado** | com 4 GB a caixa sobe no perfil conservador, sem hugepages, e o dataplane não recebe placa nenhuma: `show interface` só mostra `local0` e nada é encaminhado |
| Instruções do processador | **SSE4.2** (AVX2 é aproveitado quando existe) | sem SSE4.2 o dataplane morre na partida, em laço; o autoteste de boot confere o processador **antes** de olhar para o dataplane e diz o que falta |
| Páginas de 1 GB (`pdpe1gb`) | desejável, não obrigatório | a caixa cai para páginas de 2 MB sozinha; veja *Modelo de processador sem páginas de 1 GB* abaixo |

Medido numa VM com 8 núcleos, depois do segundo boot, que é o que aplica o ajuste:

| RAM da VM | O que a caixa faz |
|-----------|-------------------|
| 4 GB | sobe, não encaminha |
| 6 GB | encaminha, com 1 GB de heap: cabem umas 2,7 milhões de sessões |
| 8 GB | encaminha, com 2 GB de heap |

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

| | Com IOMMU | Sem IOMMU |
|---|---|---|
| driver usado | `vfio-pci` | `uio_pci_generic` |
| o que você ganha ou perde | a placa só alcança a memória que recebeu, então um defeito no dataplane não escreve fora dela | encaminha igual, sem essa proteção |
| onde conferir | `show hardware` diz, por placa ainda não entregue, para qual driver a caixa vai movê-la | o mesmo |

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
|------------------------|------------------------------|
| 1 | 1, qualquer que seja o número de núcleos |
| igual ou maior que o número de workers | todos |

Para aproveitar os núcleos da VM, ligue no hipervisor várias filas na placa virtual (a opção costuma se chamar *multiqueue*) com um número igual ao de workers. A caixa relê as filas a cada boot.

# Desligar a VM

:::warning
**Na versão atual a caixa ignora o botão de desligar da VM.** O *Shutdown* do hipervisor envia o botão de energia (ACPI), e a caixa não reage a ele. Um desligamento forçado depois disso é o mesmo que tirar o cabo de força: os blocos que o dataplane está segurando não são fechados no log de retenção.
:::

Desligue ou reinicie pela VTY, que diz o custo antes de agir e fecha os blocos no caminho:

```
system shutdown
```

```
system reboot
```

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
