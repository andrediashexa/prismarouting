# Configuração de CGNAT

Comandos dentro de `configure`, no prefixo `cgnat`.

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `cgnat pool <nome>` | `<word>`  | cria ou edita um pool (entra no modo pool) |
| `cgnat interface <if> inside\|outside` |           | habilita CGNAT numa interface, do lado interno ou externo |
| `cgnat block-holddown <s>` | `<1-65535>` | segundos que um bloco fica reservado depois de ocioso |
| `cgnat session-table buckets <n> memory <MB>` |           | dimensionamento da tabela de sessões |

## Timeouts globais

`cgnat timeout <tipo> <segundos>`:

| Tipo | O que expira |
|------|--------------|
| `udp` | sessões UDP  |
| `icmp` | sessões ICMP |
| `tcp-transitory` | TCP em transição |
| `tcp-established` | TCP estabelecido |

Um pool pode sobrescrever esses valores — ver a página de configuração de pool. Um pool sem timeout próprio herda o global.

## Logging IPFIX

| Comando | O que faz |
|---------|-----------|
| `cgnat logging enable` | habilita exportação por UDP |
| `cgnat logging disable` | desabilita exportação por UDP |
| `cgnat logging tcp <coletor> [port <n>]` | exporta por TCP |

TCP existe porque a pergunta que importa não é "o coletor recebeu?", é **"o que acontece quando o coletor cai?"** — e com UDP a resposta é silêncio.

## Removendo

No modo de configuração:

```
no cgnat pool <nome>          remove um pool
no cgnat interface <nome>     desabilita CGNAT na interface
```

## Habilitando numa interface

```
hexarouting# [edit] cgnat interface TenGigabitEthernet0/0/0 inside
hexarouting# [edit] cgnat interface TenGigabitEthernet0/0/1 outside
```


:::warning
**Nunca entregue ao VPP a interface de gerência.** O DPDK tira a NIC do Linux. Entregue a porta pela qual você administra a máquina e ela fica inalcançável — e inalcançável é inconsertável remotamente. Identifique-a antes de configurar qualquer coisa, e confirme que ela não aparece em `cgnat interface`.

:::

# Ver também

* [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6) — os campos de dentro do pool
* [commit, rollback e save](/doc/commit-rollback-e-save-jqCB7kxPuA) — como aplicar o que foi configurado aqui
