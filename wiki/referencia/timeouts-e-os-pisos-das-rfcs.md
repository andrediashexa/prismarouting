# Timeouts e os pisos das RFCs

Quanto tempo cada tipo de sessão vive, quais são os pisos que as RFCs estabelecem, e o que quebra quando você desce abaixo deles. Para quem está com pool enchendo e pensou em encurtar timeout.

# A tentação, e o que ela custa

Encurtar um timeout é a primeira coisa em que qualquer um pensa quando o pool enche, e funciona: portas reciclam mais rápido e mais assinantes cabem num endereço público.

O que ela também faz é cortar conexões que estão **ociosas, não terminadas** — uma sessão SSH deixada aberta no almoço, um pool de banco de dados, um sensor que fala uma vez por hora. Essas voltam semanas depois como *"desconecta sozinho, aleatório"*, e ninguém atribui isso a um número mudado em outro mês.

# Os pisos

| Timeout | Piso | Referência | Padrão | O que quebra abaixo dele |
|---------|-----:|------------|-------:|--------------------------|
| `udp`   | 120 s | RFC 4787 REQ-5 | 300 s  | um mapeamento fechando enquanto a aplicação ainda acredita que ele existe |
| `icmp`  | 60 s | RFC 5508 REQ-2 | 60 s   | uma resposta de eco chegando depois que o mapeamento fechou — lê-se como perda de pacote em todo teste de caminho |
| `tcp-transitory` | 240 s | RFC 5382 REQ-5 | 240 s  | uma conexão meio-fechada descartada antes de o par terminar de fechá-la |
| `tcp-established` | 7440 s | RFC 5382 REQ-5 | 7440 s | uma conexão ociosa mas viva, cortada |

Os padrões já estão no piso ou acima dele: uma caixa que ninguém ajustou não dispara aviso nenhum.


:::info
Esses são **SHOULD**s das RFCs, e o equipamento os trata como tal: um valor abaixo do piso é **aceito**. Você pode ter um motivo, e uma caixa que recusa é menos útil do que a que você está substituindo. O que ela não faz é aceitar em silêncio.

:::

O aviso vai para **os dois lugares** - para quem configurou e para o log - porque quem encurta um timeout quando o pool enche e quem diagnostica as desconexões aleatórias seis semanas depois não são a mesma pessoa. Mas os dois não recebem a mesma frase.

Na VTY, o `commit` imprime uma linha por valor, com o nome do timeout, o valor que passou a valer e a RFC:

```
warning:  udp                 119   BELOW the floor RFC 4787 REQ-5 asks for
```

**O piso em segundos e o que quebra abaixo dele ficam no log**, não na tela do commit. Se você quer os três na mesma frase, é o log que responde - a tabela acima serve para o resto.

Ele dispara **uma vez por mudança** — nunca por pacote, nunca por sessão. E um valor exatamente **no** piso não diz nada: um aviso que sempre dispara é um aviso que o operador aprende a pular.

# Qual timeout vale para uma conexão TCP, e quando

Os quatro pisos acima não se aplicam todos ao mesmo tempo. Uma sessão TCP muda de timeout conforme o que passa por ela, e é isso que decide quanto tempo uma porta fica fora de circulação depois que a conexão acabou:

| Estado da conexão | Timeout que vale | Por quê |
|-------------------|------------------|---------|
| SYN enviado, nunca estabeleceu | `tcp-syn` (20 s por padrão) | um host varrendo a internet produz um destes por sondagem; presos pelo transitório, esgotariam o bloco do próprio assinante |
| estabelecida      | `tcp-established` | a conexão está viva, ainda que ociosa |
| FIN visto, de qualquer um dos lados | `tcp-transitory` | alguém começou a fechar: ela sai do timeout longo na hora |
| RST visto, de qualquer um dos lados | nenhum — a porta é liberada | um reset acabou com a conexão; não há o que segurar |

A leitura prática é esta: uma conexão encerrada com **reset** devolve a porta quase na hora; uma encerrada com o fechamento normal espera o transitório, não as duas horas; e só fica no `tcp-established` a conexão que **ninguém fechou**.


:::info
Isso importa quando um assinante enche o bloco sem tráfego que justifique. Se as portas estivessem presas por conexões já encerradas, o número cairia sozinho em poucos minutos. Um bloco que não esvazia é bloco cheio de sessões que ninguém fechou — aplicação que abandona socket, ou host infectado — e não de sobras do que já terminou.

:::

O rastreamento vale **nos dois sentidos**: o reset ou o fechamento contam venha de onde vier, porque quem encerra uma conexão tanto pode ser o assinante quanto o outro lado.

# Configurando

Global, no nível `nat` > `cgnat` — só os quatro timeouts com piso:

```
configure
 nat
  cgnat
   timeout udp 300
   timeout icmp 60
   timeout tcp-transitory 240
   timeout tcp-established 7440
   exit
  exit
 commit
```

Esses globais valem também para os pools NAT64: eles ficam em `nat` > `nat64`, mas os timeouts, as interfaces e o log são os de `nat` > `cgnat`, divididos pelos dois.

Por pool — sobrescreve o global; ausente, herda. Um pool aceita os quatro acima e mais quatro que só existem nele:

```
configure
 nat
  cgnat
   pool residencial
    timeout udp 300
    timeout tcp-syn 60
    timeout gre 3600
    timeout port-holddown 8
    timeout alg-expect 30
    exit
   exit
  exit
 commit
```

| Timeout só de pool | Padrão | O que expira |
|--------------------|-------:|--------------|
| `tcp-syn`          | 20 s   | TCP meio-aberto, antes do handshake completar |
| `gre`              | 1800 s | o mapeamento GRE de uma chamada PPTP, ocioso |
| `port-holddown`    | 4 s    | quanto tempo uma porta liberada espera antes de ser reusada |
| `alg-expect`       | 60 s   | quanto tempo uma porta de mídia anunciada por um ALG espera pelo primeiro pacote |

O `port-holddown` existe para que um segmento atrasado da conexão que acabou não caia na conexão nova que pegou a mesma porta — o que o assinante vê como um reset que ninguém consegue reproduzir. A porta volta para o bloco do **mesmo** assinante, então isso não mexe em quem responde por ela.

## Retenção de bloco, em BPA

Num pool BPA (`mode bpa`), um bloco que ficou ocioso não volta para o pool na hora: fica reservado ao assinante por `block-holddown` segundos, global, no nível `nat` > `cgnat`. O padrão é 120 s.

```
configure
 nat
  cgnat
   block-holddown 120
   exit
  exit
 commit
```

É a principal alavanca do volume de log: cada liberação e cada nova alocação são um registro IPFIX, e com uma retenção curta demais um assinante que pausa entre dois pacotes libera e realoca o bloco o tempo todo — dois registros a mais no coletor a cada pausa. O valor mínimo aceito é 1 s. Num pool determinístico, vale só para os blocos da `reserve`: o bloco que a fórmula dá a cada assinante nunca é liberado.

Todos são ajustes **aplicados no lugar**: não derrubam sessão.

# Como conferir

```
show this
```

Dentro de `nat` > `cgnat`, ou dentro do pool, `show this` mostra os timeouts configurados naquele nível. E o `commit` avisa: **todo valor abaixo do piso** é marcado no momento em que a mudança é aplicada, global e por pool com as sobrescritas já resolvidas, para que o estado fique visível a quem não sabia que devia perguntar.


:::success
Nenhum valor marcado abaixo do piso, e `show cgnat top` mostrando os assinantes longe do limite do bloco: o pool está dimensionado sem precisar apertar timeout.

:::

Se o pool está enchendo, encurtar timeout é o último recurso, não o primeiro. Antes dele: [ampliar a capacidade com um segundo pool](/s/wiki/doc/ampliar-capacidade-sem-derrubar-assinantes-rWQkaj9r1u), reduzir `block-size` numa janela, ou limitar quem está consumindo demais com `max-sessions`.

# Ver também

* [Ampliar capacidade sem derrubar assinantes](/s/wiki/doc/ampliar-capacidade-sem-derrubar-assinantes-rWQkaj9r1u) — a alternativa a apertar timeout
* [Configuração de pool](/s/wiki/doc/configuracao-de-pool-btyS572Ih6) — os campos por pool
* [Comportamento NAT e compatibilidade](/s/wiki/doc/comportamento-nat-e-compatibilidade-8eYzcDTnmJ) — a outra causa comum de "desconecta sozinho"
