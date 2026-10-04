# Configuração de BGP

Como configurar sessões BGP pelo `prismash`: vizinhos, peer groups, famílias de endereço e o que o equipamento recusa antes de mandar para o roteamento. Para quem está subindo um trânsito, um peering ou anunciando os prefixos públicos do CGNAT.

Entra-se no BGP a partir do modo de configuração:

```
prisma> configure
prisma# [edit] router bgp 65000
prisma# [edit router bgp 65000]
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

# O que o bloco `router bgp` entende

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `bgp router-id` | `<A.B.C.D>` | o identificador desta caixa no BGP |
| `bgp log-neighbor-changes` |           | registra toda mudança de estado de sessão |
| `bgp graceful-shutdown` |           | escoa o tráfego antes de uma manutenção (RFC 8326; seção própria abaixo) |
| `bgp default ipv4-unicast` |           | ativa IPv4 unicast em todo vizinho sem precisar de `activate` |
| `bgp bestpath as-path multipath-relax` |           | trata como iguais caminhos de mesmo comprimento vindos de AS diferentes |
| `neighbor <vizinho> ...` |           | uma sessão; a tabela seguinte |
| `address-family ipv4 unicast` ou `ipv6 unicast` |           | entra na família de endereço; sai-se com `exit-address-family` ou `exit` |
| `network` | `<prefixo>` | um prefixo que esta caixa origina; só é anunciado se estiver na tabela de rotas |
| `redistribute` | `connected`, `static`, `ospf` ou `isis` | injeta no BGP rotas de outra origem |

No nível de `address-family` valem `neighbor` (a lista por família, abaixo), `network`, `redistribute` e as saídas `exit-address-family` e `exit`.

# O que um vizinho entende

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `remote-as` | `<1-4294967295>`, `internal` ou `external` | o AS do outro lado; é isto que cria a sessão. `internal` é o seu próprio AS, `external` qualquer outro |
| `description` | `<texto>` | para que serve a sessão, para quem ler depois |
| `password` | `<texto>` | senha MD5 da sessão; nunca é impressa de volta |
| `update-source` | `<endereço>` ou `<interface>` | de onde a sessão sai |
| `ebgp-multihop` | `<1-255>` | saltos até um vizinho que não está numa rede conectada |
| `timers` | `<keepalive> <hold>` | temporizadores da sessão |
| `maximum-prefix` | `<n> [<pct>] [restart <min>]` | derruba a sessão acima desse número de prefixos |
| `next-hop-self` |           | anuncia esta caixa como próximo salto |
| `bfd`   | `[profile <nome>]` | acompanha a sessão por BFD, para a falha ser de milissegundos e não de minutos; com `profile`, nos tempos daquele perfil (veja [BFD](/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m), seção BFD) |
| `shutdown` |           | segura a sessão derrubada, mantendo a configuração |
| `peer-group` | `<nome>`  | põe o vizinho num peer group |
| `capability extended-nexthop` |           | rotas IPv4 por cima de uma sessão IPv6; é o que uma sessão sem endereço no enlace usa |

Dentro de `address-family`, o mesmo vizinho entende `activate`, `route-map`, `prefix-list`, `soft-reconfiguration inbound`, `remove-private-AS`, `default-originate`, `advertise`, `maximum-prefix` e `next-hop-self`. Os dois últimos aparecem nas duas listas de propósito: valem por família, e o roteamento aceita os dois lugares. `default-originate policy <política>` e `advertise` estão em [Política de roteamento](/doc/politica-de-roteamento-policy-options-DULDDxEALE); na configuração em vigor o `advertise` aparece como `advertise-map`, a forma em que o roteamento o guarda.

Essa tabela é o que o `prismash` **confere**. O BGP do roteamento entende muito mais do que isso, e o que não está aqui é repassado sem leitura - o que é legítimo, e é por isso que sai marcado com `<- not checked` no `show this` e no `show configuration`. Vale reparar que `soft-reconfiguration inbound` está na lista da família de endereço e não na lista de cima: digitada direto dentro de `router bgp`, ela sai marcada.

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
warning: FRR knows this interface as vppwan0, not host-veth-vpp: written this
    way the session has nothing to run over
    neighbor host-veth-vpp interface remote-as external
```

Repare na ordem: **o motivo vem primeiro e a sentença recusada vem por último, numa linha só dela**. Não é estilo. A sentença é o que você vai procurar no `show configuration` e digitar de novo, então ela tem de sair inteira e num pedaço só; o motivo é para ler, então ele é quebrado na largura do console. Com os dois na mesma linha, um nome de route-map comprido ou um vizinho IPv6 punham a linha entre 104 e 264 colunas, e o que se perdia na dobra era metade de cada um.


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

# Política de roteamento: `policy-options`

A política é escrita uma vez, com nome, e aplicada em cada vizinho como `import` (o que entra) ou `export` (o que sai). O formato é o do Junos. As listas nomeadas e as políticas ficam em `policy-options`:

```
prisma# [edit] policy-options
prisma# [edit policy-options] prefix-list OUTRO-AS
prisma# [edit policy-options / prefix-list OUTRO-AS] 198.51.100.0/24
prisma# [edit policy-options / prefix-list OUTRO-AS] exit
prisma# [edit policy-options] community NOSSA members 65000:100
prisma# [edit policy-options] policy-statement DO-TRANSITO
prisma# [edit policy-options / policy-statement DO-TRANSITO] term sem-outro-as
prisma# [edit policy-options / policy-statement DO-TRANSITO / term sem-outro-as] from prefix-list OUTRO-AS
prisma# [edit policy-options / policy-statement DO-TRANSITO / term sem-outro-as] then reject
```

O que cada nível de `policy-options` aceita:

| Nível | Comando | O que faz |
|-------|---------|-----------|
| `policy-options` | `prefix-list <nome>` | cria ou edita uma lista de redes e entra nela; dentro, uma rede por linha (`<rede>/<tamanho>`, sem bits de host) e `no <rede>` para tirar |
| `policy-options` | `community <nome> members <asn:valor>...` | um conjunto nomeado de communities; digitar de novo o mesmo nome substitui |
| `policy-options` | `as-path <nome> <expressão>` | uma expressão regular de AS-path com nome |
| `policy-options` | `policy-statement <nome>` | cria ou edita uma política e entra nela |
| `policy-options` | `no <declaração>` | remove uma lista, uma community, um as-path ou uma política |
| `policy-statement` | `term <nome>` | cria ou edita um term e entra nele |
| `policy-statement` | `no term <nome>` | remove o term inteiro |
| `term` | `from ...`, `set ...`, `then ...` | as três partes, na tabela abaixo |
| `term` | `no <declaração>` | remove um `from`, um `set` ou o `then` |

Um nome — de lista, community, as-path ou política — tem letras, números, `.`, `_` e `-`, até 32 caracteres. Um `then` novo substitui o anterior do mesmo term: vale a última palavra dita.

Cada `term` é avaliado na ordem em que foi digitado, e tem três partes:

| Parte | O que diz |
|-------|-----------|
| `from` | o que a rota precisa casar: `prefix-list <nome>`, `route-filter <rede> exact\|orlonger\|longer\|upto /<n>`, `community <nome>`, `as-path <nome>`, `protocol bgp\|ospf\|isis\|static\|direct\|kernel`, `tag <n>`, `metric <n>`. Várias do mesmo tipo valem como "ou"; de tipos diferentes, como "e". Sem `from`, o term casa com tudo |
| `set` | o que muda na rota: `local-preference <n>`, `community add\|set\|delete <nome>`, `as-path-prepend <asn>...`, `metric <n>`, `tag <n>`, `next-hop <endereço>` |
| `then` | como o term termina: `accept` ou `reject` param a avaliação; `next term`, ou nenhum `then`, aplica os `set` e segue para o próximo term |

**Uma rota que nenhum term decidiu é aceita**, no `import` e no `export`, como no Junos. Para rejeitar o resto, termine com um term sem `from` e com `then reject`.

A política é aplicada dentro da família de endereço do BGP, por vizinho ou peer group:

```
router bgp 65000
 address-family ipv4 unicast
  neighbor 10.0.0.2 import DO-TRANSITO
  neighbor 10.0.0.2 export PARA-O-TRANSITO
 exit-address-family
```

A caixa traduz cada política aplicada para o que o roteamento entende. Na configuração em vigor, o vizinho aparece com `route-map PO-DO-TRANSITO-IN in`. Os nomes começados por `PO-` são dessa tradução: uma `route-map` ou uma `prefix-list` digitada à mão com esse começo é recusada.

Medido num laboratório com dois enlaces para o mesmo trânsito e um terceiro AS atrás dele:

* o `import` com `then reject` tirou do enlace o prefixo do outro AS;
* `set local-preference 200` num enlace fez dele o caminho escolhido, e o dataplane passou a encaminhar só por ele;
* o `export` com `as-path-prepend` e `community add` chegou ao vizinho assim;
* sem as linhas de `import` e `export`, nada disso aconteceu.

## Conferindo uma política: `show policy` e `test policy`

`show policy` lista as políticas, quantos terms cada uma tem e onde o roteamento em vigor as aplica. Com o nome, mostra os terms como foram digitados:

```
prisma> show policy
policy-statement UP
  terms: 2
  applied: neighbor 10.0.0.2 import, ospf export
```

`test policy <política> <prefixo>` mostra o que a política faz com uma rota para aquele prefixo, term por term, antes de você aplicá-la em qualquer lugar:

```
prisma> test policy UP 198.51.100.0/23
  term 10: no match
      the prefix is in none of its lists or filters
  term 20: reject
      matches
result: reject, by term 20
```

O teste parte só do prefixo. Uma condição que o prefixo sozinho não responde - community, as-path, protocolo, tag, métrica - não é adivinhada: o term aparece como "decide, se a rota tiver aquilo", e o teste segue como se não tivesse, para você ver os dois caminhos. Quando nenhum term decide, a resposta diz os dois padrões: aceito num vizinho BGP, não exportado para OSPF ou IS-IS.

Em modo de configuração, `show policy` olha a candidata, ou seja, o que você está escrevendo e ainda não aplicou. O `test policy` existe só no modo operacional e testa a política em vigor: para testar uma mudança, faça o `commit` e teste em seguida, ou leia a candidata com `show policy`.

## Exportando para OSPF e IS-IS

A mesma política exporta rotas para dentro do OSPF ou do IS-IS. Dentro do `router ospf` ou do `router isis`, `export <política>` redistribui o que ela aceita, e só isso:

```
prisma# [edit] policy-options
prisma# [edit policy-options] prefix-list LOOPBACKS
prisma# [edit policy-options / prefix-list LOOPBACKS] 10.255.0.0/16
prisma# [edit policy-options / prefix-list LOOPBACKS] exit
prisma# [edit policy-options] policy-statement PARA-O-OSPF
prisma# [edit policy-options / policy-statement PARA-O-OSPF] term estaticas
prisma# [edit policy-options / policy-statement PARA-O-OSPF / term estaticas] from protocol static
prisma# [edit policy-options / policy-statement PARA-O-OSPF / term estaticas] set metric 77
prisma# [edit policy-options / policy-statement PARA-O-OSPF / term estaticas] then accept
prisma# [edit policy-options / policy-statement PARA-O-OSPF / term estaticas] top
prisma# [edit] router ospf
prisma# [edit router ospf] export PARA-O-OSPF
```

**Numa exportação para OSPF ou IS-IS, uma rota que nenhum term aceitou não é exportada**, como no Junos. É o contrário do BGP, onde o que ninguém decidiu passa: aqui, só entra no protocolo o que a política aceitou de propósito. `from protocol` diz de onde a rota veio - `static`, `direct` (as redes das interfaces), `kernel`, `bgp`, ou o outro protocolo interno; um term sem `from protocol` vale para qualquer origem.

`no export <política>` desfaz. No IS-IS a exportação vale para IPv4 e IPv6 e para os dois níveis.

**O que só existe numa rota BGP é recusado numa exportação para OSPF ou IS-IS:** `from community`, `from as-path`, `set local-preference`, `set community`, `set as-path-prepend` e `set next-hop`. Um mapa que ignorasse em silêncio a condição de um term exportaria justamente o que aquele term foi escrito para segurar.

Medido num laboratório com uma adjacência OSPF e duas rotas estáticas: a política que aceitava só uma delas, com `set metric 77`, fez o vizinho receber aquela rota com custo 77, e só ela. Com `redistribute static` no lugar da política, as duas chegaram, com o custo padrão.

## Route-maps e listas, a forma do próprio roteamento

`policy-options` é o jeito recomendado, mas não o único: as route-maps e as listas do roteamento continuam aceitas, e são o que um vizinho usa em `neighbor <vizinho> route-map <nome> in|out` e `neighbor <vizinho> prefix-list <nome> in|out`. O que o `prismash` confere delas, digitado no topo do modo de configuração:

| Comando | O que cria |
|---------|------------|
| `ip prefix-list <nome> ...` e `ipv6 prefix-list <nome> ...` | uma lista de prefixos, com `permit`/`deny` e faixa `ge`/`le` |
| `bgp as-path access-list <nome> ...` | uma lista de expressões de AS-path |
| `bgp community-list <nome> ...` e `bgp large-community-list <nome> ...` | uma lista de communities ou de large communities |
| `route-map <nome> permit\|deny <seq>` | uma cláusula de route-map; entra nela |

Dentro de uma cláusula de route-map:

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `match ip address prefix-list` | `<lista>` | rotas IPv4 cujo prefixo está na lista |
| `match ip next-hop prefix-list` | `<lista>` | rotas IPv4 cujo próximo salto está na lista |
| `match ipv6 address prefix-list` | `<lista>` | rotas IPv6 cujo prefixo está na lista |
| `match rpki` | `valid`, `invalid` ou `notfound` | pelo estado da validação de origem (seção RPKI, abaixo) |
| `match as-path` | `<lista>` | pelo AS-path |
| `match community` e `match large-community` | `<lista>` | pelas communities |
| `match metric` | `<0-4294967295>` | pelo MED  |
| `match peer` | `<vizinho>` | pelo vizinho que mandou a rota |
| `set local-preference` | `<0-4294967295>` | qual caminho esta caixa prefere; maior ganha |
| `set metric` | `<valor>`, `+`/`-` valor, `rtt`, `+rtt`, `-rtt`, `igp` ou `aigp` | o MED enviado, substituído ou ajustado |
| `set community` e `set large-community` | `<AS:NN> [additive]` | as communities levadas; `additive` mantém as que já estavam |
| `set next-hop` | `<A.B.C.D>` | o próximo salto anunciado |
| `set as-path prepend` e `set as-path exclude` | `<asn> [<asn> ...]` | alonga o caminho, ou tira ASs dele |
| `set weight` | `<0-4294967295>` | a preferência local desta caixa, nunca anunciada |
| `set origin` | `igp`, `egp` ou `incomplete` | o atributo de origem |
| `call`  | `<route-map>` | roda outra route-map antes de decidir |
| `on-match next` ou `on-match goto <seq>` |           | o que fazer quando a cláusula casa |
| `description` | `<texto>` | para que serve a cláusula |

O que não está nessas tabelas também chega ao roteamento, marcado com `<- not checked`. Os detalhes — a ordem das cláusulas, o que é recusado, a faixa `ge`/`le`, as expressões de AS-path — estão em [Roteamento, SSH, usuários e SNMP](/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m).

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

# Validação de origem (RPKI)

Um provedor que valida origens no resto da rede não deveria ter, na borda, uma caixa que aceita o que lhe disserem. O roteamento desta caixa fala RTR: ele se liga a um validador que você já opera e passa a marcar cada rota aprendida como `valid`, `invalid` ou `notfound`, para que a sua política decida o que fazer com cada uma.

```
configure
 rpki
  rpki cache tcp 10.0.0.10 8282 preference 1
  exit
 route-map RM-TRANSITO-IN deny 5
  match rpki invalid
  exit
 route-map RM-TRANSITO-IN permit 10
  exit
 router bgp 65000
  neighbor 200.150.0.2 route-map RM-TRANSITO-IN in
  exit
 commit
```

A primeira cláusula descarta o que o validador reprova; a segunda deixa passar todo o resto. Sem a segunda, a route-map nega tudo no fim — que é o comportamento normal de uma política, e aqui filtraria a sessão inteira em vez de filtrar só o inválido.

## O que o bloco `rpki` entende

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `rpki cache tcp` | `<host> <porta> [source <A.B.C.D>] preference <1-255>` | um validador, por RTR sobre TCP |
| `rpki cache ssh` | `<host> <porta> <usuário> <chave-privada> [known-hosts] [source <A.B.C.D>] preference <1-255>` | um validador, por RTR sobre SSH |
| `rpki polling_period` | `<1-86400>` | segundos entre as atualizações |
| `rpki expire_interval` | `<600-172800>` | segundos até descartar o que o validador mandou |
| `rpki retry_interval` | `<1-7200>` | segundos até tentar de novo um validador perdido |
| `rpki reset` |           | descarta o que os validadores mandaram e pergunta de novo |

## A palavra do transporte não é opcional

A forma é `rpki cache tcp <host> <porta> [source <A.B.C.D>] preference <1-255>`, ou `rpki cache ssh ...`. Omitir o `tcp` não é um atalho: é um comando desconhecido, e a caixa o recusa como recusaria qualquer palavra que não existe.

## É um módulo, e um módulo que não está carregado não existe

O suporte a RPKI vem num pacote separado do roteamento, `frr-rpki-rtrlib`. A appliance já o instala. O sintoma de quando ele falta é enganoso e vale reconhecer:

```
bgpd is not running
```

Toda e qualquer palavra de RPKI responde isso — porque o roteamento se recusa a subir quando mandam carregar um módulo que ele não acha. A resposta aponta para o lugar errado: parece roteamento caído, é módulo ausente. Por isso o autoteste de boot confere o módulo **pelo nome**, e não pela versão do roteamento, que informa suporte a RPKI esteja o módulo presente ou não.

## As duas coisas que o `commit` avisa

Escrever a política antes de configurar o cache é uma ordem legítima de trabalho, então nenhuma das duas é recusa — mas as duas mudam o que a política faz, e em silêncio.

```
warning: no RPKI cache is configured, so every route is notfound and this
    clause matches all of them
    match rpki notfound
```

Sem cache, tudo é `notfound`, e é disso que o aviso trata: uma cláusula em `notfound` casa **todas** as rotas da caixa. O outro lado também é avisado, com a frase equivalente — uma cláusula em `valid` ou `invalid` não casa **nenhuma**, porque sem validador nenhuma rota chega a ser uma coisa nem outra. A mesma política, com e sem cache, faz coisas opostas.

## A falha que isso evita, e a que isso cria


:::warning
Um validador que caiu acaba marcando **tudo** como `notfound`. Uma política que nega `invalid` continua funcionando nesse estado; uma política que trata `notfound` como suspeito passa a degradar todas as rotas da caixa no dia em que o cache ficar fora — e cache fora é coisa ordinária, não catástrofe.

:::

Essa é a razão de a recomendação ser negar `invalid` e não desconfiar de `notfound`. A primeira falha fechada para o que o validador reprova; a segunda falha fechada para a internet inteira.

## O que a caixa diz quando o cache some

Com o cache de pé, o resumo do BGP diz assim:

```
  RPKI: 1 of 1 cache(s) connected
```

E quando ele some:

```
  RPKI: 1 cache(s) configured, NONE connected
        validation is running on data the caches already sent, which expires;
        after that every route is notfound
```

As duas metades dessa segunda frase importam. A validação **não** para na hora: a tabela de prefixos que o cache já mandou continua valendo, e só expira depois do `expire_interval` do próprio roteamento — 7200 segundos por padrão. Dizer "nada está validado" mandaria você procurar um incidente que ainda não começou; dizer só "cache fora" esconderia que ele vai começar.


:::info
Em `show rpki cache-connection`, a linha de cabeçalho `**Connected to group 1**` continua lá enquanto o cache estiver configurado — medido setenta segundos depois do validador ter sido morto, ela ainda dizia *Connected*. Quem responde a verdade é o marcador `**(connected)**` de cada cache, que some em menos de um segundo. Tudo neste produto lê o marcador; ao conferir na mão, leia o marcador também.

:::

# Operar a sessão: o que se digita quando algo está errado

## `show bgp summary`

```
prisma> show bgp summary
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
error: 200.150.0.2 does not keep what it received
    so this would print an empty table either way
    add 'neighbor 200.150.0.2 soft-reconfiguration inbound' under the address
    family; it costs memory
    'show bgp neighbor 200.150.0.2 routes' shows what survived inbound policy,
    which the box does have
```

`clear bgp <endereço> soft in` recusa pelo mesmo motivo, e a saída que ele oferece é a que sempre funciona: reiniciar a sessão e pegar as rotas de novo.

```
error: 200.150.0.2 does not keep what it received
    so there is nothing to re-apply
    add 'neighbor 200.150.0.2 soft-reconfiguration inbound' under the address
    family, which costs memory,
    or use 'clear bgp 200.150.0.2' to reset the session and take the routes
    again
```

As duas recusas vêm em linhas dentro da largura do console. Por um tempo o veredito e a primeira explicação saíam grudados numa linha só, que media de 92 a 106 colunas numa tela de 79.

E repare que o veredito **começa pelo endereço**, sem a palavra `neighbour` na frente. Ela saiu porque o endereço logo a seguir já diz de quem se fala, e ela custava dez colunas numa linha que o equipamento não quebra: com um vizinho IPv6 no lugar de um IPv4, o veredito chegava a 87 colunas numa tela de 79. O endereço do vizinho entra em todas as linhas desta recusa, então é ele que manda na largura de todas elas — e é por isso que a palavra a mais era a primeira a sair.

## E quando quem não responde é o roteamento

São **três** respostas e não duas. "O vizinho não guarda" é uma leitura da configuração que está valendo — e numa caixa cujo roteamento não está respondendo não há configuração lida: não há leitura nenhuma. Dizer ali que falta o `soft-reconfiguration inbound` mandaria você acrescentar uma linha que a caixa talvez já tenha, por um daemon que não está de pé para recebê-la, e apontaria para um comando que pergunta ao mesmo roteamento calado.

```
error: FRR is not answering
    so whether 200.150.0.2 keeps what it received cannot be read
    an empty table here would be this box's control plane being down rather
    than the neighbour sending nothing: 'systemctl status frr' and its journal
    say why
```

O `clear bgp <endereço> soft in` responde do mesmo jeito, e acrescenta a parte que importa ali: `**clear bgp**` **sozinho também não resolveria**, porque quem segura a sessão é justamente o roteamento que está calado.

```
error: FRR is not answering
    so whether 200.150.0.2 keeps what it received cannot be read
    nothing was re-applied, and 'clear bgp' would reset nothing either - both
    go to the FRR that is silent: 'systemctl status frr' and its journal say
    why
```

O veredito dessas duas **não carrega o endereço do vizinho**, e isso é deliberado: o endereço vai na linha de baixo. A primeira linha não é quebrada por ninguém, e com um vizinho IPv6 no meio dela ela chegaria a 99 colunas numa tela de 79.

Uma tabela vazia numa caixa dessas é o plano de controle da **sua** caixa fora do ar, e não o vizinho sem mandar rota. São problemas diferentes e é o que essa terceira resposta existe para separar.

## `clear`

```
prisma> clear bgp 200.150.0.2
prisma> clear bgp 200.150.0.2 soft in
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
