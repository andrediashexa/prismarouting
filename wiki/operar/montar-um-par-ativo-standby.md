# Montar um par ativo/standby

Como pôr duas caixas Prisma Routing sustentando o mesmo endereço de serviço, uma ativa e outra de reserva, e como provar que a troca funciona antes de precisar dela. Para quem já tem as duas caixas instaladas e encaminhando; o porquê de cada escolha está em [Alta disponibilidade](/s/wiki/doc/alta-disponibilidade-Zc1pBKocic).


:::info
**O par preserva endereço, não conexão.** Não existe sincronismo de sessão: as conexões em curso caem na troca, como em qualquer NAT. Num pool **determinístico** o assinante volta no **mesmo endereço público e no mesmo bloco de portas**, porque o bloco é uma fórmula da configuração. Num pool **BPA** (alocação dinâmica) a caixa que assume não sabe que blocos a outra tinha entregue: o assinante recebe **um bloco novo**, alocado e registrado pela caixa que assumiu.

:::

Nos exemplos, a caixa A é a que deve ficar com o serviço e a caixa B é a reserva. `ens18` é a porta de dentro (assinantes) e `ens19` a porta de fora (upstream); troque pelos nomes das suas caixas. Os nomes das portas não precisam ser iguais nas duas: o par é casado pelo número do roteador virtual e pelo endereço, nunca pelo nome da interface.

# 1. Os mesmos pools nas duas caixas

Configure os pools de CGNAT **exatamente iguais** nas duas caixas: prefixos de dentro e de fora (digitados ou vindos de uma `prefix-list`), `block-size`, `ports`, `policy`, `mode`, `reserve` e as VRFs — e, num pool NAT64, `subscriber-length` e `nat64-prefix`. São os campos que decidem onde cada assinante cai, e duas caixas que discordam neles falham **em silêncio** — a troca põe os assinantes em blocos que a outra nunca usou, e nenhuma das duas registra nada de anormal.

O que não mexe no mapeamento pode diferir sem problema: timeouts, ALGs, clamp de TCP, listas de bypass, limites de sessão e de taxa, encaminhamentos de porta, `block-stride`, alarme de utilização.

Como configurar cada campo está em [Configuração de pool](/s/wiki/doc/configuracao-de-pool-btyS572Ih6).

# 2. `vrrp` nas portas de dentro e de fora

Em cada porta que os vizinhos usam como gateway, declare um roteador virtual (RFC 5798) com um endereço de serviço que **nenhuma das duas caixas** tem como endereço próprio. Cada caixa mantém o próprio endereço na porta; o endereço de serviço é o que se move.

Na caixa A, prioridade maior, e cada porta acompanhando (`track`) a outra:

```
configure
 interface ens18
  ipv4 address 100.64.3.252/22
  vrrp 1 address 100.64.3.254
  vrrp 1 priority 200
  vrrp 1 track ens19 decrement 150
  exit
 interface ens19
  ipv4 address 198.51.100.2/29
  vrrp 2 address 198.51.100.1
  vrrp 2 priority 200
  vrrp 2 track ens18 decrement 150
  exit
```

Na caixa B, os **mesmos números de roteador virtual e os mesmos endereços de serviço**, com o endereço próprio de cada porta e uma prioridade **diferente** — a padrão, 100, serve:

```
configure
 interface ens18
  ipv4 address 100.64.3.253/22
  vrrp 1 address 100.64.3.254
  vrrp 1 track ens19 decrement 150
  exit
 interface ens19
  ipv4 address 198.51.100.3/29
  vrrp 2 address 198.51.100.1
  vrrp 2 track ens18 decrement 150
  exit
```

O que cada linha muda:

| Linha | Faixa | O que faz por você |
|-------|-------|--------------------|
| `vrrp <id> address <A.B.C.D>` | id de 1 a 255 | o endereço que os assinantes (ou o upstream) usam como gateway; responde ARP com o MAC virtual `00:00:5e:00:01:<id>`, que muda de caixa junto com ele |
| `vrrp <id> priority <n>` | 1 a 254, padrão 100 | quem fica com o serviço: vence a maior |
| `vrrp <id> track <interface> decrement <n>` | 1 a 254 | se a interface acompanhada cai, a caixa anuncia a prioridade menos `n`. Com 200 e `decrement 150`, a caixa A cai para 50 e perde para os 100 da B |

**Sem** `**track**`**, o par sobrevive a uma caixa morta e não a uma caixa surda.** Uma caixa que perdeu o upstream e continua viva seguiria com o endereço de dentro, recebendo os assinantes e sem ter para onde mandá-los.

**Prioridades iguais nas duas caixas funcionam, mas quem decide é o endereçamento**, não você: a RFC desempata pelo maior endereço. Use prioridades diferentes.

O intervalo de anúncio não é configurável: é o padrão da RFC, um segundo, e precisa ser igual nas duas caixas. A tomada leva por volta de três segundos e meio.

**Um pool NAT64 no par precisa do gateway IPv6 também.** Um endereço IPv6 em `vrrp <id> address` cria, ao lado do roteador IPv4, o roteador virtual IPv6 do mesmo id, com a mesma prioridade, o mesmo `track` e o mesmo `preempt`. O primeiro endereço IPv6 tem de ser um link-local (por exemplo `vrrp 1 address fe80::1`, antes do global) — o `commit check` recusa um roteador IPv6 sem ele —, e é para esse link-local que os assinantes IPv6, ou o BNG na frente deles, apontam o gateway. O MAC virtual dele é `00:00:5e:00:02:<id>`. O `track` é por família: um roteador IPv4 que acompanha uma interface sem endereço IPv4 nunca a vê de pé, e o `commit check` avisa nomeando a interface.


:::info
**O endereço virtual não responde ping.** A caixa não liga o modo *accept*, e a RFC 5798 manda não responder ICMP nesse caso. O encaminhamento **através** dele funciona normalmente. A sua monitoração deve pingar o endereço próprio de cada caixa, que é o que diz se ela está viva.

:::

# 3. `commit`, e o que o `commit check` recusa

Antes do `commit`, `commit check` confere a candidata. Duas recusas são específicas do `vrrp`, e as duas saem já no `check` — não no meio da aplicação:

Um roteador virtual sem endereço:

```
error: interface ens18: vrrp 1 has no address
    a virtual router with nothing to stand for would elect a master that
    answers for nothing
```

Um `track` apontando para uma interface que a caixa não tem:

```
error: interface ens18: vrrp 1: no interface ens20
    the dataplane does not have the interface this virtual router was told to
    track
```

E `vrrp` numa interface que a caixa não tem é recusado com as mesmas palavras de qualquer outra linha numa interface inexistente. Corrija e rode `commit`.


:::warning
**Mudar um roteador virtual que já existe custa duas eleições.** O roteador não é editado no lugar: a caixa o apaga e o recria. Se ele está com o endereço de serviço, a parceira assume durante a reconstrução e o `preempt` devolve em seguida. A caixa avisa antes de aplicar:

```
  changing vrrp 1 on ens18 restarts it: this box stops advertising while it is
  rebuilt, the peer takes the address, and preempt hands it back - two
  elections, a few seconds apart
```

As conexões em curso caem nas duas trocas. Criar um roteador novo não custa nada; mudar qualquer campo de um existente é mudança de janela de manutenção.

:::

# 4. `save` em cada caixa, e a cópia para a outra

`commit` faz valer agora; `save` faz sobreviver ao boot. Rode `save` nas duas caixas:

```
save
```

A caixa responde `configuration saved to /etc/prisma-routing/prisma-routing.conf`.

Para que uma caixa confira a outra, ela precisa ter a configuração dela. O VRRP não carrega configuração nenhuma, então ela chega como arquivo: copie o arquivo que o `save` da caixa A gravou para `/etc/prisma-routing/peer.conf` da caixa B, e o da B para o mesmo caminho na A. A cópia é feita com `scp` por uma conta de administrador — `root` ou `prisma-admin` passam por um shell de verdade; de dentro da VTY, `start shell` abre um. Na caixa B, por exemplo:

```
scp prisma-admin@<caixa-a>:/etc/prisma-routing/prisma-routing.conf /etc/prisma-routing/peer.conf
```


:::warning
**O arquivo carrega os hashes de senha e as chaves públicas das contas da outra caixa.** Não deixe `peer.conf` legível por todos, nem copie esse arquivo para fora das duas caixas.

:::

**Copie de novo sempre que mudar a outra caixa.** Não há sessão entre as duas: o `peer.conf` é o que foi copiado por último, e `show cgnat ha` imprime a data da cópia justamente para que um arquivo velho apareça.

# 5. `show cgnat ha` nas duas caixas

```
show cgnat ha
```

A tela responde três perguntas, nesta ordem:

**O que esta caixa mapeia** — a impressão digital do mapeamento e os pools:

```
  this box
    mapping fingerprint  <digest>
    pools                <pools>
```

**O papel dela** — cada roteador virtual, com o estado, os endereços, o MAC virtual e a prioridade que está **anunciando**. Na caixa A, com tudo no lugar, o estado é `master, holding the address`; na B, `backup`. Se a prioridade anunciada difere da configurada, a linha diz os dois números:

```
      priority           50 (configured 200, and something it tracks is down)
```

Isso é um cabo fora, não um problema de VRRP.

**O par** — lido do `peer.conf`, com a data da cópia na linha `copied`. Com os roteadores virtuais casados:

```
    every virtual router this box carries has one on the peer with the same id,
    the same address and a different priority
```

e com os pools iguais:

```
  the two boxes map every subscriber to the same public address and the same
  port block
```

Os avisos que podem aparecer no lugar disso:

| Aviso | O que significa | O que fazer |
|-------|-----------------|-------------|
| a mesma prioridade nas duas caixas | quem fica com o serviço é decidido pelo endereçamento | mude a prioridade de uma delas |
| um roteador virtual que só uma caixa tem | não há reserva para aquele endereço | declare o mesmo id e endereço na outra |
| um pool que não mapeia da mesma forma | a troca muda endereço e bloco dos assinantes daquele pool | iguale o pool nas duas caixas e copie o `peer.conf` de novo |
| sem `peer.conf`, ou ilegível | a caixa não afirma que as duas concordam | faça a cópia do passo 4 |

`show cgnat ha peer <arquivo>` compara contra outro arquivo em vez do `peer.conf`.

# 6. `show interface` diz o papel da porta

Olhando uma porta só, durante uma falha:

```
show interface ens18
```

Numa porta com roteador virtual, a tela traz o papel e cada endereço de serviço com o MAC virtual:

```
    vrrp 1      master, holding the address
                100.64.3.254 on 00:00:5e:00:01:01
```

Na caixa de reserva a linha diz `backup`. É o papel lido do dataplane, não a configuração. Uma porta sem roteador virtual não mostra linha nenhuma.

# 7. O self-test do boot

A cada boot, a caixa compara as linhas `vrrp` do arquivo salvo com o que o dataplane realmente tem, e diz no console:

```
  [ ok ] every virtual router the saved configuration names is on the
         dataplane: 1 holding an address, 1 standing by
```

Se o arquivo declara um roteador que o dataplane não aceitou, a linha é `[FAIL]` e diz qual (interface e id): a caixa está no par só no papel, e a outra sustenta o serviço sozinha, sem reserva. O motivo da recusa está no journal de `prisma-routing`. Uma caixa sem `vrrp` não recebe linha nenhuma.

# 8. Mudanças de pool num par: `commit diverge`

Numa caixa com `vrrp` e com um `peer.conf` legível, o `commit` **recusa** uma mudança que faria as duas mapearem assinantes de formas diferentes, e diz qual pool diverge:

```
error: this change maps subscribers differently from the peer
    this box stands for a service address with another one, so a takeover would
    translate them there
```

A recusa termina com `if that is what you mean, type: commit diverge`. Como as duas caixas não recebem o `commit` no mesmo instante, toda ampliação correta passa por esse momento. O procedimento:


1. Na primeira caixa, faça a mudança e confirme com `commit diverge`.
2. Na segunda, faça a mesma mudança. Com o `peer.conf` antigo, ela também pode recusar; use `commit diverge` de novo.
3. `save` nas duas e copie os dois arquivos de novo (passo 4).
4. `show cgnat ha` nas duas deve voltar a dizer que mapeiam igual.

`commit check` mostra a mesma recusa, para você decidir antes de digitar o `commit`. Numa caixa sem `vrrp` nada disso é recusa: um `peer.conf` ali só gera aviso.

A recusa não vê a outra caixa mudar depois da cópia — só enxerga o arquivo. Por isso a data em `show cgnat ha`.

# 9. `preempt`: quem volta, retoma

`preempt` está sempre ligado. Quando a caixa A volta — reiniciada, com o cabo de volta, com a prioridade restaurada —, ela **retoma** o endereço de serviço, e as conexões em curso caem de novo nessa segunda troca. O par termina onde a configuração diz, e não onde a última falha deixou. Para tirar a caixa ativa de serviço sem desligá-la, baixe a prioridade dela, como descrito em [Receitas do dia a dia](/s/wiki/doc/receitas-do-dia-a-dia-IcocgdbXW2).

# 10. Testar a troca

Faça isto numa janela, antes de precisar do par:


1. Nas duas caixas, `show cgnat ha`: A em `master, holding the address`, B em `backup`, o par casado e os pools iguais.
2. Anote o endereço público e o bloco de um assinante de teste na caixa A:

   ```
   show cgnat subscriber <ip do assinante>
   ```
3. Provoque a falha na caixa A. Uma de cada vez, e com o tempo de voltar entre elas:
   * **tire o cabo do upstream** (`ens19`): prova o `track`. A caixa A deve anunciar 50 e perder para a B;
   * **baixe a prioridade** pela VTY (`vrrp 1 priority 50` e `vrrp 2 priority 50`, `commit`): prova a eleição;
   * **desligue a caixa A**: prova a perda da caixa inteira.
4. Na caixa B, `show cgnat ha` deve mostrar `master, holding the address`, e o assinante deve voltar a navegar em alguns segundos. Confira `show cgnat subscriber` na B: num pool determinístico, o mesmo endereço público e o mesmo bloco; num pool BPA, um bloco novo.
5. Desfaça a falha. Com o `preempt`, a caixa A retoma o serviço; confira de novo nas duas.


:::info
Detectar o cabo fora depende de a placa informar a queda de link ao dataplane. Confira na sua placa: com o cabo fora, `show cgnat ha` na caixa A deve mostrar a prioridade anunciada menor que a configurada.

:::

# Como conferir

Nas duas caixas:

```
show cgnat ha
```


:::success
Uma caixa com `master, holding the address` e a outra com `backup` em cada roteador virtual; na seção do par, `every virtual router this box carries has one on the peer with the same id, the same address and a different priority`; no fim, `the two boxes map every subscriber to the same public address and the same port block`; e a data em `copied` posterior à última mudança da outra caixa. No console do boot, a linha `[ ok ] every virtual router the saved configuration names is on the dataplane`.

:::
