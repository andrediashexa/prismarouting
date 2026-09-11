# Timeouts e os pisos das RFCs

Quanto tempo cada tipo de sessão vive, quais são os pisos que as RFCs estabelecem, e o que quebra quando você desce abaixo deles. Para quem está com pool enchendo e pensou em encurtar timeout.

# A tentação, e o que ela custa

Encurtar um timeout é a primeira coisa em que qualquer um pensa quando o pool enche, e funciona: portas reciclam mais rápido e mais assinantes cabem num endereço público.

O que ela também faz é cortar conexões que estão **ociosas, não terminadas** — uma sessão SSH deixada aberta no almoço, um pool de banco de dados, um sensor que fala uma vez por hora. Essas voltam semanas depois como *"desconecta sozinho, aleatório"*, e ninguém atribui isso a um número mudado em outro mês.

# Os pisos

| Timeout | Piso | Referência | O que quebra abaixo dele |
|---------|-----:|------------|--------------------------|
| `udp`   | 120 s | RFC 4787 REQ-5 | um mapeamento fechando enquanto a aplicação ainda acredita que ele existe |
| `icmp`  | 60 s | RFC 5508 REQ-2 | uma resposta de eco chegando depois que o mapeamento fechou — lê-se como perda de pacote em todo teste de caminho |
| `tcp-transitory` | 240 s | RFC 5382 REQ-5 | uma conexão meio-fechada descartada antes de o par terminar de fechá-la |
| `tcp-established` | 7440 s | RFC 5382 REQ-5 | uma conexão ociosa mas viva, cortada |


:::info
Esses são **SHOULD**s das RFCs, e o equipamento os trata como tal: um valor abaixo do piso é **aceito**. Você pode ter um motivo, e uma caixa que recusa é menos útil do que a que você está substituindo. O que ela não faz é aceitar em silêncio.

:::

O aviso nomeia a RFC, o piso e o que quebra, e vai para **os dois lugares**: para quem configurou e para o log. Porque quem encurta um timeout quando o pool enche e quem diagnostica as desconexões aleatórias seis semanas depois não são a mesma pessoa.

Ele dispara **uma vez por mudança** — nunca por pacote, nunca por sessão. E um valor exatamente **no** piso não diz nada: um aviso que sempre dispara é um aviso que o operador aprende a pular.

# Configurando

Global:

```
configure
 nat cgnat timeout udp 300
 nat cgnat timeout icmp 60
 nat cgnat timeout tcp-transitory 240
 nat cgnat timeout tcp-established 7440
 commit
```

Por pool — sobrescreve o global; ausente, herda:

```
configure
 nat cgnat pool residencial
  timeout udp 300
  timeout tcp-syn 60
  timeout gre 3600
  timeout port-holddown 120
  timeout alg-expect 30
  exit
 commit
```

| Timeout só de pool | O que expira |
|--------------------|--------------|
| `tcp-syn`          | TCP meio-aberto, antes do handshake completar |
| `gre`              | o mapeamento GRE de uma chamada PPTP |
| `port-holddown`    | quanto tempo uma porta liberada espera antes de ser reusada |
| `alg-expect`       | quanto tempo uma porta de mídia anunciada por um ALG espera |


:::warning
`port-holddown` não é um detalhe de desempenho, é de prova. Reusar uma porta imediatamente faz o registro de quem a usava se sobrepor ao do próximo dono — e a resposta a uma ordem judicial passa a apontar duas pessoas para o mesmo par endereço/porta num intervalo curto.

:::

Todos são ajustes **aplicados no lugar**: não derrubam sessão.

# Como conferir

```
show cgnat pool
```

No dataplane, `show bpa timeouts` marca **todo valor abaixo do piso**, global e por pool com as sobrescritas já resolvidas — para que o estado fique visível a quem não sabia que devia perguntar.


:::success
Nenhum valor marcado abaixo do piso, e `show cgnat top` mostrando os assinantes longe do limite do bloco: o pool está dimensionado sem precisar apertar timeout.

:::

Se o pool está enchendo, encurtar timeout é o último recurso, não o primeiro. Antes dele: [ampliar a capacidade com um segundo pool](/doc/ampliar-capacidade-sem-derrubar-assinantes-rWQkaj9r1u), reduzir `block-size` numa janela, ou limitar quem está consumindo demais com `max-sessions`.

# Ver também

* [Ampliar capacidade sem derrubar assinantes](/doc/ampliar-capacidade-sem-derrubar-assinantes-rWQkaj9r1u) — a alternativa a apertar timeout
* [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6) — os campos por pool
* [Comportamento NAT e compatibilidade](/doc/comportamento-nat-e-compatibilidade-8eYzcDTnmJ) — a outra causa comum de "desconecta sozinho"
