# Encaminhamento de porta

Como abrir uma porta de entrada para um assinante — câmera, servidor pequeno, console de jogo — sem lhe vender um endereço público. E por que o equipamento recusa alguns pedidos.

Um assinante atrás de CGNAT não é alcançável de fora, e alguns precisam ser. A resposta antiga do provedor era "compre um endereço público", que é uma venda perdida exatamente quando ele não tem nenhum — que é por que o CGNAT está ali.

# Como fazer

```
configure
 nat
  cgnat
   pool residencial
    forward tcp 6200 to 100.64.0.5 80
    exit
   exit
  exit
 commit
 exit
save
```

```
show cgnat forward
```

É ajuste **aplicado no lugar**: não derruba sessão de ninguém.

Vale também num pool com `filter address-dependent`: a porta encaminhada fica fora dessa filtragem, porque quem chega por ela nunca foi contatado antes pelo assinante — é esse o propósito do forward.

Num [pool NAT64](/s/wiki/doc/nat64-assinantes-so-ipv6-no-mesmo-espaco-publico-4Se191EJRd), que fica em `nat` > `nat64`, o forward é o mesmo e nomeia um endereço IPv6 do prefixo do assinante (`forward tcp 6200 to 2001:db8:1200:ab00::5 80`): quem chega de fora pelo IPv4 alcança aquele host IPv6.

# O endereço público não é escolha sua

Você informa a **porta** pública e nunca o **endereço**: o endereço é o do próprio assinante, aquele que o mapeamento já atribui a ele.

Dois motivos, e os dois são propriedades que o resto do equipamento já garante:

| Motivo | O que quebraria se fosse diferente |
|--------|------------------------------------|
| **Pareamento de endereço** (RFC 4787 REQ-2) | um assinante que recebe tráfego num endereço e envia de outro não está pareado, e aplicações que conferem de onde veio a resposta enxergariam dois pares diferentes. Vale dizer até onde a caixa garante isso: sempre, num pool determinístico sem `reserve`, onde cada assinante tem um bloco só. Em pool dinâmico, e no bloco extra que sai de uma `reserve`, quando o assinante enche o bloco dele e ganha outro a caixa **procura um bloco livre no endereço público dele** antes de pegar qualquer um - preferência, não regra, porque recusar a extensão custaria tráfego ao cliente em vez de custar um endereço. A linha `RFC 4787 REQ-2 paired:` de `show cgnat blocks` mostra a conta do seu próprio pool. O encaminhamento continua recusado em qualquer endereço que não seja o primeiro do assinante, que é o lado conservador disso |
| **A aritmética reversa** | é ela que recupera o assinante a partir de (endereço público, porta) sem consulta a tabela. Um forward em outro endereço seria um mapeamento que a fórmula não produz, e todo leitor da trilha calcularia a resposta errada exatamente para aquelas portas |

Por isso a porta pública **precisa cair dentro do bloco** que o pool atribui àquele assinante. Um forward fora do bloco é recusado, nomeando o assinante cujo bloco ele teria tomado:

```
public port 2048 belongs to 100.64.0.9's block, not to 100.64.0.5's.
100.64.0.5 holds 200.150.0.17:1536-2047
```

Descubra o bloco antes de escolher a porta:

```
show cgnat subscriber 100.64.0.5
```


:::info
Na prática o roteiro é: consulte o bloco do assinante, escolha uma porta **dentro** dele, e configure o forward. A porta pública não é um número que você inventa — é um número que você tira do bloco dele.

:::

## A mesma porta serve um assinante por endereço público

O pool tem tantos endereços públicos quantos você deu a ele, e entrega **a mesma faixa de portas a um assinante de cada endereço**. Então `203.0.113.16:1100` e `203.0.113.17:1100` não são a mesma coisa: são duas tomadas, em dois endereços, que o pool já entregou a dois clientes diferentes.

Por isso a porta 1100 pode ser vendida uma vez **em cada endereço público** do pool:

```
203.0.113.16:1100 -> 10.0.0.4:8080
203.0.113.17:1100 -> 10.0.0.5:80
```

Num pool com quatro endereços públicos isso é a diferença entre vender aquela porta a um cliente e vendê-la a quatro. Você não escolhe em qual endereço o forward cai: ele cai no endereço do próprio assinante, como sempre.

O que continua recusado é **a mesma tomada duas vezes**: a mesma porta, no mesmo endereço público, apontando para dois lugares. A recusa nomeia quem já a tem:

```
public port 1600 on 200.150.0.17 is already forwarded to 100.64.0.5:80
```

E também o contrário, dois forwards diferentes apontando para a mesma porta do mesmo assinante, porque um cliente que responde em duas portas públicas deixa de estar pareado.

## Como remover

```
configure
 nat
  cgnat
   pool residencial
    no forward tcp 6200 to 100.64.0.5 80
    exit
   exit
  exit
 commit
 exit
save
```

`no forward tcp 6200` sozinho também serve, enquanto aquela porta estiver encaminhada em **um** endereço público só. Se estiver em mais de um, o equipamento recusa e nomeia os assinantes envolvidos, em vez de remover o primeiro que encontrar. Remover o forward de um cliente que você não citou é o tipo de coisa que se descobre pelo telefone:

```
error: forward tcp 6200 is configured for more than one subscriber
    on different public addresses: 100.64.0.5, 100.64.0.6
    name the subscriber: no forward tcp 6200 to <private-ip> <private-port>
```

Os endereços da segunda linha são os **assinantes** - os endereços privados que você citaria no comando -, e não os públicos onde o encaminhamento caiu. São eles que a terceira linha pede, então são eles que a segunda mostra.

A recusa vem em três linhas próprias, dentro da largura do console. Por um tempo ela saía numa frase só, mais larga que a tela: o terminal a quebrava onde calhasse, e o que ficava perdido era justamente o fim, que é onde está o comando a digitar.

# Em pool dinâmico, quem fica preso é o bloco

Num pool dinâmico o bloco é entregue no primeiro fluxo e devolvido depois que o assinante fica ocioso. Uma porta fixada dentro dele seguiria o bloco para o próximo assinante: o forward continuaria funcionando e passaria a alcançar **outro cliente** — pior do que não funcionar, porque nada parece quebrado.

Por isso o que fica preso não é a porta, é o **bloco**. Configurar um forward num pool dinâmico prende o bloco daquele assinante, e bloco preso **não é devolvido por ociosidade nem entregue a mais ninguém** enquanto o forward existir. O comando é o mesmo dos demais pools — você continua informando só a porta pública.


:::info
Prender é permanente por definição, e o custo é esse: aquele bloco sai da rotatividade do pool. `show cgnat blocks` conta os blocos presos à parte dos blocos em uso, justamente para você saber quanto do pool nenhuma ociosidade vai devolver.

:::

## Três recusas que você vai encontrar

| Recusa | O que fazer |
|--------|-------------|
| o bloco está **com outro assinante agora** — a recusa nomeia quem | esperar aquele cliente ficar ocioso, ou atender este por outra porta/pool. Tomar o bloco encerraria as sessões vivas dele |
| o assinante está **além da contagem de blocos do pool** | pool dinâmico é sobrevendido de propósito; para esse assinante não existe bloco. Amplie o conjunto público ou atenda-o por um pool que o alcance |
| a porta está **fora do bloco** daquele assinante | a mesma regra dos pools determinísticos: consulte o bloco antes de escolher a porta |

A recusa por bloco ocupado aparece já no `commit check`, antes de qualquer mudança no equipamento.

## Depois de um reinício

O bloco volta preso, e você não precisa fazer nada para isso. Qual bloco está preso não é um estado guardado à parte: é deduzido do próprio forward na configuração, então basta a configuração voltar. Não existe um segundo lugar para ficar desatualizado.

## No log

Prender um bloco é registrado como **alocação** e soltá-lo como **liberação**, exatamente como qualquer outro bloco (RFC 8158). O evento é do bloco, não do forward: um segundo forward dentro de um bloco já preso não gera outro registro, e a liberação só sai quando o último forward do bloco é removido **e** o bloco volta de fato ao pool. Se o assinante ainda tem sessões nele, o bloco vira um bloco dinâmico comum e sai depois, pela ociosidade, com a liberação dele. Em modo dinâmico o log é o único registro de quem segurou um bloco, e um bloco segurado por um ano sem registro é um mapeamento que ninguém consegue atribuir a um assinante depois — o que aqui é obrigação legal, não capricho.

Não há um registro separado para o "prender": a trilha de configuração já anota cada `forward` acrescentado ou removido, com o operador que o fez, e o bloco preso é deduzido dele.

Uma configuração é aplicada com os pools primeiro e a exportação depois, que é a ordem de todo boot. Por isso, quando a exportação é ligada, o equipamento reanuncia como alocação todos os blocos que estão presos naquele instante — senão a caixa recém-reiniciada seguraria cada um deles sem registro nenhum.

## Onde ver

```
show cgnat blocks                    quantos blocos estão presos
show cgnat subscriber 100.64.0.5     diz que o bloco está preso por um forward
```

# Como conferir

```
show cgnat forward
show cgnat subscriber 100.64.0.5
```

Depois, do lado de fora, alcance o endereço público do assinante na porta configurada.


:::success
O forward listado no pool, a porta dentro do bloco do assinante, e a conexão entrando de fora: pronto — sem gastar um endereço público.

:::

# Ver também

* [Configuração de pool](/s/wiki/doc/configuracao-de-pool-btyS572Ih6) — o campo `forward` e os demais aplicados no lugar
* [Quando o bloco de um assinante enche](/s/wiki/doc/quando-o-bloco-de-um-assinante-enche-kb1sJTtCKx) — o bloco de onde a porta tem de sair
* [Receitas do dia a dia](/s/wiki/doc/receitas-do-dia-a-dia-IcocgdbXW2) — a versão curta desta tarefa
