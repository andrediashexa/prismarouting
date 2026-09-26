# Configuração de interface

Tudo o que se configura dentro de uma interface: o par que o roteamento enxerga, endereços, VLAN, VRF, MTU, estado administrativo e o filtro de origem. Para quem está subindo um enlace, terminando assinantes em VLAN ou investigando uma porta que não passa tráfego.

Entra-se numa interface a partir do modo de configuração, e sai-se com `exit`:

```
prisma> configure
prisma# [edit] interface ens18
prisma# [edit interface ens18]
```

O prompt diz em qual interface você está. `?` ou TAB listam o que vale ali dentro e nada além disso.

## O que uma interface entende

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `description` | `<texto>` | para que a interface serve; aparece em `show interface description` |
| `shutdown` |           | derruba a interface administrativamente |
| `no shutdown` |           | traz de volta |
| `ipv4 address` | `<prefixo>` | endereço IPv4; pode repetir |
| `ipv6 address` | `<prefixo>` | endereço IPv6; pode repetir |
| `no ipv4 address` | `<prefixo>` | remove **um** endereço, sem tocar nos outros |
| `mtu`   | `<576-9216>` | MTU, na interface e no par Linux dela |
| `linux-name` | `<nome>`  | o nome pelo qual o roteamento e o `ip` enxergam esta interface; uma placa já entra com o próprio nome, e **um loopback sem ele o roteamento não enxerga de jeito nenhum** |
| `vrf`   | `<1-4294967295>` | tabela de roteamento onde os endereços vivem; `0` tira a interface de uma |
| `vlan`  | `<1-4094> [inner <1-4094>]` | tag da sub-interface |
| `mac-address` | `<xx:xx:xx:xx:xx:xx>` | o MAC ao qual a interface responde |
| `no ipv6 nd suppress-ra` |           | anuncia esta caixa como roteador nesta porta (por padrão não anuncia) |
| `urpf`  | `strict\|loose\|off` | descarta pacotes cuja origem não volta por aqui |
| `vrrp`  | `<1-255> address <A.B.C.D>` | um endereço de serviço que esta caixa disputa com o par (RFC 5798) |
| `vrrp`  | `<1-255> priority <1-254>` | quem fica com o endereço quando as duas estão boas; maior ganha |
| `vrrp`  | `<1-255> track <interface> decrement <1-254>` | quanto a prioridade cai quando aquela outra porta cai |
| `no vrrp` | `<1-255>` | tira o roteador virtual inteiro desta interface |
| `exit`  |           | sai da interface |

## Endereços

```
interface ens18
 ipv4 address 200.150.0.1/24
 ipv4 address 200.150.1.1/24
 ipv6 address 2001:db8::1/64
 exit
```

A palavra e o prefixo têm de combinar. `ipv6 address 10.0.0.1/24` é recusado:

```
error: '10.0.0.1/24' is not an IPv6 prefix
```

Isso não é preciosismo. Sem a checagem o endereço seria aceito e configurado do jeito errado, calado.

`no ipv4 address 200.150.1.1/24` remove **aquele** endereço. Os outros ficam.


:::info
Ao trocar um endereço por outro, o antigo sai antes de o novo entrar — quem escreve as duas linhas no mesmo commit termina com um endereço, não com dois por um instante.

:::

## `linux-name` — o nome pelo qual o roteamento vê a porta

O dataplane e o sistema operacional são dois mundos, e o roteamento mora no segundo. O que liga os dois é o par: o nome pelo qual o kernel, o `ip` e o FRR enxergam esta interface.

**Uma placa entra no Linux com o próprio nome.** A caixa batiza cada placa que reivindica com o nome que ela tinha no Linux, e assim que a placa tem um bloco `interface` - com qualquer coisa dentro, até só um `no shutdown` - ela aparece no `ip` e no roteamento com esse mesmo nome. A `ens18` do dataplane é a `ens18` do FRR, sem nenhuma linha a mais, e `interface ens18` num bloco de roteamento age sobre a porta.

`linux-name` é o que escolhe **outro** nome para o par, e o que cria o par numa interface que não é placa - um loopback:

```
interface ens18
 linux-name vppwan0
 ipv4 address 200.150.0.1/24
 exit
```


:::warning
**Num loopback sem** `**linux-name**`**, BGP, OSPF e IS-IS são configuráveis e inertes.** A configuração é aceita, commita limpa, a estrofe aparece na configuração em vigor — e nenhuma adjacência sobe, porque o roteamento não tem interface nenhuma com aquele nome para usar.

:::

O nome é do Linux, e as regras dele valem: no máximo 15 caracteres e nenhuma barra. Uma placa que ninguém nomeou fica com a grafia que o dataplane dá a ela - cheia de barras - e copiar essa grafia é o engano óbvio, recusado dizendo por quê:

```
error: linux-name: 'GigabitEthernet0/12/0' cannot be a Linux interface name (15 characters, no '/')
```

O par é **lido de volta** do equipamento, não lembrado pela VTY: um estado que se liga e não se lê de volta é um estado que o operador não consegue desligar. Por isso o nome que a placa recebeu por padrão aparece na configuração como um `linux-name ens18` escrito: `show configuration` mostra, `save` grava, e trocar por outro nome é trocar essa linha.

Uma porta cujo nome o roteamento não aceita - mais de 16 caracteres, que é o que uma placa sem nome recebe do dataplane - é recusada no `commit`, com o limite na frase, antes de qualquer coisa ser aplicada. Numa caixa instalada isso não acontece: toda placa reivindicada leva o nome que tinha.

## Loopbacks

Um endereço numa porta física some junto com a porta. Um `router-id` de BGP, de OSPF ou de IS-IS, um `update-source` de iBGP que sobrevive à queda de um enlace, um endereço de origem de SNMP ou de log que não muda de lugar — todos querem uma interface que está sempre no ar.

**Nomear um loopback é o que o cria**, do mesmo jeito que nomear uma sub-interface: não há mais nada no equipamento de onde criá-lo.

```
configure
 interface loop0
  linux-name loop0
  ipv4 address 10.99.0.1/32
  description router-id, que uma porta não leva junto
  exit
 commit
```

`loop<n>` é a grafia do próprio dataplane, e o número que você escrever é o número que ele recebe: `loop3` produz `loop3`, e não o próximo livre. Assim a configuração e o `show interface` não podem discordar sobre como a coisa se chama.

`no interface loop0` desfaz o caminho inteiro — endereço, par e interface. Uma **porta física** continua sendo recusada: não é algo que uma configuração possa remover, e aceitar as palavras prometeria o que o `commit` não entrega.

## Sub-interfaces e VLAN

**Como se cria uma, do começo ao fim.** Basta digitar um nome que a caixa ainda não tem, no formato `<pai>.<tag>`: é isso que cria a sub-interface. O `?` diz isso na linha abaixo da lista de interfaces — ele oferece as que existem, e a nota embaixo diz quais nomes criam uma nova.

```
prisma> configure
prisma# [edit] interface ens18.100
prisma# [edit interface ens18.100] vlan 100
prisma# [edit interface ens18.100] ipv4 address 100.64.0.1/22
prisma# [edit interface ens18.100] exit
prisma# [edit] commit
commit complete
```

Depois disso a sub-interface existe no dataplane, carrega a tag, e ganha o próprio par `linux_cp` — o `lcp-auto-subint` cria um para cada sub-interface de uma placa já pareada — então o Linux e o roteamento a enxergam com o endereço:

```
prisma> show interface
  Interface                  State  MTU    Address             Linux
  ens18                      up     1500   10.95.200.198/24    tap4096 ens18
  ens18.100                  up     1500   100.64.0.1/22       tap4096.100 ens18.100
```

**Se o pai estiver** `**shutdown**`**, a sub-interface fica esperando — e o** `**commit**` **passa assim mesmo.** O VPP não sobe uma sub-interface enquanto a interface-pai está down, então a caixa não tenta e diz isso:

```
warning: interface ens19.100: left shut down
    the dataplane will not bring a sub-interface up while its parent is down,
    and ens19 is shut down; 'no shutdown' on ens19 brings both up
```

Um `no shutdown` no pai sobe os dois. Até a versão 0.107.0 isso era uma falha de `commit` com a mensagem `unspecified error` - e, como falhar não muda o que a configuração pede, **todo** `commit` seguinte falhava igual, mesmo os que não tinham nada a ver com aquela interface.

**O pai precisa existir.** `interface ens99.100` numa caixa sem `ens99` é recusado no `commit`, nomeando o pai:

```
error: interface ens99.100: its parent ens99 is not on this box
error: candidate did not pass validation; nothing applied
```

Uma sub-interface é uma interface: tudo da tabela acima vale dentro dela.

```
interface ens18.100
 vlan 100
 ipv4 address 100.64.0.1/22
 exit

interface ens18.200
 vlan 200 inner 10
 exit
```

`no interface ens18.100` remove a sub-interface **e o que estava nela** — um endereço deixado para trás descreveria uma porta que não existe.


:::warning
**O número depois do ponto tem de ser igual à tag externa.** `ens18.7` com `vlan 100` é recusado.

O equipamento nomeia a sub-interface pela tag, e a configuração em vigor é lida do equipamento. Se os dois nomes pudessem divergir, você leria um nome e a caixa teria outro.

:::


:::warning
**A tag vai de 1 a 4094, nas duas posições.** `vlan 0`, `vlan 4095` e qualquer número acima disso são recusados na hora em que você digita - e o mesmo vale para a tag interna, `vlan 100 inner 0` e `vlan 100 inner 4095`.

```
error: vlan 4095 out of range (1-4094): 802.1Q reserves 0 and 4095
```

Zero e 4095 são reservados pelo próprio 802.1Q: zero é a marcação de prioridade, um quadro que carrega o cabeçalho de tag e nenhuma VLAN dentro, e 4095 não é atribuível. Nenhum switch do outro lado entrega uma nem outra, então uma sub-interface construída em cima delas fica com cara de configurada e não carrega nada.

:::

Numa sub-interface de duas tags (`inner`), quadros que chegam com **mais** de duas tags e o mesmo par externo/interno também casam. Se o seu desenho depende de casar exatamente duas, confirme o comportamento antes de colocar em produção.

## VRF

```
interface ens18
 vrf 7
 ipv4 address 10.7.0.1/24
 exit
```

A tabela é numérica, igual à de um pool — um número, um significado. A tabela 0 é o padrão e não precisa ser escrita.

Mover uma interface de VRF **leva os endereços junto**: eles são removidos, a interface muda de tabela, e os endereços voltam. Você escreve só a linha do `vrf`; o resto é consequência.

## Estado administrativo

`shutdown` derruba os dois lados: a interface que encaminha pacotes e o par que o roteamento enxerga. Derrubar só um deixaria o protocolo de roteamento segurando adjacência sobre uma porta que parou de encaminhar — o vizinho acredita que o enlace está bom até o próprio temporizador dele expirar, e o tráfego some nesse meio-tempo.

Num mesmo commit, o que desce é aplicado **antes** das outras mudanças e o que sobe **depois**: uma interface que está sendo derrubada para de carregar tráfego antes de a configuração mudar embaixo dela, e uma que sobe já sobe configurada.

## Anúncio de roteador IPv6

Uma interface com endereço IPv6 **não anuncia** esta caixa como roteador. Esse é o padrão, e é deliberado: numa porta voltada para assinante, anunciar seria um segundo gateway para todo mundo que recebe prefixo por DHCPv6-PD, ao lado do roteador que o assinante já usa.

Nas portas em que o anúncio faz sentido — um uplink, um trânsito, uma porta voltada para outros roteadores — ligue por exceção:

```
interface ens18
 no ipv6 nd suppress-ra
 exit
```

Ligado, o anúncio sai a cada 200 segundos com validade de 600 segundos, e mais três anúncios iniciais logo que é ligado. `show running-config` mostra a linha só nas interfaces onde ela foi escrita, porque nas outras ela é o padrão.


:::info
`ipv6 nd suppress-ra` também é aceito. Ele não muda nada — diz em voz alta o que já vale — e serve para deixar a intenção escrita na configuração, ou para desfazer a exceção numa interface onde ela foi ligada.

:::

## Um par de caixas e o endereço que passa de uma para a outra

Duas caixas, um endereço de serviço, e o assinante apontado para ele em vez de para qualquer uma das duas. É o VRRP da RFC 5798, e aqui ele roda **na interface do dataplane**, que é onde os pacotes chegam:

```
interface ens18
 ipv4 address 100.64.3.252/22
 vrrp 1 address 100.64.3.254
 vrrp 1 priority 200
 vrrp 1 track ens19 decrement 150
 exit
```

Na outra caixa, o mesmo com o endereço dela e prioridade menor:

```
interface ens18
 ipv4 address 100.64.3.251/22
 vrrp 1 address 100.64.3.254
 vrrp 1 priority 100
 vrrp 1 track ens19 decrement 150
 exit
```

**O** `**vrid**` **tem que ser o mesmo nas duas caixas**, e diferente do de qualquer outro par no mesmo domínio de broadcast: é ele que diz de qual eleição cada anúncio fala, e é dele que sai o MAC virtual (`00:00:5e:00:01:<vrid>`).

**O MAC virtual é o que faz o assinante não precisar saber de nada.** Quem assume o endereço assume também esse MAC e manda um ARP gratuito. O CPE que estava com aquele endereço na tabela ARP continua mandando para o mesmo MAC, e o quadro passa a chegar na outra caixa — sem ninguém limpar cache em lugar nenhum.

`**priority**` **é a sua afirmação de quem deve ficar com o serviço.** Maior ganha, e a caixa que volta de uma manutenção **retoma** o endereço, porque é isso que a configuração diz. 255 é recusado: a RFC reserva esse valor para quem é dono do endereço, e uma caixa que divide o endereço com outra não é.

`**track**` **é a falha que o resto não cobre.** Uma caixa com o uplink arrancado continua viva e continua anunciando: ela segura o endereço do gateway e não alcança nada. Com `track`, a prioridade dela cai pelo decremento e o par assume. Escolha o decremento de forma que a queda leve a prioridade **abaixo** da do outro — 200 com decremento 150 vira 50, contra os 100 do par.

**Cada lado quer o seu.** Um endereço que se move no lado do assinante e nada no lado do uplink é um failover que o tráfego de volta não acompanha: configure um roteador virtual em cada lado, com `vrid` diferente.

### O que o endereço virtual não faz

**Ele não responde ping.** Quem não é dono do endereço não responde ICMP por ele, que é o comportamento padrão da RFC 5798 (seção 6.4.3) — e é o que esta caixa faz. O monitoramento deve pingar o endereço **próprio** de cada caixa, que é o que diz se aquela caixa está viva; o endereço virtual só diz qual das duas está com ele. Encaminhar **através** dele funciona normalmente, e é para isso que ele existe.

**Ele não sincroniza sessão nenhuma.** Conexões em curso caem no failover, como caem em qualquer NAT. O que não muda é o endereço público do assinante, porque em modo determinístico o mapeamento é função da configuração — a página de alta disponibilidade explica o que isso preserva e o que não preserva.

### Vendo o papel de cada caixa

```
prisma> show cgnat ha
  this box
    mapping fingerprint  e3b0c44298fc1c14
    pools                residencial

  role
    ens18 vrrp 1   master, holding the address
      address(es)        100.64.3.254
      virtual mac        00:00:5e:00:01:01
      priority           200
```

Quando alguma porta rastreada está fora, a linha da prioridade diz as duas coisas — o que está sendo anunciado e o que foi configurado — porque uma caixa anunciando menos do que foi configurado é uma caixa com um cabo fora, e não um problema de VRRP.

## Removendo um ajuste: `no`

`no ?` dentro da interface lista o que pode ser tirado, e depois da palavra o argumento continua sendo oferecido - `no urpf ?` mostra `strict`, `loose` e `off`.

Tirar um ajuste **não é o mesmo que ajustá-lo para o padrão**: a configuração deixa de dizer aquilo, e a interface fica com o que a caixa já tinha. `mac-address aa:bb:cc:dd:ee:ff` seguido de `no mac-address` devolve a interface ao MAC dela, que é o que se quer de quem digitou e voltou atrás.

A interface carrega dois tipos de linha: as desta caixa - `mtu`, `urpf`, `vlan`, `vrf`, `mac-address`, `linux-name`, endereços - e as dos protocolos de roteamento - `ip ospf ...`, `isis ...`. `no` funciona nos dois, e em cada caso vai para quem é dono do ajuste.

### `no vlan` não existe, e a caixa diz o que fazer no lugar

Uma sub-interface **é** a tag dela: o nome `ens18.700` e a VLAN 700 são a mesma informação, e a caixa recusa criar uma em que os dois discordem. Não existe, portanto, sub-interface sem tag - o que se quer é a sub-interface fora:

Digitar `no vlan 700` dentro da sub-interface é recusado, e a recusa diz o que fazer no lugar:

```
error: vlan cannot be removed
    a sub-interface is named after its tag, so one without a tag is not
    something the dataplane can hold
    remove the sub-interface itself: no interface <name>
```

A recusa vem em linhas próprias, dentro da largura do console: o veredito na primeira, o motivo no meio, e **o que digitar no lugar na última**. Por um tempo ela saía numa frase só, mais larga que a tela, e o terminal a quebrava onde calhasse - o que punha justamente o conselho do fim em qualquer lugar. Agora a última linha é sempre a última linha.

`no vlan`, sem número, recebe a mesma recusa: não há tag a tirar de qualquer forma.

`no interface ens18.700`, no nível de cima, remove a sub-interface e tudo o que estava nela.

## Uma interface em detalhe

`show interface` lista todas. Nomeando uma, a resposta muda de tabela para relatório:

```
prisma> show interface ens18
  ens18
    state       up
    mtu         9000
    addresses   200.150.0.1/24
    linux       vppwan0
    vrf         0 (default)
    mac         02:fe:c1:ed:07:aa
    urpf        off
    cgnat       not attached
    counters
      rx bytes                     120
      punt                           2
```

Os contadores são o motivo de o relatório existir. *No ar* e *no ar e descartando quatro por cento do que chega* são idênticos numa tabela, e é o segundo que gera o chamado.

**Numa caixa que faz par, o relatório diz o papel da porta**, logo abaixo do CGNAT:

```
    cgnat       in2out
    vrrp 1      master, holding the address
                100.64.3.254 on 00:00:5e:00:01:01
```

É a primeira coisa que importa sobre uma porta quando existe parceira: se os assinantes estão nesta caixa ou na outra. `show cgnat ha` responde isso para a caixa inteira; aqui é para a porta que você já está olhando.

O que aparece é o **papel em vigor**, lido do dataplane — o que foi *configurado* está em `show this` e no arquivo salvo. Porta sem roteador virtual não mostra linha nenhuma: a maioria não tem, e aviso que aparece sempre é aviso que ninguém lê.

O nome exato tem precedência: pedir `ens18` numa caixa que também tem `ens18.100` traz o relatório da interface, não a lista das duas.

## A placa entregue ao dataplane não é uma destas

`interface <nome>` configura uma interface que o dataplane **já tem**. Qual placa física ele recebe é outra decisão, um nível abaixo, e mora no topo do modo de configuração:

```
prisma> configure
prisma# [edit] system
prisma# [edit system] dataplane
prisma# [edit system / dataplane] interface enp2s0
prisma# [edit] commit
```

A diferença é prática. Numa caixa que ainda não recebeu placa nenhuma o dataplane não tem interface alguma, então não há `interface <nome>` para entrar — e `show interface` está vazio justamente na hora em que você precisa escolher. Por isso o nome aqui é o que o **Linux** dá à placa, e quem lista os candidatos é o próprio `?`.

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `interface`, em `system` > `dataplane` | `<placa>` | entrega esta placa ao dataplane no próximo início dele |
| `no interface`, no mesmo nível |           | a caixa deixa de pegar placa: estado seguro, alcançável por rede |

O `commit` guarda a escolha, recalcula o ajuste de boot e **não reinicia o dataplane** — a entrega derruba todas as sessões da caixa, inclusive a sua, então a janela é sua. A placa que carrega a rota default é recusada na hora em que você digita, e um nome que a caixa não tem também, com a lista dos que ela tem. Os dois casos, com o que a caixa imprime, estão em [Primeiros passos](/doc/primeiros-passos-TAWDdSrYG7).

## Placa adicionada com a caixa rodando

O dataplane só pega placas pelo DPDK quando **inicia**. Para uma placa adicionada no hypervisor com a caixa no ar, a caixa não espera o próximo boot, e também não reinicia o dataplane sozinha: um reinício derruba o CGNAT inteiro, com o bloco de portas e as sessões de todos os assinantes.

Em vez disso ela **anexa a placa ao dataplane em funcionamento pelo AF_XDP**, que lê a placa pelo driver do kernel. A placa encaminha na hora, e ninguém perde sessão.

**O que a caixa faz sozinha**, segundos depois de a placa aparecer:

* confere se a regra do boot pegaria essa placa. A placa reservada para gerência, o `PRISMA_DATAPLANE_IF` e a placa que tem endereço ou a rota default continuam sendo do Linux, exatamente como no boot;
* anexa a placa com o **mesmo nome** que o Linux deu a ela (`ens20`, por exemplo) e com o MAC dela mesma.

**O que você faz** é configurar a placa como qualquer outra: `interface ens20`, `linux-name ens20`, o endereço e `no shutdown`. O `show interface` avisa, embaixo da contagem, que ela está no AF_XDP e o que a leva para o DPDK.

**Passar a placa para o DPDK** é uma decisão sua, na janela que você escolher:

```
prisma> system dataplane restart
```

**Dê** `**save**` **antes.** O que volta depois do reinício é a configuração **salva**, e o comando avisa se houver algo confirmado e não salvo. Ele diz o que custa e reinicia o dataplane. A placa volta pelo DPDK, com o mesmo nome e a mesma configuração, junto com as outras placas de dados. Medido numa VM: o encaminhamento parou por 18 segundos e voltou sem perda. Um reboot faz o mesmo.


:::info
**O custo do AF_XDP é velocidade, não correção.** Numa placa virtio com uma fila só, o próprio kernel avisa que o envio fica num modo mais lento. Para uma placa que vai carregar assinantes em volume, deixe no AF_XDP só até a próxima janela.

:::

# Como conferir

* `show interface <nome>` traz o relatório, com estado, endereços, VRF, MAC e contadores;
* `show interface terse` traz uma linha por interface com **duas** colunas de estado — `Admin`, que é o que alguém configurou, e `Link`, que é o que o cabo está fazendo: up/down é problema de enlace, down/down é `shutdown`;
* `show interface description` mostra a descrição de todas, ao lado dessas duas colunas;
* `show interface utilization <nome>` amostra os contadores e mostra quanto está passando agora, em bit/s;
* `show running-config` mostra o bloco da interface como o equipamento o lê de volta — o que está lá é o que está em vigor, não o que foi digitado.

# Ver também

* [Configuração de CGNAT](/doc/configuracao-de-cgnat-EIWRFS6OnJ) — anexar CGNAT a uma interface
* [Multi-tenant com VRF](/doc/multi-tenant-com-vrf-3PDsWxfy6D) — quando cada cliente tem a própria tabela
