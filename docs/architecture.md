# hxcgnat — arquitetura

CGNAT com BPA (Bloco de Portas Alocadas) sobre VPP/DPDK.
Alvo: 100 Gbps+, 200k+ assinantes, appliance completo.

## Decisoes tomadas

| # | Decisao | Escolha | Motivo |
|---|---------|---------|--------|
| D1 | Base do dataplane | plugin proprio dentro do VPP | herda FIB, ARP/ND, fragmentacao, ICMP, tracing, API e BGP via linux-cp; sobra foco para a logica de BPA |
| D2 | Modos de BPA | deterministico **e** dinamico, por pool | deterministico da log zero; dinamico economiza IPv4 |
| D3 | Politica de mapeamento padrao | `interleaved` | shift-only nas duas direcoes + menor blast radius de blacklist |
| D4 | Sharding entre workers | handoff do VPP, por indice de assinante | garante que in2out e out2in do mesmo assinante caiam no mesmo worker |
| D5 | Enderecamento do pool | conjunto de prefixos nao contiguos | e produto para varios provedores, e nenhum tem IPv4 num bloco unico alinhado |

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

## Modo dinamico (PBA)

Bloco alocado no primeiro fluxo, liberado por inatividade.

- free-list de blocos por pool
- bihash `ip_priv -> bloco`
- timer de inatividade por bloco
- 1 log por alocacao e 1 por liberacao (syslog RFC 5424 / IPFIX)
- permite oversubscription: 200k assinantes com muito menos que um /21

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
- [ ] Nodes in2out / out2in / handoff
- [ ] Tabela de sessao (bihash) + timers
- [ ] Alocador dinamico de blocos
- [ ] Exportador de log (syslog / IPFIX)
- [ ] CLI e API binaria
- [ ] Appliance: ISO, tuning de kernel, systemd, HA
