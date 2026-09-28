# Política de roteamento (policy-options)

Referência de tudo o que `policy-options` aceita no `prismash`: listas de prefixos, communities, expressões de AS-path, políticas com seus terms, onde uma política é aplicada e como conferi-la antes e depois. Para quem escreve filtros de BGP ou decide o que entra no OSPF e no IS-IS.

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
| `as-path` | `<nome> <expressão>` | uma expressão regular de AS-path com nome. Aspas em volta são tiradas. Digitar o mesmo nome de novo substitui a expressão |
| `policy-statement` | `<nome>`  | cria ou edita uma política e entra nela |
| `no`    | `<declaração>` | remove uma lista, uma community, um as-path ou uma política, com tudo o que ela tem dentro |

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

**Condições do mesmo tipo valem como "ou"; de tipos diferentes, como "e".** Duas `from prefix-list` casam uma rota que esteja em qualquer das duas; uma `from prefix-list` e uma `from community` exigem as duas coisas. `from prefix-list` e `from route-filter` são do mesmo tipo: as duas falam do prefixo.

`from tag` e `from metric` valem por um valor só: um segundo, no mesmo term, fica no lugar do primeiro.

**Um term sem** `**from**` **casa com qualquer rota.** É assim que se escreve o "todo o resto" no fim de uma política.

## `set`: o que muda na rota

| Ação | Argumento | O que faz |
|------|-----------|-----------|
| `set local-preference` | `<0-4294967295>` | a preferência local do BGP |
| `set community` | `add <nome>` | acrescenta as communities do conjunto às que a rota já tem |
| `set community` | `set <nome>` | troca as communities da rota pelas do conjunto |
| `set community` | `delete <nome>` | tira da rota as communities do conjunto |
| `set as-path-prepend` | `<asn>...` | prepende esses ASNs, na ordem digitada |
| `set metric` | `<0-4294967295>` | a métrica: o MED no BGP, o custo no OSPF e no IS-IS |
| `set tag` | `<0-4294967295>` | a tag da rota |
| `set next-hop` | `<endereço>` | o próximo salto no BGP, IPv4 ou IPv6 |

Os `set` só são aplicados quando o term casa.

## `then`: como o term termina

| Comando | O que faz |
|---------|-----------|
| `then accept` | aceita a rota, com os `set` do term, e **para** a avaliação |
| `then reject` | rejeita a rota e para a avaliação |
| `then next term` | aplica os `set` e segue para o próximo term |
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

Para rejeitar o resto no BGP, termine a política com um term sem `from` e com `then reject`. Para exportar o resto para um IGP, termine com um term sem `from` e com `then accept`.

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

A política precisa existir: `import` ou `export` de um nome que nenhum `policy-statement` tem é recusado no prompt. Num vizinho BGP as rotas são do BGP, então um term com `from protocol` que não inclua `bgp` não se aplica ali.

Na configuração em vigor o vizinho aparece com a `route-map` em que a política foi traduzida: `neighbor 10.0.0.2 route-map PO-SAIDA-OUT out`. Todos os nomes que começam por `PO-` são dessa tradução, e uma `route-map` ou lista digitada à mão com esse começo é recusada.

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

Uma política escrita e ainda não usada em lugar nenhum diz `applied: nowhere in the running routing`.

`test policy` percorre os terms na ordem e para no que decide:

```
prisma> test policy SAIDA 203.0.113.0/24
  term 10: accept (set as-path-prepend 65000 65000)
      matches
result: accept (set as-path-prepend 65000 65000), by term 10
```

O teste parte só do prefixo. Uma condição que o prefixo sozinho não responde (community, as-path, protocolo, tag, métrica) não é adivinhada: o term aparece como "decide, se a rota tiver aquilo", e o teste segue como se não tivesse, para você ver os dois caminhos. Quando nenhum term decide, a resposta dá os dois padrões:

```
prisma> test policy SAIDA 198.51.100.0/24
  term 10: no match
      the prefix is in none of its lists or filters
  term 20: if the route's community match: reject
      the prefix alone does not say
result: no term decided it
    on a BGP neighbor that is accepted; exported into OSPF or IS-IS it is not
```

# O que é recusado

Tudo o que está abaixo é recusado **no prompt**, e nada é guardado na candidata.

| Você digitou | A caixa diz |
|--------------|-------------|
| um nome com caractere fora do permitido ou com mais de 32 | `a name is letters, digits, '.', '_' and '-', at most 32 characters` |
| `10.0.0.1/8` numa lista ou num `route-filter` | `'10.0.0.1/8' is not a network (address/length, host bits zero)` |
| `community C members 70000:1` | `'70000:1' is not a community: ASN:value, each 0-65535` |
| uma expressão de as-path que não compila | `the expression does not compile`, com o motivo |
| `from route-filter 10.0.0.0/8 between` | `route-filter: exact, orlonger, longer or upto /<length>` |
| `from route-filter 10.0.0.0/8 upto /4` | `the length is between 8 and 32` |
| `from protocol rip` | `from protocol: bgp, ospf, isis, static, direct or kernel` |
| `from metric 99999999999` | `from metric <0-4294967295>` |
| uma condição que não existe | `from: prefix-list, route-filter, community, as-path, protocol, tag or metric` |
| uma ação que não existe | `set: local-preference, community add\|set\|delete, as-path-prepend, metric, tag or next-hop` |
| `set next-hop abc` | `set next-hop: 'abc' is not an address` |
| `then` com outra coisa que não `accept`, `reject` ou `next term` | `then: accept, reject or next term; a change to the route is 'set ...'` |
| `no` de algo que não existe | `policy-options has no such statement; nothing removed`, com a declaração |
| `neighbor ... import` ou `export` de uma política que não existe, ou `export` num IGP | `no policy-statement named <nome>` |
| uma `route-map` ou lista com nome começado por `PO-` | `the PO- names belong to policy-options; nothing staged` |

## O que só existe no BGP, exportado para um IGP

Uma rota de OSPF ou de IS-IS não tem community, AS-path, preferência local nem próximo salto de BGP. Um term que fala dessas coisas é recusado na hora do `export`: a caixa responde `this policy cannot be exported into isis; nothing staged` (ou `ospf`) e, embaixo, uma linha por term culpado, dizendo o term e o que nele `is for BGP routes`.

O que conta como só de BGP: `from community`, `from as-path`, `set local-preference`, `set community`, `set as-path-prepend` e `set next-hop`. A recusa existe porque ignorar a condição em silêncio exportaria justamente o que o term foi escrito para segurar. Se a mesma lógica serve aos dois, escreva duas políticas: uma para o vizinho BGP, outra para o IGP.


:::warning
**Crie as listas antes de citá-las num term, e confira com** `**show policy <política>**` **e** `**test policy**` **antes do** `**commit**`**.** O term aceita o nome de uma `prefix-list`, `community` ou `as-path` sem conferir se ela existe. Uma política aplicada num vizinho decide o que a sessão carrega: um term que não faz o que você pensava derruba ou vaza prefixos na sessão inteira.

:::

# Ver também

* [Configuração de BGP](/doc/configuracao-de-bgp-9wJnuicEDZ) — vizinhos, peer groups e famílias de endereço, onde `import` e `export` são aplicados
* [Roteamento, SSH, usuários e SNMP](/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m) — o OSPF e o IS-IS, e as route-maps e listas escritas na forma do próprio roteamento
* [commit, rollback e save](/doc/commit-rollback-e-save-jqCB7kxPuA) — como a política vai para o roteamento e como desfazer
* [Comandos show](/doc/comandos-show-YlNji1X5W4) — `show policy` ao lado dos outros
