# Política de roteamento (policy-options)

Referência de tudo o que `policy-options` aceita no `prismash`: listas de prefixos, communities, large e extended communities, expressões de AS-path, políticas com seus terms, onde uma política é aplicada e como conferi-la antes e depois. Para quem escreve filtros de BGP ou decide o que entra no OSPF e no IS-IS.

A política é escrita uma vez, com nome, no formato do Junos, e aplicada em quantos lugares for preciso. A caixa a traduz para o que o roteamento entende no `commit`.

# Os níveis e os prompts

`policy-options` é um nível do modo de configuração, e dentro dele há outros dois, um dentro do outro:

| Nível | Como se entra | Prompt | O que se digita ali |
|-------|---------------|--------|---------------------|
| `policy-options` | `policy-options` em `[edit]` | `prisma# [edit policy-options]` | listas, communities, as-paths e políticas |
| lista de prefixos | `prefix-list <nome>` | `prisma# [edit policy-options / prefix-list <nome>]` | uma rede por linha  |
| política | `policy-statement <nome>` | `prisma# [edit policy-options / policy-statement <nome>]` | os terms            |
| term  | `term <nome>` dentro da política | `prisma# [edit ...ptions / policy-statement <nome> / term <nome>]` | `from`, `set` e `then` |

Um prompt maior que 44 caracteres guarda o **fim**, que é onde a próxima linha vai cair; por isso o do term começa com `...`. Entrar num nível com nome já cria o que ele nomeia: uma lista ou um term recém-criado existe antes de ter qualquer coisa dentro.

Em qualquer nível, `exit` sobe um nível e `top` volta para `[edit]`. `?` e TAB listam o que o nível aceita.

```
prisma# [edit] policy-options
prisma# [edit policy-options] prefix-list CLIENTES
prisma# [edit policy-options / prefix-list CLIENTES] 203.0.113.0/24
prisma# [edit policy-options / prefix-list CLIENTES] exit
prisma# [edit policy-options] community MARCA members 65000:100
prisma# [edit policy-options] policy-statement SAIDA
prisma# [edit policy-options / policy-statement SAIDA] term 10
```

A mesma política, na forma em que a caixa a salva e em que você pode digitá-la ou colá-la:

```
policy-options
 prefix-list CLIENTES
  203.0.113.0/24
  exit
 community MARCA members 65000:100
 policy-statement SAIDA
  term 10
   from prefix-list CLIENTES
   set as-path-prepend 65000 65000
   then accept
   exit
  term 20
   from community MARCA
   then reject
   exit
  exit
 exit
```

# O nível `policy-options`

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `prefix-list` | `<nome>`  | cria ou edita uma lista de redes e entra nela |
| `community` | `<nome> members <asn:valor>...` | um conjunto nomeado de communities. Cada membro é `ASN:valor`, os dois de 0 a 65535. Digitar o mesmo nome de novo **substitui** o conjunto |
| `large-community` | `<nome> members <asn:valor:valor>...` | um conjunto nomeado de large communities (RFC 8092). Cada parte vai de 0 a 4294967295. Digitar o mesmo nome de novo substitui o conjunto |
| `extended-community` | `<nome> members rt:<asn>:<valor>\|soo:<asn>:<valor>...` | um conjunto nomeado de extended communities: route target (`rt`) e site of origin (`soo`). O primeiro campo é um ASN ou um endereço IPv4. Com ASN de até 65535 o valor vai até 4294967295; com ASN maior ou com endereço, até 65535 |
| `as-path` | `<nome> <expressão>` | uma expressão regular de AS-path com nome. Aspas em volta são tiradas. Digitar o mesmo nome de novo substitui a expressão |
| `policy-statement` | `<nome>`  | cria ou edita uma política e entra nela |
| `no`    | `<declaração>` | remove uma lista, uma community, um conjunto de large ou extended communities, um as-path ou uma política, com tudo o que ela tem dentro |

Um nome, de lista, community, as-path, política ou term, tem letras, números, `.`, `_` e `-`, começa por letra ou número e tem até 32 caracteres.

Na expressão de AS-path, `_` é a regra do roteamento: qualquer fronteira (começo, fim, espaço ou vírgula), não um sublinhado. Uma expressão que não compila é recusada na hora.

## Dentro de `prefix-list <nome>`

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `<rede>` | endereço/tamanho | põe a rede na lista. IPv4 e IPv6 podem ficar na mesma lista. Os bits de host têm de ser zero: `10.0.0.1/8` é recusado |
| `no`    | `<rede>`  | tira a rede da lista |


:::info
**Uma rede numa lista casa só com ela mesma**, com aquele tamanho exato. `203.0.113.0/24` na lista não casa `203.0.113.128/25`. Para casar também os mais específicos, use `from route-filter` no term, logo abaixo.

:::

## Dentro de `policy-statement <nome>`

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `term`  | `<nome>`  | cria ou edita um term e entra nele. Os terms são avaliados **na ordem em que foram criados**, não pela ordem do nome |
| `default-action` | `accept` ou `reject` | o que a política faz com a rota que nenhum term decidiu, no lugar do padrão de onde ela é aplicada. Digitar de novo substitui |
| `no`    | `term <nome>` | remove o term inteiro |

# O term: `from`, `set` e `then`

Um term tem três partes. `from` diz o que a rota precisa casar, `set` diz o que muda nela, `then` diz como a avaliação termina. Todas são opcionais.

## `from`: o que a rota precisa casar

| Condição | Argumento | Casa quando |
|----------|-----------|-------------|
| `from prefix-list` | `<nome>`  | o prefixo da rota está na lista, exatamente |
| `from route-filter` | `<rede> exact` | o prefixo é aquela rede, com aquele tamanho |
| `from route-filter` | `<rede> orlonger` | o prefixo é aquela rede ou um mais específico dentro dela |
| `from route-filter` | `<rede> longer` | o prefixo é um mais específico dentro dela, sem a própria |
| `from route-filter` | `<rede> upto /<n>` | o prefixo está dentro dela, do tamanho dela até `/n`. `n` fica entre o tamanho da rede e 32 (ou 128) |
| `from community` | `<nome>`  | a rota carrega a community do conjunto nomeado |
| `from as-path` | `<nome>`  | o AS-path da rota casa a expressão nomeada |
| `from protocol` | `bgp`, `ospf`, `isis`, `static`, `direct` ou `kernel` | a rota veio desse protocolo. `direct` são as redes das próprias interfaces |
| `from tag` | `<0-4294967295>` | a rota carrega essa tag |
| `from metric` | `<0-4294967295>` | a rota tem essa métrica |
| `from neighbor` | `<endereço>` | a rota veio desse vizinho BGP |
| `from family` | `inet` ou `inet6` | o prefixo é IPv4 (`inet`) ou IPv6 (`inet6`). Sem outra condição de prefixo, casa todos os prefixos da família |
| `from origin` | `igp`, `egp` ou `incomplete` | o atributo origin do BGP |
| `from local-preference` | `<0-4294967295>` | a rota tem essa preferência local |
| `from next-hop` | `<endereço>` | o próximo salto do BGP é esse |
| `from large-community` | `<nome>`  | a rota carrega a large community do conjunto |
| `from extended-community` | `<nome>`  | a rota carrega a extended community do conjunto |
| `from validation-state` | `valid`, `invalid` ou `unknown` | o resultado da validação RPKI de origem. `unknown` é a rota que nenhum ROA cobre |
| `from policy` | `<política>` | chama outra política como sub-rotina: se ela rejeita, a rota é rejeitada; se aceita, o term segue. Um term chama uma política só |

**Condições do mesmo tipo valem como "ou"; de tipos diferentes, como "e".** Duas `from prefix-list` casam uma rota que esteja em qualquer das duas; uma `from prefix-list` e uma `from community` exigem as duas coisas. `from prefix-list` e `from route-filter` são do mesmo tipo: as duas falam do prefixo.

`from tag` e `from metric` valem por um valor só: um segundo, no mesmo term, fica no lugar do primeiro.

`from neighbor`, `from origin`, `from local-preference`, `from next-hop` e `from validation-state` aceitam vários valores, que valem como "ou". Cada combinação deles vira uma regra no roteamento, e um term pode ter no máximo 10 combinações: mais que isso é recusado no `commit`, e a saída é dividir o term em dois.

`from validation-state` só tem sentido com um cache RPKI configurado. Sem cache, toda rota é `unknown`, e o `commit` avisa.

**Um term sem** `**from**` **casa com qualquer rota.** É assim que se escreve o "todo o resto" no fim de uma política.

## `set`: o que muda na rota

| Ação | Argumento | O que faz |
|------|-----------|-----------|
| `set local-preference` | `<0-4294967295>`, `+<n>` ou `-<n>` | a preferência local do BGP, ou somada ou subtraída da que a rota tem |
| `set community` | `add <nome>` | acrescenta as communities do conjunto às que a rota já tem |
| `set community` | `set <nome>` | troca as communities da rota pelas do conjunto |
| `set community` | `delete <nome>` | tira da rota as communities do conjunto |
| `set large-community` | `add`, `set` ou `delete` `<nome>` | acrescenta, troca ou tira as large communities do conjunto |
| `set extended-community` | `add` ou `set` `<nome>` | acrescenta ou troca as extended communities do conjunto. Não há `delete`: o roteamento não sabe tirar extended communities por lista |
| `set as-path-prepend` | `<asn>...` | prepende esses ASNs, na ordem digitada |
| `set as-path-prepend` | `last-as <1-10>` | repete o primeiro AS do caminho (o do vizinho) essa quantidade de vezes |
| `set as-path` | `remove-private` | tira do AS-path todo AS privado (64512 a 65534 e 4200000000 a 4294967294), onde quer que esteja, inclusive o do próprio vizinho |
| `set metric` | `<0-4294967295>` | a métrica: o MED no BGP, o custo no OSPF e no IS-IS |
| `set tag` | `<0-4294967295>` | a tag da rota |
| `set next-hop` | `<endereço>` | o próximo salto no BGP, IPv4 ou IPv6 |
| `set next-hop` | `self`    | num `export`, anuncia esta caixa como próximo salto |
| `set next-hop` | `peer-address` | num `import`, usa o endereço do vizinho como próximo salto |
| `set next-hop` | `discard` | descarta o tráfego para o prefixo: é o blackhole disparado remotamente (RTBH). O próximo salto vira 192.0.2.1 (ou 100::1 no IPv6), que a caixa resolve para um blackhole. Num vizinho eBGP direto, o vizinho precisa de `neighbor <vizinho> disable-connected-check`: sem isso o roteamento não resolve esse próximo salto, a rota não é instalada e o tráfego segue a rota menos específica. O `commit` recusa e diz a linha que falta |
| `set med` | `<0-4294967295>`, `+<n>`, `-<n>` ou `igp` | o MED do BGP; `igp` usa a métrica do IGP até o próximo salto |
| `set weight` | `<0-65535>` | o peso, que só vale nesta caixa e pesa antes da preferência local |
| `set origin` | `igp`, `egp` ou `incomplete` | o atributo origin |
| `set metric-type` | `1` ou `2` | o tipo da rota externa no OSPF. Só num `export` para o OSPF ou no `default-information` do OSPF |
| `set src` | `<endereço>` | o endereço de origem que a rota instalada leva no Linux, para o que a própria caixa envia por ela. Só numa `fib-policy`. Um term com `set src` IPv4 vale só para rotas IPv4, como se tivesse `from family inet` (e o mesmo para IPv6) |

Os `set` só são aplicados quando o term casa.

## `then`: como o term termina

| Comando | O que faz |
|---------|-----------|
| `then accept` | aceita a rota, com os `set` do term, e **para** a avaliação |
| `then reject` | rejeita a rota e para a avaliação |
| `then next term` | aplica os `set` e segue para o próximo term |
| `then next policy` | aplica os `set` e passa a rota para a próxima política da cadeia. Numa política sozinha, vale o padrão de onde ela é aplicada |
| sem `then` | o mesmo que `then next term` |

Um term tem um `then` só: digitar outro substitui o anterior.

Mudar a rota é sempre `set`. Diferente do Junos, `then` não leva ação: `then local-preference 200` é recusado, e a forma certa é `set local-preference 200` com `then accept`.

## `no` dentro do term

| Comando | O que faz |
|---------|-----------|
| `no from <condição>` | tira aquela condição |
| `no set <ação>` | tira aquela ação |
| `no then <como>` | tira o `then`, e o term volta a seguir para o próximo |

O `no` leva a declaração inteira, como foi digitada: `no from prefix-list CLIENTES`, não `no from`.

# A rota que nenhum term decidiu

O padrão depende de onde a política é usada, como no Junos:

| Onde a política é aplicada | Rota que nenhum term aceitou nem rejeitou |
|----------------------------|-------------------------------------------|
| `import` ou `export` num vizinho BGP | **aceita**                                |
| `export` para OSPF ou IS-IS | **não exportada**                         |
| `network ... policy`, `aggregate-address ... policy`, `table-policy` | **aceita**: anunciada, ou instalada       |
| `fib-policy`               | **instalada**                             |
| `default-originate policy`, `default-information originate ... policy` | **não conta**: sem um term que aceite, o default não é originado |
| `suppress-policy`          | **não suprimida**                         |
| as duas políticas de `advertise` | **fora**: não é anunciada nem conta como presente |

Para rejeitar o resto no BGP, use `default-action reject` na política, ou termine com um term sem `from` e com `then reject`. Para exportar o resto para um IGP, `default-action accept`, ou um term sem `from` e com `then accept`.

Numa cadeia, a rota que nenhum term de uma política decidiu passa para a próxima; o `default-action` de uma política, quando existe, decide ali mesmo.

# Onde uma política é aplicada

## Num vizinho BGP: `import` e `export`

Dentro do `router bgp`, na família de endereço, por vizinho ou por peer group:

```
router bgp 65000
 address-family ipv4 unicast
  neighbor 10.0.0.2 import DO-TRANSITO
  neighbor 10.0.0.2 export SAIDA
 exit-address-family
 exit
```

| Comando | O que faz |
|---------|-----------|
| `neighbor <vizinho> import <política>` | a política filtra e altera o que chega desse vizinho |
| `neighbor <vizinho> export <política>` | a política filtra e altera o que se anuncia para ele |

### Uma cadeia de políticas

No lugar de uma política, uma lista entre colchetes: `neighbor 10.0.0.2 import [ BOGONS CLIENTES ]`. A rota passa pelas políticas na ordem; a primeira que aceita ou rejeita decide, e `then next policy` passa a rota adiante com o que o term mudou. O mesmo vale para `export [ ... ]` no OSPF e no IS-IS. Uma política não pode aparecer duas vezes na mesma cadeia.

A política precisa existir: `import` ou `export` de um nome que nenhum `policy-statement` tem é recusado no prompt. Num vizinho BGP as rotas são do BGP, então um term com `from protocol` que não inclua `bgp` não se aplica ali.

Na configuração em vigor o vizinho aparece com a `route-map` em que a política foi traduzida: `neighbor 10.0.0.2 route-map PO-SAIDA-OUT out`, ou `PO-BOGONS+CLIENTES-IN` para uma cadeia. Todos os nomes que começam por `PO-` são dessa tradução, e uma `route-map` ou lista digitada à mão com esse começo é recusada.

## Num IGP: `export <política>`

Dentro do `router ospf` ou do `router isis <nome>`, `export <política>` redistribui para o protocolo o que a política aceita, e só isso:

```
prisma# [edit] router ospf
prisma# [edit router ospf] export PARA-O-OSPF
```

| Comando | Onde | O que faz |
|---------|------|-----------|
| `export <política>` | `router ospf` | redistribui para o OSPF o que a política aceita, vindo de `bgp`, `direct`, `kernel`, `static` ou `isis` |
| `export <política>` | `router isis <nome>` | o mesmo para o IS-IS, vindo de `bgp`, `direct`, `kernel`, `static` ou `ospf`, em IPv4 e IPv6 e nos dois níveis |
| `no export <política>` | nos dois | desfaz a exportação |

`from protocol` é o que escolhe de onde a rota veio; um term sem `from protocol` vale para qualquer origem. A exportação cobre todas as origens desde já, então um term acrescentado depois vale sem digitar o `export` de novo.

O `router ospf6` não tem `export`.

## No BGP, além do vizinho

Dentro do `router bgp`, na família de endereço:

| Comando | O que faz |
|---------|-----------|
| `neighbor <vizinho> default-originate policy <política>` | anuncia a rota default para o vizinho enquanto houver na tabela do BGP uma rota que a política aceita. Os `set` da política vão na default |
| `network <prefixo> policy <política>` | origina o prefixo com os `set` da política; se ela rejeita, o prefixo não é anunciado |
| `aggregate-address <prefixo> [summary-only] [policy <política>] [suppress-policy <política>]` | origina o agregado. `policy` dá os `set` do agregado; `suppress-policy` segura as rotas mais específicas que ela aceita e deixa passar as outras. `summary-only` segura todas, e por isso não vai junto com `suppress-policy` |
| `neighbor <vizinho> advertise <política> exist <condição>` | anuncia o que a primeira política aceita só enquanto existir na tabela uma rota que a condição aceita |
| `neighbor <vizinho> advertise <política> non-exist <condição>` | o contrário: anuncia só enquanto **não** existir. É o anúncio de backup |
| `table-policy <política>` | decide quais das melhores rotas do BGP vão para a tabela de roteamento e para o encaminhamento. A rota rejeitada continua no BGP e continua sendo anunciada; só não é instalada. Aceita `set metric` e `set next-hop` |

O BGP confere a condição do `advertise` a cada 60 segundos. Para menos, `bgp conditional-advertisement timer <segundos>` no `router bgp`.

## Na tabela do roteamento: `fib-policy`

No `[edit]`, uma política por protocolo decide o que entra na tabela de encaminhamento, depois que o protocolo escolheu a rota:

```
prisma# [edit] fib-policy ospf SO-O-QUE-SERVE
```

| Comando | O que faz |
|---------|-----------|
| `fib-policy bgp\|ospf\|isis\|static <política>` | rota que a política rejeita não é instalada: o protocolo continua com ela, mas o tráfego não a segue. `set src <endereço>` dá à rota instalada o endereço de origem |
| `no fib-policy <protocolo> <política>` | desfaz    |

É assim que se filtra o que o OSPF e o IS-IS instalam: o roteamento não tem filtro de entrada dentro desses protocolos. Vale para IPv4 e IPv6. Uma `fib-policy` só aceita `set src`, e nada que só o BGP tem.

## O default do OSPF e do IS-IS

| Comando | Onde | O que faz |
|---------|------|-----------|
| `default-information originate [always] [metric <n>] [metric-type 1\|2] policy <política>` | `router ospf` | sem `always`, origina a default enquanto houver uma rota que a política aceita, entre as que o OSPF redistribui. Com `always`, sempre, com os `set` da política |
| `default-information originate ipv4\|ipv6 level-1\|level-2 [always] [metric <n>] policy <política>` | `router isis <nome>` | a política é aplicada à própria rota default: aceita, a default é originada com os `set` dela; rejeitada, não é |

Aqui o protocolo escolhe as rotas que olha, e `from protocol` é recusado.

Uma rota estática pode levar tag: `<prefixo> next-hop <destino> tag <n>` no `router static`, e `from tag <n>` casa com ela. É o jeito de marcar uma estática para um `export` ou para uma condição.

# Conferir: `show policy` e `test policy`

| Comando | Modo | O que mostra |
|---------|------|--------------|
| `show policy` | operacional e configuração | cada política, quantos terms tem e onde o roteamento em vigor a aplica |
| `show policy <política>` | operacional e configuração | a mesma coisa, com os terms como foram digitados |
| `test policy <política> <prefixo>` | operacional | o que a política faz com uma rota para aquele prefixo, term por term |

No modo operacional as duas leem a política aplicada. Em modo de configuração, `show policy` lê a candidata, o que você está escrevendo e ainda não aplicou.

```
prisma> show policy
policy-statement SAIDA
  terms: 2
  applied: neighbor 10.0.0.2 export, ospf export
```

Os outros lugares aparecem do mesmo jeito: `neighbor 10.0.0.2 default-originate`, `network 10.40.0.0/24`, `aggregate-address 10.30.0.0/16 policy` ou `suppress-policy`, `neighbor 10.0.0.2 advertise` e, para a condição, `neighbor 10.0.0.2 advertise non-exist`, `table-policy`, `fib-policy static`, `ospf default-information`.

Uma política escrita e ainda não usada em lugar nenhum diz `applied: nowhere in the running routing`. Uma política numa cadeia aparece com a cadeia inteira: `neighbor 10.0.0.2 import [ BOGONS CLIENTES ]`. O `default-action`, quando existe, aparece antes dos terms.

`test policy` percorre os terms na ordem e para no que decide:

```
prisma> test policy SAIDA 203.0.113.0/24
  term 10: accept (set as-path-prepend 65000 65000)
      matches
result: accept (set as-path-prepend 65000 65000), by term 10
```

O teste parte só do prefixo. `from family` ele responde pelo prefixo. Uma condição que o prefixo sozinho não responde (community, large ou extended community, as-path, protocolo, tag, métrica, vizinho, origin, preferência local, próximo salto, validação RPKI, política chamada) não é adivinhada: o term aparece como "decide, se a rota tiver aquilo", e o teste segue como se não tivesse, para você ver os dois caminhos. Um term com `then next policy` termina o teste com `result: next policy, by term <term>`. Uma política com `default-action` termina com `result: reject, by the policy's default-action` (ou `accept`). Quando nenhum term decide e não há `default-action`, a resposta dá os dois padrões:

```
prisma> test policy SAIDA 198.51.100.0/24
  term 10: no match
      the prefix is in none of its lists or filters
  term 20: if the route's community match: reject
      the prefix alone does not say
result: no term decided it
    on a BGP neighbor that is accepted; exported into OSPF or IS-IS it is not
    as a network, aggregate-address, table or FIB policy it is accepted; as the
    routes an origination, a suppression or a conditional advertisement waits
    for, it is not
```

# O que é recusado

Tudo o que está abaixo é recusado **no prompt**, e nada é guardado na candidata.

| Você digitou | A caixa diz |
|--------------|-------------|
| um nome com caractere fora do permitido ou com mais de 32 | `prefix-list '<nome>': not a name` (ou o tipo que for), e embaixo `a name is letters, digits, '.', '_' and '-', at most 32 characters`; o prompt continua no nível em que estava |
| `10.0.0.1/8` numa lista ou num `route-filter` | `'10.0.0.1/8' is not a network (address/length, host bits zero)` |
| `community C members 70000:1` | `'70000:1' is not a community: ASN:value, each 0-65535` |
| uma expressão de as-path que não compila | `the expression does not compile`, com o motivo |
| `from route-filter 10.0.0.0/8 between` | `route-filter: exact, orlonger, longer or upto /<length>` |
| `from route-filter 10.0.0.0/8 upto /4` | `the length is between 8 and 32` |
| `from protocol rip` | `from protocol: bgp, ospf, isis, static, direct or kernel` |
| `from metric 99999999999` | `from metric <0-4294967295>` |
| uma condição que não existe | `from:` seguido da lista das condições |
| `large-community L members 1:2` | `'1:2' is not a large community: ASN:value:value, each 0-4294967295` |
| `extended-community E members xx:1:2` | `'xx:1:2' is not an extended community: rt:<asn>:<value> or soo:<asn>:<value>, or an address in place of the ASN` |
| `from family ipx`, `from origin bgp`, `from validation-state good` | a lista do que a condição aceita |
| `set extended-community delete E` | `set extended-community add\|set <name>`: o roteamento não tira extended communities por lista |
| `then next hop` | `then: accept, reject, next term or next policy; a change to the route is 'set ...'` |
| um segundo `from policy` no mesmo term | `a term calls one policy` |
| `neighbor ... import [ A A ]` ou dois nomes sem colchetes | `usage: neighbor <peer> import\|export <policy> \| [ <policy>... ]` |
| `set next-hop self` numa política aplicada como `import`, `set next-hop peer-address` como `export`, `set metric-type` fora de um `export` para o OSPF | `this policy cannot be applied as import; nothing staged` (ou `export`), com uma linha por term culpado |
| uma ação que não existe | `set:` seguido da lista das ações |
| `set src` numa política aplicada fora de uma `fib-policy` | `cannot be applied on network; nothing staged` (ou onde for), e embaixo `set src is for a fib-policy` |
| numa `fib-policy`, um `set` que não é `src`; numa `table-policy`, um que não é `metric` nem `next-hop`; numa `suppress-policy` ou num `advertise`, qualquer `set` | `set <ação> does nothing on <onde>`, com o que aquele lugar aceita |
| `aggregate-address ... summary-only suppress-policy ...` | `summary-only suppresses every more specific route, and suppress-policy the ones it accepts: one or the other` |
| `fib-policy connected ...`, ou um dos comandos novos digitado pela metade | `not how this statement is typed; nothing staged`, e embaixo a forma certa |
| `from protocol` numa política do `default-information` | `from protocol has nothing to match on ospf default-information` (ou `isis`) |
| `set next-hop abc` | `set next-hop: 'abc' is not an address` |
| `then` com outra coisa que não `accept`, `reject` ou `next term` | `then: accept, reject or next term; a change to the route is 'set ...'` |
| `no` de algo que não existe | `policy-options has no such statement; nothing removed`, com a declaração |
| `neighbor ... import` ou `export` de uma política que não existe, ou `export` num IGP | `no policy-statement named <nome>` |
| uma `route-map` ou lista com nome começado por `PO-` | `the PO- names belong to policy-options; nothing staged` |
| no `commit`, um term que cita uma `prefix-list`, `community`, `large-community`, `extended-community`, `as-path` ou política que não existe | `policy-statement <política> term <term>: no prefix-list <lista>` (ou o tipo que for); nada é aplicado |
| no `commit`, `set next-hop discard` no `import` de um vizinho eBGP direto sem `disable-connected-check` | `set next-hop discard is not resolved on a single-hop eBGP neighbor: add 'neighbor <vizinho> disable-connected-check', or the route is never installed and its traffic is forwarded` |
| no `commit`, políticas que se chamam em roda com `from policy` | `policy-statement <política>: from policy reaches itself again` |
| no `commit`, um term com mais de 10 combinações | `more than 10 combinations of neighbor, origin, local-preference, next-hop and validation-state; split it into terms` |

## O que só existe no BGP, exportado para um IGP

Uma rota de OSPF ou de IS-IS não tem community, AS-path, preferência local nem próximo salto de BGP. Um term que fala dessas coisas é recusado na hora do `export`: a caixa responde `this policy cannot be exported into isis; nothing staged` (ou `ospf`) e, embaixo, uma linha por term culpado, dizendo o term e o que nele `is for BGP routes`.

O que conta como só de BGP: `from community`, `from as-path`, `from neighbor`, `from origin`, `from local-preference`, `from next-hop`, `from large-community`, `from extended-community`, `from validation-state`, `set local-preference`, `set community`, `set large-community`, `set extended-community`, `set as-path-prepend`, `set as-path`, `set med`, `set weight`, `set origin` e `set next-hop`. Vale também para as políticas que a exportada chama com `from policy` ou que vêm depois dela na cadeia. A recusa existe porque ignorar a condição em silêncio exportaria justamente o que o term foi escrito para segurar. Se a mesma lógica serve aos dois, escreva duas políticas: uma para o vizinho BGP, outra para o IGP.


:::warning
**Confira com** `**show policy <política>**` **e** `**test policy**` **antes do** `**commit**`**.** Uma política aplicada num vizinho decide o que a sessão carrega: um term que não faz o que você pensava derruba ou vaza prefixos na sessão inteira. O term pode ser digitado antes da lista que ele cita, mas o `commit` recusa um term que cita uma lista que não existe. Uma `prefix-list` que existe e está **vazia** não casa rota nenhuma: o term que depende só dela não pega nada. E lembre que, num vizinho BGP, o que nenhum term decide é **aceito**; para anunciar só o que as listas dizem, termine a política com um term que rejeita o resto.

:::

# Ver também

* [Configuração de BGP](/doc/configuracao-de-bgp-9wJnuicEDZ) — vizinhos, peer groups e famílias de endereço, onde `import` e `export` são aplicados
* [Roteamento, SSH, usuários e SNMP](/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m) — o OSPF e o IS-IS, e as route-maps e listas escritas na forma do próprio roteamento
* [commit, rollback e save](/doc/commit-rollback-e-save-jqCB7kxPuA) — como a política vai para o roteamento e como desfazer
* [Comandos show](/doc/comandos-show-YlNji1X5W4) — `show policy` ao lado dos outros
