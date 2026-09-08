# Comandos show

Os comandos `show` funcionam nos modos operacional **e** de configuração.

## `show cgnat`

As quatro primeiras respondem a pergunta de um operador em vez de reportar um contador. Todas tiram a resposta do **dataplane**, então funcionam pela VTY tão bem quanto pelo `vppctl` — um operador que precisa descer para a CLI do dataplane para diagnosticar é um operador para quem esta VTY é decoração.

| Comando | Argumento | Responde |
|---------|-----------|----------|
| `show cgnat subscriber` | `<endereço-interno>` | tudo sobre um assinante |
| `show cgnat translation` | `<endereço-público> <porta>` | quem estava atrás de um endereço público e porta |
| `show cgnat top` | `[<n>]`   | assinantes mais próximos de esgotar seus blocos |
| `show cgnat drops` |           | por que pacotes não passaram |
| `show cgnat pool` |           | pools de CGNAT configurados |
| `show cgnat session` |           | sessões ativas por thread |
| `show cgnat blocks` |           | blocos alocados em modo dinâmico |
| `show cgnat logging` |           | estado da exportação IPFIX |
| `show cgnat history` |           | trilha datada de configuração |

`show cgnat translation` é o comando que responde uma ordem judicial: dado um IP público, uma porta e um instante, quem era o assinante.

`show cgnat history` é o outro lado disso no modo determinístico — qual configuração estava em vigor naquela data, e quem a colocou lá.

## `show` — restante

| Comando | Mostra |
|---------|--------|
| `show configuration` | a configuração candidata |
| `show configuration compare` | diferenças da candidata contra a configuração em vigor |
| `show running-config` | a configuração em efeito, CGNAT e roteamento |
| `show rollback` | revisões commitadas disponíveis |
| `show version` | versões do HexaRouting, do VPP e do FRR |
| `show ip ...` | informação de IP (repassado ao FRR) |
| `show bgp ...` | informação de BGP (FRR) |
| `show isis ...` | informação de IS-IS (FRR) |
| `show interface` | interfaces (FRR) |
| `show logging` | log do sistema (FRR) |

Os `show` de roteamento são repassados ao `vtysh` do FRR, então a sintaxe completa do FRR vale depois da primeira palavra.

## O que olhar primeiro

| Sintoma | Comando |
|---------|---------|
| "assinante sem internet" | `show cgnat subscriber <ip-interno>` |
| "pacotes sumindo" | `show cgnat drops` |
| "de quem era esse IP:porta?" | `show cgnat translation <ip> <porta>` |
| "o pool vai encher?" | `show cgnat top` |
| "o coletor está recebendo?" | `show cgnat logging` |
| "o que mudou aqui?" | `show cgnat history` |
| "o que eu estou prestes a aplicar?" | `show configuration compare` |

## Como ler a resposta de `show cgnat translation`

Este é o comando que responde uma ordem judicial, e a resposta dele **diz quanto vale como prova**. Não são a mesma coisa:

```mermaid
flowchart TD
    T["show cgnat translation<br/>&lt;ip-público&gt; &lt;porta&gt;"] --> Q{"A resposta traz…"}
    Q -->|"FORMULA<br/>past or present"| A["Determinístico.<br/>A conta é estável, logo vale<br/>para um instante passado —<br/>junto com show cgnat history."]
    Q -->|"DYNAMIC<br/>NOW ONLY"| B["Dinâmico.<br/>Descreve o AGORA.<br/>O passado está nos registros<br/>IPFIX que foram ao coletor."]
```

Uma resposta dinâmica **nunca** afirma `FORMULA`. Essa recusa é deliberada: tratar uma alocação sob demanda como se fosse aritmética estável seria apresentar como prova algo que não sustenta a afirmação.

## O que cada resposta traz

| Comando | Na resposta você encontra |
|---------|---------------------------|
| `show cgnat subscriber <ip>` | o endereço público, a faixa de portas e `sessions N` |
| `show cgnat subscriber` de um IP fora dos pools | `not inside any configured pool` — é resposta, não erro |
| `show cgnat translation` | o assinante, mais `FORMULA`/`past or present` ou `DYNAMIC`/`NOW ONLY` |
| `show cgnat top` | os assinantes mais carregados, com as portas que seguram (ex.: `3/512`) |
| `show cgnat drops` | seção *packets that did not get through* com o motivo, por exemplo `no pool for source address` |


:::info
`**show cgnat drops**` **não mistura sucesso com falha.** Pacotes traduzidos aparecem sob o próprio cabeçalho, fora da seção de descartes. O VPP conta as duas coisas como se fossem iguais; durante um incidente elas não são.

:::

# Ver também

* [Operação e diagnóstico](/doc/operacao-e-diagnostico-A4fcKDVwZt) — estes comandos aplicados a um problema real
* [Logging IPFIX e trilha de auditoria](/doc/logging-ipfix-e-trilha-de-auditoria-lREEjJu1Bq) — o que responde pelo passado
