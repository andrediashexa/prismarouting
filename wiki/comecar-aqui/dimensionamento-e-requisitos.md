# Dimensionamento e requisitos

Quanto de máquina e quantos endereços públicos o seu caso exige. Leia antes de comprar hardware ou de fechar o bloco de IPv4.

# Requisitos de máquina

| Recurso | Mínimo | Por quê |
|---------|--------|---------|
| RAM     | **4 GB** | rodando pela ISO, a raiz gravável fica inteira em RAM sobre o squashfs; com 2 GB o OOM killer mata o VPP e leva o resto do boot junto |
| CPU     | **SSE4.2** | o binário do VPP assume SSE4.2 e usa AVX quando existe |


:::warning
**CPU sem SSE4.2 não é um problema lento, é um problema mudo.** O VPP morre com `SIGILL` em milissegundos e, com `Restart=always`, o sintoma vira um laço de reinício que não diz nada sobre a causa. Por isso o self-test de boot confere o conjunto de instruções **antes** de olhar para o VPP.

:::

Medido durante a validação: com 2 GB a ISO reporta `Out of memory: Killed process (vpp)` com o VPP segurando apenas **19 MB residentes** — ele não era o glutão, era a vítima escolhida. Instalado em disco o consumo cai, porque não há a sobreposição em RAM, mas 4 GB continua sendo o piso sensato para um dataplane.

# Se a caixa vai receber a tabela BGP completa

4 GB é o piso de uma caixa que **não** carrega tabela cheia. Recebendo uma tabela IPv4 completa de um trânsito, o roteamento sozinho passa a ocupar cerca de **1,4 GB** de RAM. Medido com uma sessão e um milhão de prefixos: o `bgpd` ficou com \~840 MB e o `zebra` com \~550 MB, contra 37 MB somados numa caixa que só recebe rota padrão. Cada trânsito adicional com tabela cheia soma de novo, e IPv6 soma por cima.

| Cenário de peering | RAM só para o roteamento | Piso recomendado da máquina |
|--------------------|-------------------------:|----------------------------:|
| Só rota padrão     | \~40 MB                  | 4 GB |
| Uma tabela IPv4 cheia | \~1,4 GB              | 16 GB |
| Dois trânsitos com tabela cheia, mais IPv6 | mais que isso | 32 GB |

O dataplane reserva a maior parte da RAM em hugepages e deixa `máx(2, RAM/4)` GB para todo o resto. Numa caixa de 4 GB isso são 2 GB, e uma tabela cheia sozinha já ocupa quase tudo — por isso 8 GB já é apertado e 16 GB é o número sensato.


:::info
**Não existe limite de memória configurado para o roteamento, e isso é deliberado.** Um limite apertado não deixa o BGP mais lento: ele mata o `bgpd` no meio da convergência, o serviço reinicia cinco segundos depois, converge de novo e morre de novo. Quem protege a caixa é o outro lado — o dataplane está fora da escolha do OOM killer, de modo que, faltando memória, o que o kernel escolhe é o próprio processo de roteamento que cresceu, e a caixa continua encaminhando com o que já aprendeu. O self-test de boot diz na tela se essa proteção está valendo.

:::

# Quantos núcleos, e o que sobra para o plano de controle

O equipamento entrega ao dataplane **todos os núcleos físicos do processador que atende a placa de rede, menos o primeiro**. Esses núcleos ficam isolados: o escalonador do Linux não coloca mais nada neles, e cada um roda uma thread de encaminhamento que nunca dorme. O primeiro núcleo fica com o sistema operacional, com o roteamento, com a VTY, com o SSH - e com a thread principal do dataplane.

Numa caixa de 4 ou 8 núcleos isso é equilibrado. Num processador de 32 núcleos, não: são 31 núcleos encaminhando e **um** carregando tudo o que não encaminha. E a thread principal do dataplane, que está nesse mesmo núcleo, precisa parar o encaminhamento por um instante uma vez por segundo para fazer a manutenção das sessões. Quanto mais trabalho de controle disputa esse núcleo, mais tempo ela espera para ser escalonada - e o encaminhamento espera junto.

| Núcleos do processador | Encaminhando | Para todo o resto |
|-----------------------:|-------------:|------------------:|
| 4   | 3  | 1 |
| 8   | 7  | 1 |
| 32  | 31 | 1 |

## Devolvendo núcleos

`HEXA_MAX_WORKERS`, em `/etc/hexarouting/tune.env`, limita quantos núcleos físicos viram threads de encaminhamento. Ele **vem vazio**, e vazio significa sem limite - exatamente o comportamento acima. Preenchendo, os núcleos liberados deixam de ser isolados e voltam para o escalonador, onde o roteamento e a VTY podem usá-los de fato.

Cada núcleo devolvido é aquela fatia da capacidade de encaminhamento. Não existe um valor padrão porque a troca depende de quanto trabalho de controle a caixa carrega: uma caixa que só recebe rota padrão quase não tem plano de controle; uma que recebe duas tabelas cheias, refaz política BGP e ainda serve consultas tem bastante.

**Diminuir o número de workers custa um boot; aumentar custa dois, e o segundo é diferente.** Medido:

| O que você faz | Próximo boot | O boot seguinte |
|---|---|---|
| Baixa o limite (menos workers) | já sobe com menos workers | os núcleos liberados saem do isolamento e voltam para o escalonador |
| Sobe o limite, ou apaga o valor | sobe **em modo conservador**: sem workers, sem hugepages, sem placa de dados | sobe com o plano completo |

O modo conservador do segundo caso não é um defeito: o equipamento não pode fixar um worker num núcleo que o kernel ainda não isolou, e isolar é coisa de linha de comando do kernel, que só vale no boot seguinte. Mas é uma caixa sem encaminhamento por um boot. **Programe essa mudança para uma janela**, e confira o self-test do primeiro boot antes de dar por encerrado.


:::info
**O self-test de boot diz de que lado a sua caixa está.** Ele imprime quantos núcleos sobraram para o plano de controle e avisa quando sobrou **um** havendo mais de um isolado - ou seja, quando havia núcleo para devolver e não foi devolvido. Uma caixa de dois núcleos não recebe esse aviso: ela não tem o que devolver.

:::


:::warning
**Não existe limite de CPU configurado para o plano de controle, e isso também é deliberado: o limite já existe, e é o próprio isolamento.** Medido numa caixa instalada de dois núcleos com o núcleo 1 isolado: o processo `init` roda só no núcleo 0, e o processo de roteamento, que é filho dele, herda essa mesma permissão. O kernel confina tudo o que não é o dataplane aos núcleos que sobraram, sem que ninguém precise configurar nada. Limitar o roteamento por cima disso não libera nada para o encaminhamento, porque o roteamento já não chega nos núcleos de encaminhamento; o que um limite desses faria é atrasar a convergência BGP e a recarga de configuração sem devolver nada em troca.

**Herdado não é o mesmo que imposto.** Na mesma caixa, um processo que pede explicitamente para rodar no núcleo isolado **consegue**. Quem escreve numa sessão de shell que quer um núcleo de encaminhamento pediu aquilo de propósito, e nada que o produto instala faz isso sozinho, mas vale saber que a proteção é por herança e não por cerca.

Quem decide quanto de CPU o plano de controle tem é `HEXA_MAX_WORKERS`, e isso é uma decisão de dimensionamento, não um limite de processo.

:::
## O self-test confere os núcleos que o dataplane recebeu

O equipamento calcula o plano de núcleos a cada boot e o grava em um arquivo que o dataplane lê ao iniciar. Duas coisas podem sair do lugar sem nenhum sintoma:

* o dataplane pode estar rodando desde **antes** da última mudança de plano, e nesse caso ele continua nos núcleos com que subiu enquanto a tela reporta o plano novo;
* a linha de comando do kernel pode não ter acompanhado, e nesse caso uma thread de encaminhamento divide o núcleo com todo o resto.

Nos dois casos a caixa dá boot, encaminha, responde a tudo e entrega uma fração da capacidade. **Não há contador que denuncie isso**: uma thread que nunca dorme marca 100% de uso tendo ou não o núcleo só para ela.

Por isso o self-test não confere o arquivo: ele confere onde o kernel realmente colocou as threads do dataplane e quais núcleos o kernel realmente está isolando, e reclama quando os três não concordam. Reiniciar o dataplane resolve o primeiro caso; o segundo pede um `update-grub` e um reboot.

# Memória por estrutura

| Estrutura | Dimensionamento | Ordem de grandeza |
|-----------|-----------------|-------------------|
| Bitmap de portas | `block-size` bits por assinante ativo | 64 bytes com blocos de 512 → \~13 MB para 200 mil assinantes |
| Tabelas de sessão | duas por worker | 16 MB cada por padrão |
| Posse de bloco (modo dinâmico) | 4 bytes por bloco, por fatia de worker |                   |

A tabela de sessões é ajustada com:

```
nat cgnat session-table buckets <n> memory <MB>
```

O padrão é 16 mil buckets e 16 MB por tabela. A reserva é **virtual**: ela não custa RAM enquanto a tabela não é preenchida, e o OOM killer também não a enxerga — quem escolhe a vítima olha memória **residente**, não memória mapeada. O padrão modesto continua certo pelo motivo mais simples: a tabela acaba sendo usada, e uma caixa pequena não tem essa memória. Uma implantação de 200 mil assinantes deve elevar esse valor; uma caixa pequena não deve pagar por capacidade que nunca vai usar.

# Quantos endereços públicos

No **modo determinístico** todo assinante ocupa seu bloco esteja online ou não. A conta é direta:

```
endereços públicos = assinantes × block-size ÷ 64512
```

(64512 é a faixa útil de portas, de 1024 a 65535.)

| Assinantes | Bloco | Endereços | Prefixo |
|-----------:|------:|----------:|---------|
| 50\.000    | 512   | \~397     | `/23`   |
| 100\.000   | 512   | \~794     | `/22`   |
| 200\.000   | 512   | \~1.588   | `/21`   |
| 200\.000   | 256   | \~794     | `/22`   |
| 200\.000   | 1024  | \~3.175   | `/20`   |

O **modo dinâmico** aloca só para quem está usando, então cabe sobreassinatura e a necessidade cai bastante — em troca de voltar a gerar log.

## Em modo dinâmico, dimensione pelo número que o equipamento garante

Numa caixa com mais de um worker, os blocos de um pool dinâmico são **repartidos entre os workers**: cada bloco pertence a um worker só, e cada assinante sai por um worker só. Os dois repartes são calculados de formas diferentes, então nem sempre casam - um assinante pode receber "sem bloco livre" enquanto sobra bloco na fatia de outro worker.

Por isso `show cgnat pool` mostra **dois** números num pool dinâmico:

```
capacity     768 subscribers (usable 768)
             384 whatever the addresses are: blocks are sharded 2 ways and a subscriber draws only from its own worker's slice
```

* o primeiro é o **teto**: o que o pool atende se os assinantes se distribuírem perfeitamente entre os workers;
* o segundo é o **piso garantido**: o que o pool atende independentemente de quais endereços internos estejam online.

**Dimensione pelo segundo.** O primeiro depende de sorte no plano de endereçamento, e sorte não é um número de compra.

A segunda linha só aparece quando há algo a perder. Um pool com bloco sobrando para todo endereço interno não perde nada para o reparte, e nesse caso o equipamento imprime só o teto.

A distância entre os dois encolhe rápido conforme o pool cresce em relação ao número de workers. Medido, com os assinantes espalhados aleatoriamente pela faixa interna:

| Blocos no pool | Workers | Garantido | Atendidos na prática | Perda |
|---------------:|--------:|----------:|---------------------:|------:|
| 12    | 2  | 6     | \~10,6   | 11%  |
| 48    | 4  | 12    | \~43,3   | 10%  |
| 192   | 4  | 48    | \~183    | 4,5% |
| 768   | 8  | 96    | \~742    | 3,4% |
| 7.936 | 8  | 992   | \~7.848  | 1,1% |

E a perda **desaparece** quando os endereços internos são entregues em sequência a partir do início da faixa, que é o caso de um pool DHCP preenchendo de baixo para cima. Um plano de endereçamento contíguo não perde nada; um plano esburacado perde o da coluna da direita.


:::warning
A conclusão prática: em modo dinâmico, **pool pequeno com muitos workers é o pior caso**. Um pool de 12 blocos com 8 workers garante um único assinante. Se o pool é pequeno por escolha - e no modo dinâmico costuma ser, já que ele existe justamente para economizar IPv4 - prefira menos workers ou um pool maior a descobrir isso com o cliente na linha.

:::


:::info
Bloco menor economiza endereço e aperta o assinante. 512 portas atendem uso residencial comum com folga; abaixo de 256, aplicações que abrem muitas conexões simultâneas (um navegador com muitas abas, um cliente de torrent) começam a esbarrar no limite. Acompanhe com `show cgnat top` antes de reduzir.

:::

# Volume de log

Premissas explícitas, para ordem de grandeza e não para precisão: 200 mil assinantes, modo dinâmico, um bloco renegociado \~5 vezes por dia por assinante ativo, \~40 bytes por registro com o enquadramento IPFIX.

| Cenário | Registros/dia | Volume bruto/dia | Um ano |
|---------|--------------:|-----------------:|-------:|
| BPA dinâmico (alocação + liberação) | \~2 milhões   | \~80 MB          | dezenas de GB |
| NAT por fluxo (referência) | \~40 milhões  | \~2,4 GB         | dezenas de TB |
| BPA determinístico | 0             | 0                | 0      |

## Os registros têm sistema de arquivos próprio

Em modo dinâmico o log de alocação **é** o registro de conexão que o Marco Civil exige guardar por um ano. Por isso o instalador cria três partições — ESP, raiz e `/var/log/hexarouting` — em vez de deixar tudo junto.


:::warning
Compartilhando o sistema de arquivos com o resto, um log que enche impede a máquina de gravar a própria configuração e a própria trilha, porque ela está ocupada registrando quem se conectou a quê. Com volume próprio, um log cheio é só um log cheio.

:::

O tamanho padrão é aritmético e a conta aparece na tela: \~80 MB por dia para 200 mil assinantes em modo dinâmico, ou cerca de **29 GB por ano**. Modo determinístico não registra alocação nenhuma e quase não precisa de espaço — que é por que isso é uma pergunta e não uma constante. A raiz nunca fica com menos de 8 GB.

Em instalação não assistida, `hexarouting.log-gb=` responde essa pergunta na linha de comando do kernel.

O volume é montado com `nofail`: uma máquina que não dá boot porque o volume de registros sumiu é uma máquina em que ninguém consegue entrar para descobrir o porquê. Por isso o self-test de boot **reporta onde os registros estão** — sem isso, uma caixa cujo volume não montou sobe caladinha gravando na raiz.

Essa tabela é o argumento comercial do BPA em uma linha: guardar um ano de registros passa de dezenas de terabytes para dezenas de gigabytes — ou para nada.

# Como conferir

Depois de instalar, no console:

* o self-test reporta RAM, conjunto de instruções, VPP, contadores de reinício, estado do tuning, FRR e pools configurados;
* ele também confere **os núcleos**: se o dataplane está nos núcleos que o plano dele nomeia e se o kernel está isolando esses núcleos, e diz quantos sobraram para o plano de controle;
* ele **distingue aviso de falha** e sempre sai com código zero, porque um diagnóstico não pode derrubar o boot;
* o contador de reinícios importa mais do que parece: um serviço em laço pode estar "ativo" no instante em que você olha, e só `NRestarts` conta a história.


:::success
Self-test dizendo pronto, sem aviso de instrução ausente e sem contador de reinício subindo: a máquina está dimensionada para o que você pediu dela.

:::

# Ver também

* [Primeiros passos](/doc/primeiros-passos-TAWDdSrYG7) — a configuração depois que o hardware está certo
* [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6) — onde `block-size` e o modo são escolhidos
* [Ampliar capacidade sem derrubar assinantes](/doc/ampliar-capacidade-sem-derrubar-assinantes-rWQkaj9r1u) — quando a conta acima mudar
