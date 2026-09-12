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
* ele **distingue aviso de falha** e sempre sai com código zero, porque um diagnóstico não pode derrubar o boot;
* o contador de reinícios importa mais do que parece: um serviço em laço pode estar "ativo" no instante em que você olha, e só `NRestarts` conta a história.


:::success
Self-test dizendo pronto, sem aviso de instrução ausente e sem contador de reinício subindo: a máquina está dimensionada para o que você pediu dela.

:::

# Ver também

* [Primeiros passos](/doc/primeiros-passos-TAWDdSrYG7) — a configuração depois que o hardware está certo
* [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6) — onde `block-size` e o modo são escolhidos
* [Ampliar capacidade sem derrubar assinantes](/doc/ampliar-capacidade-sem-derrubar-assinantes-rWQkaj9r1u) — quando a conta acima mudar
