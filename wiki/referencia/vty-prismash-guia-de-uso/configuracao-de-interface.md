# Configuração de interface

Tudo o que se configura dentro de uma interface: endereços, VLAN, VRF, MTU, MAC, estado administrativo, filtro de origem, o lado do CGNAT, VRRP, OSPF e IS-IS na porta, loopbacks e bundles LACP. Para quem está subindo um enlace, terminando assinantes em VLAN ou investigando uma porta que não passa tráfego.

Entra-se numa interface a partir do modo de configuração, e sai-se com `exit`:

```
prisma> configure
prisma# [edit] interface ens18
prisma# [edit interface ens18]
```

O prompt diz em qual interface você está. `?` ou TAB listam o que vale ali dentro e nada além disso.

Mudar uma interface exige a capacidade de **administrador**. A exceção é o lado do CGNAT (`cgnat inside|outside`), que uma conta `cgnat-operator` também pode mudar.

## O que uma interface entende

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `description` | `<texto>` | para que a interface serve, até 63 caracteres; aparece em `show interface description` |
| `shutdown` |           | derruba a interface administrativamente |
| `no shutdown` |           | traz de volta |
| `ipv4 address` | `<prefixo>` | endereço IPv4; pode repetir |
| `ipv6 address` | `<prefixo>` | endereço IPv6; pode repetir |
| `no ipv4 address` | `<prefixo>` | remove **um** endereço, sem tocar nos outros |
| `mtu`   | `<576-9216>` | MTU, na interface e no par Linux dela; sem ela, 1500 |
| `linux-name` | `<nome>`  | **outro** nome pelo qual o roteamento e o `ip` enxergam esta interface; sem ele, toda interface já entra no Linux com o próprio nome |
| `vrf`   | `<1-4294967295>` | tabela de roteamento onde os endereços vivem; `0` tira a interface de uma |
| `vlan`  | `<1-4094> [inner <1-4094>]` | tag da sub-interface |
| `mac-address` | `<xx:xx:xx:xx:xx:xx>` | o MAC ao qual a interface responde |
| `cgnat` | `inside\|outside` | o lado desta interface no CGNAT: `inside` é por onde os assinantes chegam, `outside` por onde o tráfego traduzido sai |
| `no cgnat` |           | tira a interface do CGNAT |
| `no ipv6 nd suppress-ra` |           | anuncia esta caixa como roteador nesta porta (por padrão não anuncia) |
| `ipv6 nd suppress-ra` |           | volta ao padrão: não anuncia |
| `ipv6 nd nat64` | `[<X:X::X:X/M>]` | põe o prefixo NAT64 nos anúncios (PREF64, RFC 8781), para o CLAT do assinante; sem prefixo, `64:ff9b::/96`; pode repetir. Ver *O prefixo NAT64 nos anúncios* abaixo |
| `no ipv6 nd nat64` | `[<X:X::X:X/M>]` | tira aquele prefixo dos anúncios, ou todos |
| `urpf`  | `strict\|loose\|off` | descarta pacotes cuja origem não volta por aqui |
| `vrrp`  | `<1-255> address <A.B.C.D>` | um endereço de serviço que esta caixa disputa com o par (RFC 5798); IPv4, sem máscara; pode repetir |
| `vrrp`  | `<1-255> priority <1-254>` | quem fica com o endereço quando as duas estão boas; maior ganha |
| `vrrp`  | `<1-255> track <interface> decrement <1-254>` | quanto a prioridade cai quando aquela outra porta cai |
| `no vrrp` | `<1-255>` | tira o roteador virtual inteiro desta interface |
| `member`, `lacp`, `load-balance` |           | só num bundle `ae<n>`; ver *Bundles LACP* abaixo |
| `ip ospf ...`, `ipv6 ospf6 ...` |           | OSPF e OSPFv3 nesta interface; ver *OSPF e IS-IS na interface* abaixo |
| `ip router isis`, `ipv6 router isis` | `<instância>` | IS-IS nesta interface, para IPv4 e para IPv6 |
| `isis ...` |           | ajustes do IS-IS nesta interface |
| `exit`  |           | sai da interface |

## MTU

Uma interface sem `mtu` fica em **1500**, o padrão da caixa desde a 0.152.1, e `no mtu` volta para ele. Vale também para o que o `commit` cria: uma VLAN, um loopback, um bundle. Numa placa que não aguenta 1500 o padrão é o máximo da placa.

```
interface ens18
 mtu 9000
 exit
```

O `mtu` muda a interface do dataplane e o par dela no Linux, juntos. O `tcp-mss auto` de um pool é tirado da MTU das interfaces do CGNAT: em 1500 ele dá 1460.

Numa caixa instalada **antes** da 0.152.1 as interfaces continuam em 9000: a atualização escreveu `mtu 9000` em cada uma que não tinha `mtu`, e a placa segue aceitando quadros de até 9000. Ali, `no mtu` também leva a interface para 1500.

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

## O nome pelo qual o roteamento vê a porta

O dataplane e o sistema operacional são dois mundos, e o roteamento mora no segundo. O que liga os dois é o par: a interface pela qual o kernel, o `ip` e o FRR enxergam a porta do dataplane.

**Toda interface entra no Linux com o próprio nome, sozinha.** Assim que uma interface tem um bloco `interface` - com qualquer coisa dentro, até só um `no shutdown` -, ela aparece no `ip` e no roteamento com o mesmo nome. Isso vale para placa, sub-interface, loopback e placa adicionada com a caixa rodando. A `ens18` do dataplane é a `ens18` do FRR, e nada disso aparece na configuração: não há linha a escrever.

O nome é do Linux, e as regras dele valem: no máximo 15 caracteres e nenhuma barra. Uma placa que ninguém nomeou fica com a grafia que o dataplane dá a ela, cheia de barras, e essa o Linux não aceita. Para essa, e para quem quer outro nome, existe `linux-name`:

```
interface ens18
 linux-name vppwan0
 ipv4 address 200.150.0.1/24
 exit
```

Só nesse caso a linha aparece na configuração: quando diz um nome **diferente** do da interface. Um nome que o Linux não aceita é recusado dizendo por quê:

```
error: linux-name: 'GigabitEthernet0/12/0' cannot be a Linux interface name (15 characters, no '/')
```

O par é **lido de volta** do equipamento, não lembrado pela VTY. Uma configuração salva por uma versão anterior, que traz `linux-name ens18` em `interface ens18`, é lida normalmente, e o próximo `save` grava o arquivo sem a linha. A caixa continua exatamente igual.

A caixa não toma um nome que o Linux já usa para outra coisa: pedir o par com um nome ocupado falha, e derrubaria o `commit` inteiro.

Uma porta cujo nome o roteamento não aceita - mais de 16 caracteres, que é o que uma placa sem nome recebe do dataplane - é recusada no `commit`, com o limite na frase, antes de qualquer coisa ser aplicada. Numa caixa instalada isso não acontece: toda placa reivindicada leva o nome que tinha.

## Loopbacks

Um endereço numa porta física some junto com a porta. Um `router-id` de BGP, de OSPF ou de IS-IS, um `update-source` de iBGP que sobrevive à queda de um enlace, um endereço de origem de SNMP ou de log que não muda de lugar — todos querem uma interface que está sempre no ar.

**Nomear um loopback é o que o cria**, do mesmo jeito que nomear uma sub-interface: não há mais nada no equipamento de onde criá-lo.

```
configure
 interface loop0
  ipv4 address 10.99.0.1/32
  description router-id, que uma porta não leva junto
  exit
 commit
```

O loopback entra no Linux e no roteamento com o próprio nome, `loop0`, sem linha nenhuma a mais — como qualquer outra interface.

`loop<n>` é a grafia do próprio dataplane, e o número que você escrever é o número que ele recebe: `loop3` produz `loop3`, e não o próximo livre. Assim a configuração e o `show interface` não podem discordar sobre como a coisa se chama.

`no interface loop0` desfaz o caminho inteiro — endereço, par e interface. Uma **porta física** continua sendo recusada: não é algo que uma configuração possa remover, e aceitar as palavras prometeria o que o `commit` não entrega.

## Bundles LACP (802.3ad)

Duas ou mais portas viram uma interface só, `ae<n>`, como no Junos. O bundle é criado quando é nomeado, igual a um loopback, e cada porta entra com `member`:

```
configure
 interface ae0
  member ens19
  member ens20
  lacp rate fast
  ipv4 address 10.9.0.1/24
  exit
 commit
```

| Comando | O que faz | Padrão |
|---------|-----------|--------|
| `member <porta>` | uma porta no bundle; pode repetir |        |
| `lacp mode active\|passive` | quem começa a negociação | `active` |
| `lacp rate fast\|slow` | LACP a cada segundo ou a cada 30 | `slow` |
| `load-balance l2\|l23\|l34` | como os fluxos se espalham pelos membros | `l34`  |
| `no member <porta>` | tira a porta do bundle |        |
| `no interface ae0` | desfaz o bundle; as portas voltam a ser portas |        |

Endereços, MTU, descrição, OSPF, BGP e o lado do CGNAT vão no `ae0`, nunca nos membros. O `commit` recusa um membro que a caixa não tem, uma porta em dois bundles e um membro com configuração própria.

Mudar `lacp mode`, `lacp rate` ou `load-balance` refaz o bundle: o balanceamento é fixado na criação, e o modo e o ritmo na entrada de cada membro. Por isso a mudança derruba o bundle por um instante, e é melhor fazê-la numa janela.

`show lacp` mostra o estado de cada membro. Um membro que está no bundle aparece com `MUX-state: COLLECTING_DISTRIBUTING`.

Medido num laboratório contra um bond 802.3ad do Linux: os dois membros negociaram e ficaram agregados dos dois lados, o tráfego atravessou o bundle e continuou atravessando com um membro cortado.

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

Depois disso a sub-interface existe no dataplane, carrega a tag, e ganha o próprio par no Linux com o mesmo nome, como toda interface que tem um bloco — então o Linux e o roteamento a enxergam com o endereço:

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

Um `no shutdown` no pai sobe os dois. É um aviso e não uma recusa: o `commit` aplica o resto normalmente.

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

### A VRF do Linux: `vrf <nome> table <n>`

```
vrf red table 10
interface ens18
 vrf 10
 ipv4 address 10.10.0.1/24
 exit
```

O roteamento chama uma VRF pelo **nome** — `router bgp 65000 vrf red`, o bloco `vrf red` com as estáticas dele — e acha a tabela dela por um dispositivo VRF do Linux com esse nome. `vrf red table 10` cria esse dispositivo na tabela 10, a mesma do `vrf 10` das interfaces, e coloca nele o lado Linux de cada uma dessas interfaces (o par do linux_cp, ou o `linux-name`). Depois do commit o BGP da VRF sobe sem nenhum passo no Linux.

| Você faz | No Linux |
|----------|----------|
| `vrf red table 10` | cria `red` na tabela 10 e o liga |
| `vrf 10` numa interface | o lado Linux dela entra em `red` |
| `no vrf` numa interface | o lado Linux dela sai de `red` |
| `no vrf red table` | remove `red`; as interfaces saem junto |

Sem `table`, `vrf red` continua sendo o bloco do roteamento, como antes.

São recusados: nomes que começam por `prisma-` (a `prisma-bp` é da própria caixa), o nome `default`, as tabelas 253 a 255 do próprio Linux, uma tabela que já é de outra VRF e a tabela da `prisma-bp` (128). A `prisma-bp` não aparece no `show running-config` e nunca é tocada.

As VRFs aparecem no `show running-config` lidas do Linux, e o arquivo salvo as cria de novo no boot, antes de o roteamento precisar delas.

### A gerência numa VRF própria

Para a tabela principal levar a default dos assinantes e a gerência continuar acessível pelo gateway dela, a placa de gerência vai para uma VRF como qualquer interface, com as rotas dela (as duas defaults, IPv4 e IPv6, e qualquer outra rede de gerência) no bloco `vrf` do roteamento, e `system` > `management` > `vrf` diz à caixa que os serviços dela moram ali:

```
vrf mgmt table 100
interface enp6s18
  vrf 100
  exit
vrf mgmt
 ip route 0.0.0.0/0 10.40.200.1
 ip route 10.95.200.0/24 10.40.200.1
 ipv6 route ::/0 2804:4ed4:1000:6::1
 exit
router static
 0.0.0.0/0 next-hop 10.40.10.29
 exit
system
 management
  vrf mgmt
  exit
 exit
```

O `router static` é só a tabela principal. Rota estática de uma VRF vai no bloco `vrf <nome>` dela, fechado com `exit` ou `exit-vrf`.

Faça com `commit confirmed 10` e confirme com `commit` depois de entrar de novo pelo endereço de gerência, por IPv4 e por IPv6. Se a sessão cair, ninguém confirma e o rollback desfaz tudo: a VRF, as regras e os ajustes do kernel. A caixa volta a responder pela tabela principal.

Até a 0.152.5, logo depois de a placa entrar na VRF o endereço IPv6 dela podia ficar sem resposta por mais de 20 segundos, às vezes até a caixa falar primeiro com o gateway: o dataplane descartava as solicitações de vizinho que chegavam por multicast para uma interface movida de tabela. A partir da 0.152.6 ele responde já à primeira, nessa VRF e em qualquer outra.

Até a 0.152.6, numa caixa ocupada, tirar a VRF (um `commit confirmed` que expirou, por exemplo) e pô-la de volta podia deixar a default IPv6 da VRF fora do Linux até o FRR reiniciar: o zebra ficava com a rota conectada da placa inativa. A partir da 0.152.7 a caixa troca a placa de VRF sem endereço nenhum nela, espera o zebra acompanhar antes de devolver os endereços e só remove uma VRF depois de o zebra soltá-la. Vale para a gerência e para a VRF de qualquer cliente. Se o zebra demorar mais de 10 segundos, o commit segue e avisa, e o `show log` guarda o que ele ainda tinha.

**Placa de gerência que o dataplane não tem** (a partir da 0.155.19). Numa máquina virtual a placa de gerência costuma ser uma virtio que o Linux guarda ao lado das placas que o dataplane pega, como a `ens18` com o endereço que o instalador pôs em `/etc/network/interfaces.d/prisma-routing`. Ela entra na VRF do mesmo jeito, com `interface ens18` e `vrf 100`: a caixa move só o lado Linux, leva junto os endereços e a rota default do instalador, e põe essa default na tabela da VRF como o caminho de volta. Um `commit confirmed` que expira a tira de novo, com a default de volta na tabela principal, e o SSH volta a responder por ela. Até a 0.155.18 esse commit passava no `commit check` e falhava ao aplicar (`the dataplane does not have it`), e com a gerência na tabela principal a default do instalador ganhava da do `router static`: o dataplane ficava sem default para os assinantes. Numa placa dessas o bloco aceita só `vrf`; endereços, MTU e descrição ficam no Linux, e o `commit check` os recusa, como recusa um nome que nem o dataplane nem o Linux têm.

| O quê | Por onde vai |
|-------|--------------|
| SSH, API, SNMP e o prisma-web entrando, IPv4 e IPv6 | pela VRF de gerência; a resposta segue a conexão |
| DNS, NTP, download do `system upgrade`, traps, `system logging`, export do container de log | pelas rotas da gerência, a menos que a tabela principal tenha uma rota específica para o destino |
| IPFIX por UDP gerado pelo dataplane | pela tabela principal: o VPP manda por conta própria. Coletor atrás da gerência precisa de rota específica na principal, ou do export por TCP |

Sem `system` > `management` > `vrf mgmt` a placa fica na VRF e a caixa **não** responde por ela: os serviços escutam na tabela principal, e o snmpd e o sshd que escutam no endereço de gerência nem sobem. Com ele, a caixa liga `tcp_l3mdev_accept`/`udp_l3mdev_accept`, `ip_nonlocal_bind` (IPv4 e IPv6) e `keep_addr_on_down` do IPv6, e põe duas regras por família para o tráfego dela mesma - no commit e em todo boot, antes de o snmpd, o sshd e a API subirem. `show system management` mostra o que está configurado e o que está valendo no host. `no vrf` dentro de `management` volta tudo para a tabela principal. `commit check` recusa uma VRF que a configuração não cria, ou sem nenhuma interface dentro.

Para o SSH responder só na gerência, IPv4 e IPv6, liste os dois endereços dela em `ssh listen-address` (desde a 0.152.5), como no `snmp` e no `api`: `ssh listen-address 10.40.200.166 2804:4ed4:1000:6::166`. Com a gerência na VRF, o `commit` aceita esses endereços mesmo estando na VRF, porque o `ip_nonlocal_bind` deixa o sshd escutar neles; sem o `vrf` em `management`, recusa.

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

## O prefixo NAT64 nos anúncios (PREF64, 464XLAT)

Num acesso só IPv6 atendido por um pool NAT64, o assinante ainda tem programa que só fala IPv4: um jogo, um cliente de VPN, um aplicativo que abre conexão para um endereço IPv4 escrito por extenso. O 464XLAT (RFC 6877) resolve isso com um **CLAT** do lado do assinante — no roteador dele ou no celular —, que traduz esse IPv4 para IPv6 endereçado ao prefixo NAT64, e o pool NAT64 desta caixa traduz de volta. Para isso o CLAT precisa saber **qual** é o prefixo, e numa rede com mais de um pool, ou com um prefixo que não é o `64:ff9b::/96`, ele não tem como adivinhar.

A caixa diz a ele, nos próprios anúncios de roteador — a opção PREF64 da RFC 8781 —, na interface voltada para os assinantes:

```
interface ens19
 no ipv6 nd suppress-ra
 ipv6 nd nat64 64:ff9b:1::/48
 exit
```

O prefixo é o `nat64-prefix` de um pool NAT64 desta caixa (veja [Configuração de pool](/s/wiki/doc/configuracao-de-pool-btyS572Ih6)). Sem prefixo, `ipv6 nd nat64` anuncia o `64:ff9b::/96`; a linha pode repetir, um prefixo em cada. Os tamanhos aceitos são os da RFC 6052: `/32`, `/40`, `/48`, `/56`, `/64` ou `/96`.

O `commit check` recusa duas coisas, cada uma dizendo o que fazer:

* `ipv6 nd nat64` numa interface que **não anuncia** — sem `no ipv6 nd suppress-ra` não há anúncio para levar o prefixo;
* um prefixo que **nenhum pool NAT64 traduz** — o CLAT que aprendesse esse prefixo mandaria o IPv4 do assinante para um destino que a caixa descarta. A recusa lista os prefixos que os pools desta caixa usam.

**Quem manda esse anúncio é o FRR, não o dataplane.** O anúncio do VPP não tem a opção PREF64; o do FRR tem, e sai pela mesma porta. Numa interface com `ipv6 nd nat64` a caixa passa o anúncio para o FRR e desliga o do dataplane, para que o assinante ouça **um** roteador só, com o prefixo em todo anúncio. Tirando o `ipv6 nd nat64`, o anúncio volta para o dataplane. Você não precisa escrever nada no FRR: as linhas são da caixa, `show running-config` as mostra dentro do bloco da interface e nunca na parte de roteamento.


:::info
O CLAT também pode aprender o prefixo perguntando `ipv4only.arpa` a um resolvedor DNS64 (RFC 7050). Esta caixa não é o resolvedor do assinante: o DNS64 que você opera precisa sintetizar com **o mesmo** prefixo que a caixa anuncia, ou os dois métodos discordam.

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

**O mesmo** `**vrid**` **serve o IPv6** (a partir da versão 0.153.1). Um endereço IPv6 no mesmo `vrrp <vrid>` cria o roteador virtual IPv6 (VRRPv3, RFC 5798) ao lado do IPv4, com a mesma prioridade, o mesmo `track` e o mesmo preempt — então o gateway IPv6 dos assinantes muda de caixa junto com o lado IPv4:

```
interface ens18
 ipv6 address 2001:db8:1200:ab00::2/56
 ipv4 address 100.64.3.252/22
 vrrp 1 address 100.64.3.254
 vrrp 1 address fe80::1
 vrrp 1 address 2001:db8:1200:ab00::1
 vrrp 1 priority 200
 vrrp 1 track ens19 decrement 150
 exit
```

O roteador IPv6 precisa de **um** endereço link-local (`fe80::...`), que é o primeiro que ele anuncia e o gateway para onde os assinantes apontam (RFC 5798, seção 5.2.9); o `commit check` recusa um roteador IPv6 sem ele, e um segundo link-local é recusado na hora. O MAC virtual do IPv6 é `00:00:5e:00:02:<vrid>`, e quem assume manda um Neighbor Advertisement não solicitado. O `show cgnat ha` mostra os dois roteadores do mesmo `vrid`, o IPv6 marcado `ipv6`.

**O** `**track**` **vale para a família do roteador.** O dataplane só considera "de pé" uma interface rastreada que tem endereço da família do roteador. A caixa liga o IPv6 (só o link-local, sem anúncio de roteador) na interface que um roteador IPv6 rastreia; para o roteador IPv4, a interface rastreada precisa de um endereço IPv4, e o `commit check` avisa quando não tem — um lado dos assinantes só IPv6 rastreado pelo roteador IPv4 do uplink nunca seria visto de pé.

**O que é recusado na hora em que você digita:** um endereço com máscara (`100.64.3.254/22` — o endereço virtual é um dos endereços da sub-rede da interface, não uma sub-rede), prioridade 255, e uma interface rastreando a si mesma: uma porta que caiu já parou de anunciar, e o que vale rastrear é o **outro** lado.

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

## OSPF e IS-IS na interface

Os protocolos de roteamento entram na porta por declarações dentro do próprio bloco `interface`, que é onde o FRR as guarda. Estas vão para o roteamento palavra por palavra; as da tabela do começo da página são aplicadas pela caixa no dataplane.

```
interface ens19
 ip ospf area 0
 ip ospf network point-to-point
 ip ospf cost 10
 ip router isis CORE
 isis network point-to-point
 isis metric 100
 exit
```

### OSPF (`ip ospf ...`)

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `ip ospf area` | `<área>`  | roda OSPF nesta interface, nesta área |
| `ip ospf cost` | `<1-65535>` | o custo desta interface |
| `ip ospf network` | `point-to-point\|broadcast` | `point-to-point` dispensa a eleição de DR, que é o que um enlace de trânsito quer; `broadcast` é o padrão em Ethernet |
| `ip ospf hello-interval` | `<1-65535>` | segundos entre hellos |
| `ip ospf dead-interval` | `<1-65535>` | segundos até o vizinho ser dado como caído |
| `ip ospf priority` | `<0-255>` | a prioridade desta caixa na eleição de DR |
| `ip ospf authentication` | `message-digest\|null` | MD5, ou nenhuma, sobrepondo a da área |
| `ip ospf authentication-key` | `<chave>` | a chave em texto; nunca é mostrada de volta |
| `ip ospf message-digest-key` | `<1-255> md5 <chave>` | uma chave MD5; nunca é mostrada de volta |
| `ip ospf passive` |           | roda OSPF aqui e não manda hello |
| `ip ospf bfd` |           | acompanha a adjacência com BFD |
| `ip ospf mtu-ignore` |           | não compara MTU na troca de base — a saída quando os dois lados discordam e não podem ser igualados |

### OSPFv3 (`ipv6 ospf6 ...`)

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `ipv6 ospf6 area` | `<área>`  | roda OSPFv3 nesta interface, nesta área — é a **única** forma de pôr uma interface no OSPFv3 |
| `ipv6 ospf6 cost` | `<1-65535>` | o custo desta interface |
| `ipv6 ospf6 network` | `point-to-point\|broadcast` | como no OSPF |
| `ipv6 ospf6 passive` |           | roda OSPFv3 aqui e não manda hello |
| `ipv6 ospf6 bfd` |           | acompanha a adjacência com BFD |
| `ipv6 ospf6 mtu-ignore` |           | não compara MTU |

`ipv6 ospf6 hello-interval`, `dead-interval` e `priority` também são aceitos, na grafia do FRR, mas o `?` não os oferece: digitados no valor padrão, o FRR não os guarda.

### IS-IS

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `ip router isis` | `<instância>` | roda IS-IS para IPv4 nesta interface |
| `ipv6 router isis` | `<instância>` | roda IS-IS para IPv6 nesta interface, na topologia que a instância tiver |
| `isis circuit-type` | `level-1\|level-2-only\|level-1-2` | de quais níveis esta interface participa |
| `isis network point-to-point` |           | sem eleição de DIS nem pseudo-nó; configurado de um lado só, a adjacência não sobe |
| `isis metric` | `<0-16777215> [level-1\|level-2]` | a métrica desta interface; acima de 63 exige `metric-style wide` na instância |
| `isis passive` |           | anuncia a interface e não manda hello |
| `isis hello-interval` | `<1-600>` | segundos entre hellos |
| `isis hello-multiplier` | `<2-100>` | quantos hellos perdidos até o vizinho cair |
| `isis password` | `clear\|md5 <senha>` | autenticação desta interface; nunca é mostrada de volta |
| `isis bfd` |           | acompanha a adjacência com BFD |

As chaves e senhas aparecem como `<set, not shown>` em `show this`, `show configuration` e `show running-config`. A configuração salva e as revisões de rollback guardam o valor de verdade, ou um rollback não conseguiria restaurá-lo.

O bloco do protocolo (`router ospf`, `router ospf6`, `router isis`) está em [Roteamento, SSH, usuários e SNMP](/s/wiki/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m).

## Removendo um ajuste: `no`

`no ?` dentro da interface lista o que pode ser tirado, e depois da palavra o argumento continua sendo oferecido - `no urpf ?` mostra `strict`, `loose` e `off`.

Tirar um ajuste **não é o mesmo que ajustá-lo para o padrão**: a configuração deixa de dizer aquilo, e a interface fica com o que a caixa já tinha. `mac-address aa:bb:cc:dd:ee:ff` seguido de `no mac-address` devolve a interface ao MAC dela, que é o que se quer de quem digitou e voltou atrás.

A interface carrega dois tipos de linha: as desta caixa - `description`, `shutdown`, `mtu`, `urpf`, `vlan`, `vrf`, `mac-address`, `linux-name`, endereços, `ipv6 nd suppress-ra`, `ipv6 nd nat64`, `cgnat`, `vrrp`, `member`, `lacp`, `load-balance` - e as dos protocolos de roteamento - `ip ospf ...`, `ipv6 ospf6 ...`, `ip router isis`, `isis ...`. `no` funciona nos dois, e em cada caso vai para quem é dono do ajuste.

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
    linux       tap4096 vppwan0
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
prisma# [edit system / dataplane] commit
```

A diferença é prática. Numa caixa que ainda não recebeu placa nenhuma o dataplane não tem interface alguma, então não há `interface <nome>` para entrar — e `show interface` está vazio justamente na hora em que você precisa escolher. Por isso o nome aqui é o que o **Linux** dá à placa, e quem lista os candidatos é o próprio `?`.

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `interface`, em `system` > `dataplane` | `<placa>` | entrega esta placa ao dataplane no próximo início dele |
| `no interface`, no mesmo nível |           | a caixa deixa de pegar placa: estado seguro, alcançável por rede |

O `commit` guarda a escolha, recalcula o ajuste de boot e **não reinicia o dataplane** — a entrega derruba todas as sessões da caixa, inclusive a sua, então a janela é sua. Um nome que o Linux não tem é recusado na hora em que você digita, com a lista das placas que ele tem; o caso, com o que a caixa imprime, está em [Primeiros passos](/s/wiki/doc/primeiros-passos-TAWDdSrYG7).

A placa que carrega a rota default **não** é recusada: uma caixa nova é configurada no console, e é o ajuste de boot que, no boot que entrega a placa, lê o caminho de volta por ela e recusa a placa se não conseguir lê-lo.

`system dataplane restart`, no modo operacional, é outra coisa: reinicia o dataplane que já existe, sem trocar de placa.

## Placa adicionada com a caixa rodando

O dataplane só pega placas pelo DPDK quando **inicia**. Para uma placa adicionada no hypervisor com a caixa no ar, a caixa não espera o próximo boot, e também não reinicia o dataplane sozinha: um reinício derruba o CGNAT inteiro, com o bloco de portas e as sessões de todos os assinantes.

Em vez disso ela **anexa a placa ao dataplane em funcionamento pelo AF_XDP**, que lê a placa pelo driver do kernel. A placa encaminha na hora, e ninguém perde sessão.

**O que a caixa faz sozinha**, segundos depois de a placa aparecer:

* confere se a regra do boot pegaria essa placa. A placa reservada para gerência, o `PRISMA_DATAPLANE_IF` e a placa que tem endereço ou a rota default continuam sendo do Linux, exatamente como no boot;
* anexa a placa com o **mesmo nome** que o Linux deu a ela (`ens20`, por exemplo) e com o MAC dela mesma.

**O que você faz** é configurar a placa como qualquer outra: `interface ens20`, o endereço e `no shutdown`. Ela entra no Linux e no roteamento com o próprio nome, como as outras. O `show interface` avisa, embaixo da contagem, que ela está no AF_XDP e o que a leva para o DPDK.

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
* `monitor interface <nome>` mostra o mesmo ao vivo, num gráfico por sentido redesenhado a cada segundo até `q`;
* `show running-config` mostra o bloco da interface como o equipamento o lê de volta — o que está lá é o que está em vigor, não o que foi digitado.

# Ver também

* [Configuração de CGNAT](/s/wiki/doc/configuracao-de-cgnat-EIWRFS6OnJ) — o nível `cgnat`, e o que o lado de cada interface significa
* [Roteamento, SSH, usuários e SNMP](/s/wiki/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m) — os blocos `router ospf`, `router ospf6` e `router isis`
* [Alta disponibilidade](/s/wiki/doc/alta-disponibilidade-Zc1pBKocic) — o par de caixas e o que o VRRP preserva
* [uRPF e origens forjadas](/s/wiki/doc/urpf-e-origens-forjadas-1Mb4LB6Dhf) — quando usar `strict` e quando `loose`
* [Multi-tenant com VRF](/s/wiki/doc/multi-tenant-com-vrf-3PDsWxfy6D) — quando cada cliente tem a própria tabela
