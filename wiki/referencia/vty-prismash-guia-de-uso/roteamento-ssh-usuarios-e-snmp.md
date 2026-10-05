# Roteamento, SSH, usuários e SNMP

Tudo que não é CGNAT: rotas estáticas, OSPF e IS-IS, política de roteamento, a placa do dataplane, DNS, SSH, contas e SNMP — cada declaração com o argumento e o efeito. Todas essas mudanças exigem a capacidade de **administrador** (`prisma-admin` ou root).

# Roteamento (FRR)

```
router static
router bgp <AS>
router ospf [vrf <nome>]
router ospf6
router isis <nome>
```

Tudo que roteia fica debaixo de `router`: os protocolos, e também as rotas que ninguém aprende — as estáticas. Os protocolos são do FRR. Dentro de cada bloco, `?` e TAB oferecem as declarações que esta VTY modela (as tabelas abaixo); o resto da sintaxe do FRR também é aceito, e cada linha é conferida pelo próprio FRR na hora em que é digitada — uma linha que ele não aceita é recusada no prompt, com a resposta dele, e nada é guardado. O BGP tem página própria: [Configuração de BGP](/doc/configuracao-de-bgp-9wJnuicEDZ).

`router ospf` e `router ospf6` são **instâncias separadas**, não duas metades da mesma coisa: a primeira carrega IPv4, a segunda carrega IPv6, e uma caixa pode rodar as duas ao mesmo tempo. Elas também não ligam a interface do mesmo jeito. O OSPFv2 aceita `network <prefixo> area <área>` dentro do bloco; o OSPFv3 não tem `network` nenhum - quem coloca a interface na área é `ipv6 ospf6 area <área>` dentro do bloco `interface`:

```
configure
 router ospf6
  ospf6 router-id 10.0.0.1
  exit
 interface ens18
  ipv6 ospf6 area 0
  exit
 commit
```

Se você digitar só o `router ospf6` e esperar que ele encontre as interfaces sozinho, a configuração é aceita e nenhuma adjacência sobe: falta a linha na interface.

## O que o bloco `router ospf` oferece

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `ospf router-id` | `<A.B.C.D>` | o identificador desta caixa no OSPF |
| `network` | `<A.B.C.D/M> area <área>` | roda OSPF nas interfaces que esse prefixo cobre |
| `area`  | `<área> ...` | ajustes de uma área, na sintaxe do FRR |
| `passive-interface` | `<interface>` | roda OSPF na interface sem mandar hello — o que toda porta voltada a assinante deve ser. O FRR guarda isso como `ip ospf passive` no bloco da interface |
| `default-information originate` | `[always] [metric <n>] [metric-type 1\|2] [route-map <nome>]` | anuncia `0.0.0.0/0` |
| `export` | `<política>` | exporta para o OSPF o que uma política de `policy-options` aceitar; o que nenhum termo aceita **não** é exportado |
| `redistribute connected\|static\|bgp\|isis` | `[metric <n>] [metric-type 1\|2] [route-map <nome>]` | injeta rotas de outra origem. `bgp` numa caixa de borda é uma tabela inteira dentro do IGP: dê a ele uma route-map |
| `auto-cost reference-bandwidth` | `<1-4294967>` | em Mbit/s. Sem isso, todo enlace de 100 Mbit/s para cima custa 1, e numa caixa de 10G não há escolha de caminho |
| `maximum-paths` | `<1-256>` | quantos caminhos de mesmo custo instalar |
| `log-adjacency-changes` | `[detail]` | registra cada mudança de adjacência |
| `timers throttle spf` | `<atraso> <espera-inicial> <espera-máxima>` | em milissegundos |
| `distance` | `<1-255>` | distância administrativa |
| `neighbor` | `<A.B.C.D> [poll-interval <n>] [priority <n>]` | um vizinho numa rede não-broadcast |

## O que o bloco `router ospf6` oferece

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `ospf6 router-id` | `<A.B.C.D>` | o identificador desta caixa no OSPFv3; tem forma de endereço IPv4 e não é um endereço |
| `area`  | `<área> ...` | ajustes de uma área |
| `redistribute connected\|static\|bgp\|isis` | `[route-map <nome>]` | injeta rotas de outra origem |
| `default-information originate` | `[always] [metric <n>] [metric-type 1\|2] [route-map <nome>]` | anuncia `::/0` |
| `auto-cost reference-bandwidth` | `<1-4294967>` | como no OSPF |
| `maximum-paths` | `<1-256>` | quantos caminhos de mesmo custo instalar |
| `log-adjacency-changes` | `[detail]` | registra cada mudança de adjacência |
| `timers throttle spf` | `<atraso> <espera-inicial> <espera-máxima>` | em milissegundos |
| `distance` | `<1-255>` | distância administrativa |

Não há `network` nem `export` no OSPFv3: a interface entra pela linha `ipv6 ospf6 area` no bloco dela.

## O que o bloco `router isis <nome>` oferece

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `net`   | `<49.xxxx.xxxx.xxxx.xxxx.00>` | o endereço NSAP desta caixa; sem ele a instância não sobe |
| `is-type` | `level-1\|level-2-only\|level-1-2` | de quais níveis a caixa participa; o núcleo de um provedor costuma rodar `level-2-only` |
| `metric-style` | `wide\|narrow\|transition` | `narrow` para em 63 e não expressa uma topologia de 10G para cima; `transition` manda os dois durante uma migração |
| `topology ipv6-unicast` |           | um SPF separado para IPv6 (RFC 5120) |
| `set-overload-bit` | `[on-startup <0-86400>]` | diz a todos para não transitarem por esta caixa; ver a drenagem abaixo |
| `export` | `<política>` | exporta para o IS-IS o que uma política aceitar; o que nenhum termo aceita não é exportado |
| `redistribute ipv4\|ipv6` | `<origem> level-1\|level-2 [metric <n>] [route-map <nome>]` | injeta rotas de outra origem |
| `default-information originate` | `ipv4\|ipv6 level-1\|level-2 [always] [route-map <nome>]` | anuncia uma rota default |
| `domain-password` | `clear\|md5 <senha>` | autenticação de nível 2; nunca é mostrada de volta |
| `area-password` | `clear\|md5 <senha>` | autenticação de nível 1; nunca é mostrada de volta |
| `log-adjacency-changes` |           | registra cada mudança de adjacência |
| `lsp-mtu` | `<128-4352>` | o maior LSP que a caixa gera; maior que a menor MTU das interfaces, eles não saem |
| `lsp-gen-interval` | `<1-120>` | segundos entre regenerações de LSP |
| `spf-interval` | `<1-120>` | segundos entre execuções do SPF |
| `max-lsp-lifetime` | `<350-65535>` | segundos que um LSP vale |

As declarações de OSPF e IS-IS que vão **na interface** (`ip ospf ...`, `ipv6 ospf6 ...`, `ip router isis`, `isis ...`) estão em [Configuração de interface](/doc/configuracao-de-interface-j1q58jKWTQ).

`export <política>` recusa uma política que dependa do que só uma rota BGP carrega — comunidade, AS-path, local-preference, prepend, próximo salto —, em vez de ignorá-lo em silêncio. As políticas são escritas em `policy-options`, descrito em [Configuração de BGP](/doc/configuracao-de-bgp-9wJnuicEDZ); `test policy <política> <prefixo>` diz o que ela faz com uma rota antes de ser aplicada.

## Rotas estáticas: o bloco `router static`

Uma rota estática não precisa de protocolo nenhum e é a primeira coisa que se configura numa caixa nova:

```
configure
 router static
  0.0.0.0/0 next-hop 200.150.0.1
  192.168.0.0/24 next-hop 172.16.0.1 distance 200 tag 100 description link para o POP-2
  2001:db8::/32 next-hop 2001:db8:0:1::1
  exit
 commit
```

Dentro do bloco:

| Comando | O que faz |
|---------|-----------|
| `<prefixo> next-hop <próximo-salto\|interface\|blackhole\|Null0\|reject>` | uma rota estática |
| `... distance <1-255>` | a distância administrativa; sem ela vale a do roteamento |
| `... tag <1-4294967295>` | uma marca numérica, que uma política de redistribuição pode casar |
| `... bfd [multi-hop] [source <endereço>] [profile <nome>]` | a rota sai da tabela enquanto a sessão BFD até o próximo salto estiver caída (veja a seção BFD, abaixo) |
| `... description <texto>` | um texto seu sobre a rota, até 63 caracteres; vai sempre por último |
| `no <prefixo> next-hop <próximo-salto>` | remove a rota, com o que ela tiver de distância, tag e descrição |
| `exit`  | sai do bloco |

`distance` e `tag` podem vir em qualquer ordem. **Uma rota é o prefixo e o próximo salto:** digitar o mesmo par de novo muda aquela rota, não cria uma segunda ao lado. Para mudar só a descrição, digite a rota inteira de novo com a descrição nova.

**A família sai do prefixo.** Prefixo com dois-pontos é IPv6 e não pode ser outra coisa, então não existe um comando para cada família. Pedir a família outra vez seria pedir ao operador a chance de se contradizer.

**A descrição fica guardada na própria caixa**, porque o roteamento não tem onde guardá-la. Ela aparece em `show configuration` e `show running-config` junto da rota, entra no `commit` e no `rollback` como qualquer outra mudança, e some sozinha se a rota deixar de existir.


:::warning
**A forma** `**route <prefixo> <próximo-salto>**` **dentro do bloco não existe mais.** Ela é recusada no prompt e também num arquivo salvo. Uma caixa cujo arquivo salvo ainda tem essa forma recusa o arquivo inteiro no boot e sobe sem configuração nenhuma. Numa caixa que tem rota estática, rode `save` depois de atualizar e antes de reiniciar: ele grava as rotas em vigor já na forma nova. [Levar a caixa para uma versão nova](/doc/levar-a-caixa-para-uma-versao-nova-C09kO2kU8X) explica.

:::

`commit check` recusa um próximo salto que não está em nenhuma rede conectada:

```
error: this next hop is on no connected network: 172.31.99.1
    router static / 10.55.0.0/16 next-hop 172.31.99.1
    the route would be accepted and never reach the forwarding table
```

Aceita, essa rota ficaria no FRR e nunca chegaria à tabela de encaminhamento.

Repare que a recusa nomeia a rota **no bloco em que ela está** - `router static / 10.55.0.0/16 next-hop ...` - e não como uma linha solta. É assim mesmo quando a rota foi digitada no topo, na grafia do roteamento, `ip route ...`: a caixa mostra onde a rota mora na configuração, que é onde você vai editá-la. Uma estática dentro de uma VRF não tem grafia de topo nenhuma para procurar, e é por isso que o bloco aparece.

## BFD: a queda do vizinho em milissegundos

O BGP só percebe que o vizinho morreu quando o hold time acaba - 90 segundos por padrão - e uma rota estática nunca percebe: ela continua apontando para um próximo salto que parou de encaminhar. O caso que importa é o vizinho que **para de responder com o enlace de pé** - um switch no meio que perdeu a VLAN, uma placa travada -, porque aí nenhuma interface cai para avisar ninguém. O BFD troca pacotes pequenos com o vizinho em intervalos de milissegundos e derruba a sessão quando eles param de chegar.

```
configure
 bfd
  profile rapido
   receive-interval 100
   transmit-interval 100
   detect-multiplier 3
  exit
  peer 10.0.0.2 interface enp1s0
   profile rapido
  exit
 exit
 router bgp 65000
  neighbor 10.0.0.2 bfd profile rapido
 exit
 router static
  203.0.113.0/24 next-hop 10.0.0.2 bfd profile rapido
 exit
 commit
```

Com 100 ms e multiplicador 3, a sessão cai depois de 300 ms sem pacote. Medido no laboratório, com o vizinho mudo e o enlace de pé: o BFD viu a queda em 252 ms, a rota estática saiu da tabela de encaminhamento em 252 ms e a rota aprendida por BGP em 325 ms - com o hold time do BGP em 30 segundos. Sem o BFD, nenhuma das duas saiu em 12 segundos.

Dentro de `bfd`:

| Comando | O que faz |
|---------|-----------|
| `peer <endereço> [multihop] [local-address <endereço>] [interface <interface>] [vrf <nome>]` | uma sessão com um vizinho; entra no bloco dela |
| `profile <nome>` | um conjunto de tempos com nome, que sessões e protocolos usam; entra no bloco dele |
| `exit`  | sai do bloco |

Dentro de um `peer` ou de um `profile`:

| Comando | O que faz |
|---------|-----------|
| `detect-multiplier <2-255>` | quantos pacotes perdidos até a sessão cair (padrão 3) |
| `receive-interval <10-4294967>` | o intervalo mais lento em que esta caixa quer receber, em ms (padrão 300) |
| `transmit-interval <10-4294967>` | o intervalo mais rápido em que esta caixa envia, em ms (padrão 300) |
| `echo-mode` | manda pacotes de eco que o vizinho devolve; só IPv4, a não ser que o vizinho também seja FRR |
| `echo receive-interval` e `echo transmit-interval` | os intervalos do eco, em ms (padrão 50) |
| `passive-mode` | espera o vizinho falar primeiro |
| `minimum-ttl <1-254>` | só multihop: o menor TTL aceito (padrão 254) |
| `shutdown` | segura a sessão derrubada, mantendo a configuração |
| `profile <nome>` | só dentro de um `peer`: a sessão usa os tempos daquele perfil; o que for configurado na própria sessão vale por cima |

O mesmo `profile <nome>` quer dizer duas coisas: no nível do `bfd` ele **cria** o perfil, dentro de um `peer` ele **usa** o perfil. O prompt mostra onde você está.

Quem pede a sessão é o protocolo: `neighbor <endereço> bfd [profile <nome>]` no BGP ([Configuração de BGP](/doc/configuracao-de-bgp-9wJnuicEDZ)), e `bfd` depois do próximo salto numa rota estática. Um `peer` no bloco `bfd` serve para dar à sessão tempos próprios, e para ela existir mesmo sem nenhum protocolo pedindo.

Para ver:

* `show bfd peers` - duas linhas por sessão: onde ela roda (salto único ou multihop, a interface, a origem, o perfil, e se foi pedida por um protocolo), e depois o estado, há quanto tempo, o intervalo, em quanto tempo ela cai, o diagnóstico quando há um e quantas vezes caiu.
* `show bfd peer <endereço>` - cada sessão com aquele vizinho em detalhe: os tempos desta caixa e os do vizinho, o tempo de detecção, o eco, os pacotes que entraram e saíram, e quantas vezes subiu e caiu.

No OSPF, no OSPFv3 e no IS-IS o BFD vai na interface, com ou sem perfil: `ip ospf bfd`, `ip ospf bfd profile <nome>` (e `ipv6 ospf6 bfd ...`, `isis bfd ...`). A caixa escreve a linha `bfd` sozinha antes da do perfil, porque o FRR recusa o perfil sem ela.

As sessões também saem pela API, campo a campo e com um aviso a cada mudança de estado, e pelo SNMP, na BFD-STD-MIB (RFC 7331), que o agente da própria caixa responde. Ainda não há trap de sessão caindo: para alarme, consulte a tabela da MIB periodicamente.


:::warning
**Numa caixa atualizada por uma versão de correção, o BFD só funciona depois do reboot.** Quem roda as sessões é o `bfdd`, e ele é ligado pelo contêiner de roteamento quando esse contêiner sobe na versão nova - o que, numa atualização de correção, acontece no próximo reboot. Até lá o `commit` recusa qualquer configuração com `bfd`, dizendo que o `bfdd` não está rodando e que o reboot o liga. É de propósito: sem o `bfdd`, o BGP e a rota estática aceitam a palavra `bfd` e nenhuma sessão é criada - o vizinho ficaria vigiado por ninguém.

:::

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
prisma> show route
  Destination    Next hop    Interface Kind
  0.0.0.0/0      200.150.0.1 ens19     forward
  200.150.0.0/24 -           ens19     connected
  203.0.113.0/24 200.150.0.1 ens19     forward

  3 entries carrying traffic
```

As colunas têm a largura do que a caixa tem de fato. Quando uma rota não cabe no console — um prefixo IPv6 longo, por exemplo —, cada rota vira um pequeno bloco, com `via:`, `on:` e `kind:` em linhas próprias, em vez de uma tabela que o terminal quebraria no meio.


:::warning
`show ip route` responde pela tabela do FRR. Numa caixa que está descartando o tráfego, a rota aparece **presente e correta** ali — porque quem encaminha é a FIB do VPP, e a resposta do FRR não diz nada sobre a rota ter chegado lá.

Quando o tráfego não passa e a rota parece certa, use `show route verbose`: ele compara as duas e marca onde discordam.

:::

A coluna VPP diz o que o VPP **tem** — `forward`, `connected`, `local`, `neighbour`, `drop` ou `absent` — e não sim/não. Um prefixo que o VPP descarta de propósito e um que ele nunca ouviu falar são situações diferentes, com causas diferentes.

Endereços locais e rotas por interfaces que o VPP não possui — a de gerência, por exemplo — **não** são marcadas como discordância. Elas estão no FRR e ausentes da FIB corretamente e para sempre.

## Quais daemons rodam

`zebra`, `mgmtd` e `staticd` sobem sempre. `bgpd`, `ospfd`, `ospf6d` e `isisd` são habilitados pelo instalador - o pacote do Debian os traz desligados, e uma caixa instalada sem isso oferecia os protocolos no VTY sem rodar nenhum. O `bfdd`, que roda as sessões BFD, é ligado pelo próprio contêiner de roteamento ao subir, e por isso chega também por uma atualização de correção - mas só a partir do próximo reboot.

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

As declarações de interface que importam para o roteamento — `mtu`, que muda a interface do dataplane **e** o TAP dela juntos, `urpf strict|loose|off`, e as linhas de OSPF e IS-IS na porta — estão com o resto do bloco em [Configuração de interface](/doc/configuracao-de-interface-j1q58jKWTQ). uRPF numa interface interna é o que impede uma origem forjada de ser traduzida.


:::info
**Toda interface com um bloco** `**interface**` **existe para o roteamento com o próprio nome.** Não há linha a escrever: o par que o roteamento enxerga é criado sozinho. `linux-name` só serve para dar **outro** nome. Uma porta que "está configurada e não fala com ninguém" é outro assunto; o self-test do boot diz quais portas ficaram sem par, se alguma ficou.

:::

# Sistema: a placa entregue ao dataplane

```
configure
 system
  dataplane
   interface enp2s0
   exit
  exit
 commit
```

| Comando | Onde | O que faz |
|---------|------|-----------|
| `interface <placa>` | `system` > `dataplane` | entrega esta placa, pelo nome que o **Linux** dá a ela, ao dataplane no próximo início dele; um nome que o Linux não tem é recusado com a lista dos que ele tem |
| `no interface` | `system` > `dataplane` | a caixa deixa de pegar placa no próximo início do dataplane |
| `control-plane`, `main-thread`, `workers`, `cgnat-workers` | `system` > `cpu` | os núcleos por papel |
| `control-plane`, `buffers` | `system` > `memory` | a memória por papel |

As três valem a partir do próximo boot ou do próximo início do dataplane: o `commit` grava a escolha e **não reinicia nada**. `cpu` e `memory` estão explicados em [Dimensionamento e requisitos](/doc/dimensionamento-e-requisitos-f42dC4k45s), e a escolha da placa em [Configuração de interface](/doc/configuracao-de-interface-j1q58jKWTQ).

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

| Ajuste | Padrão |
|--------|--------|
| `ssh port` | `22`   |
| `ssh listen-address` | `0.0.0.0` |
| `ssh permit-root-login` | `prohibit-password` |
| `ssh password-authentication` | `yes`  |

`listen-address` tem de ser um endereço: qualquer outra coisa é recusada no prompt, porque o SSH recusaria o próprio arquivo e não subiria — numa caixa cujo outro caminho é um cabo serial.

**O** `**commit**` **recusa uma configuração em que ninguém mais consegue entrar por SSH**, ou em que ninguém que entra consegue mudar nada — tirar o papel do último administrador, por exemplo. O console não conta: pode não estar ao alcance de quem está configurando.

# Nome da caixa (hostname)

```
configure
 hostname cgnat-sp1
 commit
```

O nome da caixa, num lugar só: o do Linux (o que `hostname` responde, o `/etc/hostname` e a linha `127.0.1.1` do `/etc/hosts`) e o `sysName` que o SNMP responde. Antes da versão 0.145.0, mudar o nome era editar esses arquivos à mão.

O nome segue as regras de hostname: letras, dígitos e hífens, com pontos separando as partes, até 64 caracteres, e **começando por letra** — o FRR recusa qualquer outro. `no hostname` volta ao nome padrão da instalação, `prisma-routing`.

`show running-config` mostra o nome que a caixa tem agora. Uma configuração salva antes da 0.145.0 não tem essa linha e não muda o nome da caixa ao carregar; o primeiro `save` depois do upgrade passa a gravá-la.

A partir da versão 0.148.0 **o prompt mostra o nome da caixa**: `cgnat-sp1>` no modo operacional e `cgnat-sp1# [edit]` no de configuração. Ele muda logo depois do `commit` que renomeia a caixa, na mesma sessão. Uma caixa com o nome padrão da instalação (`prisma-routing`) continua mostrando `prisma>`, como nas telas deste guia. Um nome com mais de 20 caracteres aparece cortado, com um `~` no fim.

Os daemons do FRR recebem o nome no `commit`, mas continuam mostrando o nome com que foram iniciados.

# DNS

```
configure
 dns server 1.1.1.1
 dns server 2606:4700::1111
 commit
```

Os servidores DNS que a caixa consulta, sem editar `/etc/resolv.conf` à mão. Até três, consultados na ordem em que foram digitados. Um quarto é recusado, porque o sistema só consulta os três primeiros e um quarto apareceria na configuração sem nunca ser usado. `no dns server <endereço>` remove um.

**Numa caixa com gerência por DHCP, o DHCP para de mexer no DNS enquanto houver** `**dns server**` **configurado.** Sem isso, a próxima renovação do lease colocaria de volta os servidores do DHCP, sem aviso. Removido o último `dns server`, a caixa volta a usar o que o DHCP (ou a instalação) tinha definido.

`show running-config` mostra os `dns server` que foram configurados pela VTY. Os servidores recebidos do DHCP não aparecem ali, porque não são uma configuração sua.

**Comando por ssh cai no VTY.** `ssh <conta>@<caixa> "show cgnat pool"` é executado pelo VTY, com o papel da conta. Só root e administradores (`prisma-admin`) recebem um shell do sistema, seja por comando direto, por `scp`/`rsync` ou quando o VTY falha. Uma conta de CGNAT ou somente leitura que tenta um comando do sistema recebe uma explicação e a sessão termina.

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
 no user <nome>
```

| Comando | O que faz |
|---------|-----------|
| `role administrator\|cgnat-operator` | o que a conta pode mudar; sem papel, somente leitura |
| `ssh-key <tipo> <chave> [comentário]` | uma chave pública autorizada, como aparece no `authorized_keys`; pode repetir |
| `password <hash>` | um hash crypt; texto claro é recusado |
| `no ssh-key <chave>` | tira uma chave; o começo dela basta |
| `no user <nome>` | remove a conta; `root` não pode ser removido — restrinja-o com `ssh permit-root-login` |

O hash aparece como `password <set, not shown>` em `show this` e `show configuration`.

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

| Comando | O que define | Padrão |
|---------|--------------|--------|
| `snmp enabled` | roda o agente ou não | `no`   |
| `snmp listen-address` | endereço de escuta | `127.0.0.1` |
| `snmp port` | porta UDP    | `161`  |
| `snmp enterprise-number` | o Private Enterprise Number IANA da sua organização | nenhum |
| `snmp community` | uma comunidade SNMPv2c, somente leitura | nenhuma |

Com o padrão `127.0.0.1`, o agente só responde de dentro da caixa: para o NMS alcançá-lo, `listen-address` precisa ser um endereço que ele alcança.

**Ligar o agente sem comunidade e sem usuário v3 é recusado**: seria um agente que ninguém consegue consultar. A comunidade e as senhas dos usuários v3 aparecem como `<set, not shown>` em `show configuration`.


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

O agente só atende um usuário v3 com autenticação **e** privacidade: configure os dois, `auth` e `priv`.

## Política de roteamento: o que a VTY confere

Prefix-lists e route-maps são escritos na sintaxe do roteamento e guardados por ele. A VTY completa os nomes que a caixa já tem:

```
prisma# [edit] ip ?
  prefix-list  a named list of prefixes, for matching in policy
prisma# [edit] ip prefix-list <TAB>
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

# API de gerência (gRPC)

A partir da versão 0.148.4 a caixa oferece uma API de gerência em gRPC (`prisma.v1`), para a interface web e outros sistemas que precisem configurar e ler a caixa sem passar pelo terminal. Ela **não é um segundo jeito de configurar**: cada chamada de configuração é a própria VTY trabalhando — o `configure`, as linhas que você digitaria, o `commit check`, o `commit`, o `commit confirmed`, o `rollback` e o `save` —, então uma recusa pela API é a mesma recusa da VTY, com as mesmas palavras, e uma mudança feita pela API aparece no `show running-config`.

Ela vem **desligada**. Para ligar, você precisa de três coisas:


1. **Os certificados.** A API só aceita TLS mútuo: a caixa tem o próprio certificado e só atende quem apresentar um certificado de cliente assinado pela autoridade (CA) que você indicar. Coloque em `/etc/prisma-routing/api/` os arquivos `server.key`, `server.crt` e `clients-ca.crt` (a CA dos clientes). Com OpenSSL, numa máquina qualquer (a chave da CA, `ca.key`, fica fora da caixa):

```sh
# a CA que assina os clientes
openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=prisma clients" -keyout ca.key -out ca.crt
# o certificado da caixa, com o endereço que o cliente usa
openssl req -newkey rsa:3072 -nodes -subj "/CN=prisma-api" -keyout server.key -out server.csr
printf 'subjectAltName=IP:10.40.200.166\n' > server.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -extfile server.ext -out server.crt
# o certificado do cliente: o CN é a conta da caixa que ele usa
openssl req -newkey rsa:3072 -nodes -subj "/CN=webui" -keyout webui.key -out webui.csr
printf 'extendedKeyUsage=clientAuth\n' > client.ext
openssl x509 -req -in webui.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -extfile client.ext -out webui.crt
```

Na caixa vão `server.key`, `server.crt` e o `ca.crt` com o nome `clients-ca.crt`; o cliente usa `ca.crt`, `webui.crt` e `webui.key`. 2. **A conta.** O certificado do cliente diz, no campo CN, **qual conta da caixa** ele é. A chamada tem exatamente as permissões que essa conta tem na VTY: `prisma-admin` configura tudo, `prisma-cgnat` só o CGNAT, e qualquer outra só lê. Tudo o que ela muda fica registrado no histórico em nome dela. 3. **O endereço.** A API escuta **só na rede de gerência**:

```
configure
api enabled yes
api listen-address 10.40.200.166
api port 50051
commit
```

Ligar sem dizer o endereço é recusado no commit:

```
error: api enabled yes needs 'api listen-address <management address>'
```

Para desligar, `no api enabled` e `commit`.

O que a API oferece, em resumo: abrir uma cópia da configuração (o candidato), editar com as linhas da VTY, comparar, validar, aplicar (inclusive com confirmação), confirmar, voltar uma revisão e salvar; ler interfaces, pools, assinantes, BGP, OSPF, IS-IS, VRRP e bundles; rodar qualquer `show`; e receber ao vivo os contadores das interfaces e os totais do CGNAT. Se alguém mudar a configuração enquanto o seu candidato está aberto, o commit dele é recusado mostrando a diferença, em vez de passar por cima.

Desde a 0.149.2 ela também:

* **lista a tabela de rotas** já separada em campos (VRF, prefixo, próximos saltos, protocolo, distância, métrica, se foi a escolhida), em páginas, com filtro por VRF, família, prefixo e protocolo — pensada para a tabela cheia: a primeira página chega enquanto o resto ainda está sendo lido, e a lista inteira é uma fotografia só, sem rota repetida nem faltando mesmo que a tabela mude no meio. E dá a contagem por VRF e protocolo;
* diz a **memória** da caixa em números e a **carga de cada worker** do plano de dados — pacotes por chamada e a fração do tempo gasta com pacotes, porque o CPU de um worker que faz polling fica sempre em 100%;
* diz a **velocidade do link** de cada interface, para calcular a utilização em %;
* guarda **24 horas de histórico** dos contadores, uma amostra por minuto, que sobrevive a um reinício da API (um upgrade, por exemplo): o gráfico da interface web não recomeça do zero;
* põe a **revisão** e o **comentário** no aviso de cada commit, para o cliente saber quais commits foram dele;
* responde ao **health check** padrão do gRPC, com o mesmo certificado de cliente;
* diz a quem chama **qual conta ele é e o papel dela** (administrador, operador de CGNAT ou só leitura), sem abrir um candidato.

Ela também lê **em campos o resto das telas do CGNAT**: quem estava atrás de um endereço público e uma porta, e se a resposta vale para qualquer instante (o pool determinístico, pela fórmula) ou só para agora (um bloco dinâmico ou de reserva, que já foi de outro); os assinantes mais perto de esgotar os blocos; os descartes; a memória e quantas sessões ainda cabem; os blocos de cada worker; quantas identidades a caixa tem (nunca uma identidade); a verificação da trilha de configuração; os ALGs ligados em cada pool; os encaminhamentos de porta; e se esta caixa e o par de HA mapeiam os assinantes do mesmo jeito. Num pool NAT64 o assinante vem como o prefixo IPv6 dele. Cada resposta traz junto a tela da VTY, igual ao que o terminal mostra, porque é a mesma leitura.

E lê em campos **as telas que faltavam**: o `show ospf interface` (cada interface com a área, o estado, o custo, os vizinhos, o MTU que o OSPF anuncia ao lado do MTU do dataplane, e se os dois travam a adjacência), o `show isis interface` (os circuitos, os que estão sem vizinho e se o dataplane está entregando o IS-IS ao sistema), o `show hardware` (as placas que o Linux tem, qual leva a rota padrão e o que o dataplane já pegou) e o `show logging` (o log de cada daemon de roteamento). O `show snmp mib` e o template do Zabbix vêm **como arquivo**, já com o número de empresa da caixa, para importar direto no NMS.

O `ping`, o `traceroute` e o `mtr` chegam **linha a linha, enquanto rodam**, cada resposta ou salto já em campos e um resumo no fim. Valem as mesmas regras da VTY: o mesmo endereço, a mesma contagem (até 100), o mesmo limite de 300 segundos, e a caixa recusa com as mesmas palavras. Se quem chamou desistir no meio, a caixa para o teste.

# Ver também

* [Acesso, login e papéis](/doc/acesso-login-e-papeis-VcE7gIbI1U) — o que cada grupo pode mudar
* [Configuração de interface](/doc/configuracao-de-interface-j1q58jKWTQ) — OSPF e IS-IS na porta, MTU, uRPF
* [Configuração de BGP](/doc/configuracao-de-bgp-9wJnuicEDZ) — vizinhos, `policy-options` e RPKI
* [Monitoramento e SNMP](/doc/monitoramento-e-snmp-YGn85RpUAb) — o que o agente SNMP responde
* [VTY prismash — guia de uso](/doc/vty-prismash-guia-de-uso-h6sDAK8Dsh) — os modos e a árvore de comandos
