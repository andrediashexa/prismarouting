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

## Trilha de configuracao: implementada

No deterministico a configuracao **e** o log. Toda mudanca de pool vai para
um arquivo append-only, datada em UTC, com a definicao completa:

```
2026-09-05T02:07:58Z add pool provedor-a inside 100.64.0.0+1024 \
  outside 45.170.16.0+64 outside 200.150.0.0+256 block 512 \
  ports 1024-65535 policy interleaved mode deterministic
2026-09-05T02:08:00Z del pool provedor-a ...
2026-09-05T02:08:01Z add pool provedor-a ... block 1024 ...
```

A pergunta que isso responde: *qual era o bloco do provedor-a na data do
oficio?* Sem a trilha, a formula **atual** responderia com o bloco atual para
uma data em que o pool usava outro - e apontaria o assinante errado com toda
a confianca.

O registro traz a definicao inteira, nao um diff, para poder ser lido sem
depender de mais nada. Ha `fsync` a cada escrita: log de compliance nao pode
ficar so no cache de pagina.

Cada entrada tambem vai para o log do VPP. Um arquivo local sobrevive a um
reboot, nao a uma perda de disco; quem leva a serio manda o log do VPP para
syslog remoto, e ai a trilha sai da caixa.

`show bpa pool history` le a trilha; `bpa audit-path` muda o caminho.

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

## Duas armadilhas encontradas ao implementar

As duas falham em silencio, e as duas custam justamente os registros do
inicio - que e quando ninguem esta olhando.

### 1. O dataplane nao pode emitir IPFIX direto

Chamar o caminho de export de dentro do dispatch do grafo escreve o buffer
mas ele nunca sai do fio. So os eventos emitidos de contexto de **processo**
chegavam ao coletor.

Desenho correto, que e o mesmo que o plugin de NAT do VPP usa: o node do
dataplane **enfileira** o evento numa fila por thread (sem lock, cada worker
so escreve na sua) e um processo drena sob a barreira e exporta. A fila tem
teto: coletor inalcancavel vira perda contabilizada, nao memoria crescendo.

### 2. Registro antes do template e indecifravel

Os primeiros eventos sairam no fio antes do primeiro template. O tcpdump
mostrava os pacotes; nenhum coletor conseguiria le-los, porque sem o template
nao ha como saber o formato do registro.

Isso e perda silenciosa logo apos habilitar o log ou reiniciar a caixa. A
correcao e segurar os eventos na fila enquanto `last_template_sent == 0`.

Vale para o coletor tambem: um coletor serio guarda o data set que chegou sem
template e reprocessa quando ele chega. `tools/ipfix_collector.py` faz isso, e
foi assim que a ordem virou visivel em vez de "sumiram registros".

## Plano de implementacao

- [x] `bpa_log.c`: template RFC 8158 via `flow_report.h`
- [x] `natEvent 16` na alocacao, `17` na liberacao, `12` no esgotamento
- [x] Fila por thread no dataplane, drenada pelo processo
- [x] Teste de ponta a ponta com coletor decodificando (`lab/ipfix.sh`),
      conferindo que toda alocacao tem sua liberacao correspondente
- [ ] Transporte TCP com buffer local; hoje so UDP, que perde registro sob
      congestionamento - e perder um `natEvent 17` recria a lacuna descrita
      acima
- [ ] Trilha de configuracao append-only e `show bpa pool history`
- [ ] Escolha de coletor por CLI propria, hoje herdada do exportador do VPP
