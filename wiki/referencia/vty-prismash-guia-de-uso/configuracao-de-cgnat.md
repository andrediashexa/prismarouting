# Configuração de CGNAT

Toda a configuração de CGNAT fica dentro de uma hierarquia: entra-se em `nat`, depois em `cgnat`, e é ali que ficam pools, interfaces, logging e timeouts. Esta página diz o que existe em cada nível e como se anda entre eles.

```
prisma> configure
prisma# [edit] nat
prisma# [edit nat] cgnat
prisma# [edit nat / cgnat] pool residencial
prisma# [edit nat / cgnat / pool residencial] inside 100.64.0.0/22
prisma# [edit nat / cgnat / pool residencial] exit
prisma# [edit nat / cgnat] interface ens18 inside
```

O prompt diz sempre em que nível você está. `?` e TAB oferecem só o que vale naquele nível: em `nat` aparece `cgnat`; em `cgnat` aparecem `pool`, `interface`, `logging`, `timeout` e o resto desta página.

## Andando entre os níveis

| Comando | Onde você vai parar |
|---------|---------------------|
| `exit`  | um nível acima: do pool para `cgnat`, de `cgnat` para `nat`, de `nat` para o topo |
| `up`    | um nível acima, igual a `exit` |
| `top` ou `end` | o topo do modo de configuração, de qualquer nível |

`commit`, `commit check`, `show` e `rollback` funcionam em qualquer nível. Não é preciso sair para aplicar.

Em `nat` só existe `cgnat`. Qualquer outra coisa é recusada:

```
prisma# [edit nat] pool x
error: under 'nat' there is 'cgnat' and nothing else
    'cgnat' enters it; 'exit' leaves 'nat'
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
| `session-table buckets <n> memory <MB>` |           | dimensionamento da tabela de sessões |
| `timeout <tipo> <segundos>` |           | timeouts globais, na tabela abaixo |
| `logging ...` |           | exportação IPFIX, na tabela abaixo |
| `no pool <nome>` |           | remove um pool |
| `no interface <nome>` |           | desabilita CGNAT na interface |

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

## Logging IPFIX

Dentro de `cgnat`:

| Comando | O que faz |
|---------|-----------|
| `logging udp <coletor> src <origem> [port <n>]` | para onde vai a exportação por UDP |
| `logging enable` | habilita exportação por UDP |
| `logging disable` | desabilita exportação por UDP |
| `logging udp disable` | apaga o destino UDP |
| `logging tcp <coletor> [port <n>]` | exporta por TCP |

`src` não é opcional, e o destino tem que existir antes de `enable`: sem ele, `commit check` recusa e nada é aplicado. Sem `port`, vale 4739.

TCP existe porque a pergunta que importa não é "o coletor recebeu?", é **"o que acontece quando o coletor cai?"**. Com UDP, a resposta é silêncio.

Um pool dinâmico, ou determinístico com `reserve`, numa caixa que não exporta nada recebe um aviso no `commit check`: sem o registro, não há como dizer quem usava uma porta.

## Como a caixa grava

`show running-config`, `show configuration` e o arquivo que o `save` grava trazem o bloco inteiro, do jeito que se digita:

```
! cgnat
nat
 cgnat
  pool residencial
   inside 100.64.0.0/22
   outside 203.0.113.0/28
   block-size 512
   policy interleaved
   mode deterministic
   exit
  interface ens18 inside
  interface ens19 outside
  exit
 exit
```

Pools primeiro, depois as interfaces, depois os ajustes globais, todos no mesmo bloco.

## Habilitando numa interface

```
prisma# [edit nat / cgnat] interface ens18 inside
prisma# [edit nat / cgnat] interface ens19 outside
```


:::warning
**Nunca entregue ao VPP a interface de gerência.** O DPDK tira a NIC do Linux. Entregue a porta pela qual você administra a máquina e ela fica inalcançável, e uma caixa inalcançável não tem conserto remoto. Identifique a placa de gerência antes de configurar qualquer coisa e confirme que ela não aparece como `interface` dentro de `cgnat`.

:::

# Ver também

* [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6) — os campos de dentro do pool
* [Configurar o CGNAT passo a passo](/doc/configurar-o-cgnat-passo-a-passo-WMfuk8C8Q7) — do zero ao primeiro assinante traduzido
* [commit, rollback e save](/doc/commit-rollback-e-save-jqCB7kxPuA) — como aplicar o que foi configurado aqui
