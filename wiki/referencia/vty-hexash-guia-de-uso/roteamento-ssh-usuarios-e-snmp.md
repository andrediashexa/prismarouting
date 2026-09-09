# Roteamento, SSH, usuários e SNMP

# Roteamento, SSH, usuários e SNMP

# Roteamento, SSH, usuários e SNMP

Tudo que não é CGNAT: roteamento, SSH, contas e SNMP. Todas essas mudanças exigem a capacidade de **administrador** (`hexa-admin` ou root).

# Roteamento (FRR)

```
router bgp <AS>
router ospf
router isis <nome>
ip route <prefixo> <próximo-salto>
```

O roteamento é do FRR, alcançado pelo TAP do `linux_cp`. Depois de entrar no protocolo, a sintaxe é a do FRR.

Uma rota estática não precisa de protocolo nenhum e é a primeira coisa que se configura numa caixa nova:

```
configure
ip route 0.0.0.0/0 200.150.0.1
commit
```

`commit check` recusa um próximo salto que não está em nenhuma rede conectada:

```
error: ip route 10.55.0.0/16 172.31.99.1: 172.31.99.1 is on no connected network
    the route would be accepted and never reach the forwarding table
```

Aceita, essa rota ficaria no FRR e nunca chegaria à tabela de encaminhamento.

## Ver a tabela: `show route`, não `show ip route`

Três tabelas guardam rotas nesta caixa e só uma encaminha pacote:

```mermaid
flowchart LR
    RIB["RIB do FRR<br/>o que o FRR decidiu"] --> K["FIB do kernel<br/>o que o FRR instalou"]
    K -->|"netlink / linux_nl"| VFIB["FIB do VPP<br/>o que encaminha"]
```

| Comando | Responde a partir de | Use quando |
|---------|----------------------|------------|
| `show route` | **FIB do VPP**       | é a pergunta normal: o que esta caixa encaminha |
| `show route verbose` | as duas, comparadas  | o tráfego não passa e a rota "está lá" |
| `show route <prefixo>` | as duas, para um destino | investigar um destino específico |
| `show ip route` | RIB do FRR (repassado ao `vtysh`) | conferir o que o FRR decidiu, não o que a caixa faz |

```
hexarouting> show route
  Destination          Next hop          Interface      Kind
  0.0.0.0/0            200.150.0.1       TenGig0/8/0    forward
  203.0.113.0/24       200.150.0.1       TenGig0/8/0    forward
  200.150.0.0/24       connected         TenGig0/8/0    connected

  3 entries carrying traffic
```


:::warning
`show ip route` responde pela tabela do FRR. Numa caixa que está descartando o tráfego, a rota aparece **presente e correta** ali — porque quem encaminha é a FIB do VPP, e a resposta do FRR não diz nada sobre a rota ter chegado lá.

Quando o tráfego não passa e a rota parece certa, use `show route verbose`: ele compara as duas e marca onde discordam.

:::

A coluna VPP diz o que o VPP **tem** — `forward`, `connected`, `local`, `neighbour`, `drop` ou `absent` — e não sim/não. Um prefixo que o VPP descarta de propósito e um que ele nunca ouviu falar são situações diferentes, com causas diferentes.

Endereços locais e rotas por interfaces que o VPP não possui — a de gerência, por exemplo — **não** são marcadas como discordância. Elas estão no FRR e ausentes da FIB corretamente e para sempre.

## Quais daemons rodam

`zebra`, `mgmtd` e `staticd` sobem sempre. `bgpd`, `ospfd` e `isisd` são habilitados pelo instalador — o pacote do Debian os traz desligados, e uma caixa instalada sem isso oferecia os três no VTY sem rodar nenhum.

`commit check` recusa um `router <protocolo>` cujo daemon não está de pé:

```
error: router bgp 65001: bgpd is not running
    the statement would commit and reach nothing. Enable bgpd in
    /etc/frr/daemons and restart frr
```

O autoteste de boot informa quais estão respondendo.

# Três armadilhas de roteamento

## 1. MTU herdada trava o OSPF

O TAP do `linux_cp` vem com 9000. Uma interface que herda esse valor sem o outro lado concordar deixa a adjacência OSPF parada em Exchange. Ajuste com `mtu` dentro do bloco `interface` — ele muda a interface do dataplane **e** o TAP dela, juntos.

## 2. IS-IS não vai sobre IP

IS-IS roda direto sobre o enlace, não sobre IP. Isso muda o que precisa estar certo para a adjacência subir, e um diagnóstico feito por endereço IP não enxerga o problema.

## 3. O CGNAT comendo o próprio plano de controle


:::warning
**Se um pool público contiver um endereço de peering, a sessão BGP cai de forma intermitente sem nada óbvio em lugar nenhum.**

Sessões BGP e adjacências OSPF/IS-IS terminam nos endereços **da própria caixa**. O `bpa-out2in` roda no arco `ip4-unicast`, antes do `ip4-lookup` e portanto antes de o `ip4-local` decidir que o pacote é local — ou seja, ele vê o pacote do BGP primeiro.

:::

Duas defesas, ambas obrigatórias no produto:


1. A criação de pool **é recusada** quando o conjunto público contém um endereço configurado numa interface local. O erro nomeia o endereço e a interface.
2. Um endereço configurado **depois** do pool não tem como ser recusado, então é reportado em nível de erro em `show logging` — operador lê isso, não o stderr do processo do VPP.

Na prática, endereços de pool normalmente **não** são endereços de interface: eles chegam à caixa por uma rota do upstream, não por configuração local.

# Interfaces

```
configure
 interface <nome>
  mtu <576-9216>
  urpf strict|loose|off
  exit
```

| Comando | Efeito |
|---------|--------|
| `mtu`   | ajusta a MTU **na interface do dataplane e no TAP dela** — as duas juntas |
| `urpf strict` | a origem precisa rotear de volta por esta interface |
| `urpf loose` | a origem precisa ter rota em algum lugar |
| `urpf off` | sem checagem (padrão) |

uRPF numa interface interna é o que impede uma origem forjada de ser traduzida.

# SSH

```
configure
 ssh port <1-65535>
 ssh listen-address <endereço>
 ssh permit-root-login yes|no|prohibit-password|forced-commands-only
 ssh password-authentication yes|no
```

| Valor de `permit-root-login` | Significado |
|----------------------------|-------------|
| `yes`                      | senha ou chave |
| `no`                       | de jeito nenhum |
| `prohibit-password`        | só chave    |
| `forced-commands-only`     | só comandos forçados |

# Contas de usuário

```
configure
 user <nome>
  ssh-key <tipo> <chave> [comentário]
  password <hash>
  no ssh-key <chave>
  no password
  exit
```

`**password**` **recebe um hash crypt, nunca uma senha em texto claro.** Gere com:

```sh
openssl passwd -6
```

O que a conta pode fazer é decidido pelos grupos do sistema (`hexa-admin`, `hexa-cgnat`), não por nada guardado aqui.

# SNMP

O agente é **somente leitura por construção** — não existe caminho de escrita para desabilitar.

```
configure
 snmp enabled yes|no
 snmp listen-address <endereço>
 snmp port <1-65535>
 snmp enterprise-number <n>
 snmp community <string>
```

| Comando | O que define |
|---------|--------------|
| `snmp enabled` | roda o agente ou não |
| `snmp listen-address` | endereço de escuta |
| `snmp port` | porta UDP    |
| `snmp enterprise-number` | o Private Enterprise Number IANA da sua organização |
| `snmp community` | uma comunidade SNMPv2c, somente leitura |


:::info
A MIB empresarial fica **inativa** até que alguém registre um Private Enterprise Number na IANA e o configure com `snmp enterprise-number`. O projeto não inventa um número que pertence a outra organização.

:::

## Usuários SNMPv3

```
configure
 snmp-user <nome>
  auth <sha|md5> <passphrase>
  priv <aes|des> <passphrase>
  exit
```

## Política de roteamento: o que a VTY confere

Prefix-lists e route-maps são escritos na sintaxe do roteamento e guardados por ele. A VTY completa os nomes que a caixa já tem:

```
hexarouting# [edit] ip ?
  prefix-list  a named list of prefixes, for matching in policy
  route        a static route
hexarouting# [edit] ip prefix-list <TAB>
  PL-CLIENTES   ip prefix-list
```

A completação sai da mesma fonte que o `show running-config` responde, então ela não oferece um nome que a caixa não tem.


:::warning
**Uma política precisa dos daemons de roteamento de pé.** Prefix-list e route-map não pertencem a nenhum protocolo, então não é o `bgpd` que as guarda — e com o daemon dono parado o roteamento **aceita a linha e não guarda nada**, sem erro.

O `commit` recusa nesse caso e diz qual daemon falta e qual arquivo editar:

```
error: 2 policy statement(s) need mgmtd, which is not running
    first one: ip prefix-list PL-CLIENTES seq 10 permit 200.150.0.0/24
    they would commit and store nothing. Enable mgmtd in /etc/frr/daemons
    and restart frr
```

Uma estrofe `router bgp` que não vai a lugar nenhum se percebe: nenhuma sessão sobe. Uma prefix-list que não vai a lugar nenhum é invisível até o tráfego que devia ser filtrado não ser.

:::

Avisos do roteamento agora chegam ao operador mesmo quando o `commit` tem sucesso:

```
frr: The route-map 'RM-CLIENTES' does not exist.
commit complete
```

Antes essa linha era descartada, e a pessoa saía com uma política apontando para nada.

### Faixa de prefixo: `ge` e `le`

Numa entrada de prefix-list com `ge` e `le`, vale `comprimento da máscara ≤ ge ≤ le ≤ 32` (ou `≤ 128`, em IPv6). Quando não vale, a linha é recusada no `commit` e no `commit check`, com o nome da sentença:

```
error: ip prefix-list PL-CLIENTES seq 10 permit 10.12.0.0/8 ge 24 le 16: prefix
range refused for 10.0.0.0/8: mask length <= ge <= le <= 32 does not hold
(length 8, ge 24, le 16)
```

Repare que o prefixo aparece normalizado — `10.12.0.0/8` vira `10.0.0.0/8`, porque `/8` só olha o primeiro octeto. Procure a sentença pelo nome da lista, não pelo prefixo impresso.


:::info
Essa recusa acontece **antes** de a linha chegar ao roteamento. É de propósito: recusada mais adiante, ela também derrubava o commit seguinte, com a mesma mensagem apontando para um prefixo que não estava em configuração nenhuma — nem a em vigor, nem a candidata. Quem visse aquilo procuraria o prefixo na própria configuração e não acharia nada para corrigir.

:::

## O prefixo público de um pool precisa ser anunciado

```
warning: pool 'clientes': nothing here announces 200.150.0.16/28
    subscribers behind it would be translated to an address the internet
    cannot route back to
```

Esta é a única verificação que cruza as duas metades do equipamento. Configure o CGNAT certo, configure o BGP certo, e o serviço pode estar morto assim mesmo: se nada anuncia um prefixo que **cobre** o lado público do pool, todo assinante atrás dele é traduzido para um endereço que a internet não sabe devolver. O tráfego sai e nada volta.

As duas metades são configuradas em lugares diferentes, por pessoas diferentes, em dias diferentes — e nada ligava uma à outra.


:::info
O aviso entende **cobertura**, não igualdade: anunciar `200.150.0.0/24` cobre um pool em `200.150.0.16/28`.

Ele **não recusa** o commit — anunciar de outro equipamento é um desenho legítimo. E ele **se cala** quando há `redistribute`, porque aí não consegue saber. Um aviso que dispara numa caixa que funciona é ignorado, e o próximo, verdadeiro, é ignorado junto.

:::

# Ver também

* [Acesso, login e papéis](/doc/acesso-login-e-papeis-VcE7gIbI1U) — o que cada grupo pode mudar
* [VTY hexash — guia de uso](/doc/vty-hexash-guia-de-uso-h6sDAK8Dsh) — os modos e a árvore de comandos
