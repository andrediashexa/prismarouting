# Configuração de BGP

Como configurar sessões BGP pelo `hexash`: vizinhos, peer groups, famílias de endereço e o que o equipamento recusa antes de mandar para o roteamento. Para quem está subindo um trânsito, um peering ou anunciando os prefixos públicos do CGNAT.

Entra-se no BGP a partir do modo de configuração:

```
hexarouting# configure
hexarouting# [edit] router bgp 65000
hexarouting# [edit router bgp 65000]
```

`?` ou TAB listam o que vale ali dentro. Dentro de uma família de endereço, listam outro conjunto — são gramáticas diferentes, porque a mesma palavra significa coisas diferentes nos dois lugares.

# Uma sessão do começo ao fim

```
router bgp 65000
 bgp router-id 10.0.0.1
 neighbor 200.150.0.2 remote-as 65001
 neighbor 200.150.0.2 description transito-primario
 address-family ipv4 unicast
  neighbor 200.150.0.2 activate
  network 200.150.8.0/22
 exit-address-family
 exit
```

`remote-as` é o que cria a sessão: sem ele o FRR recusa qualquer outra sentença daquele vizinho, e o equipamento recusa antes, dizendo qual vizinho está faltando.

`activate` é o que faz a sessão **carregar** aquela família. Uma sessão que sobe e não carrega nada é uma sessão Established e muda.

# O que um vizinho entende

| Comando | Argumento | O que faz |
|---------|-----------|-----------|
| `remote-as` | `<1-4294967295>` | o AS do outro lado; é isto que cria a sessão |
| `description` | `<texto>` | para que serve a sessão, para quem ler depois |
| `password` | `<texto>` | senha MD5 da sessão; nunca é impressa de volta |
| `update-source` | `<interface>` | de onde a sessão sai |
| `ebgp-multihop` | `<1-255>` | saltos até um vizinho que não está numa rede conectada |
| `timers` | `<keepalive> <hold>` | temporizadores da sessão |
| `maximum-prefix` | `<n> [<pct>] [restart <min>]` | derruba a sessão acima desse número de prefixos |
| `next-hop-self` |           | anuncia esta caixa como próximo salto |
| `bfd`   |           | acompanha a sessão por BFD, para a falha ser de segundos e não de minutos |
| `shutdown` |           | segura a sessão derrubada, mantendo a configuração |
| `peer-group` | `<nome>`  | põe o vizinho num peer group |

Dentro de `address-family`, o mesmo vizinho entende `activate`, `route-map`, `prefix-list`, `soft-reconfiguration inbound`, `remove-private-AS` e `default-originate`.

# Peer groups

Quarenta peerings não são quarenta estrofes. Declara-se o grupo, dá-se o AS a ele, e os membros herdam:

```
router bgp 65000
 neighbor IX peer-group
 neighbor IX remote-as 65100
 neighbor 200.150.9.10 peer-group IX
 neighbor 200.150.9.11 peer-group IX
 exit
```

Ao completar `neighbor` com TAB, o equipamento mostra os vizinhos que existem com o AS do outro lado ao lado, e marca quais nomes são grupos — um grupo não é um roteador, e procurar por ele como se fosse custa tempo.

# O que é recusado antes de chegar ao roteamento

São recusas de **forma**, não de funcionamento: se o vizinho vai responder não está na configuração, e o equipamento não finge saber.

* **vizinho sem** `**remote-as**` — o FRR recusa qualquer outra sentença dele;
* **peer group que ninguém declarou** — `neighbor X peer-group IX` sem o `neighbor IX peer-group` que cria o grupo;
* **membro cujo grupo também não tem AS** — o membro herda o AS do grupo, então um vizinho sem AS próprio está certo quando o grupo tem um; o que não pode é nenhum dos dois ter;
* `**update-source**` **apontando para uma interface que a caixa não tem** — o FRR aceita, a sessão sai de lugar nenhum e nunca sobe, e a configuração continua lendo bem.

# A senha

`neighbor <endereço> password <senha>` é o segredo em si, não um hash dele, e ele abre a sessão **no outro roteador** também. Por isso ele não é impresso de volta:

```
 neighbor 200.150.0.2 password <set, not shown>
```

Vale tanto em `show running-config` quanto em `show this` dentro do bloco. Isso é só a exibição: a configuração salva e as revisões de rollback guardam o valor real, senão um rollback não conseguiria restaurar a sessão — e é por isso que esses arquivos não são legíveis por qualquer um.

# Como conferir

* `show running-config` mostra a estrofe BGP como o equipamento a lê de volta, com a senha omitida;
* `show this` dentro do bloco mostra só o que está naquele nível;
* `show bgp summary` mostra as sessões e quantos prefixos cada uma trouxe;
* `show route` mostra o que **encaminha** — uma rota que o BGP aprendeu e não está aí não move pacote nenhum.

# Ver também

* [Roteamento, SSH, usuários e SNMP](/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m) — prefix-lists, políticas e o prefixo público de um pool
* [commit, rollback e save](/doc/commit-rollback-e-save-jqCB7kxPuA) — como uma mudança é aplicada e desfeita
