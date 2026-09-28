# A caixa subiu e não encaminha

A caixa ligou, responde na VTY, aceita configuração — e não passa pacote. Esta página é a ordem de diagnóstico para esse caso: o que o self-test do boot já disse, as duas telas que mostram o que o dataplane tem, onde fica o motivo quando o dataplane desistiu, e como pôr a caixa para encaminhar de novo. Para quem está no console de uma caixa nova ou recém-reiniciada.

Uma caixa sem interface de dataplane parece uma caixa funcionando. Por isso a própria caixa diz, em três lugares, o que decidiu e por quê — e o diagnóstico é ler esses três lugares na ordem.

# 1. Leia o self-test do boot

A cada boot a caixa imprime no console (serial, IPMI ou a tela da máquina virtual) um bloco que começa com `=============== Prisma Routing: boot self-test ==============`. Cada linha é `[ ok ]`, `[warn]`, `[FAIL]` ou `[info]`, e as falhas trazem o motivo nas linhas de baixo. As que respondem "por que não encaminha":

| O console diz | O que significa | O que fazer |
|---------------|-----------------|-------------|
| `[warn] running in FIRST BOOT mode: no pinning and no hugepages.` | primeiro boot depois da instalação: o ajuste de CPU e de memória só vale a partir do próximo boot, e até lá o dataplane sobe conservador, sem DPDK | reinicie a caixa. É o esperado, não um defeito |
| `[warn] running between two tuning plans until the next reboot:` | uma mudança de ajuste pediu mais workers do que o kernel isolou neste boot. A caixa encaminha com os workers que já estão isolados, e as duas linhas de baixo dizem quantos agora e quantos depois | reinicie quando puder para passar ao plano novo. Até lá ela encaminha, com menos núcleos |
| `[info] DPDK is off until hugepages exist; no NIC can be claimed yet` | o mesmo caso, visto pelo lado da placa | o mesmo: reinicie |
| `[warn] no dataplane interface: this box has no PCI network card for the` … | a caixa não tem placa que o dataplane possa tomar | veja o passo 3; uma placa virtio numa máquina virtual conta, uma placa que o Linux não vê não conta |
| `[warn] no dataplane interface: this box has <n> card(s) and claimed` … | a caixa tem placas e não tomou nenhuma: a única candidata era a placa pela qual ela é alcançada, e não deu para ler dela endereço, MAC e gateway para devolver ao Linux | veja o passo 3 e nomeie a placa (passo 6) |
| `[FAIL] the dataplane has no interface, and one is named` | uma placa foi entregue ao dataplane e ele não a tem; as linhas de baixo dizem a que driver ela está presa | veja o passo 5 |
| `[FAIL] the dataplane holds interface(s) its driver refused to start:` | o dataplane tem a porta, mas o driver da placa se recusou a iniciá-la; tudo o mais parece certo | veja o passo 5 |
| `[FAIL] rx queues: none opened on <placa>` | o mesmo, visto pelas filas: nenhuma fila de recepção foi aberta | veja o passo 5 |
| `[warn] rx queues: <n> of <m> on <placa>, <k> requested` | a caixa encaminha, mas a placa abriu menos filas do que o ajuste pediu: só `<n>` workers recebem dela | não impede o encaminhamento; é capacidade, não defeito |
| `[warn] requested and NOT in effect: <regra>` | uma regra de ajuste (hugepages, `isolcpus` e outras) foi pedida e o kernel não a aplicou; a linha de baixo diz o que fazer | veja o passo 5, *hugepages* |
| `[FAIL] VPP not responding` | o dataplane não está respondendo; a tela traz as últimas linhas que ele escreveu | veja o passo 4 |
| `[FAIL] ... has restarted <n> times: it is looping` | o dataplane sobe e cai em laço | veja o passo 4 |
| `[warn] a card this box has is not on the dataplane` | uma placa instalada depois do boot não foi tomada | `system reboot` entrega a placa; veja o passo 3 |

Numa caixa que encaminha, a linha da placa é `[ ok ]` e diz qual placa o dataplane tomou e por quê — `claimed by this box with nobody asked` quando a caixa escolheu sozinha, ou o nome configurado quando alguém nomeou uma.


:::info
**A caixa toma todas as placas PCI que tem, menos uma reservada ao Linux, e nunca a última.** A reservada é a da rede de gerência informada na instalação. Numa caixa de uma placa só, ela toma essa placa e o endereço de gerência vai junto para o lado Linux da porta.

:::

# 2. `show interface`

```
show interface
```

Se o dataplane não tem nenhuma porta além da interna dele, a tela começa por `the dataplane has no interface to forward on:` seguido do motivo, lido dos mesmos arquivos que o self-test lê:

| Motivo | O que fazer |
|--------|-------------|
| `DPDK is off until hugepages exist, so no NIC can be claimed yet.` | primeiro boot: reinicie |
| `the dataplane is off on the installation medium, and a reboot does not change that: nothing here survives one.` | você está na mídia de instalação: `install` põe o Prisma Routing num disco |
| `no interface was named, so VPP was given no NIC.` | nomeie a placa (passo 6) |
| `'<nome>' was named and VPP was given no NIC.` | a placa nomeada não existe, não tem endereço PCI, ou carrega a rota padrão e não deu para ler o caminho de volta dela; confira o nome em `show hardware` |
| `'<endereço PCI>' is named in the startup configuration and the dataplane does not have it.` | a placa foi entregue e o dataplane não a tem; as linhas de baixo dizem o driver (passo 5) |

Se a porta aparece, olhe o estado dela: `error` é uma porta que o driver da placa se recusou a iniciar (passo 5). Uma placa instalada com a caixa ligada aparece com a nota de que encaminha por AF_XDP até o dataplane reiniciar.

# 3. `show hardware`

```
show hardware
```

Lista as placas **como o Linux as tem**: nome, endereço PCI, driver, estado do link e velocidade, com o MAC e o que acontece com cada placa quando ela for entregue ao dataplane. A placa que carrega a rota padrão vem marcada com `*** carries the DEFAULT ROUTE ***`.

Antes da lista, a tela diz o que o dataplane já tem — uma placa que o dataplane tomou não aparece mais para o Linux e, por isso, não está na lista — ou repete o motivo do passo 2 quando ele não tem nada. No fim, diz quais placas a caixa tomou por conta própria, qual poupou para o Linux, e qual está esperando o próximo boot.

É aqui que se confere se a placa existe para o Linux. Uma placa que não aparece nem aqui nem no dataplane não existe para esta caixa: confira a máquina virtual ou o slot.

# 4. O dataplane desistiu: o arquivo do motivo

O dataplane roda num container. Se ele não consegue subir, o container é reiniciado algumas vezes e então para de tentar. Nesse ponto a placa já tinha sido tirada do Linux para ser entregue ao dataplane, e a caixa ficaria sem interface nenhuma.

**Um vigia de placa cuida disso.** A cada 30 segundos ele verifica se o container do dataplane parou de vez; se parou, ele:


1. grava o motivo em `/var/log/prisma-routing/why-the-dataplane-stopped` — o estado do container, o código de saída e as últimas 80 linhas que o dataplane escreveu;
2. devolve **todas** as placas tomadas ao driver do kernel;
3. sobe as interfaces do Linux de novo, para a caixa voltar a ser alcançável.

Ele faz isso uma vez por boot e não reinicia nada. Na prática, a caixa volta a ter a placa no Linux uns dois minutos depois do boot, sem dataplane.

**O arquivo fica em** `**/var/log**`**, e não em** `**/run**`**, porque o próximo passo de todo mundo é reiniciar**, e o reinício apaga o log do container. Leia antes de mexer em qualquer coisa. Com o dataplane parado, `show version` aponta para ele:

```
show version
```

A linha do plugin diz que o dataplane não está respondendo e termina com `the dataplane gave up and /var/log/prisma-routing/why-the-dataplane-stopped has what it printed before it did`. Para ler o arquivo, uma conta de administrador abre um shell com `start shell`:

```
start shell
cat /var/log/prisma-routing/why-the-dataplane-stopped
exit
```

As últimas linhas são as que dizem o motivo. As mais comuns estão no passo 5.

# 5. As causas, e o conserto de cada uma

**Hugepages.** O dataplane precisa de memória reservada em páginas grandes: de 1 GB quando a CPU oferece esse tamanho, de 2 MB quando não oferece — o caso de muitas máquinas virtuais com o modelo de CPU padrão do hipervisor. O ajuste escolhe o tamanho sozinho e grava a reserva para o próximo boot.

* No primeiro boot a reserva ainda não existe: reinicie (passo 1).
* Se o self-test diz `requested and NOT in effect:` com uma regra de hugepages, o kernel não fez a reserva pedida; a linha de baixo diz o que fazer. Em máquina virtual, confira a memória dada à máquina: 8 GB é o piso a usar, e uma máquina de 6 GB não subiu o dataplane.
* Se o arquivo do motivo termina em `rte_eal_init failed: Cannot allocate memory (12)` ou em `Invalid argument (22)` numa caixa de páginas de 2 MB, é o defeito corrigido nas versões 0.119 e 0.120: leve a caixa para uma versão atual.

**Uma placa que o driver recusou.** A porta aparece em `show interface` em estado `error`, e o self-test diz `the dataplane holds interface(s) its driver refused to start:`. Tudo o mais — driver, par com o Linux, endereço — parece certo, e a porta não passa pacote.

* A causa conhecida é **MTU**: a caixa inicia cada porta no menor MTU máximo entre as placas que tomou; uma porta em `error` depois de um `mtu` configurado é um valor que a placa não carrega. Baixe o `mtu` daquela interface.
* O motivo do driver fica no journal do dataplane; `show interface` e o self-test indicam onde.

**Uma placa entregue e não tomada.** O self-test diz `[FAIL] the dataplane has no interface, and one is named`, e a linha seguinte diz a situação:

| A linha seguinte diz | O que significa |
|----------------------|-----------------|
| que ela está num driver que o DPDK usa | a entrega foi feita; o motivo está no journal do dataplane |
| que ela está num driver do kernel | o DPDK não consegue tomar a placa por ele |
| que nenhum driver está preso a ela | a placa ficou sem dono |
| que nenhum driver que o DPDK usa está carregado | nem `vfio-pci`, nem `uio_pci_generic`, nem `igb_uio` |

Numa caixa que toma várias placas, uma placa que não pôde ser entregue fica de fora e o dataplane sobe com as outras.

**A placa nomeada não existe.** Se `show interface` diz que o nome configurado não recebeu placa, confira o nome em `show hardware` e nomeie de novo (passo 6).

# 6. Conserte, e reinicie o dataplane

Para dizer qual placa o dataplane deve tomar:

```
configure
 system
  dataplane
   interface <nome>
 commit
```

Um nome que a caixa não tem é recusado, e a recusa lista os que ela tem. O `commit` grava a escolha e recalcula o ajuste, mas **não** entrega a placa: a tela diz `NOT IN EFFECT YET` e explica o que a entrega vai derrubar.

Depois de consertar a causa, faça o dataplane subir de novo, de um dos dois jeitos:

```
system dataplane restart
```

ou

```
system reboot
```

`system dataplane restart` devolve todas as placas ao Linux, recalcula o ajuste, toma as placas de novo e sobe o dataplane com a configuração **salva**. Uma placa que o vigia tinha devolvido é tomada de novo aqui ou no próximo boot. As duas formas pedem uma conta de administrador.


:::warning
**Reiniciar o dataplane derruba o encaminhamento até ele voltar, e todo assinante perde o bloco de portas e as sessões abertas;** os blocos são alocados de novo conforme o tráfego volta. E o dataplane volta com a configuração **salva**: rode `save` antes, ou o que foi aplicado com `commit` e não salvo não volta.

:::

# Como conferir

```
show interface
show hardware
```


:::success
`show interface` lista a placa com o estado `up`, e não a frase `the dataplane has no interface to forward on`; `show hardware` diz que o dataplane tem a placa. No boot seguinte, o self-test mostra `[ ok ]` na linha da placa, `[ ok ] every tuning rule this box can read back is in effect` e nenhum `[FAIL]`.

:::

# Ver também

* [Instalação da appliance](/doc/instalacao-da-appliance-06NmxvBDb5) — o que a instalação pergunta, incluindo a rede de gerência que fica com o Linux
* [Dimensionamento e requisitos](/doc/dimensionamento-e-requisitos-f42dC4k45s) — memória, CPU e placas
* [Operação e diagnóstico](/doc/operacao-e-diagnostico-A4fcKDVwZt) — por onde começar quando o problema não é a placa
* [Levar a caixa para uma versão nova](/doc/levar-a-caixa-para-uma-versao-nova-C09kO2kU8X) — quando o conserto é uma versão atual
