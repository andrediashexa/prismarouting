# Configuração de PPPoE

Esta página é a referência do bloco `pppoe` e do bloco `aaa` > `radius`: a caixa termina as sessões PPPoE dos assinantes, autentica cada um no seu RADIUS, limita a banda de cada sessão e traduz o tráfego deles pelo CGNAT, como faz com um assinante atrás de uma interface `inside`. Ela diz o que cada declaração faz, o que a caixa recusa e como ler as sessões.

```
prisma> configure
prisma# [edit] aaa
prisma# [edit aaa] radius
prisma# [edit aaa / radius] server 192.0.2.10 secret <segredo> priority 1
prisma# [edit aaa / radius] server 192.0.2.11 secret <segredo> priority 2
prisma# [edit aaa / radius] exit
prisma# [edit aaa] exit
prisma# [edit] pppoe
prisma# [edit pppoe] interface ens19
prisma# [edit pppoe] gateway 100.64.0.1
prisma# [edit pppoe] address-pool 100.64.0.10-100.64.3.250
prisma# [edit pppoe] dns 8.8.8.8
prisma# [edit pppoe] exit
prisma# [edit] commit
```

O `commit` gera a configuração do plano de controle PPPoE a partir da candidata e o põe no ar. Como todo o resto, a mudança entra no `rollback`, no `commit confirmed` e na trilha de auditoria. Os arquivos que o plano de controle lê são gerados pela caixa e reescritos a cada commit; não se editam à mão.


:::warning
Toda mudança no bloco `pppoe` reinicia o plano de controle PPPoE: **todas as sessões caem** e os CPEs discam de novo. O encaminhamento dos outros assinantes, o roteamento e a VTY não são afetados. Uma mudança só no `aaa` > `radius` (um servidor a mais, outro modo, outro tempo) **não derruba ninguém**: o plano de controle relê a configuração do RADIUS com as sessões de pé. As exceções são o primeiro servidor (o PPPoE passa de usuários locais para RADIUS) e o `dynamic-authorization`, que reiniciam.

:::

## Declarações

| Declaração | O que faz |
|------------|-----------|
| `interface <nome>` | uma interface de acesso: a placa, ou uma sub-interface dela com **uma** tag VLAN (`vlan <id>` no bloco da sub-interface). Pode haver várias, todas da mesma placa |
| `gateway <endereço>` | o endereço da caixa em cada sessão (o endereço local do IPCP). Obrigatório |
| `address-pool <prefixo>` ou `<primeiro>-<último>` | endereços para quem o RADIUS não manda `Framed-IP-Address`. Até 16 |
| `dns <endereço>` | um resolvedor entregue ao CPE. Até 2 |
| `authentication pap chap mschap-v2` | os métodos oferecidos; os três se omitido |
| `session-limit <n>` | no máximo tantas sessões |
| `mtu <576-1500>` | MTU e MRU do PPP; 1492 se omitido |
| `tcp-mss <536-1460>` | limita o MSS do TCP em cada sessão |
| `rate-limit default <download>/<upload>` | o limite de banda de uma sessão para a qual o RADIUS não mandou nenhum, em kbit/s ou com K, M ou G (`20M/10M`); sem ele, essa sessão não tem limite |
| `ac-name <nome>` | o nome do concentrador no PADO; `prisma-routing` se omitido |
| `local-user <nome> password <senha>` | um usuário local, para laboratório sem RADIUS |

`no <declaração>` desfaz cada uma; `no pppoe`, no topo do modo de configuração, remove o bloco inteiro e desliga o plano de controle PPPoE no `commit`.

O RADIUS não fica mais dentro do `pppoe`: está no `aaa` > `radius`, abaixo. Digitar `radius ...` dentro do `pppoe` é recusado com o lugar novo. Uma caixa que veio da 0.154.0 com `radius server ...` no bloco `pppoe` não precisa de nada: ao carregar a configuração salva, cada uma dessas linhas vira a equivalente no `aaa` > `radius`, com o mesmo segredo, na mesma ordem (o primeiro servidor recebe `priority 1`), e a caixa avisa linha por linha e regrava o arquivo.

## RADIUS: `aaa` > `radius`

| Declaração | O que faz |
|------------|-----------|
| `server <endereço> secret <segredo> [auth-port <n>] [acct-port <n>] [role auth\|acct\|both] [priority <1-255>]` | um servidor. Portas 1812 e 1813 se omitidas. `role auth` só autentica, `role acct` só recebe contabilização, `both` (o padrão) os dois. `priority`: o menor é tentado primeiro; sem ela, o servidor vai depois do último. Até 8 |
| `mode failover` ou `mode round-robin` | como os servidores são usados; `failover` se omitido |
| `timeout <1-60>` | segundos que um pedido espera resposta; 3 se omitido |
| `retries <0-10>` | reenvios ao mesmo servidor antes de passar ao próximo; 1 se omitido |
| `dead-time <1-3600>` | segundos que um servidor que não respondeu fica de fora; 30 se omitido |
| `nas-identifier <nome>` | o NAS-Identifier enviado; `prisma-routing` se omitido |
| `interim-interval <segundos>` | atualizações intermediárias de contabilização: 0 (nenhuma) ou 60 a 86400; 300 se omitido |
| `dynamic-authorization listen <endereço> [port <n>]` | onde a caixa recebe Disconnect e CoA (RFC 5176); porta 3799 se omitida |
| `dynamic-authorization client <endereço> secret <segredo>` | um servidor que pode mandar Disconnect e CoA, com o segredo dele. Até 16 |
| `rate-profile <nome> down <taxa> up <taxa>` | as taxas que um `sub-qos-policy` do `Cisco-AVPair` com esse nome quer dizer (abaixo). Até 64 |

**`failover`**: todo pedido vai ao servidor de menor `priority`. Se ele não responde em `timeout` segundos, `retries` vezes, o pedido vai ao seguinte, e o que não respondeu fica de fora por `dead-time` segundos: o próximo assinante já vai direto ao seguinte. Com os padrões, um servidor morto custa 6 segundos a **um** assinante e nenhum aos outros. Passado o `dead-time`, ele volta a ser o primeiro. Dois servidores com a mesma `priority` dividem a carga entre si.

**`round-robin`**: cada pedido vai ao servidor com menos pedidos e sessões em andamento, o que alterna entre servidores ociosos e equilibra os ocupados. Não é um rodízio estrito: é o que o plano de controle faz.

A contabilização segue a mesma escolha, entre os servidores que recebem contabilização; os Interim e o Stop de uma sessão vão ao servidor que recebeu o Start dela, enquanto ele responder. Sem nenhum servidor de contabilização (todos `role auth`), a contabilização fica desligada, e o `show pppoe summary` diz isso.

Uma sessão nunca é derrubada porque a contabilização ficou sem resposta.

```
prisma# [edit aaa / radius] server 192.0.2.10 secret <segredo> priority 1
prisma# [edit aaa / radius] server 192.0.2.11 secret <segredo> priority 2
prisma# [edit aaa / radius] server 192.0.2.20 secret <segredo> role acct priority 3
prisma# [edit aaa / radius] dynamic-authorization listen 10.0.0.1
prisma# [edit aaa / radius] dynamic-authorization client 192.0.2.10 secret <segredo>
```

## Limite de banda por assinante

O limite de cada sessão vem do RADIUS, no Access-Accept, em dois atributos do próprio Prisma (fornecedor 67033, o número IANA do produto):

| Atributo | Número | Tipo | O que é |
|----------|--------|------|---------|
| `Prisma-Rate-Limit-Down` | 1 | inteiro | download (da caixa para o assinante), em kbit/s |
| `Prisma-Rate-Limit-Up`   | 2 | inteiro | upload (do assinante para a caixa), em kbit/s |

0, ou o atributo ausente, é sem limite naquele sentido. `20000` é 20 Mbit/s. O máximo é 10 Gbit/s.

O `Cisco-AVPair` também é lido, quando o pacote não traz nenhum atributo do Prisma (se trouxer os dois, vale o do Prisma):

| `Cisco-AVPair` | O que limita |
|----------------|--------------|
| `lcp:interface-config=rate-limit output <bps> <burst> <burst-máx> conform-action transmit exceed-action drop` | o download, em bits por segundo |
| `lcp:interface-config=rate-limit input <bps> ...` (o mesmo formato) | o upload: `input` é o que a caixa recebe do assinante |
| `lcp:interface-config#1=...` | o mesmo, na forma numerada |
| `ip:sub-qos-policy-out=<nome>` ou `subscriber:sub-qos-policy-out=<nome>` | o download do `rate-profile` com esse nome |
| `ip:sub-qos-policy-in=<nome>` ou `subscriber:sub-qos-policy-in=<nome>` | o upload do `rate-profile` com esse nome |

Uma política só traz um nome; a taxa que ele quer dizer é configurada na caixa, em `aaa` > `radius`:

```
prisma# [edit aaa / radius] rate-profile PLANO_100M down 100M up 50M
```

Um `Cisco-AVPair` de taxa fora desses formatos (um `rate-limit` com `access-group`, sem as ações, uma taxa com sufixo) faz **nenhuma** das taxas Cisco do pacote valer, e a caixa registra o motivo: metade de um plano não é um plano. Um nome sem `rate-profile` deixa aquele sentido sem limite, também registrado. O `Mikrotik-Rate-Limit` não é lido.

O dicionário para instalar no seu servidor RADIUS sai da própria caixa:

```
prisma> show aaa radius dictionary
```

No FreeRADIUS, salve o que esse comando imprime como `/usr/share/freeradius/dictionary.prisma` e acrescente `$INCLUDE /usr/share/freeradius/dictionary.prisma` ao arquivo `/etc/freeradius/3.0/dictionary`. No IXC ou em outro sistema, cadastre o fornecedor 67033 e os dois atributos da tabela.

O limite é um policer no dataplane, na interface da própria sessão: o que passa do limite é descartado. Uma sessão sem limite do RADIUS recebe o `rate-limit default` do bloco `pppoe`, se houver.

## Disconnect e CoA

Com `dynamic-authorization`, o seu RADIUS (ou o IXC) pode, sem o assinante discar de novo:

* **derrubar uma sessão** com um Disconnect-Request, identificando-a por `User-Name` ou por `Acct-Session-Id`. A caixa responde Disconnect-ACK, a sessão cai e o Stop sai com `Acct-Terminate-Cause = Admin-Reset`;
* **trocar o limite de banda** com um CoA-Request com `Prisma-Rate-Limit-Down` e/ou `-Up`, ou com um `Cisco-AVPair` de taxa: vale na hora, na sessão de pé. Um sentido ausente fica como estava; 0 tira o limite naquele sentido;
* **dar um prazo à sessão** com `Session-Timeout` num CoA-Request: ela cai quando ele vence.

Um CoA-Request com um atributo que a caixa não aplica (um `Mikrotik-Rate-Limit`, um `Filter-Id`), uma taxa acima de 10 Gbit/s, um `Cisco-AVPair` de taxa mal formado ou um `sub-qos-policy` sem `rate-profile` é recusado inteiro com CoA-NAK, e nada muda. Um pedido de um endereço que não é `client`, ou com o segredo errado, não recebe resposta nenhuma.

O endereço de cada sessão vem do RADIUS (`Framed-IP-Address`) quando ele manda um, e do `address-pool` quando não. Para o CGNAT traduzir as sessões, os endereços que elas recebem têm de estar no `prefix-inside` de um pool, como os de qualquer assinante.

## O que o commit recusa

| Situação | Por quê |
|----------|---------|
| sem `interface`, sem `gateway` | o plano de controle não teria onde atender nem que endereço usar |
| sem servidor em `aaa` > `radius` e sem `local-user` | ninguém para autenticar |
| sem `address-pool` e sem RADIUS | ninguém para dar endereço |
| servidores em `aaa` > `radius`, nenhum que autentique (todos `role acct`) | ninguém para autenticar |
| `dynamic-authorization listen` sem `client`, ou `client` sem `listen` | ninguém poderia assinar um pedido, ou não haveria onde recebê-lo |
| interfaces de acesso em duas placas | o PPPoE do dataplane responde de um MAC só |
| uma sub-interface com duas tags (QinQ) | o PPPoE do dataplane lê uma tag só |
| uma interface que o dataplane não tem | nada para atender nela |
| um segredo com espaço, vírgula, aspas ou `#` | quebraria a configuração gerada |

## Os segredos do RADIUS

O segredo de cada servidor e de cada `client` fica na configuração salva, para que um `rollback` consiga restaurá-lo, e **nunca aparece numa tela**: `show running-config`, `show configuration` e `show configuration compare` mostram `<set, not shown>`. Num `compare`, trocar o segredo aparece como uma linha removida e uma acrescentada, nenhuma das duas com o valor. A senha de um `local-user` é tratada do mesmo jeito.

## Sessões

| Comando | O que mostra ou faz |
|---------|---------------------|
| `show pppoe sessions` | uma linha por sessão: o id, o usuário, o endereço, o limite (download/upload, `-` sem limite) e há quanto tempo está de pé; uma sessão ainda autenticando diz `start` no lugar do tempo |
| `show pppoe session <id>` | uma sessão inteira: o MAC do CPE, o tráfego, o limite e, para cada sentido limitado, quantos pacotes o policer deixou passar e quantos descartou |
| `show pppoe summary` | se o plano de controle PPPoE está rodando, quantas sessões estão ativas, começando e terminando, cada servidor RADIUS (com `failed` se o plano de controle o deu como fora) e o resumo da configuração |
| `clear pppoe session <id>` | encerra uma sessão; o id é o da primeira coluna de `show pppoe sessions`, o mesmo `Acct-Session-Id` que o RADIUS recebeu |
| `clear pppoe session user <nome>` | encerra as sessões de um usuário |
| `clear pppoe session all` | encerra todas       |

Uma sessão encerrada manda PADT ao CPE e Accounting-Stop ao RADIUS; o CPE disca de novo. As interfaces que o dataplane cria para as sessões nunca entram na configuração: são assinantes discando, não algo que alguém configurou.

## Como conferir

```
prisma> show pppoe summary
prisma> show pppoe sessions
```

O resumo deve dizer `running` e contar as sessões ativas; cada assinante que discou aparece em `show pppoe sessions` com o endereço que recebeu. No seu RADIUS, cada sessão deixa um Accounting-Start, as atualizações intermediárias com os bytes da sessão e, ao cair, um Accounting-Stop.


:::info
Nesta versão o PPPoE é IPv4. IPv6, QinQ e alta disponibilidade das sessões ainda não existem. O limite de banda é por descarte (policer), não por fila (shaper).

:::

## Ver também

* [Configuração de CGNAT](/doc/configuracao-de-cgnat-EIWRFS6OnJ) — o pool que traduz as sessões
* [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6) — o `prefix-inside` que cobre os endereços das sessões
* [Configuração de interface](/doc/configuracao-de-interface-j1q58jKWTQ) — a sub-interface com a VLAN de acesso
* [commit, rollback e save](/doc/commit-rollback-e-save-jqCB7kxPuA) — como aplicar o que foi configurado aqui
