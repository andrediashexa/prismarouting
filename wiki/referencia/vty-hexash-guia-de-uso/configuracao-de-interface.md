# Configuração de interface

# Configuração de interface

Tudo o que se configura dentro de uma interface: endereços, VLAN, VRF, MTU, estado administrativo e o filtro de origem. Para quem está subindo um enlace, terminando assinantes em VLAN ou investigando uma porta que não passa tráfego.

Entra-se numa interface a partir do modo de configuração, e sai-se com `exit`:

```
hexarouting# configure
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
| `vrf`   | `<tabela>` | tabela de roteamento onde os endereços vivem |
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

# Como conferir

* `show interface <nome>` traz o relatório, com estado, endereços, VRF, MAC e contadores;
* `show interface description` mostra a descrição de todas;
* `show running-config` mostra o bloco da interface como o equipamento o lê de volta — o que está lá é o que está em vigor, não o que foi digitado.

# Ver também

* [Configuração de CGNAT](/doc/configuracao-de-cgnat-EIWRFS6OnJ) — anexar CGNAT a uma interface
* [Multi-tenant com VRF](/doc/multi-tenant-com-vrf-3PDsWxfy6D) — quando cada cliente tem a própria tabela
