# Configuração de BGP

Como configurar sessões BGP pelo `hexash`: vizinhos, peer groups, famílias de endereço e o que o equipamento recusa antes de mandar para o roteamento. Para quem está subindo um trânsito, um peering ou anunciando os prefixos públicos do CGNAT.

Entra-se no BGP a partir do modo de configuração:

```
hexarouting> configure
hexarouting# [edit] router bgp 65000
hexarouting# [edit router bgp 65000]
```

`?` ou TAB listam o que vale ali dentro. Dentro de uma família de endereço, listam outro conjunto — são gramáticas diferentes, porque a mesma palavra significa coisas diferentes nos dois lugares.

# Uma sessão do começo ao fim

```
router bgp 65000
 bgp router-id 10.0.0.1
 neighbor 200.150.0.2 remote-as 65001
 neighbor 200.150.0.2 description transito-primario
 address-family ipv4 unicast
  neighbor 200.150.0.2 activate
  network 200.150.8.0/22
 exit-address-family
 exit
```

`remote-as` é o que cria a sessão: sem ele o FRR recusa qualquer outra sentença daquele vizinho, e o equipamento recusa antes, dizendo qual vizinho está faltando.

`activate` é o que faz a sessão **carregar** aquela família. Uma sessão que sobe e não carrega nada é uma sessão Established e muda.

# O que um vizinho entende

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `remote-as` | `<1-4294967295>` | o AS do outro lado; é isto que cria a sessão |
| `description` | `<texto>` | para que serve a sessão, para quem ler depois |
| `password` | `<texto>` | senha MD5 da sessão; nunca é impressa de volta |
| `update-source` | `<interface>` | de onde a sessão sai |
| `ebgp-multihop` | `<1-255>` | saltos até um vizinho que não está numa rede conectada |
| `timers` | `<keepalive> <hold>` | temporizadores da sessão |
| `maximum-prefix` | `<n> [<pct>] [restart <min>]` | derruba a sessão acima desse número de prefixos |
| `next-hop-self` |           | anuncia esta caixa como próximo salto |
| `bfd`   |           | acompanha a sessão por BFD, para a falha ser de segundos e não de minutos |
| `shutdown` |           | segura a sessão derrubada, mantendo a configuração |
| `peer-group` | `<nome>`  | põe o vizinho num peer group |
| `capability extended-nexthop` |     | rotas IPv4 por cima de uma sessão IPv6; é o que uma sessão sem endereço no enlace usa |

Dentro de `address-family`, o mesmo vizinho entende `activate`, `route-map`, `prefix-list`, `soft-reconfiguration inbound`, `remove-private-AS`, `default-originate`, `maximum-prefix` e `next-hop-self`. Os dois últimos aparecem nas duas listas de propósito: valem por família, e o roteamento aceita os dois lugares.

Essa tabela é o que o `hexash` **confere**. O BGP do roteamento entende muito mais do que isso, e o que não está aqui é repassado sem leitura - o que é legítimo, e é por isso que sai marcado com `<- not checked` no `show this` e no `show configuration`. Vale reparar que `soft-reconfiguration inbound` está na lista da família de endereço e não na lista de cima: digitada direto dentro de `router bgp`, ela sai marcada.

# Sessão sem endereço no enlace (BGP unnumbered)

Um trânsito ou um peering com um vizinho diretamente conectado não precisa de um /30 no enlace. A sessão sobe pelo endereço de enlace local que os dois lados já têm, e o vizinho é a **interface**:

```
router bgp 65000
 neighbor vppwan0 interface remote-as external
 address-family ipv4 unicast
  neighbor vppwan0 activate
 exit-address-family
 exit
```

`remote-as` vai na mesma linha, e aceita `external`, `internal` ou o número do AS. `external` é o caso comum: qualquer AS diferente do seu serve, o que é exatamente o que se quer num enlace ponto a ponto onde o outro lado é quem diz quem é.

A forma com peer group é a mesma:

```
 neighbor UPLINKS peer-group
 neighbor UPLINKS remote-as external
 neighbor vppwan0 interface peer-group UPLINKS
```

O nome ali é o nome pelo qual **o roteamento** conhece a interface — o mesmo que vale em `passive-interface` e nas estrofes de OSPF e IS-IS. Uma interface do `linux_cp` atende por mais de um nome, e quem escrever o nome do dataplane recebe um aviso dizendo qual é o outro:

```
warning: neighbor host-veth-vpp interface remote-as external: FRR knows this
interface as vppwan0, not host-veth-vpp: written this way the session has
nothing to run over
```

:::warning
O enlace precisa carregar os anúncios de roteador do vizinho (router advertisement) para que o roteamento descubra o endereço de enlace local do outro lado. É assim que uma sessão sem endereço encontra com quem falar; sem isso a sessão fica em Idle com AS 0, mesmo com a configuração inteira certa. Confira com `show bgp summary`.

Deste lado não há nada a habilitar: o que chega pela interface é entregue ao roteamento. Se a sessão ficar em Idle com AS 0, o que falta está do outro lado ou no caminho - um vizinho que não anuncia, ou um enlace que não entrega o multicast de enlace local. `show bgp neighbors <interface>` mostra o endereço aprendido quando ele chega, e `(unspec)` enquanto não chegou.
:::

# Peer groups

Quarenta peerings não são quarenta estrofes. Declara-se o grupo, dá-se o AS a ele, e os membros herdam:

```
router bgp 65000
 neighbor IX peer-group
 neighbor IX remote-as 65100
 neighbor 200.150.9.10 peer-group IX
 neighbor 200.150.9.11 peer-group IX
 exit
```

Ao completar `neighbor` com TAB, o equipamento mostra os vizinhos que existem com o AS do outro lado ao lado, e marca quais nomes são grupos — um grupo não é um roteador, e procurar por ele como se fosse custa tempo.

# O que é recusado antes de chegar ao roteamento

São recusas de **forma**, não de funcionamento: se o vizinho vai responder não está na configuração, e o equipamento não finge saber.

* **vizinho sem** `**remote-as**` — o FRR recusa qualquer outra sentença dele; numa sessão sem endereço o FRR aceita e deixa o vizinho parado em Idle com AS 0 para sempre, que é pior, então a recusa vale para as duas formas;
* **sessão sem endereço apontada para uma interface que a caixa não tem** — o FRR aceita, o vizinho fica em Idle e nada mais é dito sobre ele;
* **peer group que ninguém declarou** — `neighbor X peer-group IX` sem o `neighbor IX peer-group` que cria o grupo;
* **membro cujo grupo também não tem AS** — o membro herda o AS do grupo, então um vizinho sem AS próprio está certo quando o grupo tem um; o que não pode é nenhum dos dois ter;
* `**update-source**` **apontando para uma interface que a caixa não tem** — o FRR aceita, a sessão sai de lugar nenhum e nunca sobe, e a configuração continua lendo bem.

## Uma route-map citada e nunca definida

```
error: no route-map called 'RM-TRANSITO-IN' is defined, and FRR denies
    everything this statement governs until one is; define it, or write
    'route-map RM-TRANSITO-IN permit 10' to let everything through
    neighbor 200.150.0.2 route-map RM-TRANSITO-IN in
```

Esta é a recusa que evita a sessão silenciosa. O roteamento **aceita** apontar um vizinho para uma route-map que não existe: a sessão sobe, fica Established, e não carrega rota nenhuma. Três semanas depois `show bgp summary` mostra uma sessão de pé com zero prefixos, e a configuração continua lendo perfeitamente bem — a política está lá, escrita, apontando para o nada.

A recusa vale para as três formas que foram medidas negando tudo: `neighbor <endereço> route-map`, `redistribute ... route-map` e `network <prefixo> route-map`. A mensagem **nomeia a route-map que falta** e diz as duas saídas: definir a política, ou escrever a cláusula que deixa tudo passar.

:::info
`call` para uma route-map inexistente **não** é recusado, e a diferença foi medida e não deduzida: o roteamento pula uma chamada que não resolve e a cláusula segue adiante, então o prefixo continua sendo anunciado. É uma linha morta, não uma linha perigosa:

```
warning: no route-map called 'RM-COMUNIDADES' is defined; FRR skips a call it
    cannot resolve, so this line does nothing
    call RM-COMUNIDADES
```

O contrário — uma route-map definida que ninguém usa — também é só aviso. Configuração morta é desarrumação, não perigo, e é exatamente o que fica na caixa entre dois commits de quem monta a política pela política primeiro.

:::

:::info
Numa caixa que **já está** nesse estado a sentença sai como aviso e o commit passa. É de propósito: a mesma verificação roda quando a caixa carrega a configuração salva no boot, e ali ela recusaria o arquivo **inteiro** — uma caixa cuja configuração guardada tem a referência subiria sem nada. Recusa-se o commit que **introduz** a referência; a caixa que já a tem continua podendo ser trabalhada.

:::

# O que é avisado e não recusado

## Uma sessão eBGP sem `maximum-prefix`

```
warning: this eBGP session has no 'maximum-prefix': a peer that leaks a full
    table fills this box and nothing here stops it. 'neighbor 200.150.0.2
    maximum-prefix 10000' tears the session down instead of the box
    neighbor 200.150.0.2 remote-as 65001
```

Um vizinho que vaza a tabela inteira dentro de um equipamento dimensionado para alguns milhares de rotas derruba o equipamento, e o BGP **não impede nada por padrão**: o roteamento aceita cada prefixo que o vizinho mandar até a memória acabar. `maximum-prefix` é a única sentença que derruba a sessão em vez do equipamento, e até aqui nada nesta caixa mencionava que ela estava faltando.

É **aviso e nunca recusa**, e é a decisão certa: um route server, um cliente cuja tabela se conhece pequena e um enlace de laboratório rodam sem limite de propósito. A escolha é de quem opera.

:::info
O aviso **fala uma vez**: ele sai pelos vizinhos que **esta mudança** coloca nesse estado. Uma caixa com três trânsitos deliberadamente sem limite carregaria três avisos em todo commit que fizesse pelo resto da vida, e alarme que está sempre ligado é alarme que ninguém lê.

Sessões **iBGP** ficam de fora por decisão, não por esquecimento: do outro lado está um equipamento do mesmo operador, a tabela é a que este AS já decidiu carregar, e um limite ali dispara quando a rede cresce e não quando alguém vaza. Vale para `remote-as` igual ao AS da própria estrofe e para `remote-as internal`.

Um `maximum-prefix` no **peer group** cobre todos os membros dele — é onde quem tem seis trânsitos põe o limite uma vez só. Já `maximum-prefix-out` não cobre: aquela sentença limita o que **esta** caixa anuncia, que é outro assunto.

:::

# A senha

`neighbor <endereço> password <senha>` é o segredo em si, não um hash dele, e ele abre a sessão **no outro roteador** também. Por isso ele não é impresso de volta:

```
 neighbor 200.150.0.2 password <set, not shown>
```

Vale tanto em `show running-config` quanto em `show this` dentro do bloco. Isso é só a exibição: a configuração salva e as revisões de rollback guardam o valor real, senão um rollback não conseguiria restaurar a sessão — e é por isso que esses arquivos não são legíveis por qualquer um.


# Operar a sessão: o que se digita quando algo está errado

## `show bgp summary`

```
hexarouting> show bgp summary
  Neighbour          AS       State          Prefixes  Up
  200.150.0.2        65001    Established    412       03:11:55
  200.150.1.2        65002    Established    398       01:02:37

  2 session(s), 2 established
  all 810 BGP prefix(es) are in the forwarding table
```

A última linha é a que existe só aqui. Uma sessão Established carregando 412 prefixos é uma resposta verdadeira para **outra** pergunta: quem encaminha é a tabela do dataplane, e um prefixo que o roteamento tem e o dataplane não recebeu não move pacote nenhum. Quando os dois discordam:

```
  3 of 412 BGP prefix(es) are NOT in the forwarding table
    200.150.9.0/24
    ...
    'show route verbose' compares the two tables in full
```

A contagem é do total, não por sessão. A tabela de encaminhamento não guarda por qual sessão a rota chegou, e pôr um número por vizinho ali seria pôr um número que não se sustenta.

## Rotas que o equipamento não está guardando

`show bgp neighbor <endereço> received-routes` mostra o que o vizinho mandou **antes** da política de entrada — e isso o equipamento só tem se mandaram guardar. Sem `soft-reconfiguration inbound` a tabela sai vazia, e "não recebi rota" e "não estou guardando" são respostas diferentes para se olhar durante um incidente:

```
error: neighbour 200.150.0.2 does not keep what it received, so this would
print an empty table either way
    add 'neighbor 200.150.0.2 soft-reconfiguration inbound' under the address
    family; it costs memory
    'show bgp neighbor 200.150.0.2 routes' shows what survived inbound policy,
    which the box does have
```

`clear bgp <endereço> soft in` recusa pelo mesmo motivo e diz o mesmo, com a saída que sempre funciona: reiniciar a sessão e pegar as rotas de novo.

## `clear`

```
hexarouting> clear bgp 200.150.0.2
hexarouting> clear bgp 200.150.0.2 soft in
```

É operacional: não muda nada em disco. Exige conta de administrador, porque reiniciar sessão é uma queda de propósito.

## Manutenção sem derrubar tráfego

```
router bgp 65000
 bgp graceful-shutdown
 exit
```

RFC 8326. Antes de uma manutenção, o tráfego **escoa** em vez de cair: os anúncios saem marcados com a comunidade de graceful shutdown, e as rotas que esta caixa recebeu passam a ter preferência local zero — ou seja, ela deixa de ser o caminho preferido enquanto quem tiver outro caminho assume.

`no bgp graceful-shutdown` desfaz as duas coisas. Uma chave de manutenção que não volta não é uma chave de manutenção.

:::info
Confira pelo efeito, não pelo texto: `show bgp <prefixo>` no vizinho deve mostrar a comunidade `gracefulShutdown`, e `show bgp <prefixo>` aqui deve mostrar preferência local 0. Se a configuração tem a linha e o vizinho não vê a comunidade, a sessão não reanunciou ainda.
:::


# Como conferir

* `show running-config` mostra a estrofe BGP como o equipamento a lê de volta, com a senha omitida;
* `show this` dentro do bloco mostra só o que está naquele nível;
* `show bgp summary` mostra as sessões e quantos prefixos cada uma trouxe;
* `show route` mostra o que **encaminha** — uma rota que o BGP aprendeu e não está aí não move pacote nenhum.

# Ver também

* [Roteamento, SSH, usuários e SNMP](/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m) — prefix-lists, políticas e o prefixo público de um pool
* [commit, rollback e save](/doc/commit-rollback-e-save-jqCB7kxPuA) — como uma mudança é aplicada e desfeita
