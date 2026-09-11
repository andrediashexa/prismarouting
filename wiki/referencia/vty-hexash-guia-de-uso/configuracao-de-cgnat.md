# Configuração de CGNAT

Comandos dentro de `configure`, na hierarquia `nat cgnat`.

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `nat cgnat pool <nome>` | `<word>`  | cria ou edita um pool (entra no modo pool) |
| `nat cgnat interface <if> inside\|outside` |           | habilita CGNAT numa interface, do lado interno ou externo |
| `nat cgnat block-holddown <s>` | `<1-65535>` | segundos que um bloco fica reservado depois de ocioso |
| `nat cgnat session-table buckets <n> memory <MB>` |           | dimensionamento da tabela de sessões |

`nat` sozinho **não é um bloco em que se entra** — é uma hierarquia. Digitado só, é recusado dizendo o que digitar no lugar:

```
error: nat: a hierarchy, not a block - 'nat cgnat pool <name>' or 'nat cgnat interface ...'
```

O bloco em que se entra continua sendo o pool: `nat cgnat pool <nome>`.


:::info
**A grafia antiga continua aceita.** Uma configuração salva antes desta mudança — e as revisões de rollback dela — está escrita como `cgnat pool ...`, sem o `nat` na frente, e é isso que a caixa relê a cada boot. Essas linhas continuam valendo, digitadas ou lidas de arquivo, e nada precisa ser convertido à mão.

O que mudou é o que a caixa **oferece** e o que ela **escreve**: TAB e `?` oferecem só `nat cgnat ...`, e `show running-config`, `show configuration` e o arquivo que o `save` grava saem na grafia nova. Um `save` depois desta versão já reescreve o arquivo na hierarquia nova.

:::

## Timeouts globais

`nat cgnat timeout <tipo> <segundos>`:

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
| `nat cgnat logging enable` | habilita exportação por UDP |
| `nat cgnat logging disable` | desabilita exportação por UDP |
| `nat cgnat logging tcp <coletor> [port <n>]` | exporta por TCP |

TCP existe porque a pergunta que importa não é "o coletor recebeu?", é **"o que acontece quando o coletor cai?"** — e com UDP a resposta é silêncio.

## Removendo

No modo de configuração:

```
no nat cgnat pool <nome>          remove um pool
no nat cgnat interface <nome>     desabilita CGNAT na interface
```

## Habilitando numa interface

```
hexarouting# [edit] nat cgnat interface TenGigabitEthernet0/0/0 inside
hexarouting# [edit] nat cgnat interface TenGigabitEthernet0/0/1 outside
```


:::warning
**Nunca entregue ao VPP a interface de gerência.** O DPDK tira a NIC do Linux. Entregue a porta pela qual você administra a máquina e ela fica inalcançável — e inalcançável é inconsertável remotamente. Identifique-a antes de configurar qualquer coisa, e confirme que ela não aparece em `nat cgnat interface`.

:::

# Ver também

* [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6) — os campos de dentro do pool
* [commit, rollback e save](/doc/commit-rollback-e-save-jqCB7kxPuA) — como aplicar o que foi configurado aqui
