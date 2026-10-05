# Configuração de CGNAT

A configuração de CGNAT fica dentro de uma hierarquia: entra-se em `nat`, depois em `cgnat`, e é ali que ficam pools, logging e timeouts. O lado de cada interface (`inside` ou `outside`) fica no bloco da própria interface. Esta página diz o que existe em cada nível e como se anda entre eles.

```
prisma> configure
prisma# [edit] nat
prisma# [edit nat] cgnat
prisma# [edit nat / cgnat] pool residencial
prisma# [edit nat / cgnat / pool residencial] prefix-inside 100.64.0.0/22
prisma# [edit nat / cgnat / pool residencial] exit
prisma# [edit nat / cgnat] top
prisma# [edit] interface ens18
prisma# [edit interface ens18] cgnat inside
```

O prompt diz sempre em que nível você está. `?` e TAB oferecem só o que vale naquele nível: em `nat` aparece `cgnat`; em `cgnat` aparecem `pool`, `interface`, `logging`, `timeout` e o resto desta página.

## Andando entre os níveis

| Comando | Onde você vai parar |
|---------|---------------------|
| `exit`  | um nível acima: do pool para `cgnat`, de `cgnat` para `nat`, de `nat` para o topo |
| `up`    | um nível acima, igual a `exit` |
| `up <n>` | `n` níveis acima de uma vez |
| `top` ou `end` | o topo do modo de configuração, de qualquer nível |

`commit` (com todas as suas variantes) e `show` funcionam em qualquer nível, inclusive dentro de um pool. Não é preciso sair para aplicar, e depois do `commit` você continua no nível em que estava. `rollback` funciona em `nat` e em `cgnat`; dentro de um pool, dê `exit` antes. `ping`, `traceroute` e `mtr` também respondem em qualquer nível.

Em `nat` existem dois níveis: `cgnat`, para os pools com dentro IPv4, e `nat64`, para os pools NAT64 (dentro IPv6). Qualquer outra coisa é recusada:

```
prisma# [edit nat] pool x
error: under 'nat' there is 'cgnat' and 'nat64'
    each enters its level; 'exit' leaves 'nat'
```


:::warning
**A forma de uma linha só não existe mais.** `nat cgnat pool residencial`, `nat cgnat interface ens18 inside` e a grafia mais antiga `cgnat pool ...` são recusadas no prompt e também num arquivo salvo:

```
prisma# [edit] nat cgnat pool residencial
error: the one-line CGNAT form is gone
    enter 'nat', then 'cgnat', and type the rest there: 'pool <name>',
    'interface <name> inside', 'logging ...'
```

Uma caixa cujo arquivo salvo ainda tem essa forma recusa o arquivo inteiro no boot. Depois de atualizar, e **antes de reiniciar**, rode `save`: ele grava o que está rodando já na forma nova. [Levar a caixa para uma versão nova](/doc/levar-a-caixa-para-uma-versao-nova-C09kO2kU8X) explica.

:::

## O que fica em `cgnat`

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `pool <nome>` | `<word>`  | cria ou edita um pool (entra no pool) |
| `interface <if> inside\|outside` |           | habilita CGNAT numa interface, do lado interno ou externo |
| `block-holddown <s>` | `<1-4294967295>` | segundos que um bloco fica reservado depois de ocioso |
| `session-table buckets <n> memory <MB>` |           | quantos buckets a tabela de sessões tem; o número de `memory` não dimensiona nada. Vale só antes do primeiro pool: com a tabela já criada, o dataplane recusa |
| `memory` | `<GB>`    | a memória dada às sessões do CGNAT, em GB inteiros; vale a partir do próximo reboot, e `show cgnat memory` diz se já está em vigor |
| `timeout <tipo> <segundos>` |           | timeouts globais, na tabela abaixo |
| `logging ...` |           | exportação dos eventos de bloco (IPFIX ou syslog), na tabela abaixo |
| `identity ...` |           | de onde a caixa lê quem estava num endereço privado, para pôr no registro; tabela abaixo |
| `no pool <nome>` |           | remove um pool |
| `no interface <nome>` |           | desabilita CGNAT na interface (a forma antiga; hoje é `no cgnat` no bloco da interface) |

Os campos de dentro de um pool estão na página [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6).

## Timeouts globais

`timeout <tipo> <segundos>`, dentro de `cgnat`:

| Tipo | O que expira |
|------|--------------|
| `udp` | sessões UDP  |
| `icmp` | sessões ICMP |
| `tcp-transitory` | TCP em transição |
| `tcp-established` | TCP estabelecido |

Um pool pode sobrescrever esses valores (ver a página de configuração de pool). Um pool sem timeout próprio herda o global.

Um nome de timeout que não é um dos quatro é recusado no prompt, com a lista dos que existem. Um valor abaixo do piso que a RFC daquele protocolo estabelece é **aplicado** e avisado no `commit`: a decisão é sua, e o aviso existe para que ela seja tomada sabendo. Os pisos, e o que quebra abaixo de cada um, estão em [Timeouts e os pisos das RFCs](/doc/timeouts-e-os-pisos-das-rfcs-GvPsvW3hLf).

## Logging IPFIX

Dentro de `cgnat`:

| Comando | O que faz |
|---------|-----------|
| `logging udp <coletor> src <origem> [port <n>]` | para onde vai a exportação IPFIX por UDP |
| `logging enable` | habilita a exportação por UDP |
| `logging disable` | desabilita a exportação por UDP |
| `logging udp disable` | apaga o destino UDP |
| `logging tcp <coletor> [port <n>]` | exporta IPFIX por TCP |
| `logging tcp disable` | para a exportação por TCP |
| `logging syslog <coletor> enterprise <n> [port <n>] [facility <0-23>] [severity <0-7>]` | exporta os mesmos eventos como linhas syslog RFC 5424, por TCP |
| `logging syslog disable` | para a exportação syslog |

`src` não é opcional, e o destino tem que existir antes de `enable`: sem ele, `commit check` recusa e nada é aplicado. Sem `port`, o IPFIX vai para a 4739 e o syslog para a 601.

TCP existe porque a pergunta que importa não é "o coletor recebeu?", é **"o que acontece quando o coletor cai?"**. Com UDP, a resposta é silêncio. Por TCP — IPFIX ou syslog — o que o coletor não recebeu fica guardado na caixa e é reenviado, em ordem, quando ele volta.

O syslog existe para o coletor que não fala IPFIX, que é a maioria. O `enterprise` é obrigatório e é o **seu** número de empresa privada na IANA: a RFC 5424 reserva os nomes sem esse sufixo para o que a própria IANA registrou, e toda linha que a caixa escreve o carrega. Sem ele o `commit` recusa a linha.

UDP, TCP e syslog são independentes: dá para exportar por mais de um ao mesmo tempo.

## Quem estava no endereço: `identity`

Uma ordem judicial nomeia uma pessoa; o registro nomeia um endereço privado num instante. `identity` faz o registro carregar a identidade do assinante, lida no instante da alocação, para que ninguém precise cruzar duas bases sob pressão.

| Comando | De onde a identidade vem |
|---------|--------------------------|
| `identity file <caminho>` | um arquivo com uma linha por assinante: `<endereço-interno> <identidade>` |
| `identity dhcp-leases <caminho>` | o arquivo de leases do servidor DHCP (formato ISC dhcpd); só lease ativo conta |
| `identity radius secret <segredo> [port <n>]` | o accounting RADIUS que o BNG já manda; sem `port`, 1813 |
| `identity radius disable` | para de ouvir o accounting |
| `identity none` | nenhuma tabela: todo registro diz `unknown` |

A caixa **não** é um servidor RADIUS: ela só ouve o accounting, confere cada pacote com o segredo compartilhado e descarta sem resposta o que não confere. Sem `secret` o `commit` recusa a linha.

O caminho é configuração; o conteúdo do arquivo não é, e **nenhuma tela mostra uma identidade**. `show cgnat identity` diz quantas a caixa tem e quando as leu, e o segredo do RADIUS sai como `<set, not shown>` no `show running-config`.

Um pool dinâmico, ou determinístico com `reserve`, numa caixa que não exporta nada recebe um aviso no `commit check`: sem o registro, não há como dizer quem usava uma porta.

## Como a caixa grava

`show running-config`, `show configuration` e o arquivo que o `save` grava trazem o bloco inteiro, do jeito que se digita:

```
! cgnat
nat
 cgnat
  pool residencial
   prefix-inside 100.64.0.0/22
   prefix-outside 203.0.113.0/28
   block-size 512
   policy interleaved
   mode deterministic
   exit
  exit
 exit
interface ens18
 cgnat inside
 exit
interface ens19
 cgnat outside
 exit
```

Dentro de `nat` / `cgnat` ficam os pools e depois os ajustes globais. O lado de cada interface aparece no bloco dela, junto com o endereço, a descrição e o MTU: quem lê uma porta vê ali mesmo que os assinantes chegam por ela.

## Habilitando numa interface

O lado do CGNAT é uma propriedade da interface e se digita no bloco dela:

```
prisma# [edit] interface ens18
prisma# [edit interface ens18] cgnat inside
prisma# [edit interface ens18] exit
prisma# [edit] interface ens19
prisma# [edit interface ens19] cgnat outside
prisma# [edit interface ens19] exit
```

`inside` é a porta por onde os assinantes chegam; `outside`, a porta por onde o tráfego traduzido sai. `no cgnat`, dentro do bloco, tira a interface do CGNAT.

A forma antiga, `interface ens18 inside` dentro de `nat` / `cgnat`, continua aceita, para que roteiros e scripts já escritos sigam funcionando. A caixa avisa onde a linha passa a aparecer:

```
prisma# [edit nat / cgnat] interface ens18 inside
  note: kept as 'cgnat inside' in the block of interface ens18
```

Um arquivo salvo por uma versão anterior, com as linhas dentro de `cgnat`, é lido normalmente, e o próximo `save` grava cada uma no bloco da sua interface.


:::info
**A placa que vai para o dataplane não se escolhe aqui.** Ela é nomeada em `system` > `dataplane` > `interface <placa>`, e `show hardware` marca qual delas carrega a rota padrão — o caminho por onde a caixa é gerenciada. Numa caixa com outra placa, a caixa recusa entregar essa; numa caixa de uma placa só, o endereço de gerência vai junto para o par que o dataplane mantém. O passo a passo está em [Primeiros passos](/doc/primeiros-passos-TAWDdSrYG7).

:::

# Ver também

* [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6) — os campos de dentro do pool
* [Configurar o CGNAT passo a passo](/doc/configurar-o-cgnat-passo-a-passo-WMfuk8C8Q7) — do zero ao primeiro assinante traduzido
* [commit, rollback e save](/doc/commit-rollback-e-save-jqCB7kxPuA) — como aplicar o que foi configurado aqui
