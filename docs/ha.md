# HexaRouting: alta disponibilidade

Este documento e analise e plano. **Nada de HA esta implementado.**

## O que o modo deterministico da de graca

Num CGNAT convencional, o mapeamento assinante -> (IP publico, porta) e
resultado de alocacao dinamica: existe so na tabela da caixa que o criou. Uma
caixa reserva nao sabe nada, e um failover troca o IP publico de todo mundo.

No modo deterministico o mapeamento e **funcao da configuracao**. Duas caixas
com o mesmo pool produzem exatamente o mesmo bloco para o mesmo assinante,
sem trocar um byte de estado.

Consequencias praticas de um failover:

| | NAT convencional | BPA deterministico |
|---|---|---|
| IP publico do assinante apos failover | muda | **o mesmo** |
| Bloco de portas | muda | **o mesmo** |
| Conexoes em curso | caem | caem |
| Conexoes novas | dependem de nova alocacao | funcionam de imediato |
| Estado a sincronizar para isso | tabela inteira | **nenhum** |

A linha que importa: as conexoes em curso caem nos dois casos, mas no BPA
deterministico elas **se restabelecem no mesmo endereco publico**. Para o
assinante, a diferenca entre "recarreguei a pagina e voltou" e "minha
conexao mudou de IP e o banco me deslogou".

Isso nao e HA - e o piso do qual o HA parte, e ele ja e mais alto que o
usual.

## O que ainda falta para failover de verdade

Manter conexao em curso exige sincronizar a tabela de sessao, porque o
caminho de volta precisa da **porta privada** original, e essa nao sai de
formula nenhuma (ver [architecture.md](architecture.md)).

Tres desenhos possiveis:

| Desenho | Custo | O que preserva |
|---|---|---|
| Ativo/reserva sem sincronismo | ~zero | endereco publico e bloco; conexoes caem |
| Sincronismo de sessao por multicast | alto: cada sessao criada vira mensagem | conexoes em curso |
| Ativo/ativo com particao de assinantes | medio | metade do trafego nao e afetada por uma queda |

O sincronismo de sessao e o unico que preserva conexao, e e o mais caro:
numa caixa de 200k assinantes com centenas de sessoes cada, replicar toda
criacao e remocao e um fluxo de controle comparavel ao proprio log. Vale
notar que o **modo dinamico** tem uma exigencia adicional: a alocacao de
bloco precisaria ser coordenada, ou as duas caixas alocariam blocos
diferentes para o mesmo assinante.

## Proposta para a primeira versao

Ativo/reserva com VRRP no lado publico e no lado interno, sem sincronismo
de sessao, e configuracao identica garantida pela trilha de configuracao que
ja existe.

E honesto sobre o que entrega: nao preserva conexao, preserva endereco. Para
um provedor, isso ja resolve o pior sintoma de failover de CGNAT.

O que precisa ser construido:

1. Integracao com `keepalived` ou o VRRP do FRR, coerente com o `linux_cp`
2. Verificacao de que as duas caixas tem a **mesma** configuracao de pool -
   configuracao divergente em modo deterministico e pior que nao ter reserva,
   porque o failover passaria a mapear assinantes para blocos errados sem
   nenhum erro visivel
3. Um `show bpa ha` que compare o hash da configuracao entre os pares
4. Teste de laboratorio com duas instancias e queda forcada, conferindo que o
   assinante mantem endereco e bloco

O item 2 e o que nao pode ser esquecido: em modo deterministico, duas caixas
com configuracao diferente falham em silencio.
