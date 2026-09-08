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

# Ver também

* [Acesso, login e papéis](/doc/acesso-login-e-papeis-VcE7gIbI1U) — o que cada grupo pode mudar
* [VTY hexash — guia de uso](/doc/vty-hexash-guia-de-uso-h6sDAK8Dsh) — os modos e a árvore de comandos
