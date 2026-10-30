# Começar aqui

Tudo que é preciso para sair de uma máquina sem sistema e chegar a um CGNAT traduzindo tráfego. Leia nesta ordem se é a sua primeira vez com o produto.

| Página | Responde |
|--------|----------|
| [Dimensionamento e requisitos](/s/wiki/doc/dimensionamento-e-requisitos-f42dC4k45s) | Quanta máquina, quantos IPs públicos e quanto log o seu caso exige |
| [Rodar o Prisma Routing numa máquina virtual](/s/wiki/doc/rodar-o-prisma-routing-numa-maquina-virtual-4l9Vu3QoL5) | Memória, CPU, placas virtio, páginas de 1 GB, o agente do QEMU, desligar pela VTY e o que uma medição numa VM mede |
| [Instalação da appliance](/s/wiki/doc/instalacao-da-appliance-06NmxvBDb5) | Como a ISO instala, o que ela pergunta e como automatizar |
| [Acesso, login e papéis](/s/wiki/doc/acesso-login-e-papeis-VcE7gIbI1U) | Onde você cai ao entrar, e quem pode mudar o quê |
| [Primeiros passos](/s/wiki/doc/primeiros-passos-TAWDdSrYG7) | Do disco vazio até assinantes navegando, em dez passos |
| [Configurar o CGNAT passo a passo](/s/wiki/doc/configurar-o-cgnat-passo-a-passo-WMfuk8C8Q7) | Das duas portas ao primeiro assinante traduzido, com a tela que a caixa mostra em cada passo |
| [Pôr assinantes PPPoE no ar, passo a passo](/s/wiki/doc/por-assinantes-pppoe-no-ar-passo-a-passo-Y43rTeDSXs) | Da porta de acesso e da VLAN ao primeiro assinante PPPoE autenticado no RADIUS, com plano de banda, traduzido pelo CGNAT e rastreável no histórico |


:::info
**Antes de instalar, decida se a caixa vai ser administrada pela rede.** Uma caixa nova é configurada no console, e o dataplane fica com todas as placas que ela tem. Se ela precisa ser alcançada por `ssh` depois do primeiro reboot, responda na instalação a placa de gerência: é a única que o dataplane não reivindica. Uma caixa de uma placa só entrega essa placa ao dataplane mesmo assim, e o endereço de gerência vai junto com ela.

:::

# Ver também

* [Receitas do dia a dia](/s/wiki/doc/receitas-do-dia-a-dia-IcocgdbXW2) — depois que estiver no ar
* [Levar a caixa para uma versão nova](/s/wiki/doc/levar-a-caixa-para-uma-versao-nova-C09kO2kU8X) — `system upgrade`, e o que fazer antes do próximo reboot
* [Determinístico e BPA — conceitos e mapeamento](/s/wiki/doc/deterministico-e-bpa-conceitos-e-mapeamento-uh9nF6sRaa) — por que o mapeamento é uma conta e não uma tabela
