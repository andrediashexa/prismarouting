# HexaRouting — plano de roteamento (OSPF, IS-IS, BGP) e VTY

O sistema deixa de ser "uma caixa de CGNAT" e passa a ser um roteador com
CGNAT BPA embutido. Este documento e o projeto; nada aqui esta implementado.

## Componentes

| Peca | Papel |
|------|-------|
| VPP | dataplane: encaminhamento e CGNAT BPA (plugin `bpa`) |
| FRR | plano de controle: `bgpd`, `ospfd`, `isisd`, `zebra` |
| `linux_cp` + `linux_nl` (VPP) | cola entre os dois |
| `hexad` | daemon de configuracao do HexaRouting, integrado ao VTY |

## Como FRR conversa com o VPP

O VPP 26.06 ja traz `linux_cp_plugin.so` e `linux_nl_plugin.so`, com headers
em `/usr/include/lcp/`. Nao ha integracao a inventar:

1. `linux_cp` espelha cada interface do VPP como um TAP no Linux (um "LIP",
   Linux Interface Pair) e sincroniza estado e enderecos.
2. FRR roda no Linux e enxerga os TAPs como netdevs normais. Forma
   adjacencia OSPF/IS-IS e sessao BGP sem nenhuma modificacao.
3. As rotas que o FRR instala na FIB do Linux sao lidas por `linux_nl` via
   netlink e programadas na FIB do VPP.

FRR nao precisa saber que o VPP existe. E o mesmo desenho que produtos
comerciais de VPP usam.

### Alternativa para a fase 2

FRR tem plano de dados plugavel (`zebra_dplane`). Um plugin que programe o
VPP direto pela API binaria elimina a ida e volta pela FIB do kernel e
melhora a convergencia. Custa acoplamento com o FRR. Fica para depois de o
caminho padrao estar funcionando.

## O detalhe que quebra desenho ingenuo: IS-IS nao roda sobre IP

Cada protocolo tem exigencia diferente de punt:

| Protocolo | Encapsulamento | Endereco |
|-----------|----------------|----------|
| BGP | TCP porta 179 | unicast, endereco local da caixa |
| OSPFv2 | IP protocolo 89 | multicast 224.0.0.5 e 224.0.0.6 |
| IS-IS | **OSI CLNS direto sobre o enlace** | multicast L2 `01:80:C2:00:00:14/15` |

IS-IS **nao usa IP**. Um caminho de punt que so entenda IPv4 e IPv6 forma
adjacencia OSPF e sessao BGP normalmente, e simplesmente nunca sobe IS-IS —
falha silenciosa e chata de diagnosticar. O TAP do `linux_cp` espelha quadro
Ethernet, entao carrega CLNS; e por isso que ele e o caminho escolhido, e
nao um punt so de IP.

## Duas armadilhas encontradas no lab (nao teoricas)

Ambas descobertas ao subir os tres protocolos em `lab/routing.sh`. As duas
falham de um jeito que nao aponta para a causa.

### 1. MTU herdado trava o OSPF em Exchange

O TAP criado pelo `linux_cp` nasce com MTU 9000. Se o enlace fisico estiver
em 1500, o OSPF forma vizinhanca, chega ate **Exchange/DR e para ali**: ele
compara MTU nos pacotes DD e desiste em silencio. Nao ha log dizendo "MTU
divergente"; o operador ve so uma adjacencia que nunca completa.

Com `set interface mtu 1500 <interface>` (que o `lcp-sync` propaga ao TAP), a
adjacencia foi para **Full em 8 segundos**.

Conclusao para o appliance: **MTU tem que ser configuracao explicita**, nunca
herdada. Entra na gramatica da VTY como parametro de interface.

### 2. IS-IS morre em `osi-input` se o OSI nao for habilitado

Confirmado o que o desenho previa, e com o ponto exato da morte. Capturas nos
tres pontos do caminho:

| Onde | Hellos IS-IS |
|------|--------------|
| `veth-out`, o peer enviando | presentes |
| `veth-vpp`, chegando no VPP | presentes |
| `vppwan0`, o TAP | **zero** |

O contador do VPP mostrava `osi-input: unknown osi protocol`. O quadro chega,
e reconhecido como OSI, e ninguem esta registrado para receber IS-IS.

O `linux_cp` do VPP 26.06 ja tem a solucao, so nao vem ligada:

```
lcp osi-proto enable 0x83
```

0x83 e o NLPID do IS-IS. Com isso o vizinho subiu e a rota chegou na FIB.

O detalhe que torna isto perigoso: **BGP e OSPF sobem normalmente sem esse
comando.** Quem testar so com eles conclui que a integracao esta pronta, e o
IS-IS fica mudo ate alguem tentar usar. Por isso o IS-IS ficou por ultimo na
ordem de implementacao: e ele que valida o caminho de punt L2.

## A armadilha: CGNAT sobre o proprio roteamento

Sessao BGP e adjacencia OSPF/IS-IS terminam em enderecos **da propria
caixa**. Se um pool BPA contiver um desses enderecos, `bpa-out2in` vai
tentar traduzir os pacotes de protocolo: a sessao BGP cai de forma
intermitente e o motivo nao aparece em lugar nenhum obvio.

O node `bpa-out2in` roda no arco `ip4-unicast`, **antes** do `ip4-lookup` e
portanto antes do `ip4-local` que entrega trafego local. Ou seja, ele ve o
pacote BGP primeiro. Duas defesas, as duas necessarias:

1. **Validar na criacao do pool**: recusar pool cujo conjunto publico
   contenha qualquer endereco de interface local. Pega o erro no instante em
   que o operador o comete, que e onde ele e barato.
2. **Pular destino local no out2in**: consultar a FIB e nao traduzir o que e
   endereco local. Cobre o endereco configurado *depois* do pool.

Nota relacionada: os enderecos do pool publico normalmente **nao** sao
enderecos de interface. Eles chegam a caixa por rota do upstream, nao por
configuracao local. Se alguem configurar um endereco do pool numa interface,
as duas defesas acima entram em conflito com a intencao - por isso a
validacao recusa em vez de ajustar sozinha.

## VTY

O objetivo e uma CLI unica, no estilo que operador de provedor espera:
`configure terminal`, `show running-config`, `write memory` - cobrindo
roteamento **e** CGNAT no mesmo lugar.

Tres caminhos:

| Opcao | Como | Custo |
|-------|------|-------|
| A. Envolver o `vtysh` | shell propria roteia `router bgp` para o vtysh e `cgnat ...` para a API do VPP | rapido, mas dois arquivos de config e dois `write memory` |
| B. `hexad` como daemon FRR | linka a `libfrr`, registra no `vtysh`, traduz comando em chamada da API binaria do VPP | CLI realmente unica; acopla com internals do FRR |
| C. VTY propria do zero | controle total | mais trabalho, e reimplementa o que a `libfrr` ja faz bem |

**Escolhida: A, e por um motivo que so apareceu ao verificar.**

A opcao B parecia melhor no papel, mas a lista de daemons do `vtysh` e
**compilada** (`vtysh_client[]`) e nao ha mecanismo de plugin. Registrar um
daemon novo exigiria manter um fork do `vtysh` pelo resto da vida do produto,
e reaplica-lo a cada versao do FRR. Custo permanente alto para um ganho de
integracao que o repasse resolve.

O `hexash` trata CGNAT nativamente e repassa roteamento ao `vtysh`,
preservando o contexto de bloco. O resultado e o mesmo que se buscava:
`show running-config` mostrando BGP e CGNAT na mesma saida.

### A fonte da verdade e o dataplane

Detalhe que so apareceu no teste: cada `hexash -c ...` e um processo novo,
entao estado em memoria nao sobrevive entre invocacoes, e a primeira versao
gravava configuracao vazia. A correcao foi fazer o plugin emitir a propria
configuracao em sintaxe de VTY (`show bpa config`), que o `hexash` le.

E o mesmo desenho do `vtysh`: quem sabe a configuracao e o daemon, nao a
shell. Efeito colateral util: uma faixa publica fundida a partir de prefixos
adjacentes e decomposta de volta em prefixos CIDR na saida, entao a
configuracao gravada pode ser relida.

### Limites desta primeira versao

- sem `?` e sem completacao por tab; historico e edicao de linha vem do
  readline
- a deteccao de erro do dataplane e por padrao de texto, porque o `vppctl`
  devolve 0 mesmo em erro. Trocar pela API binaria esta no roadmap
- escrita em Python: e plano de controle interativo, fora de qualquer caminho
  de pacote. Em C seriam 3x as linhas para o mesmo resultado

Esboco da gramatica:

```
hexarouting# configure terminal
hexarouting(config)# cgnat pool provedor-a
hexarouting(config-cgnat-pool)# inside 100.64.0.0/12
hexarouting(config-cgnat-pool)# outside 200.150.0.0/24
hexarouting(config-cgnat-pool)# outside 45.170.16.0/24
hexarouting(config-cgnat-pool)# block-size 512
hexarouting(config-cgnat-pool)# mode deterministic
hexarouting(config-cgnat-pool)# exit
hexarouting(config)# router bgp 65000
hexarouting(config-router)# neighbor 200.150.0.2 remote-as 65001
```

## Versao do FRR

O Debian 12 traz FRR 8.4.4, ja antigo. Para produto, fixar uma versao do
repositorio do proprio FRR (`deb.frrouting.org`), como ja e feito com o VPP
no `build/Dockerfile.dev`. Versao de plano de controle nao pode flutuar com
o que a distro tiver no dia.

## Estado

- [x] `linux_cp` + `linux_nl` no lab (`lab/lcp.sh`)
- [x] BGP, OSPF e IS-IS sobre o TAP, com as tres rotas na FIB do VPP
      (`lab/routing.sh`)
- [x] Validacao de sobreposicao entre pool e endereco local, nas duas direcoes
- [ ] `hexad` com a gramatica de CGNAT no `vtysh`
- [ ] MTU como parametro explicito de interface na VTY
- [ ] Migrar a configuracao para a VTY, mantendo a CLI de debug do plugin
