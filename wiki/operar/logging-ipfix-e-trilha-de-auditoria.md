# Logging IPFIX e trilha de auditoria

Duas coisas diferentes com o mesmo objetivo — poder responder *"quem estava usando este IP público, nesta porta, neste instante?"* — e que se aplicam a modos diferentes.

```mermaid
flowchart TD
    P["Pergunta: quem usava<br/>este IP:porta naquela data?"] --> M{"Modo do pool"}
    M -->|deterministic| A["a aritmética responde<br/>+<br/>show cgnat history prova<br/>qual config estava em vigor"]
    M -->|bpa| B["os registros IPFIX ou syslog<br/>no coletor respondem"]
```


:::info
**A caixa não guarda os registros de conexão: ela os exporta, e quem guarda o ano de guarda é o seu coletor.** O volume de log da caixa tem só duas coisas: a trilha de configuração e o transbordo do exportador, os registros que um coletor fora do ar ainda não recebeu. O transbordo de cada transporte para em 256 MB, seja qual for o tamanho do volume: é esse teto que diz quanto tempo a caixa aguenta exportando para um coletor inalcançável, e nada ali é histórico.

:::

# Modo determinístico: a configuração **é** o log

Não existem eventos de alocação por assinante, então não há o que exportar. A prova oferecida a uma autoridade é:

> o algoritmo, mais a configuração que estava em vigor naquele instante

Isso só se sustenta enquanto se souber **qual** configuração estava em vigor. É para isso que existe a **trilha de configuração**.

**Uma exceção: a** `**reserve**`**.** Um pool determinístico com `reserve` guarda blocos fora da fórmula para entregar a quem enche o próprio bloco. Esses são justamente os blocos que a fórmula **não** produz, então cada um sai como `natEvent 16` ao ser entregue e `natEvent 17` ao voltar, igual a um bloco de um pool BPA — e um pool assim precisa de exportação ligada como um pool BPA.

## A trilha de configuração

Propriedades que não são acidentais:

* **Append-only** — nada é reescrito.
* `**fsync**` **a cada entrada** — sobrevive a queda de energia.
* **Carrega a definição inteira, não um diff** — reconstruir o estado de uma data somando diffs desde o começo é frágil; guardar o estado completo não é.
* **Registra o operador que fez a mudança**, capturado no instante em que a mudança é aplicada.
* **Não só a definição do pool**: cada forward acrescentado ou removido, e cada interface ligada ou desligada do CGNAT, com o lado (`inside` ou `outside`) na linha. Os pools dizem *como* traduzir e as interfaces dizem *onde*; uma trilha que omitisse o segundo descreveria um mapeamento que talvez nem estivesse valendo.
* **Cada entrada termina com um número de sequência e um resumo SHA-256**, que é o que permite provar que ela não foi editada (mais abaixo).

Cada entrada nomeia quem fez a mudança num destes formatos:

| Na linha | O que quer dizer |
|----------|------------------|
| `by marina uid 1001 via api` | a VTY sabia o nome da conta e o uid com que ela agia |
| `by marina uid 0 via api` | marina, **agindo como root** — o que nenhum dos dois campos diz sozinho |
| `by uid 1001 via api` | o sistema não conseguiu nomear a conta; o número é o que se sabe |
| `by unattributed via cli` | digitado direto no dataplane, onde não há sessão a quem perguntar |
| `by unattributed via api` | um cliente da API que não mandou identidade nenhuma |

Uma entrada que não puder ser atribuída a alguém **diz isso**, com a palavra `unattributed`. Omitir o campo seria indistinguível de uma entrada em que ninguém foi responsável — e um pedido sem nome e sem uid é registrado como não atribuído, nunca creditado ao root.


:::info
**O que a atribuição é, e o que não é.** É o registro de qual operador fez o quê entre as pessoas que já têm acesso à caixa — que é o que uma trilha de auditoria é. Não é prova contra quem tem acesso: o cliente declara a própria identidade, e quem alcança a API já pode reconfigurar a caixa.

:::

```
show cgnat history
```

Na trilha, um pool BPA aparece como `mode dynamic`, a palavra de antes da 0.151.0: uma entrada nunca é reescrita, e as novas usam a mesma palavra das antigas.

# Modo BPA: IPFIX por bloco

Um registro por **alocação de bloco** (RFC 8158) — não por fluxo. É a diferença entre alguns registros por assinante por dia e um por conexão.

Três transportes, que podem ficar ligados ao mesmo tempo:

| Transporte | Por onde sai | Quando usar |
|------------|--------------|-------------|
| IPFIX sobre UDP | pelo contêiner de log da caixa, de um socket no endereço `src`; pelo próprio dataplane quando o exportador está numa VRF, quando o contêiner não consegue usar esse endereço ou quando ele não está no ar | telemetria. Perde registro sob congestionamento, sem aviso |
| IPFIX sobre TCP | pelo Linux da caixa (o contêiner de log; o próprio dataplane, se o contêiner não estiver no ar), com transbordo em disco se o coletor cair | **guarda legal**. Carrega a identidade do assinante, como o syslog; o UDP não |
| syslog (RFC 5424 sobre TCP) | pelo Linux, com o mesmo transbordo | o coletor que só lê syslog, que é a maioria |


:::warning
**Um pool BPA sem nenhuma exportação é um pool que não consegue responder a um ofício.** Em modo BPA o registro exportado é a única prova de quem segurava uma porta pública. `commit check` e `commit` avisam, nomeando o pool, quando um pool BPA — ou determinístico com `reserve` — vai ficar numa caixa em que nada exporta: nem UDP ligado com coletor, nem coletor TCP, nem syslog. É aviso e não recusa, porque um pool montado antes do coletor existir é uma configuração correta; mas não o deixe carregando assinantes assim.

:::

## Configurando

A exportação por UDP precisa saber **para onde** ir antes de ser ligada. São dois comandos, nesta ordem:

```
configure
 nat
  cgnat
   logging udp <coletor> src <origem> [port <n>]   destino do UDP
   logging enable                                  liga a exportação UDP
   exit
  exit
 commit
```

`src` é o endereço de origem dos pacotes IPFIX e **não é opcional**: sem ele a caixa recusa. Use um endereço que o coletor reconheça como sendo desta caixa — normalmente o de gerência. Sem `port`, vale 4739, a porta registrada pelo RFC 7011.

Os demais comandos, todos dentro de `nat` > `cgnat`:

| Comando | O que faz |
|---------|-----------|
| `logging tcp <coletor> [port <n>]` | exportação por TCP |
| `logging tcp disable` | desliga o transporte TCP |
| `logging syslog <coletor> enterprise <n> [port <n>] [facility <0-23>] [severity <0-7>]` | exporta os mesmos eventos como linhas syslog (RFC 5424) |
| `logging syslog disable` | desliga o transporte syslog |
| `logging udp disable` | apaga o destino UDP |
| `logging disable` | desliga a exportação UDP |

O destino UDP volta em `show running-config`, sobrevive a `save`, a `rollback` e ao boot — e é reaplicado na ordem certa, o destino antes do estado.

### Se você ligar a exportação sem dizer para onde

`commit check` recusa antes de aplicar qualquer coisa:

```
error: cgnat logging enable: UDP export has no collector
    nothing would leave this box; add 'logging udp <collector> src <source>'
    beside it under 'nat' / 'cgnat', or export over TCP with 'logging tcp
    <collector>'
    the dataplane refuses the statement, and it would refuse it half way
    through this commit
```

`logging tcp` **não** satisfaz esse requisito: os dois transportes são caminhos independentes, e `logging enable` liga o de UDP.

A recusa vale quando é **esta mudança** que cria o problema. Numa caixa que já está nesse estado — exportação ligada e o destino apagado por baixo dela — a mesma frase sai como `warning:` e o commit passa, porque senão a única configuração que a caixa não conseguiria aplicar seria justamente a que conserta isso.


:::warning
**Em versões até a 0.63.0 a exportação por UDP não funcionava em nenhuma appliance instalada.** Não havia comando na VTY que dissesse para qual coletor enviar, então o dataplane recusava `logging enable` — e, até a 0.63.0, o `commit` respondia `commit complete` por cima dessa recusa. Uma operação que acreditava estar guardando registros de retenção legal não estava guardando nada.

Se você opera modo BPA (chamado `dynamic` até a 0.151.0) e ligou a exportação por UDP em versão anterior, trate isso como uma lacuna de retenção no período: os registros daquele intervalo não existem. A exportação por TCP nunca foi afetada.

:::

### Conferindo que está mesmo exportando

```
show cgnat logging       estado da exportação e para onde ela vai
```

Uma linha `exporter` com endereço é a prova de que existe destino; `exporter none` significa que nada sai por UDP, **mesmo com o estado** `**enabled**` — apagar o destino não desliga a exportação, só a deixa sem para onde ir.

A mesma tela tem uma parte para o transporte TCP e outra para o syslog. Cada uma diz o coletor e se está `connected`, `connecting` ou `disconnected`, quanto já foi enviado e quanto está pendente, e a linha `spill`: bytes no disco, quantos já voltaram ao coletor e quantos foram descartados. Um transporte desligado aparece como `disabled`.

Acima das duas, uma linha diz quem carrega os fluxos TCP: `TCP streams carried by the log container`, o contêiner de log da caixa, ou `TCP streams carried by the dataplane` quando ele não está no ar. Na parte do UDP, a linha `sender` diz o mesmo para o IPFIX sobre UDP e, quando é o dataplane, por quê. E a linha `file` de cada transporte diz quantos bytes o arquivo de transbordo tem agora, o teto dele (256 MB) e quanto o teto já recusou.

O autoteste de boot também confere isso, e compara duas coisas diferentes: o que a caixa foi mandada fazer e o que o dataplane realmente tem. Se a configuração pede exportação e o dataplane está sem ela, o boot falha e diz qual das duas metades não pegou — porque uma configuração recusada é indistinguível de uma que está funcionando quando se olha só para o arquivo.

```
show cgnat logging       estado da exportação
show cgnat blocks        blocos atualmente alocados (e quantos estão presos)
```

Os blocos em uso agora; os registros já exportados estão no coletor.

### Vendo os registros ao vivo

`show cgnat logging` mostra contadores. Para ver os próprios registros que a caixa está exportando, um por linha, enquanto acontecem:

```
monitor cgnat logging
```

```
monitor cgnat logging   Ctrl-C to stop
  each block event as the dataplane exports it, at the time its record carries.
  Whether an event reached a collector is not known one by one: the '--' lines
  say what each stream sent.
21:26:05 BOOT      this dataplane has been up since 2026-10-02 21:26:01
21:26:10 ALLOC     100.64.0.5      > 200.150.0.16    1024-17407  pool din
21:26:10 EXHAUSTED 100.64.0.12     no free block in pool din
21:26:12 RELEASE   100.64.0.5      > 200.150.0.16    1024-17407  pool din
21:26:15 -- ipfix-udp 10.0.0.2:4739  by the dataplane:  +7 sent
21:26:15 -- +30 exhaustion repeats held back
```

Cada linha é um evento de bloco: `ALLOC` (o assinante recebeu o bloco), `RELEASE` (devolveu), `EXHAUSTED` (pediu e não havia bloco livre; um por assinante a cada 10 segundos, não um por pacote recusado), com o IP do assinante, o IP público, a faixa de portas e o pool pelo nome. A hora é a mesma que vai no registro. Quando a caixa conhece a identidade do assinante (login PPPoE ou DHCP), ela aparece logo abaixo.

As linhas `--` saem a cada 10 segundos, só quando algo mudou, e dizem por transporte quanto foi enviado, quanto está esperando e quanto se perdeu. A tela não sabe se **um** registro específico chegou ao coletor — a exportação conta por transporte, não por registro — e diz isso no cabeçalho.

Filtros, que podem ser combinados:

```
monitor cgnat logging subscriber 100.64.0.5      só esse assinante
monitor cgnat logging public 200.150.0.16:2000   só quem usa essa porta pública
monitor cgnat logging event release              só um tipo de evento
monitor cgnat logging pool din                   só esse pool
monitor cgnat logging seconds 60                 para sozinho depois de 60 s
monitor cgnat logging raw                        a forma de fio
```

Com `raw`, cada evento sai como o registro IPFIX campo a campo (323 hora, 230 tipo do evento, 8 assinante, 225 IP público, 361/362 portas, 283 pool, 371 identidade) e, se o syslog estiver configurado, a linha RFC 5424 exatamente como o coletor a recebe.

O monitor só lê: não para, não atrasa e não tira nada da exportação. Se ele ficar para trás (um terminal lento, por exemplo), diz quantas linhas não mostrou.

## Bloco preso por encaminhamento de porta também é evento

Um [encaminhamento de porta](/s/wiki/doc/encaminhamento-de-porta-epIZfdmzU3) num pool BPA prende o bloco do assinante enquanto o forward existir. Prender gera uma **alocação** e soltar gera uma **liberação**, como qualquer outro bloco: em modo BPA o log é o único registro de quem segurou um bloco, e um bloco segurado sem registro é um mapeamento que ninguém consegue atribuir depois.

Um segundo forward dentro de um bloco que já está preso **não** gera outro registro — o evento é do bloco, não do forward. Duas alocações do mesmo bloco sem uma liberação entre elas é exatamente a lacuna que faz uma consulta futura responder errado.

## Por que TCP existe

Com UDP, a pergunta *"o coletor recebeu?"* não tem resposta — e a pergunta que realmente importa é **"o que acontece quando o coletor cai?"**. Com UDP, a resposta é silêncio: registros de retenção legal desaparecem sem que nada indique.

É exatamente esse o caso que o produto verifica: derrubar o coletor e conferir o que acontece com os registros.

# Dar nome ao assinante no registro

Uma ordem judicial nomeia uma pessoa. O registro nomeia um endereço privado num instante. Juntar os dois exigia um segundo sistema e um passo manual, feito sob pressão por quem está de plantão — que é exatamente a condição em que se manda a resposta errada.

A caixa passa a carregar a identidade no próprio registro:

```
nat
 cgnat
  identity file /etc/prisma-routing/subscribers.map
  identity none
  exit
 exit
```

O arquivo é um assinante por linha, escrito pelo seu sistema de provisionamento:

```
# comentários e linhas em branco são ignorados
100.64.0.5   joao.silva@isp
100.64.0.6   pppoe-4471
```

**Ou direto do arquivo de lease do seu DHCP**, se você roda IPoE e já tem a resposta:

```
nat
 cgnat
  identity dhcp-leases /var/lib/dhcp/dhcpd.leases
  exit
 exit
```

A gramática é a do ISC dhcpd. A identidade é o `client-hostname` quando o lease tem um, e o endereço `hardware ethernet` quando não tem — o primeiro é o que o suporte reconhece, o segundo é o que sempre existe. Lease sem nenhum dos dois não vira entrada, e aquele endereço fica `unknown` em vez de ganhar algo inventado.

**Só lease** `**active**` **conta.** O arquivo guarda todo lease que o servidor já escreveu, e um `free` ainda carrega o nome do último cliente — nomear um assinante a partir de um desses é dar a resposta errada a uma ordem judicial com cara de certa. Endereço renovado aparece várias vezes e **vence o último**, que é o contrário da regra do mapa estático; por isso são dois comandos e não um que adivinha pelo começo do arquivo.

**O arquivo é lido inteiro ou recusado inteiro.** Uma linha que não é comentário nem `<endereço> <identidade>` é recusada dizendo qual linha, e a tabela em vigor não muda — um provisionamento que escreveu uma linha torta pode ter escrito outra, e uma tabela faltando um assinante em silêncio é justamente a falha que isso existe para evitar. Endereço repetido também é recusado: duas identidades para um endereço são duas respostas para a pergunta que o juiz faz, e escolher uma seria a caixa decidindo qual assinante nomear. Um nome com mais de 31 caracteres é levado em parte, e o `show cgnat identity` conta quantos foram cortados: o assinante existe, e um nome parcial é melhor que registro nenhum.

**Ou da contabilidade que o seu BNG já manda**, se a autenticação é PPPoE e a resposta já existe num RADIUS:

```
nat
 cgnat
  identity radius secret <segredo compartilhado> [port <n>]
  identity radius disable
  exit
 exit
```

Sem `port`, a caixa escuta na 1813, a porta de contabilidade do RADIUS. `identity radius disable` fecha o listener.

Aqui a caixa não é o servidor RADIUS: ela não autentica ninguém e não responde Access-Request. Ela é um segundo destino da contabilidade, lendo dela uma coisa só — qual assinante está em qual endereço. Um `Start` ou um `Interim` que traz endereço e nome diz quem está ali, um `Stop` tira o nome, e um `Interim` sem endereço não muda nada, porque adivinhar de qual endereço ele falava é exatamente como um assinante acaba nomeado no registro de outro.

**O segredo é obrigatório e conferido em todo pacote**, e pacote que não bate é descartado sem resposta. Sem essa conferência, qualquer um que alcance a porta nomearia qualquer assinante em qualquer endereço, e o log de compliance passaria a aceitar ditado. As duas recusas são contadas separadas — autenticador ruim é a caixa de outro (ou o segredo desta errado), pacote malformado é cliente quebrado de alguém:

```
prisma> show cgnat identity
identity table: 3 entry(s), read 2026-09-18T06:41:02Z
radius accounting: listening on port 1813
  packets    2 received, 1 applied, 1 answered
  refused    1 bad authenticator, 0 malformed
```

**O segredo não volta em nenhuma tela.** O `show configuration` imprime `secret <set, not shown>`, e o arquivo salvo guarda o valor de verdade — a caixa precisa subir o próprio listener depois de um reboot. É o mesmo tratamento que a community SNMP já tem.

**A identidade é tomada no instante da alocação**, não na hora em que o registro sai. Entre uma coisa e outra o lease pode ter mudado de dono, e um registro nomeando quem tem o endereço depois é um registro bem formado, guardado, e errado.

**E a liberação repete o nome que a alocação disse.** O bloco guarda o nome com que foi anunciado, e o registro de liberação lê esse nome de volta, mesmo que a tabela tenha mudado no meio - um lease que trocou de dono, um `Stop` do RADIUS, um mapa reescrito. Assim qualquer um dos dois registros responde sozinho a um ofício, e o que ele diz sobre o mês passado é o que era verdade no mês passado. Quando não há nome guardado - um assinante que a tabela nunca teve -, a liberação consulta a tabela em vigor naquele instante.

**Endereço que a caixa não conhece vira** `**unknown**`**, e o registro sai assim mesmo.** Registro faltando é pior que registro incompleto, e o coletor precisa distinguir "a caixa não sabe" de "a caixa não disse".

**É dado pessoal, e nada imprime uma entrada.** O `show cgnat identity` diz quantas entradas existem e quando o arquivo foi lido - e, numa caixa sem tabela, que todo registro vai dizer `unknown`:

```
prisma> show cgnat identity
identity table: 3 entry(s), read 2026-09-18T06:41:02Z
```

Nenhuma outra tela desta caixa carrega identidade. A presença dela no log é a razão pela qual o log é protegido.

**Nos dois transportes que a carregam:** no syslog ela vai como `subscriberId` dentro do dado estruturado; no IPFIX sobre TCP vai como elemento **371** `**userName**`. O id do template do registro de bloco passou de 256 para 258 por causa disso — o IPFIX não permite que um id signifique dois conjuntos de campos, e um coletor com o 256 em cache leria os primeiros bytes da identidade como id de pool. Se o seu coletor guarda templates, ele vai simplesmente esperar o novo, que a caixa reenvia a cada minuto.

O exportador **UDP** não mudou: ele continua com os sete campos da RFC 8158 e sem identidade.

**Assinante só IPv6, de um** [**pool NAT64**](/s/wiki/doc/nat64-assinantes-so-ipv6-no-mesmo-espaco-publico-4Se191EJRd)**:** quem o nomeia é o `Delegated-IPv6-Prefix` (atributo 123 da RFC 4818), o prefixo que o BNG delegou. O registro leva o nome da delegação que **cobre** o prefixo do assinante: um /56 delegado a cada cliente de um pool cortado em /56, ou um /48 que cobre todos os /56 de uma empresa. Uma delegação mais longa que o assinante — um /60 dentro de um /56 — cobre só parte dele, e o registro diz `unknown`. Uma delegação nova substitui qualquer outra que ela sobreponha: o prefixo delegado agora é daquele cliente, e a entrada antiga é um `Stop` que não chegou.

**Quando a própria caixa é o BNG** (PPPoE), quem segurava cada endereço interno é ela mesma que registra: o histórico de sessões, `show pppoe history`, escrito no início e no fim de cada sessão, com ou sem resposta do RADIUS, encadeado por hashes como a trilha de configuração e guardado no mesmo volume de log. O endereço interno e o instante de um registro de bloco levam ao assinante com `show pppoe history address <endereço> at <hora>`; veja [Configuração de PPPoE](/s/wiki/doc/configuracao-de-pppoe-bfl7lzpl2O).

**A contabilidade é lida a cada segundo, com ou sem exportação ligada.** Antes ela só era lida depois que o primeiro template IPFIX saía — até um intervalo de template depois do `logging enable` —, e todo bloco alocado nesse meio-tempo saía `unknown` mesmo com o BNG já tendo dito quem era.

# Quando o seu coletor só fala syslog

A maioria dos coletores de ISP lê syslog e mais nada. Os mesmos eventos saem como linhas **RFC 5424**, sem mudar o que é registrado: um registro por alocação e por liberação, nunca por fluxo.

```
nat
 cgnat
  logging syslog 10.0.0.9 enterprise 44444
  exit
 exit
```

Uma linha de alocação chega assim:

```
<134>1 2026-09-18T02:40:59.123Z cgnat-sp-01 prisma-cgnat - BLOCK-ALLOC
[cgnat@44444 natEvent="16" subscriber="100.64.0.5" public="200.150.0.16"
portStart="1024" portEnd="1027" pool="0" subscriberId="joao.silva@isp"]
100.64.0.5 -> 200.150.0.16 ports 1024-1027 pool 0
```

(quebrada aqui em três linhas para caber na página; chega como uma linha só.)

O campo que vem depois de `prisma-cgnat -` diz o que é a linha, e é por ele que se filtra:

| Nome | Evento |
|------|--------|
| `BLOCK-ALLOC` | bloco alocado (`natEvent` 16) |
| `BLOCK-RELEASE` | bloco liberado (`natEvent` 17); a frase no fim diz `released` no lugar da seta |
| `PORTS-EXHAUSTED` | portas esgotadas (`natEvent` 12) |
| `BOOT-MARKER` | o marcador de reinício, com o instante em que esta encarnação do dataplane começou |

`subscriberId` vem sempre: com o nome da tabela de identidade, ou `unknown` quando a caixa não sabe quem estava no endereço.

**O horário é o mesmo do registro IPFIX, com a mesma precisão de milissegundo, em UTC.** É isso que permite colocar os dois lado a lado: um log cujo relógio não pode ser comparado com o outro não corrobora nada — e corroborar é a razão de existir um segundo transporte.

**O número de empresa é seu, e não tem padrão.** A RFC 5424 reserva um nome de dado estruturado sem sufixo `@empresa` para nomes registrados na IANA, e este produto não tem um. Inventar um colocaria, no log de todo operador, um nome que amanhã significa outra coisa para quem o registrar. Então a caixa **recusa** o comando sem `enterprise <n>` — no prompt, onde dá para resolver, e não em um ano de linhas que o coletor interpretou errado.

O transporte é TCP, com enquadramento por contagem de octetos (RFC 6587): o tamanho e um espaço antes de cada linha. A porta padrão é 601. Vale o mesmo que vale para o IPFIX sobre TCP — se o coletor cair, os bytes vão para um arquivo de transbordo na própria caixa e voltam na reconexão, na ordem. Cada transporte tem o arquivo dele.

Os dois podem ficar ligados ao mesmo tempo: o coletor do provedor lendo syslog e outro lendo IPFIX. `logging syslog disable` desliga só o syslog.

| Você digitou | A caixa diz |
|--------------|-------------|
| `logging syslog 10.0.0.9` | `an enterprise number is required`, com o porquê |
| `... enterprise 9 sideways 2` | `not an option of this statement`, listando as que são |
| `... enterprise 9 port 70000` | `port is 0-65535` |
| `... enterprise 9 facility 24` | `facility is 0-23` |

# O formato do registro (RFC 8158)

Cada registro exportado tem estes campos:

| IE  | Nome | Bits | Obrigatório | Conteúdo |
|----:|------|-----:|-------------|----------|
| 323 | `observationTimeMilliseconds` | 64   | sim         | instante do evento, em UTC |
| 230 | `natEvent` | 8    | sim         | 16, 17 ou 12 |
| 8   | `sourceIPv4Address` | 32   | sim         | endereço do assinante |
| 225 | `postNATSourceIPv4Address` | 32   | sim         | endereço público atribuído |
| 361 | `portRangeStart` | 16   | sim         | início do bloco |
| 362 | `portRangeEnd` | 16   | não         | fim do bloco |
| 283 | `natPoolId` | 32   | não         | índice do pool |

E um **segundo template**, com dois campos, que carrega o marcador de reinício:

| IE  | Nome | Bits | Conteúdo |
|----:|------|-----:|----------|
| 323 | `observationTimeMilliseconds` | 64   | instante em que o marcador foi escrito |
| 160 | `systemInitTimeMilliseconds` | 64   | instante em que esta encarnação do dataplane começou |


:::warning
**Registros coletados por TCP em versões até a 0.34.0 têm esse campo errado.** Eles carregavam segundos desde o boot do VPP em vez da hora do dia — o coletor decodificava uma alocação como `00:00:05`. O exportador UDP, carregando os mesmos eventos, decodificava a hora certa.

Três consequências, e a terceira é a que importa para guarda legal:

* registros de boots diferentes **não são ordenáveis**, porque o relógio volta a zero a cada reinício e o log anda para trás;
* os dois transportes não podiam se corroborar, que é a única razão de rodar os dois;
* **um pedido que nomeia uma hora do dia — que é todo pedido que este log existe para responder — não podia ser resolvido.**

Corrigido na **0.35.0**. Um log já coletado por TCP pode ser reparado se a hora de boot da caixa for conhecida; **nada recupera a ordenação entre reinícios**. Se você opera modo BPA e coletou por TCP em versão anterior, trate isso como um problema de retenção, não de cosmética.

:::

**Assinante de um** [**pool NAT64**](/s/wiki/doc/nat64-assinantes-so-ipv6-no-mesmo-espaco-publico-4Se191EJRd) tem um template próprio, porque o IPFIX não tem campo que seja "um endereço IPv4 ou IPv6". É a mesma tabela com o endereço IPv6 no lugar do IPv4, como manda a seção 4.6.10 da RFC 8158 — mais o tamanho do prefixo, que a RFC não lista e esta caixa precisa: o assinante é o prefixo delegado, e só o endereço nomearia um /128 dele.

| IE  | Nome | Bits | Conteúdo |
|----:|------|-----:|----------|
| 27  | `sourceIPv6Address` | 128  | o prefixo delegado do assinante, endereço de rede |
| 29  | `sourceIPv6PrefixLength` | 8    | o tamanho dele, o `subscriber-length` do pool |

Os outros campos são os mesmos e na mesma ordem. No syslog o assinante sai como prefixo, `subscriber="2001:db8:1200:ab00::/56"`. Um pool NAT64 determinístico não registra bloco nenhum, como o IPv4: a trilha de configuração guarda o prefixo IPv6 interno, o `subscriber-length` e o `nat64-prefix`, e com eles a fórmula responde por qualquer instante.

Os três valores de `natEvent` usados:

| Valor | Evento |
|------:|--------|
| **16** | bloco alocado (inclui extensão) |
| **17** | bloco liberado |
| **12** | portas esgotadas (um por assinante a cada 10 segundos) |

# Por que a liberação é obrigatória

Este é o ponto mais fácil de perder, e ele muda a resposta jurídica.

```mermaid
sequenceDiagram
    participant B as Bloco B
    Note over B: t0 — alocado ao assinante X → registro
    Note over B: t1 — liberado por ociosidade → REGISTRO OBRIGATÓRIO
    Note over B: t2 — alocado ao assinante Y → registro
```

Um juiz pergunta quem segurava (IP, porta em B) em **t1.5**. A consulta natural — *"a última alocação antes desse instante"* — responde **X**.


:::warning
Está errado: em t1.5 ninguém segurava aquele bloco. O provedor entregaria um assinante inocente.

Em t0.5 e t2.5 a mesma consulta acerta. O erro aparece **só na lacuna**, que é exatamente onde ninguém testa.

:::

Por isso o `natEvent 17` na liberação é correção, não capricho.

# Quando a caixa para, e quando ela cai

A mesma lacuna aparece no reinício, e por um motivo mais comum do que a ociosidade: **todo reboot planejado** deixaria para trás um bloco segurado no instante da parada, sem liberação nenhuma.

O pacote de atualização **não** passa por aqui: ele troca os arquivos e não reinicia nada, então os blocos do dataplane antigo são fechados quando você o reiniciar, no horário que escolher. A reinstalação pela mídia passa, porque começa parando a caixa.

## Parada planejada

Uma parada ordenada do dataplane - `system dataplane restart`, `system reboot`, `system shutdown` - **libera todos os blocos antes de sair**. Sai um `natEvent 17` por bloco ainda segurado, em todo transporte ligado, carimbado com o **instante da parada**.

O mesmo vale para um pool apagado, ou trocado por uma mudança de prefixo ou de tamanho de bloco: cada bloco que ele segurava é fechado com um `natEvent 17` no instante da mudança, antes de o pool deixar de saber de quem era.

O instante é o da parada e não um anterior de propósito: a caixa não observou o momento em que o bloco deixou de ser usado, e um registro que chutasse esse momento seria um registro inventado.

## Quando o coletor está atrasado na hora da parada

As liberações são escritas no instante da parada, mas o coletor pode não estar pronto para recebê-las todas de uma vez. Dois casos, e o segundo é o comum:

* um coletor lento, ou que parou de ler sem fechar a conexão;
* uma caixa com **muitos blocos segurados**. A parada escreve uma liberação por bloco, de uma só vez, e isso é mais do que cabe no socket em uma passada.

Nos dois casos os bytes que sobraram **não são perdidos** enquanto houver onde escrever: vão para um arquivo no disco da própria caixa, `/var/log/prisma-routing/ipfix-spill.bin` para o IPFIX sobre TCP e `/var/log/prisma-routing/syslog-spill.bin` para o syslog, e voltam na próxima conexão com o coletor, na ordem em que foram escritos. São os mesmos arquivos que cobrem um coletor fora do ar, e cada um para em 256 MB.

O que isso muda para quem opera:

* depois de um reboot, parte das liberações pode chegar ao coletor **alguns segundos depois de a caixa voltar**, e não no instante da parada. O carimbo de cada registro continua sendo o instante da parada, que é o que responde a uma ordem judicial;
* enquanto a exportação por TCP não voltar a conectar, o arquivo fica no disco. Uma caixa desligada em definitivo deixa esses registros lá - copie o arquivo antes de descomissionar;
* o estado da exportação mostra, por transporte, quantos bytes foram para o arquivo, quantos já voltaram e quantos foram descartados. Descarte só acontece quando não há onde escrever ou quando o arquivo chegou ao teto de 256 MB, e é contado - nunca silencioso. Um descarte na própria parada, quando o contador morre junto com o processo, é dito no registro do sistema com quantos bytes não puderam ser escritos.

```
show cgnat logging
```


:::info
Medido com o coletor lendo normalmente e 4001 blocos segurados: 70 kB de liberações ainda estavam no buffer de saída no instante em que o processo terminou, cerca de 1500 registros. Todos chegaram ao coletor no reinício seguinte.

:::

## Queda de energia

Uma caixa que perde energia, que é morta pelo kernel por falta de memória ou que entra em pânico **não emite nada** - e não há o que fazer a respeito: não existe instante em que ela poderia ter escrito o registro. As alocações dela ficam abertas.

É por isso que a parada ordenada sozinha **não** resolve o problema. Ela fecha os casos que a caixa viu chegando, e deixaria quem lê o log convencido de que toda alocação aberta é uma alocação em vigor - errando justamente nas caixas que caíram.

## O marcador de reinício

Para separar os dois casos, a caixa anuncia **quando esta encarnação do dataplane começou**, em um segundo template, no campo `systemInitTimeMilliseconds` (IE 160) - que o IANA registra exatamente como *"o instante absoluto da última (re)inicialização do dispositivo IPFIX"*. O anúncio sai a cada 30 segundos no UDP, e uma vez por boot, no começo do fluxo, no TCP e no syslog.

Com isso, uma alocação sem liberação passa a ter três leituras, e não duas:

| O que o coletor tem | Leitura |
|---------------------|---------|
| alocação e liberação | o assinante segurou aquelas portas entre os dois instantes - resposta exata |
| alocação sem liberação, e o valor do marcador **não** mudou depois | **ainda em vigor**: o assinante segura aquelas portas agora |
| alocação sem liberação, e o marcador anunciou um valor **novo** depois | **terminou naquele reinício**: a caixa caiu segurando o bloco e não observou o fim. A resposta honesta é a janela entre o último registro da encarnação anterior e o novo instante de início - não um instante |


:::info
O valor do marcador é **latched** no início do processo e não é recalculado. O que importa é que ele **mude** quando o processo é substituído; um valor recalculado a cada envio pularia a cada ajuste de relógio e seria lido como um reinício que não houve.

Em compensação, uma caixa cujo relógio ainda estava errado quando o dataplane subiu carrega um valor absoluto errado até ser reiniciada. Ele continua diferente do da encarnação anterior, que é para o que o coletor o lê.

:::


:::warning
**Um coletor que não conhece o IE 160 continua vendo alocações abertas e não consegue distingui-las.** Isso não é defeito da caixa: os registros saem, o segundo template é anunciado junto com o primeiro, e quem decodifica é o coletor. Ao escolher ou configurar um coletor para guarda legal, confirme que ele guarda o `systemInitTimeMilliseconds` por exportador e reprocessa as alocações abertas quando esse valor muda.

:::

## Conferindo

A linha do marcador aparece no estado da exportação, com o template que o carrega, quantos já saíram e o instante de início desta encarnação:

```
show cgnat logging
```

Se o número de marcadores enviados não sai do zero com a exportação ligada, o coletor não tem como separar os dois casos acima - trate como a exportação estar quebrada, não como cosmética.

# Verificando que a trilha não foi adulterada

Cada entrada da trilha carrega um número de sequência e um resumo SHA-256 sobre **o resumo anterior e o próprio texto**. Mudar um byte muda o resumo daquela entrada e de todas as seguintes — uma alteração não pode ser feita "fechar" só corrigindo a linha em que está.

```
show cgnat history verify
  7 entries checked, chain intact
  head  seq 7 sha256 4e98de83…81f4
```

Três formas de uma trilha estar errada, e elas são reportadas **separadamente**:

| O que foi feito | O que é reportado |
|-----------------|-------------------|
| o texto de uma entrada foi alterado | `BROKEN at entry 2` — a entrada inteira, e que ela não bate com o próprio resumo |
| uma entrada foi removida do meio | `BROKEN before entry 4` — a sequência pula de 2 para 4 |
| entradas foram removidas do fim | `TRUNCATED: the file ends at entry 5 and this dataplane wrote up to 7` |

A **sequência é conferida antes do resumo**, e essa ordem é o diagnóstico: remover uma entrada também quebra o resumo da seguinte, então um verificador que olhasse o hash primeiro reportaria toda remoção como alteração — da linha inocente depois da lacuna, mandando quem investiga para uma linha que ninguém tocou.

Entradas escritas antes da corrente existir continuam legíveis e são reportadas como **não cobertas**, nunca contadas como limpas. Um arquivo sem nenhuma entrada encadeada é reportado como `no chained entries: nothing here is covered` — nunca como íntegro, porque perder a corrente inteira é a primeira coisa que alguém tentaria.


:::info
**O que isso prova, e o que não prova.** Detecta uma trilha que foi **editada**, e diz onde. O que ele não faz é impedir alguém com acesso de escrita ao arquivo **e** a este algoritmo de reescrever a trilha inteira recalculando todos os resumos. Uma corrente que verifica prova que o arquivo não foi editado — não que é o mesmo arquivo. Compare o resumo do topo com a cópia no seu syslog remoto.

:::

# O que é recusado

**Log por fluxo não existe e não vai existir.** É precisamente o que o bloco de portas foi feito para evitar. Um CGNAT registrando cada fluxo gera volume que nenhum provedor consegue guardar por um ano de forma útil.

# Ver também

* [Comandos show](/s/wiki/doc/comandos-show-YlNji1X5W4) — como ler `show cgnat translation` e o que ele prova
* [Receitas do dia a dia](/s/wiki/doc/receitas-do-dia-a-dia-IcocgdbXW2) — o roteiro para atender uma ordem judicial
* [Encaminhamento de porta](/s/wiki/doc/encaminhamento-de-porta-epIZfdmzU3) — o bloco preso por um forward, e por que ele é uma alocação
* [Levar a caixa para uma versão nova](/s/wiki/doc/levar-a-caixa-para-uma-versao-nova-C09kO2kU8X) — o que o volume de log guarda, e o que uma reinstalação apaga
* [Mandar o log de eventos da caixa para o syslog do NOC](/s/wiki/doc/mandar-o-log-de-eventos-da-caixa-para-o-syslog-do-noc-N2uzxgzpZX) — o log de eventos da própria caixa (commits, enlaces, logins) é outro fluxo
