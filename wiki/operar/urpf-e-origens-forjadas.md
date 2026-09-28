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

A verificação vai na interface **interna**, a que tem `cgnat inside` no bloco dela. `urpf off`, ou `no urpf`, desliga de novo. A mudança é aplicada na hora, sem derrubar sessão, e vai para o arquivo com o próximo `save`.

| Modo | Aceita | Onde usar |
|------|--------|-----------|
| `strict` | a rota da origem aponta para a interface por onde o pacote chegou | a interface interna de um CGNAT |
| `loose` | a origem tem rota em algum lugar | topologias assimétricas, onde `strict` derrubaria tráfego que está correto |
| `off`, ou ausente (padrão) | qualquer coisa | onde o caminho de retorno não é conhecido |


:::info
**Desligado por padrão de propósito.** Ligar `strict` numa topologia com caminhos de retorno assimétricos derruba tráfego que funcionava um instante atrás — e faz isso em silêncio: não há sintoma além de que algumas coisas param. Essa decisão pertence a quem conhece a topologia.

:::

A verificação roda **antes** da tradução, então um pacote recusado nunca chega ao CGNAT: nenhuma porta é tirada do bloco que ele mirava, e nenhuma sessão existe para ser atribuída a ninguém.

Por isso o descarte do uRPF **não aparece** em `show cgnat drops`: aquela tela conta só o que o próprio CGNAT recusou, e o pacote barrado aqui nunca chegou a ele. Os dois são coisas diferentes — um é ataque ou erro de configuração; o outro, `no pool for source address`, é um endereço que esta caixa simplesmente não traduz. O descarte do uRPF entra no contador de descartes da própria interface, em `show interface <nome>`.

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
show interface ens19
```

A tela da interface traz uma linha `urpf` com o modo que o **dataplane** está aplicando — `strict`, `loose` ou `off` —, lido da caixa e não do arquivo, e os contadores da porta, onde entram os descartes.

O autoteste de boot confere a mesma coisa a cada boot: quando a configuração salva liga o uRPF em alguma interface, ele diz se o dataplane de fato o tem ligado. Com tudo certo, a linha é:

```
  [ ok ] uRPF is on in the dataplane, as the saved configuration asks
```

Se a configuração pede e o dataplane não tem, o autoteste acusa como falha — uma proteção que simplesmente não está lá não tem contador nenhum que a denuncie.


:::success
Com `urpf strict` na interface interna, `show interface` confirmando o modo e o tráfego legítimo passando normalmente (`show cgnat top` com os assinantes de sempre): você fechou a porta para origem forjada vinda de fora do espaço de assinantes, sem derrubar quem estava certo.

:::

# Ver também

* [Configuração de interface](/doc/configuracao-de-interface-j1q58jKWTQ) — o bloco `interface` onde `urpf` é configurado
* [Logging IPFIX e trilha de auditoria](/doc/logging-ipfix-e-trilha-de-auditoria-lREEjJu1Bq) — o registro que isto protege
* [Operação e diagnóstico](/doc/operacao-e-diagnostico-A4fcKDVwZt) — por onde começar quando um assinante some
