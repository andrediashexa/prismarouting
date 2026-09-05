# HexaRouting — arquitetura

Roteador em software com CGNAT BPA sobre VPP/DPDK, plano de controle em FRR.
Alvo: 100 Gbps+, 200k+ assinantes, appliance completo.

O plano de roteamento e da VTY esta em [routing.md](routing.md).

## Decisoes tomadas

| # | Decisao | Escolha | Motivo |
|---|---------|---------|--------|
| D1 | Base do dataplane | plugin proprio dentro do VPP | herda FIB, ARP/ND, fragmentacao, ICMP, tracing, API e BGP via linux-cp; sobra foco para a logica de BPA |
| D2 | Modos de BPA | deterministico **e** dinamico, por pool | deterministico da log zero; dinamico economiza IPv4 |
| D3 | Politica de mapeamento padrao | `interleaved` | shift-only nas duas direcoes + menor blast radius de blacklist |
| D4 | Sharding entre workers | handoff do VPP, por indice de assinante | garante que in2out e out2in do mesmo assinante caiam no mesmo worker |
| D5 | Enderecamento do pool | conjunto de prefixos nao contiguos | e produto para varios provedores, e nenhum tem IPv4 num bloco unico alinhado |
| D6 | Plano de controle | FRR sobre `linux_cp`/`linux_nl` | os dois plugins ja vem no VPP 26.06; FRR nao precisa saber que o VPP existe |
| D7 | Configuracao | VTY unica, `hexad` como daemon do `vtysh` | operador de provedor espera `configure terminal` e `write memory` cobrindo roteamento e CGNAT juntos |

## D5: por que o pool nao pode ser um prefixo so

A alocacao IPv4 de um provedor real e fragmentada: um /22 do LACNIC, um /24
comprado, um /23 herdado de aquisicao. Exigir um prefixo unico e alinhado
inviabilizaria o produto na maioria das instalacoes.

Entao `inside` e `outside` sao **conjuntos** de prefixos, apresentados ao
algoritmo como um espaco de indices denso `[0, count)`. A traducao
endereco <-> indice fica isolada em `bpa_addr_set_t`:

- faixas ordenadas por endereco, busca binaria nas duas direcoes
- caminho rapido para o caso de faixa unica, que e o mais comum
- prefixos adjacentes sao fundidos, o que encurta a busca no datapath
- prefixos sobrepostos sao **recusados**: sobreposicao quebra a bijecao
  endereco <-> indice, e bijecao quebrada significa um assinante sendo
  logado como outro - erro regulatorio, nao apenas bug

Consequencia: `outside.count` deixou de ser potencia de dois, entao a
otimizacao shift-only da politica interleaved nao vale sempre. O datapath
usa o reciprocal ja existente (multiply-shift) e cai no shift quando o
divisor por acaso e potencia de dois. `ref/check_magic.py` prova o
reciprocal exato para 1074 divisores arbitrarios ate 266 milhoes.

## O problema central: sharding

Com N workers, os dois sentidos do mesmo assinante precisam cair no **mesmo**
worker, senao a tabela de sessao vira estrutura compartilhada com lock — e isso
mata a escala em 100 G.

- `in2out`: o worker sai direto do IP de origem. Trivial.
- `out2in`: o pacote chega com (ip_pub, porta). Precisamos do assinante **antes**
  de saber o worker.

No modo deterministico isso e resolvido por aritmetica: `reverse()` e O(1) e
sem memoria, entao qualquer worker calcula o assinante e faz handoff para o
dono. O custo e um enqueue de indice de buffer — o mesmo mecanismo que o
`nat44-ed` do VPP usa em producao.

### Otimizacao possivel (fase 2)

Se `n_workers` for potencia de 2 e a politica for `interleaved`, entao
`pub_idx = sub_idx & (n_pub-1)`. Escolhendo `worker = pub_idx & (n_workers-1)`,
o worker passa a ser funcao apenas dos **bits baixos do IP publico de destino**.
Isso permite programar a RETA da NIC (E810 / CX6) para entregar o trafego
out2in ja na fila certa, eliminando o handoff no sentido de retorno.

## Modo deterministico: por que escala

A traducao de volta e formula pura, sem lookup de sessao:

```
pub_idx = ip_pub - outside_base
blk_idx = (port - port_min) / block_size
sub_idx = blk_idx * n_pub + pub_idx        (interleaved)
ip_priv = inside_base + sub_idx
```

Consequencias:
- zero log de alocacao — a prova para a autoridade e o algoritmo + a config
- o caminho out2in nao toca a tabela de sessao para descobrir o destino
- estado necessario e apenas o bitmap de portas por assinante, para escolher
  porta livre dentro do bloco: `block_size` bits por assinante ativo
  (512 portas -> 64 bytes; 200k assinantes -> ~13 MB)

## Sharding: implementado

`bpa-in2out-handoff` e `bpa-out2in-handoff` resolvem o indice denso do
assinante e entregam o buffer ao worker dono, via
`vlib_buffer_enqueue_to_thread()`.

- **in2out**: o indice sai direto do endereco de origem
- **out2in**: `bpa_reverse_sub()` recupera o indice a partir de
  `(endereco publico, porta)` so com aritmetica, sem consultar nenhuma
  estrutura compartilhada entre workers

A segunda linha e o motivo de o mapeamento deterministico importar alem do
log: sem ela, o caminho de volta nao teria como achar o dono sem um lookup
numa tabela global, e ai o lock voltaria.

A distribuicao usa o mesmo reciprocal do split do pool, entao o numero de
workers **nao precisa** ser potencia de dois. Sem workers configurados, a
CLI poe o node de traducao direto no arco: handoff seria uma volta pela
fila de frames sem ganho.

Verificado com 2 workers: assinantes 5, 6 e 9 previstos em thread 2, 1 e 2,
e foi exatamente onde as sessoes nasceram; as tres respostas chegaram na
thread 1 e foram entregues ao dono correto, com zero pacotes em
"sem sessao". Ver `lab/handoff.sh`.

## Tratamento de TCP e de erro ICMP

Duas lacunas que causam reclamacao de assinante, nao numero feio em grafico.

### Estado TCP

Marcar "estabelecida" em qualquer ACK e nunca tratar RST ou FIN faz uma
conexao fechada segurar a porta pelo timeout de estabelecida - 2 horas. Num
bloco de 512 portas isso esgota o assinante rapido, e chega ao suporte como
"a internet parou", sem nada obvio no dataplane.

`bpa_tcp_track()` mantem um estado minimo: RST fecha e a sessao expira na
varredura seguinte; FIM em qualquer sentido tira do timeout longo; o ACK apos
o handshake e o que promove para estabelecida.

Verificado em `lab/icmp-tcp.sh` com pacotes de flags reais: apos o RST a
sessao sai imediatamente, com o timeout de estabelecida ainda em 7440s.

### Erro ICMP e o PMTUD

Erros ICMP carregam o pacote original embutido, e e o embutido que diz de
quem eles falam. Sem traduzir isso, o "fragmentation needed" do PMTUD nunca
chega ao assinante: a conexao TCP fica pendurada e o sintoma e "alguns sites
nao abrem" - o classico do CGNAT mal feito. Traceroute tambem para.

O node reescreve o destino externo e a origem do pacote embutido. O checksum
do ICMP cobre o embutido inteiro, entao toda mudanca la dentro, **inclusive o
proprio checksum do IP embutido**, tem que ser refletida nele.

Traduzir nao basta: com o checksum errado a pilha do assinante descarta, e o
PMTUD continua quebrado com o contador dizendo que traduziu. Por isso o teste
captura o pacote que sai e confere os tres checksums em `lab/checkicmp.py`.

### Fragmentacao

Fragmento que nao e o primeiro nao carrega cabecalho L4, entao nao tem porta
para casar com sessao: sem tratamento ele e descartado, e DNS grande, VPN e
jogo param de forma intermitente - o pior tipo de sintoma para diagnosticar.

A saida e o reassembly virtual raso do VPP, ligado junto com a feature e
refcontado. Ele guarda as portas do primeiro fragmento e as anexa aos
seguintes, sem remontar o datagrama.

Duas armadilhas, as duas encontradas no teste e nenhuma visivel por leitura:

1. **Ordem de feature.** Sem `runs_after` no
   `ip4-sv-reassembly-feature`, o node do BPA podia rodar antes e ler campos
   de buffer que ainda nao existiam.
2. **Ordem de byte.** O reassembly guarda a porta em ordem de rede. Lida como
   ordem de host, 12345 (`0x3039`) vira 14640 (`0x3930`), o node nao acha a
   sessao e abre outra - **queimando uma porta do bloco por fragmento**. Num
   assinante que fragmenta bastante, isso esgota o bloco sozinho.

O segundo so apareceu porque o teste conferia a porta, e nao apenas se o
pacote saiu traduzido. Ele saia: com o endereco certo e a sessao errada.

## Modo dinamico (PBA): implementado

Bloco alocado no primeiro fluxo, devolvido apos hold-down.

### O problema de sharding, e a saida

Alocacao de bloco seria estado compartilhado entre workers, o que
reintroduziria o lock que o resto do desenho evita. Em vez disso o espaco de
blocos e **particionado**: o worker `w` so aloca blocos com
`block_idx % n_workers == w`.

E essa particao que faz o handoff continuar valendo nos dois modos:

```
in2out   worker = sub_idx   % n_workers
out2in   worker = block_idx % n_workers
```

O worker so entrega ao assinante um bloco da propria fatia, entao os dois
concordam. E no modo deterministico `block_idx == sub_idx`, entao a mesma
linha de codigo serve para os dois - o node de handoff nao ramifica por modo.

Custo declarado: a taxa de oversubscription passa a ser por worker, nao
global. Com assinantes espalhados por hash isso e estatisticamente
equivalente, mas uma fatia pode esgotar com outra ainda tendo bloco livre.

### Hold-down

Um bloco fica reservado por `block-holddown` segundos depois da ultima
sessao acabar. Nao e organizacao: sem hold-down, um assinante que fica quieto
um segundo e volta gera uma liberacao e uma alocacao, e **cada uma dessas e
um registro de log**. Hold-down e a principal alavanca sobre o volume de log
que o IPFIX vai exportar.

### Verificado em lab

`lab/dynamic.sh`, com 1024 assinantes possiveis contra 3 blocos:
alocacao sob demanda, mesmo assinante mantendo o bloco, quarto assinante
recusado com contador, volta traduzida, e blocos devolvidos apos hold-down.

`lab/dynamic-workers.sh`, com 2 workers e 12 blocos: 6 blocos por fatia,
assinantes pares na thread 1 e impares na thread 2, cada um com bloco da
propria fatia, e as quatro respostas achando o worker dono.

## Orcamento de memoria (200k assinantes)

| Estrutura | Dimensao | Tamanho |
|---|---|---|
| bitmap de portas | 200k x block_size/8 | ~13 MB @ bloco 512 |
| sessoes | 200k x ~100 medias x 64 B | ~1,3 GB |
| bihash in2out + out2in | overhead ~2x | ~2,6 GB |

Exige hugepages de 1 G e pinning de NUMA. A VM atual (8 GB, 1 no NUMA) serve
para desenvolvimento e teste funcional, nao para carga.

## Estado do projeto

- [x] Modelo de referencia do BPA validado (`ref/bpa_model.py`, `ref/validate.py`)
- [x] Nucleo em C do mapeamento, conferido contra o modelo (`make test`)
- [x] Alocador de portas por assinante (`bpa_ports.h`)
- [x] Plugin carrega no VPP 26.06 com CLI de pools
- [x] Pools de prefixos nao contiguos, para alocacao IPv4 real de provedor
- [x] Nodes in2out / out2in, com traducao verificada por injecao de pacote
- [x] Tabela de sessao (bihash) por worker + expiracao com timeouts configuraveis
- [x] Handoff entre workers, verificado com 2 workers (`lab/handoff.sh`)
- [x] Alocador dinamico de blocos, com hold-down e particao por worker
- [x] Roteamento: BGP, OSPF e IS-IS via FRR, com as tres rotas chegando na
      FIB do VPP (`lab/routing.sh`)
- [x] Validar pool contra endereco local, nas duas direcoes
- [x] VTY unica (`hexash`) - CGNAT nativo e roteamento repassado ao `vtysh`;
      o daemon registrado no `vtysh` foi descartado porque a lista de daemons
      dele e compilada e exigiria um fork permanente
- [x] Exportacao de log BPA via IPFIX, so alocacoes (RFC 8158 `natEvent`
      16, 17 e 12), verificada com coletor decodificando (`lab/ipfix.sh`)
- [ ] Trilha de configuracao append-only - e o "log" do modo deterministico
- [ ] API binaria do plugin, para a VTY e para automacao
- [x] Appliance: tuning de boot calculado do hardware, unidades systemd,
      instalador e ISO - ver [appliance.md](appliance.md)
- [x] Fragmentacao, via reassembly virtual raso
- [ ] HA / failover entre caixas
- [ ] Transporte TCP para o log IPFIX
- [x] Trilha de configuracao datada (`show bpa pool history`)
