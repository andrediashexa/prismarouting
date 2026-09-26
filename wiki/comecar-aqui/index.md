# Começar aqui

Tudo que é preciso para sair de uma máquina sem sistema e chegar a um CGNAT traduzindo tráfego. Leia nesta ordem se é a sua primeira vez com o produto.

| Página | Responde |
|--------|----------|
| [Primeiros passos](/doc/primeiros-passos-TAWDdSrYG7) | Do disco vazio até assinantes navegando, em nove passos |
| [Configurar o CGNAT passo a passo](/doc/configurar-o-cgnat-passo-a-passo-WMfuk8C8Q7) | Das duas portas ao primeiro assinante traduzido, com a tela que a caixa mostra em cada passo |
| [Dimensionamento e requisitos](/doc/dimensionamento-e-requisitos-f42dC4k45s) | Quanta máquina, quantos IPs públicos e quanto log o seu caso exige |
| [Instalação da appliance](/doc/instalacao-da-appliance-06NmxvBDb5) | Como a ISO instala, o que ela pergunta e como automatizar |
| [Acesso, login e papéis](/doc/acesso-login-e-papeis-VcE7gIbI1U) | Onde você cai ao entrar, e quem pode mudar o quê |


:::warning
Antes do primeiro comando, decida qual interface é a de gerência. Ela **não** pode ser entregue ao CGNAT: o DPDK tira a placa do Linux, e a máquina fica inalcançável pela porta que você usa para administrá-la.

:::

# Ver também

* [Receitas do dia a dia](/doc/receitas-do-dia-a-dia-IcocgdbXW2) — depois que estiver no ar
* [Determinístico e BPA — conceitos e mapeamento](/doc/bpa-conceitos-e-mapeamento-uh9nF6sRaa) — por que o mapeamento é uma conta e não uma tabela
