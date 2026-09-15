# Configuração de interface

Tudo o que se configura dentro de uma interface: o par que o roteamento enxerga, endereços, VLAN, VRF, MTU, estado administrativo e o filtro de origem. Para quem está subindo um enlace, terminando assinantes em VLAN ou investigando uma porta que não passa tráfego.

Entra-se numa interface a partir do modo de configuração, e sai-se com `exit`:

```
hexarouting> configure
hexarouting# [edit] interface GigabitEthernet0/12/0
hexarouting# [edit interface GigabitEthernet0/12/0]
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
| `linux-name` | `<nome>` | o nome pelo qual o roteamento e o `ip` enxergam esta interface; **sem ele o roteamento não a enxerga de jeito nenhum** |
| `vrf`   | `<1-4294967295>` | tabela de roteamento onde os endereços vivem; `0` tira a interface de uma |
| `vlan`  | `<1-4094> [inner <1-4094>]` | tag da sub-interface |
| `mac-address` | `<xx:xx:xx:xx:xx:xx>` | o MAC ao qual a interface responde |
| `no ipv6 nd suppress-ra` |           | anuncia esta caixa como roteador nesta porta (por padrão não anuncia) |
| `urpf`  | `strict\|loose\|off` | descarta pacotes cuja origem não volta por aqui |
| `exit`  |           | sai da interface |

## Endereços

```
interface GigabitEthernet0/12/0
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

## `linux-name` — sem ela o roteamento não vê a porta

O dataplane e o sistema operacional são dois mundos, e o roteamento mora no segundo. `linux-name` é o que cria o par: o nome pelo qual o kernel, o `ip` e o FRR passam a enxergar esta interface.

```
interface GigabitEthernet0/12/0
 linux-name vppwan0
 ipv4 address 200.150.0.1/24
 exit
```


:::warning
**Numa interface sem** `**linux-name**`**, BGP, OSPF e IS-IS são configuráveis e inertes.** A configuração é aceita, commita limpa, a estrofe aparece na configuração em vigor — e nenhuma adjacência sobe, porque o roteamento não tem interface nenhuma com aquele nome para usar.

:::

O nome é do Linux, e as regras dele valem: no máximo 15 caracteres e nenhuma barra. Copiar o nome do dataplane — que é cheio de barras — é o engano óbvio, e é recusado dizendo por quê:

```
error: linux-name: 'GigabitEthernet0/12/0' cannot be a Linux interface name (15 characters, no '/')
```

O par é **lido de volta** do equipamento, não lembrado pela VTY: um estado que se liga e não se lê de volta é um estado que o operador não consegue desligar.

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

Uma sub-interface é uma interface: tudo da tabela acima vale dentro dela.

```
interface GigabitEthernet0/12/0.100
 vlan 100
 ipv4 address 100.64.0.1/22
 exit

interface GigabitEthernet0/12/0.200
 vlan 200 inner 10
 exit
```

`no interface GigabitEthernet0/12/0.100` remove a sub-interface **e o que estava nela** — um endereço deixado para trás descreveria uma porta que não existe.


:::warning
**O número depois do ponto tem de ser igual à tag externa.** `GigabitEthernet0/12/0.7` com `vlan 100` é recusado.

O equipamento nomeia a sub-interface pela tag, e a configuração em vigor é lida do equipamento. Se os dois nomes pudessem divergir, você leria um nome e a caixa teria outro.

:::


:::warning
**A tag vai de 1 a 4094, nas duas posições.** `vlan 0`, `vlan 4095` e qualquer
número acima disso são recusados na hora em que você digita - e o mesmo vale
para a tag interna, `vlan 100 inner 0` e `vlan 100 inner 4095`.

```
error: vlan 4095 out of range (1-4094): 802.1Q reserves 0 and 4095
```

Zero e 4095 são reservados pelo próprio 802.1Q: zero é a marcação de
prioridade, um quadro que carrega o cabeçalho de tag e nenhuma VLAN dentro, e
4095 não é atribuível. Nenhum switch do outro lado entrega uma nem outra, então
uma sub-interface construída em cima delas fica com cara de configurada e não
carrega nada.

:::

Numa sub-interface de duas tags (`inner`), quadros que chegam com **mais** de duas tags e o mesmo par externo/interno também casam. Se o seu desenho depende de casar exatamente duas, confirme o comportamento antes de colocar em produção.

## VRF

```
interface GigabitEthernet0/12/0
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
interface GigabitEthernet0/12/0
 no ipv6 nd suppress-ra
 exit
```

Ligado, o anúncio sai a cada 200 segundos com validade de 600 segundos, e mais três anúncios iniciais logo que é ligado. `show running-config` mostra a linha só nas interfaces onde ela foi escrita, porque nas outras ela é o padrão.


:::info
`ipv6 nd suppress-ra` também é aceito. Ele não muda nada — diz em voz alta o que já vale — e serve para deixar a intenção escrita na configuração, ou para desfazer a exceção numa interface onde ela foi ligada.

:::

## Removendo um ajuste: `no`

`no ?` dentro da interface lista o que pode ser tirado, e depois da palavra o argumento continua sendo oferecido - `no urpf ?` mostra `strict`, `loose` e `off`.

Tirar um ajuste **não é o mesmo que ajustá-lo para o padrão**: a configuração deixa de dizer aquilo, e a interface fica com o que a caixa já tinha. `mac-address aa:bb:cc:dd:ee:ff` seguido de `no mac-address` devolve a interface ao MAC dela, que é o que se quer de quem digitou e voltou atrás.

A interface carrega dois tipos de linha: as desta caixa - `mtu`, `urpf`, `vlan`, `vrf`, `mac-address`, `linux-name`, endereços - e as dos protocolos de roteamento - `ip ospf ...`, `isis ...`. `no` funciona nos dois, e em cada caso vai para quem é dono do ajuste.

### `no vlan` não existe, e a caixa diz o que fazer no lugar

Uma sub-interface **é** a tag dela: o nome `GigabitEthernet0/12/0.700` e a VLAN 700 são a mesma informação, e a caixa recusa criar uma em que os dois discordem. Não existe, portanto, sub-interface sem tag - o que se quer é a sub-interface fora:

Digitar `no vlan 700` dentro da sub-interface é recusado, e a recusa diz o que fazer no lugar:

```
error: vlan cannot be removed
    a sub-interface is named after its tag, so one without a tag is not
    something the dataplane can hold
    remove the sub-interface itself: no interface <name>
```

A recusa vem em linhas próprias, dentro da largura do console: o veredito na
primeira, o motivo no meio, e **o que digitar no lugar na última**. Por um
tempo ela saía numa frase só, mais larga que a tela, e o terminal a quebrava
onde calhasse - o que punha justamente o conselho do fim em qualquer lugar.
Agora a última linha é sempre a última linha.

`no vlan`, sem número, recebe a mesma recusa: não há tag a tirar de qualquer
forma.

`no interface GigabitEthernet0/12/0.700`, no nível de cima, remove a sub-interface e tudo o que estava nela.

## Uma interface em detalhe

`show interface` lista todas. Nomeando uma, a resposta muda de tabela para relatório:

```
hexarouting> show interface GigabitEthernet0/12/0
  GigabitEthernet0/12/0
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

O nome exato tem precedência: pedir `GigabitEthernet0/12/0` numa caixa que também tem `GigabitEthernet0/12/0.100` traz o relatório da interface, não a lista das duas.

## A placa entregue ao dataplane não é uma destas

`interface <nome>` configura uma interface que o dataplane **já tem**. Qual placa física ele recebe é outra decisão, um nível abaixo, e mora no topo do modo de configuração:

```
hexarouting> configure
hexarouting# [edit] dataplane interface enp2s0
hexarouting# [edit] commit
```

A diferença é prática. Numa caixa que ainda não recebeu placa nenhuma o dataplane não tem interface alguma, então não há `interface <nome>` para entrar — e `show interface` está vazio justamente na hora em que você precisa escolher. Por isso o nome aqui é o que o **Linux** dá à placa, e quem lista os candidatos é o próprio `?`.

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `dataplane interface` | `<placa>` | entrega esta placa ao dataplane no próximo início dele |
| `no dataplane interface` |           | a caixa deixa de pegar placa: estado seguro, alcançável por rede |

O `commit` guarda a escolha, recalcula o ajuste de boot e **não reinicia o dataplane** — a entrega derruba todas as sessões da caixa, inclusive a sua, então a janela é sua. A placa que carrega a rota default é recusada na hora em que você digita, e um nome que a caixa não tem também, com a lista dos que ela tem. Os dois casos, com o que a caixa imprime, estão em [Primeiros passos](/doc/primeiros-passos-TAWDdSrYG7).

# Como conferir

* `show interface <nome>` traz o relatório, com estado, endereços, VRF, MAC e contadores;
* `show interface terse` traz uma linha por interface com **duas** colunas de estado — `Admin`, que é o que alguém configurou, e `Link`, que é o que o cabo está fazendo: up/down é problema de enlace, down/down é `shutdown`;
* `show interface description` mostra a descrição de todas, ao lado dessas duas colunas;
* `show interface utilization <nome>` amostra os contadores e mostra quanto está passando agora, em bit/s;
* `show running-config` mostra o bloco da interface como o equipamento o lê de volta — o que está lá é o que está em vigor, não o que foi digitado.

# Ver também

* [Configuração de CGNAT](/doc/configuracao-de-cgnat-EIWRFS6OnJ) — anexar CGNAT a uma interface
* [Multi-tenant com VRF](/doc/multi-tenant-com-vrf-3PDsWxfy6D) — quando cada cliente tem a própria tabela
