# BPA — conceitos e mapeamento

A aritmética que substitui a tabela de alocação, e as consequências práticas dela. Leia antes de mexer em qualquer campo de mapeamento de um pool.

# Como o mapeamento funciona

Um pool define dois **conjuntos de endereços** e um tamanho de bloco. Cada conjunto é uma lista de prefixos, possivelmente não contíguos, apresentados como um espaço de índices denso `[0, quantidade)`.

```mermaid
flowchart TD
    A["Assinante<br/>100.64.0.5"] --> B["índice denso<br/>sub_idx = 5"]
    B --> C{"policy"}
    C -->|interleaved| D["pub_idx = sub_idx mod n_pub<br/>blk_idx = sub_idx div n_pub"]
    C -->|blocked| E["pub_idx = sub_idx div blocos_por_ip<br/>blk_idx = sub_idx mod blocos_por_ip"]
    D --> F["endereço público<br/>+ faixa de portas"]
    E --> F
    F -.->|"a mesma conta, ao contrário"| A
```

## Regras que o mapeamento impõe

* **Prefixos dentro de um conjunto não podem se sobrepor.** Sobreposição quebra a bijeção endereço↔índice, e bijeção quebrada significa **um assinante registrado como outro** — falha de conformidade legal, não apenas bug. Prefixos sobrepostos são recusados na configuração.
* `**block-size**` **precisa ser potência de dois.**
* A capacidade de um pool não passa de 2^28 assinantes.

## Por que `interleaved` é o padrão

Com `interleaved`, assinantes consecutivos caem em endereços públicos **diferentes**. Se um endereço público entrar em blacklist, o dano se espalha fino em vez de derrubar um bairro inteiro atrás de uma OLT.

Com `blocked`, vizinhos ficam no mesmo endereço público — útil quando se quer concentrar, mas concentra também o risco de reputação.

# A consequência que mais derruba rede


:::warning
**O mapeamento é aritmético, não uma tabela.**

:::

Não existe registro de alocação para consultar. Isso é o ponto inteiro do BPA — e significa que **mudar a definição do pool muda a função**.

Acrescentar um prefixo público move **todos** os assinantes naquele instante e encerra todas as sessões do pool. Nada é corrompido, respostas passadas continuam resolvíveis, mas:

* todos os clientes caem ao mesmo tempo;
* a máquina não reporta nada de anormal, porque nada de anormal aconteceu.

Por isso o `commit` recusa mudanças assim até que sejam pedidas pelo nome, com `**commit remap**`. E por isso `commit check` reporta quantos assinantes seriam re-mapeados antes de qualquer coisa acontecer.

# Sharding: por que o handoff existe

O estado de sessão e o bitmap de portas por assinante são **por worker e sem lock**. Isso só é seguro se **as duas direções de um assinante chegarem ao mesmo worker** — e o RSS da NIC não dá essa garantia: a saída faz hash no endereço privado, a entrada no público.

O `bpa_handoff.c` resolve isso. O laboratório `handoff` prova que funciona.

# Modo determinístico vs dinâmico

|     | Determinístico | Dinâmico (PBA) |
|-----|----------------|----------------|
| Como o bloco é atribuído | por fórmula    | sob demanda    |
| Log de alocação | **nenhum**     | um registro IPFIX por bloco |
| Endereços públicos necessários | todos os assinantes precisam caber | sobreassinatura possível |
| Prova para autoridade | algoritmo + configuração vigente (trilha) | os registros IPFIX |
| Blocos estendidos | não            | sim — assinante que enche o bloco recebe outro |

200 mil assinantes em determinístico com blocos de 512 portas exigem um `/21` público. Em dinâmico com sobreassinatura, bem menos.

# Tráfego que não é traduzido

O `bypass` faz um prefixo sair sem tradução — e, o que importa tanto quanto, **sem alocar nada**.

Essa segunda metade é a que costuma ser implementada errada: um bypass posto *depois* da alocação passa no teste "saiu sem tradução" e falha em "não alocou nada", consumindo bloco de um assinante que nunca precisou dele. O laboratório `bypass` verifica as duas coisas.

# O que muda sem reconstruir o pool

Ver a página de configuração de pool. A regra: **muda o mapeamento → reconstrói; não muda → aplica no lugar**.

Isso não é otimização, é correção. Derrubar a base inteira de assinantes para desligar um ALG, limitar uma máquina infectada ou dar uma porta de entrada a um cliente não é mudança que alguém pediria.

# Ver também

* [Configuração de pool](/doc/configuracao-de-pool-btyS572Ih6) — onde a teoria vira campo configurável
* [Logging IPFIX e trilha de auditoria](/doc/logging-ipfix-e-trilha-de-auditoria-lREEjJu1Bq) — a consequência legal do modo escolhido
