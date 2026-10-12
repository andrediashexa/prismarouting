# Configuração de PPPoE

Esta página é a referência do bloco `pppoe`: a caixa termina as sessões PPPoE dos assinantes, autentica cada um no seu RADIUS e traduz o tráfego deles pelo CGNAT, como faz com um assinante atrás de uma interface `inside`. Ela diz o que cada declaração faz, o que a caixa recusa e como ler as sessões.

```
prisma> configure
prisma# [edit] pppoe
prisma# [edit pppoe] interface ens19
prisma# [edit pppoe] gateway 100.64.0.1
prisma# [edit pppoe] address-pool 100.64.0.10-100.64.3.250
prisma# [edit pppoe] dns 8.8.8.8
prisma# [edit pppoe] radius server 192.0.2.10 secret <segredo>
prisma# [edit pppoe] exit
prisma# [edit] commit
```

O `commit` gera a configuração do plano de controle PPPoE a partir da candidata e o põe no ar. Como todo o resto, a mudança entra no `rollback`, no `commit confirmed` e na trilha de auditoria. Os arquivos que o plano de controle lê são gerados pela caixa e reescritos a cada commit; não se editam à mão.

:::warning
Toda mudança no bloco `pppoe` reinicia o plano de controle PPPoE: **todas as sessões caem** e os CPEs discam de novo. O encaminhamento dos outros assinantes, o roteamento e a VTY não são afetados.
:::

## Declarações

| Declaração | O que faz |
|---|---|
| `interface <nome>` | uma interface de acesso: a placa, ou uma sub-interface dela com **uma** tag VLAN (`vlan <id>` no bloco da sub-interface). Pode haver várias, todas da mesma placa |
| `gateway <endereço>` | o endereço da caixa em cada sessão (o endereço local do IPCP). Obrigatório |
| `address-pool <prefixo>` ou `<primeiro>-<último>` | endereços para quem o RADIUS não manda `Framed-IP-Address`. Até 16 |
| `dns <endereço>` | um resolvedor entregue ao CPE. Até 2 |
| `radius server <endereço> secret <segredo> [auth-port <n>] [acct-port <n>]` | um servidor RADIUS, para autenticação e contabilização (1812 e 1813 se omitidos). Até 4 |
| `radius nas-identifier <nome>` | o NAS-Identifier enviado; `prisma-routing` se omitido |
| `radius interim-interval <segundos>` | intervalo das atualizações intermediárias de contabilização: 0 (nenhuma) ou 60 a 86400; 300 se omitido |
| `authentication pap chap mschap-v2` | os métodos oferecidos; os três se omitido |
| `session-limit <n>` | no máximo tantas sessões |
| `mtu <576-1500>` | MTU e MRU do PPP; 1492 se omitido |
| `tcp-mss <536-1460>` | limita o MSS do TCP em cada sessão |
| `ac-name <nome>` | o nome do concentrador no PADO; `prisma-routing` se omitido |
| `local-user <nome> password <senha>` | um usuário local, para laboratório sem RADIUS |

`no <declaração>` desfaz cada uma; `no pppoe`, no topo do modo de configuração, remove o bloco inteiro e desliga o plano de controle PPPoE no `commit`.

O endereço de cada sessão vem do RADIUS (`Framed-IP-Address`) quando ele manda um, e do `address-pool` quando não. Para o CGNAT traduzir as sessões, os endereços que elas recebem têm de estar no `prefix-inside` de um pool, como os de qualquer assinante.

## O que o commit recusa

| Situação | Por quê |
|---|---|
| sem `interface`, sem `gateway` | o plano de controle não teria onde atender nem que endereço usar |
| sem servidor RADIUS e sem `local-user` | ninguém para autenticar |
| sem `address-pool` e sem RADIUS | ninguém para dar endereço |
| interfaces de acesso em duas placas | o PPPoE do dataplane responde de um MAC só |
| uma sub-interface com duas tags (QinQ) | o PPPoE do dataplane lê uma tag só |
| uma interface que o dataplane não tem | nada para atender nela |
| um segredo com espaço, vírgula, aspas ou `#` | quebraria a configuração gerada |

## O segredo do RADIUS

O segredo fica na configuração salva, para que um `rollback` consiga restaurá-lo, e **nunca aparece numa tela**: `show running-config`, `show configuration` e `show configuration compare` mostram `<set, not shown>`. Num `compare`, trocar o segredo aparece como uma linha removida e uma acrescentada, nenhuma das duas com o valor. A senha de um `local-user` é tratada do mesmo jeito.

## Sessões

| Comando | O que mostra ou faz |
|---|---|
| `show pppoe sessions` | uma linha por sessão: o id, o usuário, o endereço, o MAC do CPE e há quanto tempo está de pé; uma sessão ainda autenticando diz `start` no lugar do tempo |
| `show pppoe summary` | se o plano de controle PPPoE está rodando, quantas sessões estão ativas, começando e terminando, e o resumo da configuração |
| `clear pppoe session <id>` | encerra uma sessão; o id é o da primeira coluna de `show pppoe sessions`, o mesmo `Acct-Session-Id` que o RADIUS recebeu |
| `clear pppoe session user <nome>` | encerra as sessões de um usuário |
| `clear pppoe session all` | encerra todas |

Uma sessão encerrada manda PADT ao CPE e Accounting-Stop ao RADIUS; o CPE disca de novo. As interfaces que o dataplane cria para as sessões nunca entram na configuração: são assinantes discando, não algo que alguém configurou.

## Como conferir

```
prisma> show pppoe summary
prisma> show pppoe sessions
```

O resumo deve dizer `running` e contar as sessões ativas; cada assinante que discou aparece em `show pppoe sessions` com o endereço que recebeu. No seu RADIUS, cada sessão deixa um Accounting-Start, as atualizações intermediárias com os bytes da sessão e, ao cair, um Accounting-Stop.

:::info
Nesta versão o PPPoE é IPv4. IPv6, controle de banda por assinante, CoA/DM, QinQ e alta disponibilidade das sessões ainda não existem.
:::

## Ver também

* [Configuração de CGNAT](/doc/configuracao-de-cgnat-EIWRFS6OnJ) — o pool que traduz as sessões
* [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6) — o `prefix-inside` que cobre os endereços das sessões
* [Configuração de interface](/doc/configuracao-de-interface-j1q58jKWTQ) — a sub-interface com a VLAN de acesso
* [commit, rollback e save](/doc/commit-rollback-e-save-jqCB7kxPuA) — como aplicar o que foi configurado aqui
