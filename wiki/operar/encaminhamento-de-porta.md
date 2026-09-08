# Encaminhamento de porta

Como abrir uma porta de entrada para um assinante — câmera, servidor pequeno, console de jogo — sem lhe vender um endereço público. E por que o equipamento recusa alguns pedidos.

Um assinante atrás de CGNAT não é alcançável de fora, e alguns precisam ser. A resposta antiga do provedor era "compre um endereço público", que é uma venda perdida exatamente quando ele não tem nenhum — que é por que o CGNAT está ali.

# Como fazer

```
configure
 cgnat pool residencial
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

# Só em pools determinísticos


:::warning
**Um forward em pool dinâmico é recusado.** Ali um bloco pertence a quem o segura **agora** e é liberado quando aquele assinante fica ocioso. Uma porta fixada seguiria o bloco para o próximo assinante: o forward continuaria funcionando e passaria a alcançar **outro cliente** — o que é pior do que não funcionar.

:::

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
