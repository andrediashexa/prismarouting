# Dimensionamento e requisitos

Quanto de máquina e quantos endereços públicos o seu caso exige. Leia antes de comprar hardware ou de fechar o bloco de IPv4.

# Requisitos de máquina

| Recurso | Mínimo | Por quê |
|---------|--------|---------|
| RAM     | **6 GB** (8 GB recomendado) | com menos, a caixa sobe e não encaminha: medido com 4 GB, o dataplane fica sem placa nenhuma |
| CPU     | **SSE4.2** | o binário do VPP assume SSE4.2 e usa AVX quando existe |
| Placas de rede | **1**  | uma basta, e a caixa entrega essa mesma placa ao dataplane levando o endereço de gerência junto; duas é o formato de uma appliance no rack |
| Disco   | **raiz de 8 GB, mais o volume de log** | o instalador nunca deixa a raiz com menos de 8 GB; o volume de log tem 30 GB por padrão e pode ser menor (seção *Volume de log*) |


:::warning
**CPU sem SSE4.2 não é um problema lento, é um problema mudo.** O VPP morre com `SIGILL` em milissegundos e, com `Restart=always`, o sintoma vira um laço de reinício que não diz nada sobre a causa. Por isso o self-test de boot confere o conjunto de instruções **antes** de olhar para o VPP.

:::

Medido durante a validação: com 2 GB a ISO reporta `Out of memory: Killed process (vpp)` com o VPP segurando apenas **19 MB residentes** — ele não era o glutão, era a vítima escolhida. Instalado em disco o consumo cai, porque não há a sobreposição em RAM, mas o piso de uma caixa que encaminha é 6 GB, pelo motivo da tabela acima.


:::warning
**Abaixo de 6 GB a caixa não encaminha.** Numa VM de 4 GB ela sobe no perfil conservador, sem hugepages, e o dataplane não recebe placa nenhuma.

:::

Medido numa VM com 8 núcleos, depois do reboot que aplica o ajuste:

| RAM | O que a caixa faz |
|-----|-------------------|
| 4 GB | sobe no perfil conservador, sem hugepages: o dataplane não recebe placa e **não encaminha nada** |
| 6 GB | encaminha, com 1 GB de heap: cabem umas 2,7 milhões de sessões |
| 8 GB | encaminha, com 2 GB de heap |

Até a versão 0.128 uma VM de 6 GB não subia o VPP; desde a 0.129 ela sobe.

# Quantas placas de rede

**Uma placa já serve, e é assim que a maioria das pessoas conhece este produto:** uma máquina virtual com uma placa virtual. O dataplane fica com a porta e o endereço de gerência vai junto, para a interface que o dataplane mantém emparelhada com ela do lado do Linux. A caixa deixa de ser alcançada *ao lado* do dataplane e passa a ser alcançada *através* dele, com o mesmo MAC, então nada do outro lado do cabo precisa reaprender.

**Duas placas é o formato de uma appliance no rack**, e a diferença não é desempenho: é onde fica a gerência. A caixa reivindica todas as placas menos a que foi respondida como rede de gerência na instalação, e nunca a última. Com duas e uma gerência respondida, a gerência fica **ao lado** do dataplane: reiniciar o dataplane não derruba a sua sessão. Com uma só, a gerência passa **através** do dataplane, e reiniciá-lo — ou reiniciar a caixa — derruba a sessão junto; a caixa volta a ser alcançável quando o dataplane sobe, sem ninguém digitar nada no console.

| Placas | O que a caixa faz | O que você paga |
|--------|-------------------|-----------------|
| 1      | entrega a placa ao dataplane e leva o endereço de gerência para o par | reiniciar o dataplane derruba a sessão de gerência até ele voltar |
| 2 ou mais, com gerência respondida na instalação | fica com todas menos a de gerência | nada            |
| 2 ou mais, com gerência `none` | fica com todas; o endereço de gerência, se houver, vai para o par da placa que o carregava | o mesmo da caixa de uma placa |

## Placa acrescentada com a caixa ligada

Uma placa posta na máquina com a caixa rodando — numa máquina virtual, uma placa acrescentada pelo hipervisor — encaminha **na hora**, sem reiniciar nada, se a regra do boot a reivindicaria. Ela entra com o nome que o Linux deu a ela, configura-se na VTY como qualquer outra, e `show interface` avisa que ela está num modo provisório. `system dataplane restart`, na janela que você escolher, passa essa placa para o modo definitivo: custa as sessões de todos os assinantes e alguns segundos sem encaminhar, e a tela diz isso antes de agir.

## A placa precisa ser um dispositivo PCI

O dataplane pega a porta pelo endereço PCI dela. Uma interface que **não** tem endereço PCI não pode ser entregue: `lo`, uma bridge, um bond e um veth não têm. Nesse caso a caixa escreve `no-pci` no arquivo do dataplane, sobe, roteia, responde - e não encaminha nada. O autoteste de boot diz isso em vez de deixar você descobrir pelo contador parado.

## A caixa lê a placa antes de escrever a configuração dela

O ajuste de boot pergunta **à placa** quantas filas ela tem e qual é a MTU máxima dela, em vez de deduzir do processador. Antes ele pedia uma fila por worker, e numa placa virtual, que tem **uma** fila, isso fazia o dispositivo ser recusado: a caixa subia dona de uma placa que não conseguia iniciar. O número de filas é corrigido a partir da placa.

A MTU também é lida e **corrigida**. Desde a 0.152.1 toda interface sem `mtu` começa em **1500**, o padrão da caixa — antes começava nos 9000 do próprio VPP, e com isso o `tcp-mss auto` dava 8960 e não rebaixava nada num caminho de 1500. Uma placa que não aceita nem o padrão recusa o dispositivo: a porta fica em erro e não encaminha nada. Por isso, quando alguma placa reivindicada tem máximo abaixo de 1500, a caixa faz as interfaces começarem no **menor máximo** entre as placas que ela reivindica. O valor vale para todas: numa caixa com uma placa de 1400 ao lado de uma de 9000, a de 9000 também começa em 1400, e `mtu <n>` no bloco da interface sobe aquela porta depois.

O relatório de boot diz o que ele encontrou, uma linha por placa. Numa máquina de oito núcleos com uma placa virtual `ens18` de máximo 1500:

```
# NIC queues, ens18: 1, and this box has 7 worker(s). Capped at 1: only that many workers receive packets, so expect a fraction of the forwarding this CPU could do.
# interfaces start at: 1500, this box's default. 'mtu <n>' under
```

Com uma placa de máximo 1400, aparece a linha dela e o parágrafo começa no máximo da placa:

```
# NIC max MTU, ens18: 1400, below the box's default of 1500
```

seguida de um parágrafo que começa com `# interfaces start at: 1400` e diz por que aquele valor vale para todas as placas.

**Uma caixa instalada antes da 0.152.1 continua em 9000.** A atualização escreve `mtu 9000` em toda interface da configuração salva que não tinha `mtu` e deixa o arquivo `/etc/prisma-routing/mtu-9000-kept`; com ele, o ajuste de boot não escreve `default-mtu` nenhum, como antes, e o relatório diz:

```
# interfaces start at: VPP's 9000, kept on a box installed before
```

**O limite é dito, não aplicado em silêncio.** Uma fila com sete workers significa um worker recebendo pacotes, e isso é uma fração da capacidade que aquele processador teria — é informação de dimensionamento, e esconder o corte trocaria uma caixa que não sobe por uma caixa lenta sem explicação.

Placa com filas de sobra recebe uma linha curta, e placa que aguenta o padrão **não** ganha linha de MTU nenhuma:

```
# NIC queues, ens18: 8, enough for 7 worker(s)
```

E quando a caixa não consegue ler as filas da placa, ela **diz isso**, porque é exatamente esse o estado em que o defeito morava:

```
# NIC queues, ens18: could not be read, so this asks for 7, one per worker. A card with fewer will refuse the device.
```

Nesse caso ela volta a pedir uma fila por worker, que é o número que falhava. Uma caixa prestes a recusar a própria placa não pode ter a mesma cara de uma caixa saudável.

Uma caixa **sem** placa de dataplane não fala em fila nenhuma: contagem de fila não é fato sobre uma máquina que não tem placa para contar.

## IOMMU: com ou sem, e o que muda

Havendo IOMMU no firmware, a placa vai para o `vfio-pci`, que passa o dispositivo pela IOMMU - um defeito no dataplane não consegue escrever em memória que o dispositivo nunca recebeu. Sem IOMMU o `vfio-pci` carrega e recusa todo dispositivo, então a caixa usa `uio_pci_generic`, que **não tem essa proteção**, e **diz na tela** que está usando:

```
bind: no IOMMU groups on this box, so vfio-pci cannot take a device.
      Using uio_pci_generic, which has no IOMMU protection.
```

A escolha é feita onde dá para explicá-la, e não descoberta como um bind que falha sem motivo aparente. Ligue a IOMMU no firmware (`VT-d` na Intel, `AMD-Vi` na AMD) se a máquina tiver: é uma opção de firmware, não um ajuste do produto.

### Quando a entrega da placa falha, o conselho é o da **sua** caixa

Se a placa não aceitar o driver, a caixa diz em que estado ela ficou — e o estado pior de todos, placa sem driver nenhum, é dito por extenso:

```
bind: 0000:5e:00.0 did NOT bind to vfio-pci; it is on no driver at all now.
      The dataplane will start without it. 'dmesg' says why vfio-pci
      refused the device.
```

Depois disso vem o conselho, e ele é **diferente nas duas caixas**. Numa caixa com IOMMU:

```
      This box has IOMMU groups, so the kernel command line is not the
      cause: vfio-pci is being offered a device it will not take.
      Read this, on the box:
        /sys/bus/pci/devices/0000:5e:00.0/iommu_group
      A device with no group of its own cannot go, and a group whose
      other members are still held by host drivers cannot either.
```

E numa caixa sem IOMMU:

```
      This box has no IOMMU groups, which is why vfio-pci was not
      tried. uio_pci_generic needs a legacy INTx interrupt, and a
      device that has only MSI-X is refused. Enabling an IOMMU in the
      firmware and on the kernel command line is what makes vfio-pci
      available instead.
```

Por um tempo as duas caixas recebiam a mesma frase — *"numa caixa com IOMMU a causa costuma ser* `*intel_iommu=on*` *faltando na linha de comando"* —, e ela era falsa nas duas, em direções opostas. Na caixa com IOMMU, o parâmetro **já estava** lá, e o operador era mandado conferir uma linha de comando que estava certa, na única mensagem que ele recebe quando a appliance sobe sem encaminhar. Na caixa sem IOMMU a frase falava de outra máquina, e o parâmetro que ela cita não teria ajudado: aquele driver não usa IOMMU nenhuma.

# O kernel, e o hardware que ele reconhece

O kernel da caixa é o do Prisma Routing: o mesmo código e a mesma configuração do kernel do Debian, recompilados, e `uname -r` termina em `-prisma`. Ficam **fora** as classes de módulo que um roteador não usa: som, Wi-Fi, Bluetooth, NFC, captura de vídeo, joystick, tela sensível ao toque, rádio amador, CAN, 802.15.4, modem celular, FireWire, sensores industriais, placas de vídeo AMD e NVIDIA dedicadas e os drivers experimentais (*staging*).

**Toda placa de rede, todo controlador de disco e todo driver de virtualização,** `**vfio**` **e** `**uio**` **continuam.** Uma máquina cujo hardware de rede e de disco o Debian 12 reconhece é reconhecida aqui. Um kernel novo, inclusive uma correção de segurança dele, chega **na mídia de instalação** de uma versão nova, não pelo `apt` e não pelo pacote de atualização: o pacote troca os arquivos do produto e não traz kernel. Numa caixa já em serviço, isso significa reinstalar a partir da mídia, com o que [Levar a caixa para uma versão nova](/s/wiki/doc/levar-a-caixa-para-uma-versao-nova-C09kO2kU8X) diz sobre o que a reinstalação apaga.

# Se a caixa vai receber a tabela BGP completa

6 GB é o piso de uma caixa que **não** carrega tabela cheia. Recebendo uma tabela IPv4 completa de um trânsito, o roteamento sozinho passa a ocupar cerca de **1,4 GB** de RAM. Medido com uma sessão e um milhão de prefixos: o `bgpd` ficou com \~840 MB e o `zebra` com \~550 MB, contra 37 MB somados numa caixa que só recebe rota padrão. Cada trânsito adicional com tabela cheia soma de novo, e IPv6 soma por cima.

| Cenário de peering | RAM só para o roteamento | Piso recomendado da máquina |
|--------------------|-------------------------:|----------------------------:|
| Só rota padrão     | \~40 MB                  | 6 GB                        |
| Uma tabela IPv4 cheia | \~1,4 GB                 | 16 GB                       |
| Dois trânsitos com tabela cheia, mais IPv6 | mais que isso            | 32 GB                       |

O dataplane reserva a maior parte da RAM em hugepages e, sem ajuste, deixa `máx(2, RAM/4)` GB para todo o resto; `system memory control-plane <GB>` muda esse número (seção *Memória por papel*). Numa caixa de 6 GB isso são 2 GB, e uma tabela cheia sozinha já ocupa quase tudo — por isso 8 GB já é apertado e 16 GB é o número sensato.


:::info
**Não existe limite de memória configurado para o roteamento, e isso é deliberado.** Um limite apertado não deixa o BGP mais lento: ele mata o `bgpd` no meio da convergência, o serviço reinicia cinco segundos depois, converge de novo e morre de novo. Quem protege a caixa é o outro lado — o dataplane está fora da escolha do OOM killer, de modo que, faltando memória, o que o kernel escolhe é o próprio processo de roteamento que cresceu, e a caixa continua encaminhando com o que já aprendeu. O self-test de boot diz na tela se essa proteção está valendo.

:::

# Quantos núcleos, e o que sobra para o plano de controle

O equipamento entrega ao dataplane **todos os núcleos físicos do processador que atende a placa de rede, menos o primeiro**. Esses núcleos ficam isolados: o escalonador do Linux não coloca mais nada neles, e cada um roda uma thread de encaminhamento que nunca dorme. O primeiro núcleo fica com o sistema operacional, com o roteamento, com a VTY, com o SSH - e com a thread principal do dataplane.

Numa caixa de 4 ou 8 núcleos isso é equilibrado. Num processador de 32 núcleos, não: são 31 núcleos encaminhando e **um** carregando tudo o que não encaminha. E a thread principal do dataplane, que está nesse mesmo núcleo, precisa parar o encaminhamento por um instante uma vez por segundo para fazer a manutenção das sessões. Quanto mais trabalho de controle disputa esse núcleo, mais tempo ela espera para ser escalonada - e o encaminhamento espera junto.

| Núcleos do processador | Encaminhando | Para todo o resto |
|-----------------------:|-------------:|------------------:|
| 4                      | 3            | 1                 |
| 8                      | 7            | 1                 |
| 32                     | 31           | 1                 |

## Devolvendo núcleos

`workers <n>`, em `system` > `cpu` na VTY (a partir da 0.146.1; antes, só editando `PRISMA_MAX_WORKERS` em `/etc/prisma-routing/tune.env`), limita quantos núcleos físicos viram threads de encaminhamento. Ele **vem vazio**, e vazio significa sem limite - exatamente o comportamento acima. Preenchendo, os núcleos liberados deixam de ser isolados e voltam para o escalonador, onde o roteamento e a VTY podem usá-los de fato.

Cada núcleo devolvido é aquela fatia da capacidade de encaminhamento. Não existe um valor padrão porque a troca depende de quanto trabalho de controle a caixa carrega: uma caixa que só recebe rota padrão quase não tem plano de controle; uma que recebe duas tabelas cheias, refaz política BGP e ainda serve consultas tem bastante.

**Diminuir o número de workers custa um boot; aumentar custa dois, e o segundo é diferente.** Medido:

| O que você faz | Próximo boot | O boot seguinte |
|----------------|--------------|-----------------|
| Baixa o limite (menos workers) | já sobe com menos workers | os núcleos liberados saem do isolamento e voltam para o escalonador |
| Sobe o limite, ou apaga o valor | sobe **em modo conservador**: sem workers, sem hugepages, sem placa de dados | sobe com o plano completo |

O modo conservador do segundo caso não é um defeito: o equipamento não pode fixar um worker num núcleo que o kernel ainda não isolou, e isolar é coisa de linha de comando do kernel, que só vale no boot seguinte. Mas é uma caixa sem encaminhamento por um boot. **Programe essa mudança para uma janela**, e confira o self-test do primeiro boot antes de dar por encerrado.


:::info
**O self-test de boot diz de que lado a sua caixa está.** Ele imprime quantos núcleos sobraram para o plano de controle e avisa quando sobrou **um** havendo mais de um isolado - ou seja, quando havia núcleo para devolver e não foi devolvido. Uma caixa de dois núcleos não recebe esse aviso: ela não tem o que devolver.

:::


:::warning
**O plano de controle fica preso aos núcleos que não são do dataplane.** A cada boot, a caixa prende o sistema, o roteamento, o SSH, a VTY e os containers de controle a todos os núcleos que o kernel não isolou, e o dataplane roda num grupo à parte, com os workers nos núcleos dele. Antes isso era só herança: medido numa caixa de 4 núcleos com 1 a 3 isolados, o roteamento e o SSH podiam rodar em qualquer um dos quatro, e um shell que pedisse o núcleo 3 conseguia. Agora o mesmo pedido é recusado, e `show system cpu` mostra a quais núcleos o plano de controle está preso.

Quem decide quanto de CPU o plano de controle tem é a divisão dos núcleos por papel, logo abaixo, e isso é uma decisão de dimensionamento, não um limite de processo.

:::

## Núcleos por papel: plano de controle, thread principal e workers

Os núcleos físicos do processador que atende a placa são distribuídos nesta ordem: primeiro os do **plano de controle**, depois - se pedido - um só para a **thread principal do dataplane**, e o resto vira **worker**. Isso se configura na VTY, em `system` > `cpu`:

```
prisma# [edit] system
prisma# [edit system] cpu
prisma# [edit system / cpu] control-plane 2
prisma# [edit system / cpu] main-thread dedicated
prisma# [edit system / cpu] commit
```

| Comando | O que faz | Padrão |
|---------|-----------|--------|
| `control-plane <n>` | quantos núcleos físicos ficam com o sistema, o roteamento, o SSH, a VTY e os containers | `1`    |
| `main-thread shared` | a thread principal do dataplane divide o primeiro núcleo do plano de controle | sim    |
| `main-thread dedicated` | a thread principal ganha um núcleo só dela, isolado como o de um worker; custa um worker | não    |
| `workers <n>` | no máximo `n` workers; os núcleos que sobram ficam com o Linux, sem ser isolados | um worker por núcleo restante |
| `cgnat-workers <n>` | quantos workers traduzem; os outros só recebem da placa e entregam ao worker dono do assinante | todos fazem as duas coisas |
| `no control-plane`, `no main-thread`, `no workers`, `no cgnat-workers` | volta ao padrão |        |

**Por que dar um núcleo à thread principal:** uma vez por segundo ela para os workers para expirar sessões, e o encaminhamento espera enquanto ela não roda. Dividindo núcleo com o roteamento, ela espera o roteamento - numa tabela BGP cheia convergindo, isso aparece. Num núcleo só dela, não espera ninguém.

**Workers de RX e workers de CGNAT.** Por padrão, todo worker recebe pacotes da placa e também traduz. Com `cgnat-workers <n>`, os últimos `n` workers ficam só com a tradução, e os outros só recebem e entregam cada pacote ao worker dono daquele assinante. Isso custa uma entrega entre workers por pacote; se compensa depende do tráfego e da máquina, e é exatamente o que um benchmark mede comparando os dois jeitos. Medido num laboratório com três workers e o 0 só recebendo: todo pacote entrou pelo worker 0, a tradução aconteceu só nos workers 1 e 2, e o pool dinâmico ficou dividido entre esses dois.

Pedir tantos workers de CGNAT quanto a caixa tem não separa nada: pelo menos um precisa receber, e a caixa diz isso no relatório de boot.

**O worker vem primeiro.** Numa caixa pequena demais para o que foi pedido, a caixa mantém pelo menos um worker, abre mão primeiro do núcleo dedicado e depois de núcleos do plano de controle, e diz isso no relatório de boot com uma linha `WARNING`.

A mudança vai para `tune.env` (`PRISMA_CONTROL_PLANE_CORES`, `PRISMA_MAIN_THREAD` e `PRISMA_CGNAT_WORKERS`) e **vale no boot seguinte**, como tudo o que é isolamento de núcleo - e com a mesma regra da seção anterior: tirar núcleos do plano de controle isola núcleos que não estavam isolados, e isso passa por um boot em modo conservador antes do plano completo. `show system cpu` mostra lado a lado o que está configurado e o que este boot está rodando:

```
prisma> show system cpu
configured, in effect from the next reboot:
  control-plane cores   2
  VPP main thread       dedicated
  VPP workers at most   every core left
  CGNAT workers         all, each also receiving
this boot:
  VPP main thread       core 0
  VPP workers           1-3
  translating workers   all of them
  isolated from Linux   1-3
  control plane held to 0
```

`show system tuning advice` diz se o layout que está rodando é o que a caixa deveria ter — workers desiguais, filas que não batem, thread principal sobrando ou faltando — com a linha da VTY que muda isso, e traz uma tabela de 4 a 32 vCPUs com a divisão recomendada e os workers que o ajuste da caixa tira de cada uma. Numa VM, traz também a fixação de CPUs para o hipervisor: veja [Rodar o Prisma Routing numa máquina virtual](/s/wiki/doc/rodar-o-prisma-routing-numa-maquina-virtual-4l9Vu3QoL5).

## Memória por papel: plano de controle, CGNAT e buffers

A RAM da caixa se divide em dois lados. As **hugepages** são do dataplane: a memória do CGNAT (sessões e assinantes), os buffers de pacote e uma folga. O **resto** é do plano de controle: o sistema, o roteamento, o SSH, a VTY e os containers. Uma hugepage nunca é devolvida para ninguém, então o que o plano de controle tem garantido é exatamente a RAM que não virou hugepage.

Os três números se configuram na VTY:

```
prisma# [edit] system
prisma# [edit system] memory
prisma# [edit system / memory] control-plane 12
prisma# [edit system / memory] buffers 65536
prisma# [edit system / memory] commit
```

| Comando | O que faz | Sem ele |
|---------|-----------|---------|
| `system memory control-plane <GB>` | a RAM que o plano de controle mantém, fora das hugepages | um quarto da RAM, no mínimo 2 GB |
| `system memory buffers <n>` | quantos buffers de pacote por nó NUMA | 16384 por worker |
| `nat cgnat memory <GB>` | a memória das sessões e dos assinantes do CGNAT | calculada pela meta de assinantes |

Uma tabela BGP completa ocupa uns 840 MB no bgpd e uns 550 MB no zebra, medidos. Uma caixa que recebe duas tabelas completas e ainda serve consultas quer mais do que o padrão numa máquina pequena. Se a soma pedida passar do que a RAM dá, o boot limita as hugepages e diz isso no relatório.

Vale no boot seguinte, porque as hugepages são reservadas na linha de comando do kernel. `show system memory` mostra lado a lado o configurado e o que este boot tem:

```
prisma> show system memory
configured, in effect from the next reboot:
  control plane keeps   12 GB
  CGNAT memory          8 GB
  packet buffers        65536 per NUMA node
this boot:
  RAM                   16.0 GB
  hugepages             4.0 GB
  VPP main heap         2G
  packet buffers        49152 per NUMA node
  statistics segment    VPP default, 32M
  control plane uses    3.0 GB
```

## O que o arquivo de ajuste aceita

`/etc/prisma-routing/tune.env` é lido a cada boot, e cada valor dele tem uma forma. Um valor fora dessa forma é **recusado em voz alta**, com o nome da variável e o valor entre aspas, e a caixa usa o padrão de fábrica no lugar.

| Variável | Aceita | De fábrica | Se for recusado |
|----------|--------|------------|-----------------|
| `PRISMA_DATAPLANE_IF` | nome de interface, ou vazio | vazio      | —               |
| `PRISMA_SUBSCRIBERS` | só dígitos | `200000`   | `200000`        |
| `PRISMA_LOW_LATENCY` | `1` ou `0`, mais nada | `1`        | `1`             |
| `PRISMA_MAX_WORKERS` | só dígitos, ou vazio para sem limite | vazio      | sem limite      |
| `PRISMA_CGNAT_MEMORY_GB` | GB inteiros, ou vazio para a estimativa por `PRISMA_SUBSCRIBERS` | vazio      | a estimativa    |
| `PRISMA_CONTROL_PLANE_CORES` | só dígitos, 1 ou mais, ou vazio | vazio (um núcleo) | um núcleo       |
| `PRISMA_MAIN_THREAD` | `shared` ou `dedicated`, ou vazio | vazio (`shared`) | `shared`        |
| `PRISMA_CGNAT_WORKERS` | só dígitos, 1 ou mais, ou vazio | vazio (todos) | todos fazem as duas coisas |
| `PRISMA_CONTROL_PLANE_MEMORY_GB` | GB inteiros, 1 ou mais, ou vazio | vazio (um quarto da RAM) | um quarto da RAM |
| `PRISMA_BUFFERS_PER_NUMA` | só dígitos, ou vazio | vazio (16384 por worker) | 16384 por worker |

`PRISMA_LOW_LATENCY=1` desliga os estados profundos de economia de energia do processador, desliga as páginas grandes transparentes e mantém o governador de frequência em `performance`. Custa energia e compra rajada previsível, e é por isso que vem `1`: isto é um roteador. `0` deixa os três em paz, para uma caixa que precisa economizar mais do que precisa encaminhar.


:::warning
**Escreva exatamente** `**1**` **ou** `**0**`**.** Um valor como `true` ou `sim` é recusado: a caixa diz qual variável recusou, com que valor, e quais são os dois valores que ela aceita e o que cada um faz. Ela segue com `1` e o boot continua.

Isso é deliberado: um erro de digitação **não** pode custar o ajuste da caixa. Uma configuração ignorada você conserta editando uma linha; um boot que falha no meio do ajuste deixa a linha de comando do kernel dizendo uma coisa e o arquivo do dataplane dizendo outra, e aí a caixa encaminha pela metade sem contador nenhum para mostrar.

:::

**Mudou** `**tune.env**`**? Só vale no boot seguinte.** Isolamento de núcleos, hugepages e estados de energia são linha de comando do kernel. Quando o arquivo já diz uma coisa e a caixa ainda está rodando a linha antiga, o self-test de boot diz isso com todas as letras, em vez de dar a caixa por ajustada.

**E ele lê o que o kernel fez, não o que foi pedido.** Um parâmetro pode se perder em três lugares — o `update-grub` não rodou, a caixa não reiniciou, ou o kernel viu e recusou —, e a caixa que pediu páginas de 1 GB num processador que não tem página de 1 GB ficou a vida inteira dizendo que estava ajustada. São dezesseis regras lidas de volta da máquina em funcionamento, cada uma em um de quatro estados: **valendo**, **pedido e não valendo**, **não dá para ler nesta caixa** e **não foi pedido aqui**. Os quatro estão descritos, com as linhas que a caixa imprime, em *Operação e diagnóstico*.

## O self-test confere os núcleos que o dataplane recebeu

O equipamento calcula o plano de núcleos a cada boot e o grava em um arquivo que o dataplane lê ao iniciar. Duas coisas podem sair do lugar sem nenhum sintoma:

* o dataplane pode estar rodando desde **antes** da última mudança de plano, e nesse caso ele continua nos núcleos com que subiu enquanto a tela reporta o plano novo;
* a linha de comando do kernel pode não ter acompanhado, e nesse caso uma thread de encaminhamento divide o núcleo com todo o resto.

Nos dois casos a caixa dá boot, encaminha, responde a tudo e entrega uma fração da capacidade. **Não há contador que denuncie isso**: uma thread que nunca dorme marca 100% de uso tendo ou não o núcleo só para ela.

Por isso o self-test não confere o arquivo: ele confere onde o kernel realmente colocou as threads do dataplane e quais núcleos o kernel realmente está isolando, e reclama quando os três não concordam. Reiniciar o dataplane resolve o primeiro caso; o segundo pede um `update-grub` e um reboot.

# Como a caixa calcula a memória do dataplane

A cada boot, o `prisma-routing-tune` decide quantas páginas de 1 GB (hugepages) o kernel reserva para o VPP. É memória que sai do Linux e fica dedicada ao dataplane. Dentro dela fica o **heap principal**, que é a memória do CGNAT: todas as sessões, as duas tabelas de sessão e os registros de assinante moram ali.

## Dando ao CGNAT uma memória sua

```
prisma> configure
prisma# [edit] nat
prisma# [edit nat] cgnat
prisma# [edit nat / cgnat] memory 8
prisma# [edit nat / cgnat] exit
prisma# [edit nat] exit
prisma# [edit] commit
```

O `commit` diz quantas sessões esse número comporta e que ele **vale a partir do próximo reboot**: a memória é reservada pela linha de comando do kernel, e até lá o dataplane tem a que tinha no boot. Dê `save` e reinicie na sua janela. `no memory` volta para a estimativa.

**Como escolher o número.** O que decide é o número de **sessões** abertas ao mesmo tempo, não o de assinantes. Medido: cerca de 300 bytes por sessão com milhões delas, e menos de 1 KB por assinante. 200 mil assinantes com 100 sessões cada são 20 milhões de sessões, uns **6 GB**. Com 300 sessões cada, uns 17 GB.

`show cgnat memory` mostra o tamanho do heap, quanto está em uso, quantas sessões ainda cabem, quantas foram recusadas por falta de memória e se o número dado já está em vigor.

## A conta

**1. A memória do CGNAT.** O número que você deu. Sem ele, uma estimativa: `PRISMA_SUBSCRIBERS` × 13,5 KB, arredondado para GB, no mínimo 1. Com o padrão de 200000 dá **3 GB**, umas 45 sessões por assinante.

**2. Buffers de pacote.** 1 GB para cada 4 workers, arredondando para cima, no mínimo 1. Numa máquina de 8 núcleos, 1 fica para o sistema e 7 viram workers: **2 GB**.

**3. Folga.** **1 GB**, fixo, para o resto da memória física do VPP.

**4. O desejado** é a soma. Com 8 GB dados: 8 + 2 + 1 = **11 GB**.

**5. O teto pela RAM da máquina.** O Linux, o Docker e o FRR ficam fora das hugepages, com `máx(2, RAM ÷ 4)` GB. A RAM entra em GB inteiros, arredondada para baixo: uma VM de "16 GB" mostra uns 15,6, que viram 15. Então ficam 3 GB para o sistema, o teto é 12, e a caixa reserva **mín(11, 12) = 11 GB**.

**6. O heap** é o reservado, menos os buffers, menos a folga: 11 − 2 − 1 = **8 GB**, exatamente o que foi dado. **Não há mais teto de 2 GB**: esse teto segurava qualquer caixa em uns 10 milhões de sessões, fosse qual fosse a RAM.

| RAM da VM | Memória do CGNAT pedida | Reservado | Heap do CGNAT | Sessões, cerca de |
|-----------|-------------------------|-----------|---------------|-------------------|
| 8 GB      | estimativa (3 GB)       | 5 GB      | 2 GB          | 6 milhões         |
| 16 GB     | 8 GB                    | 11 GB     | 8 GB          | 27 milhões        |
| 32 GB     | 16 GB                   | 19 GB     | 16 GB         | 56 milhões        |

Quando o teto corta o desejado, o log do ajuste avisa quantos GB faltaram. Abaixo de 2 GB a caixa não reserva nada e sobe no perfil conservador, que funciona mas não tem desempenho.

**Quando o heap enche, a caixa recusa a sessão nova e conta**, e o dataplane continua de pé: as sessões que já existem seguem traduzindo.

# Memória por estrutura

| Estrutura | Dimensionamento | Ordem de grandeza |
|-----------|-----------------|-------------------|
| Bitmap de portas | `block-size` bits por assinante ativo | 64 bytes com blocos de 512 → \~13 MB para 200 mil assinantes |
| Sessões   | um pool e duas tabelas por worker | \~300 bytes por sessão, medido com 2 milhões de sessões |
| Posse de bloco (modo dinâmico) | 4 bytes por bloco, por fatia de worker |                   |

A tabela de sessões é ajustada com:

```
nat
 cgnat
  session-table buckets <n> memory <MB>
  exit
 exit
```

O padrão é 16 mil buckets. **O número de** `**memory**` **não dimensiona nada:** as tabelas, como todas as sessões, ficam no heap principal do dataplane, e é esse heap que enche.

**Quem decide a memória é o número de sessões, não o de assinantes.** Medido: um assinante custa menos de 1 KB, e uma sessão cerca de 300 bytes quando são milhões. 200 mil assinantes com 100 sessões cada são 20 milhões de sessões, uns 6 GB.

Quando o heap enche, a caixa **recusa a sessão nova e conta**, e o dataplane continua de pé: as sessões que já existem seguem traduzindo. O contador aparece como `no memory for a new session`. Antes disso o dataplane caía inteiro, levando todos os assinantes junto.

# O segmento de contadores, que derruba o dataplane quando enche

O dataplane publica todos os contadores dele num segmento de memória de tamanho **fixo**. Cada interface que ele carrega ocupa uma parte desse segmento — cerca de 2,4 kB, mais 200 bytes por thread do dataplane —, e o segmento não cresce.

Quando ele enche, o dataplane **não recusa a interface seguinte: ele aborta**. A caixa perde o encaminhamento inteiro, de uma vez, e não escreve nenhuma explicação antes de cair.

O tamanho de fábrica é o do próprio dataplane: **32 MB**. Quanto cabe nele depende do número de threads, porque cada thread soma contadores a cada interface. Medido com subinterfaces VLAN: uma caixa de uma thread abortou depois de **7.800**, uma de três threads depois de **5.100**, e uma de oito threads — o que uma máquina de oito núcleos dá — depois de **3.300**. Com o segmento em 1 GB, uma caixa chegou a 40.000 interfaces, que foi onde a medição parou e não onde ela encontrou um limite.

**Uma caixa de CGNAT comum não chega perto disso.** Assinante não é interface: 200 mil assinantes atrás de um pool dividem um punhado de portas. Quem chega perto é quem cria **uma interface por cliente** - uma subinterface QinQ por assinante, por exemplo, ou uma sessão PPPoE, que também é uma interface.


:::info
**A caixa dimensiona esse segmento sozinha, a partir do que a configuração dela faz o dataplane carregar.** No boot ela conta as interfaces que o arquivo de configuração nomeia, o lado Linux de cada par que o dataplane mantém com o Linux, e o máximo de sessões PPPoE que a configuração permite, e reserva para cada uma **três vezes o que os contadores dela pesam** nas threads que a caixa vai rodar: uns 7,9 kB numa thread, 12,1 kB em oito. Três vezes porque crescer a tabela de contadores exige a cópia velha e a nova ao mesmo tempo, e o aborto medido chegou a 2,5 vezes o peso.

Se essas interfaces couberem nos 32 MB de fábrica, ela **não escreve nada** - reservar menos do que o dataplane já dá de graça só tiraria contadores da sua caixa. E ela nunca dá ao segmento mais de um quarto da memória que o plano de controle mantém: uma configuração que pediria mais recebe esse teto, e o ajuste de boot diz quantas interfaces ele comporta.

:::

Fora as sessões PPPoE, cujo máximo a configuração declara, o que nenhum arquivo de configuração tem é a interface criada **enquanto a caixa roda**. Se a sua topologia cria interface em tempo de execução, o número é seu e está na seção seguinte.

**O tamanho novo só vale no próximo start do dataplane.** O segmento é lido quando ele sobe e nunca cresce depois: se você criar milhares de subinterfaces numa caixa que já está ligada, elas gastam o segmento com que aquela caixa subiu. O cálculo entra no arquivo de configuração do dataplane no boot seguinte, e vale a partir dali.

O self-test de boot imprime quanto do segmento está gasto, sobre quantas interfaces, e **quem escolheu o tamanho**:

```
  [ ok ] statistics segment: 4.20M of 31.99M used, 3 interface(s)
        size: VPP's own default, which this product does not set
```

São três respostas possíveis nessa última linha: o padrão do dataplane, `chosen by prisma-routing-tune from this box's configuration` quando foi a caixa que calculou, e `set in /etc/prisma-routing/vpp-base.conf` quando foi você.

Passando da metade isso vira um aviso. Passando de três quartos vira uma **falha**, com o que acontece quando enche e com o que fazer a respeito. Três quartos, e não noventa por cento, porque o último pedaço não é aproveitável: a tabela de contadores é um bloco só de memória, e crescer significa copiá-la inteira, o que exige a cópia velha e a nova ao mesmo tempo.

## Como escolher o tamanho você mesmo

Em `/etc/prisma-routing/vpp-base.conf`, que é o arquivo base que você edita:

```
statseg {
  size 256M
}
```

Reinicie a caixa depois. O ajuste automático de boot **nunca sobrescreve** esse trecho: ele acrescenta os blocos que calcula **depois** do que está nesse arquivo, e quando existe um `statseg` seu ele não escreve nenhum - o número passa a ser inteiramente seu, inclusive se for menor do que a conta dele pediria.


:::warning
**Esse segmento é RAM que sai do resto.** Não peça 1 GB numa caixa de 6 GB por precaução. Peça o que o número de interfaces da sua topologia pede, e confira no self-test do boot seguinte quanto disso está realmente sendo usado.

:::

# Quantos endereços públicos

No **modo determinístico** todo assinante ocupa seu bloco esteja online ou não. A conta é direta:

```
endereços públicos = assinantes × block-size ÷ 64512
```

(64512 é a faixa útil de portas, de 1024 a 65535.)

| Assinantes | Bloco | Endereços | Prefixo |
|-----------:|------:|----------:|---------|
| 50\.000    | 512   | \~397     | `/23`   |
| 100\.000   | 512   | \~794     | `/22`   |
| 200\.000   | 512   | \~1.588   | `/21`   |
| 200\.000   | 256   | \~794     | `/22`   |
| 200\.000   | 1024  | \~3.175   | `/20`   |

O **modo dinâmico** aloca só para quem está usando, então cabe sobreassinatura e a necessidade cai bastante — em troca de voltar a gerar log.

## Em modo dinâmico, dimensione pelo número que o equipamento garante

Numa caixa com mais de um worker, os blocos de um pool dinâmico são **repartidos entre os workers**: cada bloco pertence a um worker só, e cada assinante sai por um worker só. Os dois repartes são calculados de formas diferentes, então nem sempre casam - um assinante pode receber "sem bloco livre" enquanto sobra bloco na fatia de outro worker.

Por isso `show cgnat pool` mostra **dois** números num pool dinâmico:

```
capacity     768 subscribers (usable 768)
             384 whatever the addresses are: blocks are sharded 2 ways and a subscriber draws only from its own worker's slice
```

* o primeiro é o **teto**: o que o pool atende se os assinantes se distribuírem perfeitamente entre os workers;
* o segundo é o **piso garantido**: o que o pool atende independentemente de quais endereços internos estejam online.

**Dimensione pelo segundo.** O primeiro depende de sorte no plano de endereçamento, e sorte não é um número de compra.

A segunda linha só aparece quando há algo a perder. Um pool com bloco sobrando para todo endereço interno não perde nada para o reparte, e nesse caso o equipamento imprime só o teto.

A distância entre os dois encolhe rápido conforme o pool cresce em relação ao número de workers. Medido, com os assinantes espalhados aleatoriamente pela faixa interna:

| Blocos no pool | Workers | Garantido | Atendidos na prática | Perda |
|---------------:|--------:|----------:|---------------------:|------:|
| 12             | 2       | 6         | \~10,6               | 11%   |
| 48             | 4       | 12        | \~43,3               | 10%   |
| 192            | 4       | 48        | \~183                | 4,5%  |
| 768            | 8       | 96        | \~742                | 3,4%  |
| 7\.936         | 8       | 992       | \~7.848              | 1,1%  |

E a perda **desaparece** quando os endereços internos são entregues em sequência a partir do início da faixa, que é o caso de um pool DHCP preenchendo de baixo para cima. Um plano de endereçamento contíguo não perde nada; um plano esburacado perde o da coluna da direita.


:::warning
A conclusão prática: em modo dinâmico, **pool pequeno com muitos workers é o pior caso**. Um pool de 12 blocos com 8 workers garante um único assinante. Se o pool é pequeno por escolha - e no modo dinâmico costuma ser, já que ele existe justamente para economizar IPv4 - prefira menos workers ou um pool maior a descobrir isso com o cliente na linha.

:::


:::info
Bloco menor economiza endereço e aperta o assinante. 512 portas atendem uso residencial comum com folga; abaixo de 256, aplicações que abrem muitas conexões simultâneas (um navegador com muitas abas, um cliente de torrent) começam a esbarrar no limite. Acompanhe com `show cgnat top` antes de reduzir.

:::

# Volume de log

Premissas explícitas, para ordem de grandeza e não para precisão: 200 mil assinantes, modo dinâmico, um bloco renegociado \~5 vezes por dia por assinante ativo, \~40 bytes por registro com o enquadramento IPFIX.

| Cenário | Registros/dia | Volume bruto/dia | Um ano |
|---------|--------------:|-----------------:|-------:|
| BPA (alocação + liberação) | \~2 milhões   | \~80 MB          | dezenas de GB |
| NAT por fluxo (referência) | \~40 milhões  | \~1,6 GB         | dezenas de TB |
| Determinístico | 0             | 0                | 0      |

## Os registros têm sistema de arquivos próprio

O instalador cria três partições — ESP, raiz e `/var/log/prisma-routing` — em vez de deixar tudo junto. O que fica no terceiro volume é a **trilha de configuração** e o **spill** do exportador: esta caixa não guarda registros de conexão, ela os exporta, e quem guarda pelo ano que o Marco Civil exige é o coletor.


:::warning
Compartilhando o sistema de arquivos com o resto, um log que enche impede a máquina de gravar a própria configuração e a própria trilha, porque ela está ocupada registrando quem se conectou a quê. Com volume próprio, um log cheio é só um log cheio.

:::

O tamanho padrão é aritmético e a conta aparece na tela: \~80 MB por dia para 200 mil assinantes em modo dinâmico, ou cerca de **29 GB por ano**. Modo determinístico não registra alocação nenhuma e quase não precisa de espaço — que é por que isso é uma pergunta e não uma constante. A raiz nunca fica com menos de 8 GB.

Em instalação não assistida, `prisma-routing.log-gb=` responde essa pergunta na linha de comando do kernel.

O volume é montado com `nofail`: uma máquina que não dá boot porque o volume de registros sumiu é uma máquina em que ninguém consegue entrar para descobrir o porquê. Por isso o self-test de boot **reporta onde os registros estão** — sem isso, uma caixa cujo volume não montou sobe caladinha gravando na raiz.

Essa tabela é o argumento comercial do CGNAT por bloco em uma linha: guardar um ano de registros passa de dezenas de terabytes para dezenas de gigabytes — ou para nada. É um argumento sobre o disco do **coletor**, não sobre o desta caixa.

# Como conferir

Depois de instalar, no console:

* o self-test reporta RAM, conjunto de instruções, VPP, contadores de reinício, estado do tuning, FRR e pools configurados;
* ele também confere **os núcleos**: se o dataplane está nos núcleos que o plano dele nomeia e se o kernel está isolando esses núcleos, e diz quantos sobraram para o plano de controle;
* ele **distingue aviso de falha** e sempre sai com código zero, porque um diagnóstico não pode derrubar o boot;
* o contador de reinícios importa mais do que parece: um serviço em laço pode estar "ativo" no instante em que você olha, e só `NRestarts` conta a história.


:::success
Self-test dizendo pronto, sem aviso de instrução ausente e sem contador de reinício subindo: a máquina está dimensionada para o que você pediu dela.

:::

# Ver também

* [Primeiros passos](/s/wiki/doc/primeiros-passos-TAWDdSrYG7) — a configuração depois que o hardware está certo
* [Configuração de pool](/s/wiki/doc/configuracao-de-pool-btyS572Ih6) — onde `block-size` e o modo são escolhidos
* [Ampliar capacidade sem derrubar assinantes](/s/wiki/doc/ampliar-capacidade-sem-derrubar-assinantes-rWQkaj9r1u) — quando a conta acima mudar
