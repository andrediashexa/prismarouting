# Exportacao de log BPA via IPFIX/NetFlow

Registro de **alocacao de bloco**, nao de fluxo. E o que torna o CGNAT
auditavel sem gerar terabytes por dia.

## O ponto de partida: quanto se loga em cada modo

| Modo | O que se loga | Volume |
|------|---------------|--------|
| Deterministico | **nada** por assinante; so mudanca de configuracao | ~zero |
| Dinamico (PBA) | alocacao e liberacao de bloco | ~1 registro por bloco |
| NAT tradicional (referencia) | cada fluxo | 1 registro por conexao |

No deterministico a prova de quem usou `(IP publico, porta, instante)` e o
algoritmo mais a configuracao do pool. Nao existe evento a registrar - e
exatamente por isso o modo existe. O que **precisa** existir e uma trilha de
auditoria da configuracao: se o pool mudou em alguma data, e preciso saber
qual era a configuracao vigente naquele instante, senao a formula responde a
pergunta errada. Ver "Trilha de configuracao" abaixo.

No dinamico ha eventos reais, e e ai que o IPFIX entra.

## Norma: RFC 8158

A RFC 8158 define elementos IPFIX para log de eventos de NAT, e traz dois
valores de `natEvent` feitos exatamente para BPA:

| natEvent | Significado |
|---------:|-------------|
| 16 | Port block allocation |
| 17 | Port block de-allocation |
| 12 | NAT ports exhausted |
| 18 | Threshold reached |

### Template do registro (Tabela 21 da RFC 8158)

| IE | Nome | Bits | Obrigatorio | Conteudo no hexarouting |
|---:|------|-----:|-------------|---------------------|
| 323 | timeStamp | 64 | sim | instante do evento, UTC |
| 230 | natEvent | 8 | sim | 16 ou 17 |
| 8 | sourceIPv4Address | 32 | sim | IP do assinante |
| 225 | postNATSourceIPv4Address | 32 | sim | IP publico atribuido |
| 361 | portRangeStart | 16 | sim | `port_lo` do bloco |
| 362 | portRangeEnd | 16 | nao | `port_hi` do bloco |
| 283 | natPoolId | 32 | nao | indice do pool |
| 463 | natInstanceID | 32 | nao | identificador da caixa |

Sao ~25 bytes de dados por registro. O VPP ja conhece `natEvent`,
`natPoolId`, `portRangeStart`, `portRangeEnd` e `postNATSourceIPv4Address`
em `vnet/ipfix-export/ipfix_info_elements.h`, e `flow_report.h` ja cuida de
template, numero de sequencia e transporte. Nao ha IPFIX para escrever do
zero: so montar o template e emitir o registro.

## Por que liberacao tambem precisa ser logada

Este e o ponto que costuma passar batido, e ele muda a resposta juridica.

Logando **so alocacao**:

```
t0  bloco B alocado ao assinante X      -> registro
t1  bloco B liberado por inatividade    -> sem registro
t2  bloco B alocado ao assinante Y      -> registro
```

Pergunta judicial: quem usava `(IP, porta em B)` no instante `t1.5`?

Uma consulta que busca "a ultima alocacao anterior ao instante" responde
**X**. Errado: em `t1.5` ninguem detinha aquele bloco. O provedor entregaria
um assinante inocente.

Em `t0.5` e `t2.5` a mesma consulta acerta. O erro so aparece na lacuna, que
e justamente onde ninguem testa. Por isso `natEvent 17` nao e organizacao:
e correcao.

## Volume esperado

Premissas explicitas, para ordem de grandeza e nao precisao:

- 200 mil assinantes
- modo dinamico, bloco renegociado ~5x por dia por assinante ativo
- ~25 bytes de dados por registro, ~40 bytes com enquadramento IPFIX

| Cenario | Registros/dia | Volume bruto/dia |
|---------|--------------:|-----------------:|
| BPA dinamico (aloc + liberacao) | ~2 milhoes | ~80 MB |
| NAT por fluxo (referencia) | ~40 milhoes | ~2,4 GB |
| BPA deterministico | 0 | 0 |

Guarda de 1 ano no dinamico fica na casa de dezenas de GB, nao de dezenas
de TB. E o argumento comercial do BPA.

## Transporte

A RFC 7011 admite SCTP, TCP e UDP.

- **UDP**: simples e o mais aceito pelos coletores, mas perde registro sob
  congestionamento. Perder um `natEvent 17` recria exatamente a lacuna da
  secao anterior.
- **TCP**: entrega confiavel, ao custo de bloquear quando o coletor cai.
- **SCTP**: o que a norma prefere, com pouca adocao pratica no Brasil.

Proposta: TCP como padrao para o log de alocacao, com buffer em disco local
quando o coletor estiver inacessivel. Log de compliance nao pode ter perda
silenciosa. UDP fica disponivel para quem so quer telemetria.

## Trilha de configuracao (vale para os dois modos)

No deterministico a configuracao **e** o log. Entao toda mudanca de pool
precisa de registro persistente e datado: prefixos, `block_size`, faixa de
portas, politica. Sem isso, uma consulta sobre uma data anterior a ultima
mudanca de configuracao responde com a formula errada.

Formato proposto: um arquivo append-only assinado, com o mesmo carimbo de
tempo UTC do IPFIX, mais um comando `show bpa pool history`.

## Carimbo de tempo

Requisito de compliance, nao detalhe de implementacao:

- gravar sempre em UTC, com fuso explicito no coletor
- exigir NTP sincronizado; recusar exportar com relogio nao sincronizado, ou
  marcar os registros como suspeitos
- usar relogio monotonico para medir duracao e relogio de parede so para o
  carimbo

## Contexto regulatorio

O Marco Civil da Internet (Lei 12.965/2014, arts. 13 a 15) obriga a guarda
de registros de conexao por 1 ano. Com CGNAT, identificar o assinante a
partir de `(IP publico, porta, instante)` depende do mapeamento - e e esse
mapeamento que o BPA torna barato de guardar. Isto descreve o requisito
tecnico; a adequacao juridica de cada provedor e com o juridico dele.

## Plano de implementacao

Depende do modo dinamico existir, porque e ele que gera os eventos.

1. `bpa_log.c`: registro do template RFC 8158 via `flow_report.h`
2. Emitir `natEvent 16` na alocacao e `17` na liberacao do bloco
3. Emitir `natEvent 12` no esgotamento do bloco - alem de compliance, e o
   alarme que antecipa chamado de suporte ("meu torrent parou")
4. Transporte TCP com buffer local; UDP opcional
5. Trilha de configuracao append-only e `show bpa pool history`
6. CLI: `bpa logging collector <ip> port <n> [transport tcp|udp]`
7. Teste de ponta a ponta: coletor de mentira no lab conferindo que cada
   alocacao tem sua liberacao correspondente
