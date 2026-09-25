# Dimensionamento e requisitos

Quanto de máquina e quantos endereços públicos o seu caso exige. Leia antes de comprar hardware ou de fechar o bloco de IPv4.

# Requisitos de máquina

| Recurso | Mínimo | Por quê |
|---------|--------|---------|
| RAM     | **4 GB** | rodando pela ISO, a raiz gravável fica inteira em RAM sobre o squashfs; com 2 GB o OOM killer mata o VPP e leva o resto do boot junto |
| CPU     | **SSE4.2** | o binário do VPP assume SSE4.2 e usa AVX quando existe |
| Placas de rede | **1**  | uma basta, e a caixa entrega essa mesma placa ao dataplane levando o endereço de gerência junto; duas é o formato de uma appliance no rack |


:::warning
**CPU sem SSE4.2 não é um problema lento, é um problema mudo.** O VPP morre com `SIGILL` em milissegundos e, com `Restart=always`, o sintoma vira um laço de reinício que não diz nada sobre a causa. Por isso o self-test de boot confere o conjunto de instruções **antes** de olhar para o VPP.

:::

Medido durante a validação: com 2 GB a ISO reporta `Out of memory: Killed process (vpp)` com o VPP segurando apenas **19 MB residentes** — ele não era o glutão, era a vítima escolhida. Instalado em disco o consumo cai, porque não há a sobreposição em RAM, mas 4 GB continua sendo o piso sensato para um dataplane.


:::warning
**Até o piso ser medido de novo, use 8 GB numa VM.** Numa VM de 6 GB com a 0.126.0 instalada, a caixa reservou 3 GB de hugepages e o VPP não subiu (`rte_eal_init failed`), com uma placa de dados ou com duas. Com 8 GB ele subiu e encaminhou. O piso de 4 GB da tabela acima está sendo medido de novo.

:::

# Quantas placas de rede

**Uma placa já serve, e é assim que a maioria das pessoas conhece este produto:** uma máquina virtual com uma placa virtual. O dataplane fica com a porta e o endereço de gerência vai junto, para a interface que o dataplane mantém emparelhada com ela do lado do Linux. A caixa deixa de ser alcançada *ao lado* do dataplane e passa a ser alcançada *através* dele, com o mesmo MAC, então nada do outro lado do cabo precisa reaprender.

**Duas placas é o formato de uma appliance no rack**, e a diferença não é desempenho: é o que acontece quando alguém erra. Com duas, a gerência não está no caminho do dataplane, e a caixa **recusa** a entrega da placa que carrega a rota default, dizendo qual é a outra. Com uma só não existe essa recusa a fazer - a placa que ela protegeria é a única que a caixa tem - então a entrega acontece, e o que a torna segura é outra coisa: ela é sempre um `commit confirmed`, com trinta minutos de janela, e uma caixa que ninguém confirmou põe a configuração anterior de volta **e se reinicia sozinha**. É seguro de tentar; continua sendo uma janela em que alguém precisa entrar.

| Placas | O que a caixa faz | O que você paga |
|--------|-------------------|-----------------|
| 1      | entrega a placa ao dataplane e leva o endereço de gerência para o par | a entrega é uma janela de trinta minutos que alguém precisa confirmar |
| 2 ou mais | a placa de gerência é recusada para o dataplane, com o nome da outra na recusa | nada            |

## A placa precisa ser um dispositivo PCI

O dataplane pega a porta pelo endereço PCI dela. Uma interface que **não** tem endereço PCI não pode ser entregue: `lo`, uma bridge, um bond e um veth não têm. Nesse caso a caixa escreve `no-pci` no arquivo do dataplane, sobe, roteia, responde - e não encaminha nada. O autoteste de boot diz isso em vez de deixar você descobrir pelo contador parado.

## A caixa lê a placa antes de escrever a configuração dela

O ajuste de boot pergunta **à placa** quantas filas ela tem e qual é a MTU máxima dela, em vez de deduzir do processador. Antes ele pedia uma fila por worker, e numa placa virtual, que tem **uma** fila, isso fazia o dispositivo ser recusado: a caixa subia dona de uma placa que não conseguia iniciar. O número de filas é corrigido a partir da placa.

A MTU é **dita e não corrigida**, e a diferença importa: o VPP não tem nenhuma opção de MTU na configuração do dataplane, portanto não há nada para escrever ali. Ele pede 9000, a placa recusa, e isso fica registado no log — o dispositivo sobe na mesma. O relatório diz qual é o máximo da sua placa para que essa linha no log tenha explicação.

O relatório de boot diz o que ele encontrou. Numa máquina de oito núcleos com placa virtual:

```
# NIC queues: 1, and this box has 7 worker(s). Capped at 1: only that many workers receive packets, so expect a fraction of the forwarding this CPU could do.
# NIC max MTU: 1500, below VPP's default of 9000. VPP has no MTU setting in its dpdk stanza, so it will ask for 9000, be refused by the card and log it
```

**O limite é dito, não aplicado em silêncio.** Uma fila com sete workers significa um worker recebendo pacotes, e isso é uma fração da capacidade que aquele processador teria — é informação de dimensionamento, e esconder o corte trocaria uma caixa que não sobe por uma caixa lenta sem explicação.

Placa com filas de sobra recebe uma linha curta, e placa com MTU de 9000 ou mais **não** ganha linha de MTU nenhuma — fica exatamente como estava:

```
# NIC queues: 8, enough for 7 worker(s)
```

E quando a caixa não consegue ler as filas da placa, ela **diz isso**, porque é exatamente esse o estado em que o defeito morava:

```
# NIC queues: could not be read, so this asks for 7, one per worker. A card with fewer will refuse the device: 'show hardware' says what the box can see.
```

Nesse caso ela volta a pedir uma fila por worker, que é o número que falhava. Uma caixa prestes a recusar a própria placa não pode ter a mesma cara de uma caixa saudável.

Uma caixa **sem** placa de dataplane não fala em fila nenhuma: contagem de fila não é fato sobre uma máquina que não tem placa para contar.

## IOMMU: com ou sem, e o que muda

Havendo IOMMU no firmware, a placa vai para o `vfio-pci`, que passa o dispositivo pela IOMMU - um defeito no dataplane não consegue escrever em memória que o dispositivo nunca recebeu. Sem IOMMU o `vfio-pci` carrega e recusa todo dispositivo, então a caixa usa `uio_pci_generic`, que **não tem essa proteção**, e **diz na tela** que está usando:

```
bind: no IOMMU groups on this box, so vfio-pci cannot take a device; using uio_pci_generic, which has no IOMMU protection
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

# Se a caixa vai receber a tabela BGP completa

4 GB é o piso de uma caixa que **não** carrega tabela cheia. Recebendo uma tabela IPv4 completa de um trânsito, o roteamento sozinho passa a ocupar cerca de **1,4 GB** de RAM. Medido com uma sessão e um milhão de prefixos: o `bgpd` ficou com \~840 MB e o `zebra` com \~550 MB, contra 37 MB somados numa caixa que só recebe rota padrão. Cada trânsito adicional com tabela cheia soma de novo, e IPv6 soma por cima.

| Cenário de peering | RAM só para o roteamento | Piso recomendado da máquina |
|--------------------|-------------------------:|----------------------------:|
| Só rota padrão     | \~40 MB                  | 4 GB                        |
| Uma tabela IPv4 cheia | \~1,4 GB                 | 16 GB                       |
| Dois trânsitos com tabela cheia, mais IPv6 | mais que isso            | 32 GB                       |

O dataplane reserva a maior parte da RAM em hugepages e deixa `máx(2, RAM/4)` GB para todo o resto. Numa caixa de 4 GB isso são 2 GB, e uma tabela cheia sozinha já ocupa quase tudo — por isso 8 GB já é apertado e 16 GB é o número sensato.


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

`PRISMA_MAX_WORKERS`, em `/etc/prisma-routing/tune.env`, limita quantos núcleos físicos viram threads de encaminhamento. Ele **vem vazio**, e vazio significa sem limite - exatamente o comportamento acima. Preenchendo, os núcleos liberados deixam de ser isolados e voltam para o escalonador, onde o roteamento e a VTY podem usá-los de fato.

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
**Não existe limite de CPU configurado para o plano de controle, e isso também é deliberado: o limite já existe, e é o próprio isolamento.** Medido numa caixa instalada de dois núcleos com o núcleo 1 isolado: o processo `init` roda só no núcleo 0, e o processo de roteamento, que é filho dele, herda essa mesma permissão. O kernel confina tudo o que não é o dataplane aos núcleos que sobraram, sem que ninguém precise configurar nada. Limitar o roteamento por cima disso não libera nada para o encaminhamento, porque o roteamento já não chega nos núcleos de encaminhamento; o que um limite desses faria é atrasar a convergência BGP e a recarga de configuração sem devolver nada em troca.

**Herdado não é o mesmo que imposto.** Na mesma caixa, um processo que pede explicitamente para rodar no núcleo isolado **consegue**. Quem escreve numa sessão de shell que quer um núcleo de encaminhamento pediu aquilo de propósito, e nada que o produto instala faz isso sozinho, mas vale saber que a proteção é por herança e não por cerca.

Quem decide quanto de CPU o plano de controle tem é `PRISMA_MAX_WORKERS`, e isso é uma decisão de dimensionamento, não um limite de processo.

:::

## O que o arquivo de ajuste aceita

`/etc/prisma-routing/tune.env` é lido a cada boot, e cada valor dele tem uma forma. Um valor fora dessa forma é **recusado em voz alta**, com o nome da variável e o valor entre aspas, e a caixa usa o padrão de fábrica no lugar.

| Variável | Aceita | De fábrica | Se for recusado |
|----------|--------|------------|-----------------|
| `PRISMA_DATAPLANE_IF` | nome de interface, ou vazio | vazio      | —               |
| `PRISMA_SUBSCRIBERS` | só dígitos | `200000`   | `200000`        |
| `PRISMA_LOW_LATENCY` | `1` ou `0`, mais nada | `1`        | `1`             |
| `PRISMA_MAX_WORKERS` | só dígitos, ou vazio para sem limite | vazio      | sem limite      |

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

A cada boot, o `prisma-routing-tune` decide quantas páginas de 1 GB (hugepages) o kernel reserva para o VPP. É memória que sai do Linux e fica dedicada ao dataplane: tabelas do CGNAT, buffers de pacote e o heap do VPP. A conta tem cinco passos.

**1. Assinantes.** `PRISMA_SUBSCRIBERS` × 13,5 KB, arredondado para GB, no mínimo 1. Com o padrão de 200000 dá 2,7, arredondado para **3 GB**.

**2. Buffers de pacote.** 1 GB para cada 4 workers, arredondando para cima, no mínimo 1. Numa máquina de 8 núcleos, 1 fica para o sistema e 7 viram workers: **2 GB**.

**3. Folga para o heap do VPP.** **1 GB**, fixo.

**4. O desejado** é a soma: 3 + 2 + 1 = **6 GB**.

**5. O teto pela RAM da máquina.** O Linux, o Docker e o FRR ficam fora das hugepages, com `máx(2, RAM ÷ 4)` GB. A RAM entra em GB inteiros, arredondada para baixo, e uma VM de "8 GB" mostra uns 7,7, que viram 7. Então ficam 2 GB para o sistema, o teto é 7 − 2 = 5, e a caixa reserva **mín(6, 5) = 5 GB**.

| RAM da VM | Para o sistema | Teto | Reservado (200 mil assinantes, 7 workers) |
|-----------|----------------|------|-------------------------------------------|
| 6 GB      | 2 GB           | 3 GB | 3 GB, e o VPP não subiu                   |
| 8 GB      | 2 GB           | 5 GB | 5 GB                                      |
| 16 GB     | 3 GB           | 12 GB | 6 GB, o desejado cabe                     |
| 32 GB     | 7 GB           | 24 GB | 6 GB                                      |

Quando o teto corta o desejado, o log do ajuste avisa quantos GB faltaram para o número de assinantes configurado. Abaixo de 2 GB a caixa não reserva nada e sobe no perfil conservador, que funciona mas não tem desempenho.

`**PRISMA_SUBSCRIBERS**` **não limita assinantes.** Ele só entra no passo 1. Uma caixa com o valor baixo não recusa o assinante seguinte: ela só tem menos memória dedicada do que a carga real pede. Mudar o valor vale no boot seguinte, porque as hugepages são reservadas pela linha de comando do kernel.


:::warning
**O passo 1 está em revisão.** Os 13,5 KB por assinante supõem uma tabela de sessões de \~2,6 GB para 200 mil assinantes. Só que a tabela de sessões padrão tem 16 MB por tabela, duas por worker (seção abaixo), e cresce só quando você a configura. Falta medir onde a memória do VPP realmente vai, com 0, 20 mil e 200 mil assinantes. A direção combinada é que a memória dedicada ao CGNAT seja **um número que você define**, e não uma estimativa.

:::

# Memória por estrutura

| Estrutura | Dimensionamento | Ordem de grandeza |
|-----------|-----------------|-------------------|
| Bitmap de portas | `block-size` bits por assinante ativo | 64 bytes com blocos de 512 → \~13 MB para 200 mil assinantes |
| Tabelas de sessão | duas por worker | 16 MB cada por padrão |
| Posse de bloco (modo dinâmico) | 4 bytes por bloco, por fatia de worker |                   |

A tabela de sessões é ajustada com:

```
nat
 cgnat
  session-table buckets <n> memory <MB>
  exit
 exit
```

O padrão é 16 mil buckets e 16 MB por tabela. A reserva é **virtual**: ela não custa RAM enquanto a tabela não é preenchida, e o OOM killer também não a enxerga — quem escolhe a vítima olha memória **residente**, não memória mapeada. O padrão modesto continua certo pelo motivo mais simples: a tabela acaba sendo usada, e uma caixa pequena não tem essa memória. Uma implantação de 200 mil assinantes deve elevar esse valor; uma caixa pequena não deve pagar por capacidade que nunca vai usar.

# O segmento de contadores, que derruba o dataplane quando enche

O dataplane publica todos os contadores dele num segmento de memória de tamanho **fixo**. Cada interface que ele carrega ocupa cerca de **2,9 kB** desse segmento, e o segmento não cresce.

Quando ele enche, o dataplane **não recusa a interface seguinte: ele aborta**. A caixa perde o encaminhamento inteiro, de uma vez, e não escreve nenhuma explicação antes de cair.

O tamanho de fábrica é o do próprio dataplane: **32 MB**. Medido, duas vezes e no mesmo ponto: uma caixa nesse tamanho chegou a **7.000 interfaces** e abortou antes de 8.000. Com o segmento em 1 GB, a mesma caixa chegou a 40.000 interfaces, que foi onde a medição parou e não onde ela encontrou um limite.

**Uma caixa de CGNAT comum não chega perto disso.** Assinante não é interface: 200 mil assinantes atrás de um pool dividem um punhado de portas. Quem chega perto é quem cria **uma interface por cliente** - uma subinterface QinQ por assinante, por exemplo.


:::info
**A caixa dimensiona esse segmento sozinha, a partir das interfaces que a configuração dela tem.** No boot ela conta as interfaces do arquivo de configuração e reserva **4,8 kB para cada uma** - que é o limite medido acima dividido pelas 7.000 interfaces que a caixa suportou, e não os 2,9 kB de conteúdo: a tabela de contadores é um bloco só de memória e precisa caber duas vezes enquanto cresce.

Se essas interfaces couberem nos 32 MB de fábrica, ela **não escreve nada** - reservar menos do que o dataplane já dá de graça só tiraria contadores da sua caixa.

:::

O que nenhum arquivo de configuração tem é a interface criada **enquanto a caixa roda**. Se a sua topologia cria interface em tempo de execução, o número é seu e está na seção seguinte.

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
**Esse segmento é RAM que sai do resto.** Não peça 1 GB numa caixa de 4 GB por precaução. Peça o que o número de interfaces da sua topologia pede, e confira no self-test do boot seguinte quanto disso está realmente sendo usado.

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
| BPA dinâmico (alocação + liberação) | \~2 milhões   | \~80 MB          | dezenas de GB |
| NAT por fluxo (referência) | \~40 milhões  | \~1,6 GB         | dezenas de TB |
| BPA determinístico | 0             | 0                | 0      |

## Os registros têm sistema de arquivos próprio

O instalador cria três partições — ESP, raiz e `/var/log/prisma-routing` — em vez de deixar tudo junto. O que fica no terceiro volume é a **trilha de configuração** e o **spill** do exportador: esta caixa não guarda registros de conexão, ela os exporta, e quem guarda pelo ano que o Marco Civil exige é o coletor.


:::warning
Compartilhando o sistema de arquivos com o resto, um log que enche impede a máquina de gravar a própria configuração e a própria trilha, porque ela está ocupada registrando quem se conectou a quê. Com volume próprio, um log cheio é só um log cheio.

:::

O tamanho padrão é aritmético e a conta aparece na tela: \~80 MB por dia para 200 mil assinantes em modo dinâmico, ou cerca de **29 GB por ano**. Modo determinístico não registra alocação nenhuma e quase não precisa de espaço — que é por que isso é uma pergunta e não uma constante. A raiz nunca fica com menos de 8 GB.

Em instalação não assistida, `prisma-routing.log-gb=` responde essa pergunta na linha de comando do kernel.

O volume é montado com `nofail`: uma máquina que não dá boot porque o volume de registros sumiu é uma máquina em que ninguém consegue entrar para descobrir o porquê. Por isso o self-test de boot **reporta onde os registros estão** — sem isso, uma caixa cujo volume não montou sobe caladinha gravando na raiz.

Essa tabela é o argumento comercial do BPA em uma linha: guardar um ano de registros passa de dezenas de terabytes para dezenas de gigabytes — ou para nada. É um argumento sobre o disco do **coletor**, não sobre o desta caixa.

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

* [Primeiros passos](/doc/primeiros-passos-TAWDdSrYG7) — a configuração depois que o hardware está certo
* [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6) — onde `block-size` e o modo são escolhidos
* [Ampliar capacidade sem derrubar assinantes](/doc/ampliar-capacidade-sem-derrubar-assinantes-rWQkaj9r1u) — quando a conta acima mudar
