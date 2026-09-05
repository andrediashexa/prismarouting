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

**Proposta: B.** O `vtysh` ja multiplexa varios daemons; um daemon a mais e
o caminho previsto pelo FRR, nao uma gambiarra. E o que entrega
`show running-config` mostrando BGP e CGNAT na mesma saida.

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

## Ordem sugerida

1. `linux_cp` + `linux_nl` no lab, com uma interface espelhada no Linux
2. FRR subindo e formando sessao BGP sobre o TAP
3. Rota aprendida por BGP aparecendo na FIB do VPP (`show ip fib`)
4. OSPF, depois IS-IS - IS-IS por ultimo, porque e o que testa o punt L2
5. Validacao de sobreposicao entre pool e endereco local (a armadilha acima)
6. `hexad` com a gramatica de CGNAT no `vtysh`
7. Migrar a CLI de debug do plugin para a VTY, mantendo a de debug
