# uRPF e origens forjadas

O que acontece quando um pacote chega com endereço de origem que o remetente não possui, por que isso é um problema de **prova** e não só de segurança, e o que o uRPF resolve — e o que ele não resolve.

# Por que isso importa mais aqui do que num roteador comum

Todas as outras propriedades deste equipamento existem para tornar o log **respondível**. Esta existe para impedir que ele fique confiantemente **errado**.

Um pacote que chega numa interface interna com origem forjada é traduzido como qualquer outro, recebe uma porta do bloco daquele endereço e — em modo determinístico — é atribuído àquele assinante por aritmética, por quanto tempo alguém quiser perguntar.


:::warning
O registro fica **indistinguível de um verdadeiro**, porque nada no sistema sabe a diferença. Uma ordem judicial respondida com esse registro aponta um assinante que não fez nada.

:::

# A defesa: uRPF

A verificação é o *unicast Reverse Path Forwarding*: o endereço de origem precisa **rotear de volta pelo caminho por onde o pacote veio**. É recurso do próprio VPP, habilitado por interface e **desligado por padrão**.

```
configure
 interface ens19
  urpf strict
  exit
 commit
```

| Modo | Aceita | Onde usar |
|------|--------|-----------|
| `strict` | a rota da origem aponta para a interface por onde o pacote chegou | a interface interna de um CGNAT |
| `loose` | a origem tem rota em algum lugar | topologias assimétricas, onde `strict` derrubaria tráfego que está correto |
| ausente (padrão) | qualquer coisa |           |


:::info
**Desligado por padrão de propósito.** Ligar `strict` numa topologia com caminhos de retorno assimétricos derruba tráfego que funcionava um instante atrás — e faz isso em silêncio: não há sintoma além de que algumas coisas param. Essa decisão pertence a quem conhece a topologia.

:::

A verificação roda **antes** do `bpa-in2out`, então um pacote recusado nunca chega ao tradutor: nenhuma porta é tirada do bloco que ele mirava, e nenhuma sessão existe para ser atribuída a ninguém.

Os descartes dele são contados em `ip4-rx-urpf-strict`, contador **diferente** de `bpa-in2out: no pool for source address`. Um é ataque ou erro de configuração; o outro é um endereço que esta caixa simplesmente não traduz.

# Onde a proteção para


:::warning
**Um assinante forjando o endereço do vizinho não é pego.** Na topologia comum, todo assinante de um pool é alcançável pela mesma interface interna — então a FIB responde "sim, por ali" para o endereço da vítima exatamente como responde para o do atacante. O `strict` deixa passar, e o mapeamento é atribuído ao vizinho.

:::

Esse é o limite do que uma verificação **de rota** consegue enxergar, e não é uma lacuna que um uRPF melhor feche. Pegar isso exige:

* o endereço amarrado ao assinante a quem foi entregue — correlação com RADIUS ou DHCP; ou
* a rede de acesso recusando a falsificação antes que o pacote chegue aqui, que é onde sai mais barato fazer.

O que o uRPF **pega** é tudo que vem de fora do espaço de assinantes injetado num enlace interno, e tráfego de um enlace de acesso reivindicando endereços que pertencem a outro. Numa caixa com mais de uma interface interna, isso é a maior parte.

Esse limite é **verificado**, não apenas descrito: a verificação envia o endereço de um vizinho pela interface certa e exige que ele seja traduzido — de modo que a fronteira falharia como teste se alguém um dia afirmasse o contrário.

# Como conferir

```
show cgnat drops
```

Descartes de uRPF aparecem separados dos descartes por falta de pool, justamente porque significam coisas diferentes.


:::success
Com `urpf strict` na interface interna e o tráfego legítimo passando normalmente: você fechou a porta para origem forjada vinda de fora do espaço de assinantes, sem derrubar quem estava certo.

:::

# Ver também

* [Roteamento, SSH, usuários e SNMP](/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m) — o bloco `interface` onde `urpf` é configurado
* [Logging IPFIX e trilha de auditoria](/doc/logging-ipfix-e-trilha-de-auditoria-lREEjJu1Bq) — o registro que isto protege
* [Operação e diagnóstico](/doc/operacao-e-diagnostico-A4fcKDVwZt) — separar descarte de uRPF de descarte por falta de pool
