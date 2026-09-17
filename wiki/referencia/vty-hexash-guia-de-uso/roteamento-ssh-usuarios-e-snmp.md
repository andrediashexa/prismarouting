# Roteamento, SSH, usuários e SNMP

Tudo que não é CGNAT: roteamento, SSH, contas e SNMP. Todas essas mudanças exigem a capacidade de **administrador** (`hexa-admin` ou root).

# Roteamento (FRR)

```
router static
router bgp <AS>
router ospf
router ospf6
router isis <nome>
```

Tudo que roteia fica debaixo de `router`: os protocolos, e também as rotas que ninguém aprende — as estáticas. Os protocolos são do FRR, alcançado pelo TAP do `linux_cp`; depois de entrar no protocolo, a sintaxe é a do FRR.

`router ospf` e `router ospf6` são **instâncias separadas**, não duas metades da mesma coisa: a primeira carrega IPv4, a segunda carrega IPv6, e uma caixa pode rodar as duas ao mesmo tempo. Elas também não ligam a interface do mesmo jeito. O OSPFv2 aceita `network <prefixo> area <área>` dentro do bloco; o OSPFv3 não tem `network` nenhum - quem coloca a interface na área é `ipv6 ospf6 area <área>` dentro do bloco `interface`:

```
configure
 router ospf6
  ospf6 router-id 10.0.0.1
  exit
 interface TenGig0/8/0
  ipv6 ospf6 area 0
  exit
 commit
```

Se você digitar só o `router ospf6` e esperar que ele encontre as interfaces sozinho, a configuração é aceita e nenhuma adjacência sobe: falta a linha na interface.

## Rotas estáticas: o bloco `router static`

Uma rota estática não precisa de protocolo nenhum e é a primeira coisa que se configura numa caixa nova:

```
configure
 router static
  route 0.0.0.0/0 200.150.0.1
  route 2001:db8::/32 2001:db8:0:1::1
  exit
 commit
```

Dentro do bloco:

| Comando | O que faz |
|---------|-----------|
| `route <prefixo> <próximo-salto\|interface\|blackhole\|Null0> [distância]` | uma rota estática |
| `no route <prefixo> ...` | remove uma rota estática |
| `exit`  | sai do bloco |

**Há um só** `**route**`**, não um para cada família.** A família sai do prefixo: prefixo com dois-pontos é IPv6 e não pode ser outra coisa. Pedir a família outra vez seria pedir ao operador a chance de se contradizer.


:::info
**A grafia antiga continua aceita.** `ip route 0.0.0.0/0 200.150.0.1` e `ipv6 route ...` digitados no topo do modo de configuração continuam valendo, e é assim que está escrita toda configuração salva antes desta mudança — inclusive as revisões de rollback que a caixa relê a cada boot. O que TAB e `?` oferecem é só `router static`, e é nessa forma que `show running-config`, `show configuration` e o arquivo do `save` passam a mostrar as estáticas: um bloco `router static` com as rotas dentro.

:::

`commit check` recusa um próximo salto que não está em nenhuma rede conectada:

```
error: this next hop is on no connected network: 172.31.99.1
    router static / route 10.55.0.0/16 172.31.99.1
    the route would be accepted and never reach the forwarding table
```

Aceita, essa rota ficaria no FRR e nunca chegaria à tabela de encaminhamento.

Repare que a recusa nomeia a rota **no bloco em que ela está** - `router static / route ...` - e não como uma linha solta. É assim mesmo quando você digitou na grafia antiga, `ip route ...`: a caixa mostra onde a rota mora na configuração, que é onde você vai editá-la. Uma estática dentro de uma VRF não tem grafia de topo nenhuma para procurar, e é por isso que o bloco aparece.

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
prismarouting> show route
  Destination          Next hop          Interface    Kind
  0.0.0.0/0            200.150.0.1       ens19        forward
  203.0.113.0/24       200.150.0.1       ens19        forward
  200.150.0.0/24       connected         ens19        connected

  3 entries carrying traffic
```


:::warning
`show ip route` responde pela tabela do FRR. Numa caixa que está descartando o tráfego, a rota aparece **presente e correta** ali — porque quem encaminha é a FIB do VPP, e a resposta do FRR não diz nada sobre a rota ter chegado lá.

Quando o tráfego não passa e a rota parece certa, use `show route verbose`: ele compara as duas e marca onde discordam.

:::

A coluna VPP diz o que o VPP **tem** — `forward`, `connected`, `local`, `neighbour`, `drop` ou `absent` — e não sim/não. Um prefixo que o VPP descarta de propósito e um que ele nunca ouviu falar são situações diferentes, com causas diferentes.

Endereços locais e rotas por interfaces que o VPP não possui — a de gerência, por exemplo — **não** são marcadas como discordância. Elas estão no FRR e ausentes da FIB corretamente e para sempre.

## Quais daemons rodam

`zebra`, `mgmtd` e `staticd` sobem sempre. `bgpd`, `ospfd`, `ospf6d` e `isisd` são habilitados pelo instalador - o pacote do Debian os traz desligados, e uma caixa instalada sem isso oferecia os protocolos no VTY sem rodar nenhum.

`commit check` recusa um `router <protocolo>` cujo daemon não está de pé:

```
error: router bgp 65001: bgpd is not running
    the statement would commit and reach nothing. Enable bgpd in
    /etc/frr/daemons and restart frr
```

O autoteste de boot informa quais estão respondendo.

## Drenar a caixa para manutenção: o bit de sobrecarga

É a drenagem do IS-IS. Com ele ligado, a caixa diz a todo mundo para **não transitar por ela**: o que termina nela continua sendo alcançado, mas ela deixa de ser caminho de passagem — que é o que você quer antes de mexer num equipamento em produção.

```
configure
 router isis HEXA
  set-overload-bit
  exit
 commit
```

`no set-overload-bit` tira. Aceita também `set-overload-bit on-startup <0-86400>`, que carrega o bit por esse tanto de segundos depois que o protocolo sobe — para a caixa que acabou de reiniciar não atrair tráfego de passagem antes de ter convergido.

O modo de falha desta função não é ligá-la: é **esquecer ligada**. Uma caixa drenada funciona, atende, responde a tudo que você perguntar, e não carrega o tráfego que deveria — por semanas, se ninguém olhar.

Por isso a conferência:

```
show isis adjacency
```

Estando ligado, a tela diz `overload bit set`.


:::info
Essa linha é lida do **LSP que esta caixa está inundando**, não da configuração que pediu o bit. A diferença é o ponto: uma drenagem reportada pelas palavras que a pediram é reportada por uma linha que pode não ter surtido efeito. O que a tela afirma é o que os vizinhos estão recebendo.

Do lado do vizinho, o mesmo fato aparece no `show isis database`, na última coluna `ATT/P/OL` da linha do LSP desta caixa: `OL` é o terceiro dos três, e vale `0/0/1` quando o bit está ligado.

:::

# Três armadilhas de roteamento

## 1. MTU herdada trava o OSPF

O TAP do `linux_cp` vem com 9000. Uma interface que herda esse valor sem o outro lado concordar deixa a adjacência OSPF parada em Exchange. Ajuste com `mtu` dentro do bloco `interface` — ele muda a interface do dataplane **e** o TAP dela, juntos.

Vale igual para o OSPFv3: ele também compara MTU antes de fechar a adjacência, e trava do mesmo jeito, sem log nenhum dizendo por quê. O `commit check` avisa quando as duas metades da interface discordam; `ipv6 ospf6 mtu-ignore` é a saída quando os dois lados não podem ser igualados.

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
  linux-name <nome-no-linux>
  mtu <576-9216>
  urpf strict|loose|off
  exit
```

| Comando | Efeito |
|---------|--------|
| `linux-name` | cria o par pelo qual o roteamento e o `ip` enxergam esta interface |
| `mtu`   | ajusta a MTU **na interface do dataplane e no TAP dela** — as duas juntas |
| `urpf strict` | a origem precisa rotear de volta por esta interface |
| `urpf loose` | a origem precisa ter rota em algum lugar |
| `urpf off` | sem checagem (padrão) |

uRPF numa interface interna é o que impede uma origem forjada de ser traduzida.


:::warning
**Uma interface sem** `**linux-name**` **não existe para o roteamento.** BGP, OSPF e IS-IS aceitam ser configurados sobre ela, o `commit` passa limpo, a estrofe aparece na configuração em vigor — e nenhuma adjacência sobe, porque o roteamento não tem interface nenhuma com aquele nome.

É a primeira coisa a conferir numa porta nova que "está configurada e não fala com ninguém".

:::

O bloco de interface inteiro — endereços, VLAN, VRF, MAC, descrição, loopbacks — está em [Configuração de interface](/doc/configuracao-de-interface-j1q58jKWTQ).

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
  role administrator|cgnat-operator
  ssh-key <tipo> <chave> [comentário]
  password <hash>
  no role
  no ssh-key <chave>
  no password
  exit
```

`**password**` **recebe um hash crypt, nunca uma senha em texto claro.** Gere com:

```sh
openssl passwd -6
```

## `role` — o que a conta pode mudar

| Valor | O que a conta pode mudar |
|-------|--------------------------|
| `role administrator` | roteamento, CGNAT, contas e a própria máquina |
| `role cgnat-operator` | só configuração de CGNAT |
| sem `role` | somente leitura          |

**A configuração é quem decide.** O papel é escrito no bloco do usuário, sai em `show running-config` e sobrevive ao `save` — e o `commit` o materializa nos grupos do sistema, acrescentando os que o papel exige e **retirando** os que ele não exige mais. Uma promoção feita por fora, direto no grupo, é desfeita no commit seguinte: é aqui que ela se escreve.

Além do grupo do papel, toda conta criada aqui entra nos grupos que dão **alcance** — os sockets do dataplane e os do roteamento. Sem eles a conta tem posto e não consegue exercê-lo: todo comando falha por permissão, inclusive os de leitura. É por isso que reaplicar o papel pela VTY conserta uma conta cujas participações saíram do lugar.

`no role` devolve a conta a somente leitura. Um papel que não existe é recusado com a lista do que existe:

```
error: role 'supervisor': expected one of administrator, cgnat-operator
```

E `root` não recebe papel — os direitos dele vêm de ser root, não de grupo:

```
error: root's rights come from being root, not from a group
    this VTY does not manage root's memberships
```

O veredito fica na primeira linha e o motivo na segunda, dentro da largura do console. Como frase única ela passava de cem colunas e o terminal a quebrava onde calhasse.

A recusa vale para qualquer papel dado ao `root`, inclusive o nome certo de um papel que existe: não é o papel que está errado, é a conta.

# SNMP

O agente é **somente leitura por construção** — não existe caminho de escrita para desabilitar.

```
configure
 snmp enabled yes|no
 snmp listen-address <endereço>
 snmp port <1-65535>
 snmp enterprise-number <1-4294967295>
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
prismarouting# [edit] ip ?
  prefix-list  a named list of prefixes, for matching in policy
prismarouting# [edit] ip prefix-list <TAB>
  PL-CLIENTES   ip prefix-list
```

A completação sai da mesma fonte que o `show running-config` responde, então ela não oferece um nome que a caixa não tem.


:::warning
**Uma política precisa dos daemons de roteamento de pé.** Prefix-list e route-map não pertencem a nenhum protocolo, então não é o `bgpd` que as guarda — e com o daemon dono parado o roteamento **aceita a linha e não guarda nada**, sem erro.

O `commit` recusa nesse caso e diz qual daemon falta e qual arquivo editar:

```
error: 2 policy statement(s) need mgmtd, which is not running
    first one: ip prefix-list PL-CLIENTES seq 10 permit 200.150.0.0/24
    they would commit and store nothing. mgmtd is started by frr itself, not
    listed in /etc/frr/daemons - so frr did not finish starting. 'systemctl
    restart frr' and read its journal
```

O conselho é **diferente** do que sai para `bgpd`, e de propósito: `zebra`, `mgmtd` e `staticd` não estão em `/etc/frr/daemons`, quem os sobe é o próprio `frr`. Se um deles não está de pé, não há linha para habilitar - o que houve foi o `frr` não terminar de subir, e a resposta está no journal dele. As estáticas respondem com a mesma frase, trocando `staticd` por `mgmtd`.

Uma estrofe `router bgp` que não vai a lugar nenhum se percebe: nenhuma sessão sobe. Uma prefix-list que não vai a lugar nenhum é invisível até o tráfego que devia ser filtrado não ser.

:::

Avisos do roteamento agora chegam ao operador mesmo quando o `commit` tem sucesso:

```
frr: The route-map 'RM-CLIENTES' does not exist.
commit complete
```

Antes essa linha era descartada, e a pessoa saía com uma política apontando para nada.

## O que `commit complete` passou a significar

Depois de aplicar, a caixa **lê de volta a configuração que o FRR está rodando** e compara com o que você escreveu. Se uma declaração que este commit introduziu não estiver lá, ele recusa e diz quais.

Isso existe porque a ferramenta que aplica a configuração pode terminar com sucesso tendo descartado uma linha. O sintoma é o pior que existe: você sai da janela de manutenção achando que aplicou, e o problema aparece dias depois, sem ninguém ligar uma coisa à outra.


:::info
**Nem tudo que o FRR aceita ele guarda, e isso não é erro.** Três formas, todas medidas nesta caixa e não deduzidas:

* **Ele omite.** `ip ospf hello-interval 10` é o padrão dele, então ele aceita e não grava. Mas `ip ospf dead-interval 40` também é o padrão e esse ele grava — ou seja, **não dá para prever pelo valor**, é declaração por declaração.
* **Ele reescreve.** `passive-interface vppwan0` volta como `ip ospf passive`, dentro da estrofe da interface e não da do roteador. `continue 20` volta como `on-match goto 20`. O próprio FRR avisa quando você digita a primeira.
* **Ele já tinha ligado.** `neighbor 10.0.0.2 activate` numa família ipv4-unicast é implícito, então ele aceita e não grava nada.

A caixa conhece essas formas e não reclama delas. Você não precisa mudar como escreve.

:::

Se aparecer uma recusa aqui, a leitura é: **o que esta caixa guarda e o que o FRR está rodando não são a mesma coisa.** `show configuration` mostra a desta caixa; `start shell` e depois `vtysh -c "show running-config"` mostra a do FRR.

# Listas de AS-path e de comunidade

São elas que uma route-map consulta em `match as-path`, `match community` e `match large-community`.

```
bgp as-path access-list AS-CLIENTES permit ^65001(_65001)*$
bgp community-list standard CM-NAO-EXPORTAR permit 65000:666
bgp large-community-list standard LC-REGIAO permit 65000:1:1
```

`?` e TAB completam os nomes que já existem na caixa, nos dois lugares: onde a lista é criada e onde a route-map a consulta.

## Uma expressão que não compila é recusada na hora

O FRR compila a expressão **na hora de aplicar**, ou seja, no meio do commit. Aqui ela é compilada quando você digita:

```
error: the regular expression does not compile: missing ), unterminated
    subpattern at position 6
    bgp as-path access-list AS-BAD permit ^65001(
```

## Uma expressão que compila e casa a coisa errada

Esse é o erro comum, e ele **não** pode ser recusado — a expressão é legítima. `permit 65001` compila, é válida, e também casa `165001` e `650012`: sem `^` e `$`, uma comparação exata vira comparação por pedaço.

E o `_` do FRR não é o caractere sublinhado: ele quer dizer **qualquer fronteira** — começo, fim ou separador entre ASNs.

Ler a própria expressão de volta não mostra isso. Contar contra os caminhos que a caixa **realmente recebeu** mostra:

```
note: AS-LOOSE '65002' matches 2 of 5 received AS path(s); e.g. '65001 65002'
```

Se o número vier muito maior do que você esperava, falta âncora. Se vier zero, a lista existe e não filtra nada.


:::info
Quando a caixa ainda não recebeu nenhum caminho, o equipamento diz isso em vez de responder zero. "Casou 0" e "não havia contra o que casar" são respostas diferentes, e só uma delas fala da sua expressão.

:::

## Um nome que ninguém definiu é avisado

```
warning: no AS-path list called 'AS-CLIENTES' is defined; until one is, the
    clause matches nothing
    match as-path AS-CLIENTES
```

A cláusula commita e não casa nada enquanto a lista não existir. **Não é recusa**: o roteamento aceita a cláusula e passa a usá-la sozinho no momento em que a lista aparecer, então escrever a política primeiro e as listas depois é uma ordem de trabalho legítima — e recusar o commit por causa disso derrubaria junto qualquer outra mudança que estivesse nele.

O aviso continua valendo a leitura: se a lista não vai aparecer, o nome tem um erro de digitação e a cláusula não casa nada para sempre — e nada mais na caixa fala sobre isso.

As duas grafias de lista de comunidade valem como definição: `bgp community-list standard NOME` e `bgp community-list NOME`.

## Como conferir

* `commit check` mostra as contagens e as recusas sem tocar na caixa;
* `show bgp ipv4 unicast` mostra os caminhos recebidos, que é contra o que a contagem foi feita;
* `show route` mostra o que **encaminha** — se a política era para tirar rota, é aí que ela some.

# Route-maps: a política em si

Uma route-map é uma lista de cláusulas numeradas. Cada cláusula diz **a quais rotas se aplica** (`match`) e **o que muda nelas** (`set`). A primeira cláusula que casa decide; as de baixo não são consultadas.

```
ip prefix-list PL-MEUS-BLOCOS seq 10 permit 200.150.8.0/22

route-map RM-TRANSITO-OUT permit 10
 match ip address prefix-list PL-MEUS-BLOCOS
 set as-path prepend 65000 65000
exit
route-map RM-TRANSITO-OUT permit 20
exit

router bgp 65000
 address-family ipv4 unicast
  neighbor 200.150.0.2 route-map RM-TRANSITO-OUT out
 exit-address-family
 exit
```

`?` e TAB dentro da cláusula listam o que `match` e `set` entendem, e completam nomes que já existem na caixa: prefix-lists, listas de AS-path, listas de comunidade, outras route-maps e vizinhos.

## O que é recusado e o que é apenas dito

**Cláusula** `**deny**` **com** `**set**` **é recusada.** O `set` nunca roda — a rota foi negada. É engano toda vez.

**Cláusula** `**permit**` **sem** `**match**` **é avisada, não recusada.** Ela libera tudo. Isso é um jeito comum e proposital de terminar uma política — e é também exatamente o que uma política pela metade parece. O equipamento não consegue distinguir, e ninguém consegue depois, então ele fala:

```
warning: no 'match': this permits every route. If that is meant, it is the end
    of the policy; if not, the policy is unfinished
    route-map RM-TRANSITO-OUT permit 20
```

Se era a intenção, o aviso é ruído de uma linha. Se não era, é a diferença entre uma política que filtra e uma que deixa passar tudo.

`**set local-preference**` **com um valor que não é número é recusado.** A gramática do roteamento para essa cláusula aceita **qualquer palavra**: `set local-preference banana` é aceito no prompt, passa no `commit check` e vai parar na configuração em vigor do BGP — onde não ajusta nada. A cláusula fica lá, com cara de política, sem fazer nada:

```
error: 'banana' is not a local-preference: expected 0-4294967295. FRR takes
    this statement with any word in it and then sets nothing
    set local-preference banana
```

O equipamento recusa aqui porque o roteamento não recusa em lugar nenhum. É o contrário de `maximum-paths` e `set weight`, em que o roteamento é a autoridade e recusa no `commit`: ali o equipamento só informa a faixa e deixa a recusa com quem manda.

A recusa **só sai para o que esta mudança está pedindo**. Um `set local-preference` desses que a caixa já carregava não volta a ser recusado em todo commit: uma caixa com uma linha dessas na configuração em vigor ficaria com todo commit recusado, inclusive os que não têm nada a ver com route-map, e recusar uma mudança por causa de um estado que já estava lá tranca o operador para fora do próprio equipamento.

**Cláusula que nenhuma rota alcança é avisada.** Se a cláusula 10 libera tudo, a 20 nunca é consultada. Os números de sequência são seus e o equipamento não renumera nada — mas uma política que parece certa e não faz nada merece uma frase.

Quem manda na ordem é o **número de sequência**, não a ordem em que as cláusulas foram digitadas. Inserir uma cláusula 5 depois de já existir uma 10 que libera tudo põe a nova na frente, e é para isso que os números existem: o equipamento lê na mesma ordem que o roteamento e não avisa nada.

`on-match next` e `continue` mudam esse percurso — a cláusula que casa não termina a política, a leitura segue adiante. Numa route-map que tem uma dessas, o equipamento não diz mais quais cláusulas são alcançáveis: para onde a leitura vai depende de a cláusula ter casado ou não, e isso não está na configuração.

## O que não foi conferido aparece marcado

A maior parte de uma route-map continua passando direto para o roteamento. Sem marcação, quem lê a configuração não distingue o que foi conferido do que foi apenas repassado — e acaba lendo garantia nos dois:

```
route-map RM-CLIENTES permit 10
 match ip address prefix-list PL-CLIENTES
 set tag 100  <- not checked
```

A marca é só da exibição: ela não vai para a configuração salva, nem para uma revisão de rollback, nem para o roteamento. E se você copiar a tela para um arquivo e carregar de volta, ela é retirada na leitura — a tela e o arquivo não divergem.

## Como conferir

* `show running-config` mostra as route-maps como o equipamento as lê de volta, com o que não foi conferido marcado;
* `commit check` mostra os avisos sem tocar na caixa;
* `show route` mostra o que **encaminha** — se a política era para mudar o caminho, é aqui que a mudança aparece.

### Faixa de prefixo: `ge` e `le`

Numa entrada de prefix-list com `ge` e `le`, vale `comprimento da máscara ≤ ge ≤ le ≤ 32` (ou `≤ 128`, em IPv6). Quando não vale, a linha é recusada no `commit` e no `commit check`, com o nome da sentença:

```
error: prefix range refused for 10.0.0.0/8: mask length <= ge <= le <= 32 does
    not hold (length 8, ge 24, le 16)
    ip prefix-list PL-CLIENTES seq 10 permit 10.12.0.0/8 ge 24 le 16
```

A sentença que foi recusada vai numa **linha própria**, inteira, embaixo do motivo - é assim que ela continua procurável na tela do `show configuration`, que é onde você vai corrigi-la.

Repare que no motivo o prefixo aparece normalizado - `10.12.0.0/8` vira `10.0.0.0/8`, porque `/8` só olha o primeiro octeto. A linha de baixo traz a sentença como você a escreveu, e é por ela que se procura.


:::info
Essa recusa acontece **antes** de a linha chegar ao roteamento. É de propósito: recusada mais adiante, ela também derrubava o commit seguinte, com a mesma mensagem apontando para um prefixo que não estava em configuração nenhuma — nem a em vigor, nem a candidata. Quem visse aquilo procuraria o prefixo na própria configuração e não acharia nada para corrigir.

:::

## O prefixo público de um pool precisa ser anunciado

```
warning: pool 'clientes': nothing here announces 200.150.0.16/28
    subscribers behind it would be translated to an address the internet cannot route back to
    announce it from another box, or originate it here with
    'ip route 200.150.0.16/28 blackhole' and 'network 200.150.0.16/28' under the BGP address family
```

As duas últimas linhas são a parte acionável: o aviso não só diz que falta anúncio, diz as duas linhas que resolvem quando é aqui que se quer anunciar.

Esta é a única verificação que cruza as duas metades do equipamento. Configure o CGNAT certo, configure o BGP certo, e o serviço pode estar morto assim mesmo: se nada anuncia um prefixo que **cobre** o lado público do pool, todo assinante atrás dele é traduzido para um endereço que a internet não sabe devolver. O tráfego sai e nada volta.

As duas metades são configuradas em lugares diferentes, por pessoas diferentes, em dias diferentes — e nada ligava uma à outra.


:::info
O aviso entende **cobertura**, não igualdade: anunciar `200.150.0.0/24` cobre um pool em `200.150.0.16/28`.

Ele **não recusa** o commit — anunciar de outro equipamento é um desenho legítimo. E ele **se cala** quando há `redistribute`, porque aí não consegue saber. Um aviso que dispara numa caixa que funciona é ignorado, e o próximo, verdadeiro, é ignorado junto.

:::

# Ver também

* [Acesso, login e papéis](/doc/acesso-login-e-papeis-VcE7gIbI1U) — o que cada grupo pode mudar
* [VTY hexash — guia de uso](/doc/vty-hexash-guia-de-uso-h6sDAK8Dsh) — os modos e a árvore de comandos
