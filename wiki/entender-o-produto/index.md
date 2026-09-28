# Entender o produto

Por que o produto é assim. Leia quando precisar decidir arquitetura ou avaliar se o CGNAT por bloco atende ao seu caso.

| Página | Responde |
|--------|----------|
| [Prisma Routing — visão geral](/doc/prisma-routing-visao-geral-cNtWoccoy5) | O que é o produto, do que é feito e o que ainda não existe |
| [Determinístico e BPA — conceitos e mapeamento](/doc/bpa-conceitos-e-mapeamento-uh9nF6sRaa) | Determinístico (bloco por fórmula) e BPA (bloco alocado sob demanda): a diferença, a conta e o que cada um exige |
| [Alta disponibilidade](/doc/alta-disponibilidade-Zc1pBKocic) | O par ativo/standby com VRRP: como montar, o que a troca preserva e o que ela derruba |

# A ideia em uma frase

O CGNAT convencional registra cada fluxo e o log vira o item mais caro do sistema. O CGNAT por bloco entrega a cada assinante um bloco contíguo de portas, e o log cai para um registro por bloco com BPA, que aloca os blocos sob demanda, ou para zero com o determinístico, que calcula o bloco em vez de alocar. BPA é só o modo dinâmico; o determinístico não é um tipo de BPA.

# Ver também

* [Logging IPFIX e trilha de auditoria](/doc/logging-ipfix-e-trilha-de-auditoria-lREEjJu1Bq) — a consequência prática dessa escolha
