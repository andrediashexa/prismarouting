# Entender e desenvolver

Por que o produto é assim. Leia quando precisar decidir arquitetura, avaliar se o BPA atende ao seu caso, ou mexer no código.

| Página | Responde |
|--------|----------|
| [HexaRouting — visão geral](/doc/hexarouting-visao-geral-cNtWoccoy5) | O que é o produto, do que é feito e o que ainda não existe |
| [BPA — conceitos e mapeamento](/doc/bpa-conceitos-e-mapeamento-uh9nF6sRaa) | A aritmética que substitui a tabela de alocação |
| [Alta disponibilidade](/doc/alta-disponibilidade-Zc1pBKocic) | O que o modo determinístico já dá, e o que falta para failover |
| [Build, testes e laboratórios](/doc/build-testes-e-laboratorios-Z6I6w1ngfu) | Como compilar, e os quatro níveis de teste |

# A ideia em uma frase

O CGNAT convencional registra cada fluxo e o log vira o item mais caro do sistema. O BPA entrega a cada assinante um bloco contíguo de portas, e o log cai para um registro por bloco — ou para zero, quando o bloco é calculado em vez de alocado.

# Ver também

* [Logging IPFIX e trilha de auditoria](/doc/logging-ipfix-e-trilha-de-auditoria-lREEjJu1Bq) — a consequência prática dessa escolha
