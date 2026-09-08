# Roteamento, SSH, usuários e SNMP

Tudo que não é CGNAT: roteamento, SSH, contas e SNMP. Todas essas mudanças exigem a capacidade de **administrador** (`hexa-admin` ou root).

# Roteamento (FRR)

```
router bgp <AS>
router ospf
router isis <nome>
```

O roteamento é do FRR, alcançado pelo TAP do `linux_cp`. Depois de entrar no protocolo, a sintaxe é a do FRR. Da mesma forma, os `show` de roteamento são repassados ao `vtysh`:

```
show ip route
show bgp summary
show isis neighbor
show interface
show logging
```

As rotas aprendidas chegam à FIB do VPP — é o que o laboratório `routing` verifica.


:::warning
**A MTU do TAP é 9000.** Uma interface que herda esse valor sem que o outro lado concorde produz adjacência OSPF travada em Exchange. Ajuste com `mtu` dentro do bloco `interface`.

:::

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
