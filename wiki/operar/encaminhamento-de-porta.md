# Encaminhamento de porta

Como abrir uma porta de entrada para um assinante — câmera, servidor pequeno, console de jogo — sem lhe vender um endereço público. E por que o equipamento recusa alguns pedidos.

Um assinante atrás de CGNAT não é alcançável de fora, e alguns precisam ser. A resposta antiga do provedor era "compre um endereço público", que é uma venda perdida exatamente quando ele não tem nenhum — que é por que o CGNAT está ali.

# Como fazer

```
configure
 nat cgnat pool residencial
  forward tcp 6200 to 100.64.0.5 80
  exit
 commit
 exit
save
```

```
show cgnat pool
```

É ajuste **aplicado no lugar**: não derruba sessão de ninguém.

# O endereço público não é escolha sua

Você informa a **porta** pública e nunca o **endereço**: o endereço é o do próprio assinante, aquele que o mapeamento já atribui a ele.

Dois motivos, e os dois são propriedades que o resto do equipamento já garante:

| Motivo | O que quebraria se fosse diferente |
|--------|------------------------------------|
| **Pareamento de endereço** (RFC 4787 REQ-2) | um assinante que recebe tráfego num endereço e envia de outro não está pareado, e aplicações que conferem de onde veio a resposta enxergariam dois pares diferentes |
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

Prender um bloco é registrado como **alocação** e soltá-lo como **liberação**, exatamente como qualquer outro bloco (RFC 8158). Em modo dinâmico o log é o único registro de quem segurou um bloco, e um bloco segurado por um ano sem registro é um mapeamento que ninguém consegue atribuir a um assinante depois — o que aqui é obrigação legal, não capricho.

Uma configuração é aplicada com os pools primeiro e a exportação depois, que é a ordem de todo boot. Por isso, quando a exportação é ligada, o equipamento reanuncia como alocação todos os blocos que estão presos naquele instante — senão a caixa recém-reiniciada seguraria cada um deles sem registro nenhum.

## Onde ver

```
show cgnat blocks                    quantos blocos estão presos
show cgnat subscriber 100.64.0.5     diz que o bloco está preso por um forward
```

# Como conferir

```
show cgnat pool
show cgnat subscriber 100.64.0.5
```

Depois, do lado de fora, alcance o endereço público do assinante na porta configurada.


:::success
O forward listado no pool, a porta dentro do bloco do assinante, e a conexão entrando de fora: pronto — sem gastar um endereço público.

:::

# Ver também

* [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6) — o campo `forward` e os demais aplicados no lugar
* [Quando o bloco de um assinante enche](/doc/quando-o-bloco-de-um-assinante-enche-kb1sJTtCKx) — o bloco de onde a porta tem de sair
* [Receitas do dia a dia](/doc/receitas-do-dia-a-dia-IcocgdbXW2) — a versão curta desta tarefa
