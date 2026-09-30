# Monitoramento e SNMP

O que a sua NMS enxerga deste CGNAT, e o que precisa ser feito para que a metade mais útil apareça. Para quem cuida do Zabbix, do LibreNMS ou equivalente.

Todo provedor já tem uma NMS, e é onde alguém de fato olha. Um CGNAT cuja única observabilidade é alguém logado na própria caixa é um CGNAT que ninguém acompanha até o cliente ligar.

# Habilitando o agente

```
configure
 snmp enabled yes
 snmp listen-address 10.0.0.1
 snmp port 161
 snmp enterprise-number 65535
 snmp community <comunidade-somente-leitura>
 snmp-user nms
  auth sha <passphrase>
  priv aes <passphrase>
  exit
 commit
 exit
save
```

| Linha | O que faz por você | Se ficar de fora |
|-------|--------------------|------------------|
| `snmp enabled yes` | liga o agente      | o agente fica desligado, que é o padrão |
| `snmp listen-address` | o endereço em que o agente atende | o agente escuta só em `127.0.0.1`, e nenhuma NMS de fora o alcança |
| `snmp port` | a porta UDP        | `161`            |
| `snmp enterprise-number` | o número sob o qual a MIB do Prisma é servida (ver abaixo) | vale o número do produto |
| `snmp community` | uma comunidade SNMPv2c, somente leitura | só o v3 consulta |
| `snmp-user` | um usuário SNMPv3, com `auth <sha\|md5>` e `priv <aes\|des>` | só o v2c consulta |

Qualquer uma dessas linhas volta ao padrão com `no` na frente — `no snmp enterprise-number` volta para o número do produto, `no snmp listen-address` volta para `127.0.0.1` (a partir da versão 0.143.2).

O `commit` recusa um agente ligado sem nenhuma comunidade e sem nenhum usuário v3: seria um agente que ninguém consegue consultar. E o agente é conferido pelo **efeito**: o arquivo é escrito, o serviço reinicia, e se ele não subir com a configuração nova a caixa devolve a anterior e diz isso no `commit`.

A partir da versão 0.144.1 a caixa responde **só em SNMPv2c e SNMPv3**: uma consulta em SNMPv1, mesmo com a comunidade certa, fica sem resposta. O v1 não carrega contadores de 64 bits, então as portas do dataplane e os contadores da MIB do Prisma nem apareceriam para ele.

O usuário v3 é criado com acesso somente leitura e exige autenticação **e** privacidade: configure as duas linhas, `auth` e `priv`. O `commit` também funciona dentro do bloco `snmp-user`, sem precisar sair dele.

A comunidade e as frases secretas do v3 são senhas que trafegam na rede, e são tratadas como tal: `show running-config` mostra `<set, not shown>` no lugar delas.

# Somente leitura, duas vezes

O arquivo de configuração do agente que a caixa gera contém só `rocommunity` e `rouser`, e **nunca** `rwcommunity`. Além disso, o próprio agente responde a todo SET com `not-writable`.

Configuração pertence à VTY, onde é validada, commitada e reversível. Um SET SNMP que reconfigura um CGNAT é uma parada a um erro de digitação de distância.

# Sem números velhos

Se o dataplane não está respondendo, o agente serve **nada** em vez dos valores que viu por último.


:::info
Um sistema de monitoramento que continua reportando os números de ontem durante uma pane **esconde** a pane; um que reporta uma lacuna a mostra. Por isso a verificação de vida roda a **cada** consulta, antes de o cache ser consultado. Os números em si ficam em cache por dez segundos: consultar mais rápido que isso devolve o mesmo valor.

:::

# O que é servido

## A caixa, o processador, a memória e os discos

A partir da versão 0.141.3, a NMS lê também o que o net-snmp da própria caixa já sabe responder — antes isso ficava fora da visão, e uma consulta à caixa não trazia nem o nome dela:

| Grupo | OID | O que traz |
|-------|-----|------------|
| sistema (SNMPv2-MIB) | `.1.3.6.1.2.1.1` | `sysDescr` com o produto e a versão instalada, `sysUpTime`, `sysName` |
| HOST-RESOURCES-MIB | `.1.3.6.1.2.1.25` | processadores, memória e armazenamento, processos rodando |
| UCD-SNMP-MIB | `.1.3.6.1.4.1.2021` | carga, memória, e a tabela de discos (`dskTable`) |

A tabela de discos já vem com dois limites: a raiz, que avisa com menos de 10% livre, e **o sistema de arquivos do log de retenção** (`/var/log/prisma-routing`), que avisa com menos de 15% livre. Esse é o que importa: disco cheio ali é o log que guarda um ano de registros de conexão parando de gravar. Na NMS, alerte em `dskErrorFlag` (`.1.3.6.1.4.1.2021.9.1.100`) igual a 1.

Um núcleo de worker aparece com carga de 100% sempre: ele não dorme, fica consultando a placa. A carga real do dataplane não é essa — ela entra numa próxima etapa do SNMP.

## As portas do dataplane, pela IF-MIB

A partir da versão 0.141.5, a `ifTable` e a `ifXTable` da caixa são as portas **do dataplane** — as que carregam os assinantes —, e não as que o Linux enxerga. Antes, a NMS que percorria a IF-MIB via só `lo` e a placa de gerência.

| Objeto | O que traz |
|--------|------------|
| `ifName`, `ifDescr` | o nome da porta, o mesmo da VTY |
| `ifAlias` | a `description` configurada na VTY |
| `ifHighSpeed`, `ifSpeed` | a velocidade do enlace, como `show hardware-interfaces` informa |
| `ifAdminStatus`, `ifOperStatus` | ligada na configuração, e com enlace |
| `ifHCInOctets`, `ifHCOutOctets` e os de pacotes | os contadores de 64 bits, somando todas as threads do dataplane |

Entram todas as portas do dataplane, incluindo as sub-interfaces, e as placas físicas que continuam com o Linux, como a de gerência. Ficam de fora as internas: `local0` e as `tap` que ligam o dataplane ao roteamento.

O `ifIndex` de cada porta é guardado pelo nome, então uma porta **mantém a mesma linha** quando o dataplane reinicia — a NMS não mistura o histórico de uma porta com o de outra.

## O roteamento: BGP, OSPF e IS-IS

A partir da versão 0.142.0, as sessões de roteamento aparecem nas MIBs padrão, servidas pelos próprios daemons que as rodam — o que a NMS lê é o estado do daemon, não uma cópia dele:

| MIB | OID | O que traz |
|-----|-----|------------|
| BGP4-MIB | `.1.3.6.1.2.1.15` | cada vizinho: estado da sessão, AS remoto, updates, último erro |
| OSPF-MIB | `.1.3.6.1.2.1.14` | vizinhos e seus estados, interfaces, tamanho da LSDB |
| OSPFV3-MIB | `.1.3.6.1.2.1.191` | o mesmo, para o OSPFv3 |
| ISIS-MIB | `.1.3.6.1.2.1.138` | adjacências e circuitos |
| BGP4V2-MIB | `.1.3.6.1.3.5.1` | cada vizinho BGP, **IPv4 e IPv6** (inclusive os *unnumbered*, por link-local): estado, AS remoto, erros e contadores de prefixos por família — a partir da 0.144.5 |

O que vale alertar: `bgpPeerState` (`.1.3.6.1.2.1.15.3.1.2.<endereço do vizinho>`) diferente de 6 (*established*), `ospfNbrState` diferente de 8 (*full*) e `isisISAdjState` diferente de 3 (*up*). O mesmo vale para o OSPFv3: `ospfv3NbrState` (`.1.3.6.1.2.1.191.1.9.1.8`) diferente de 8. Os quatro foram conferidos pelos testes do produto numa sessão real (o do OSPFv3 a partir da versão 0.142.1). A BGP4-MIB só conhece vizinhos IPv4; para um vizinho IPv6 use `bgp4V2PeerState` (`.1.3.6.1.3.5.1.1.2.1.13.1.<tipo>.<endereço>`, tipo 1 = IPv4, 2 = IPv6), que também deve ser 6.

A partir da versão 0.142.1, o tamanho da tabela de rotas também responde: `inetCidrRouteNumber` (`.1.3.6.1.2.1.4.24.6.0`) conta as rotas IPv4 e IPv6, e `ipCidrRouteNumber` (`.1.3.6.1.2.1.4.24.3.0`) só as IPv4. A tabela em si fica de fora de propósito: com a tabela completa da internet são perto de um milhão de linhas, e uma NMS que a percorresse pesaria na caixa a cada consulta. Uma queda brusca nesse número é uma sessão que caiu levando as rotas junto.

Nada precisa ser configurado além do `snmp enabled yes`: o commit liga o vínculo dos daemons de roteamento com o agente.


:::info
**Ao desligar o SNMP**, os daemons de roteamento continuam procurando o agente até a próxima vez que o FRR reiniciar — o FRR não permite desfazer esse vínculo com ele rodando — e o `commit` avisa isso. Nesse intervalo o log do FRR registra uma tentativa de conexão a cada 15 segundos por daemon. Na próxima inicialização do FRR (um reboot, por exemplo) o vínculo sai.

:::

## Timeouts, pela RFC 4008

`natDefTimeouts` em `.1.3.6.1.2.1.123.1.1` — seis objetos que mapeiam exatamente nos timeouts deste equipamento, então uma NMS existente os lê sem template customizado.

| OID sob `.1.3.6.1.2.1.123.1.1` | Objeto da RFC 4008 | Timeout desta caixa |
|------------------------------|--------------------|---------------------|
| `.1.0`                       | `natBindDefIdleTimeout` | `port-holddown`     |
| `.2.0`                       | `natUdpDefIdleTimeout` | `udp`               |
| `.3.0`                       | `natIcmpDefIdleTimeout` | `icmp`              |
| `.4.0`                       | `natOtherDefIdleTimeout` | `gre`               |
| `.5.0`                       | `natTcpDefIdleTimeout` | `tcp-established`   |
| `.6.0`                       | `natTcpDefNegTimeout` | `tcp-transitory`    |

O resto da RFC 4008 é por interface ou por *binding*: este equipamento não rastreia nenhuma dessas formas, e por *binding* significaria percorrer a tabela de sessões, coisa que nenhum `show` daqui tem permissão de fazer.

## O CGNAT na MIB padrão do IETF: NATV2-MIB

A partir da versão 0.144.2 a caixa serve também a **NATV2-MIB** (RFC 7659, `.1.3.6.1.2.1.234`), a MIB que o IETF definiu para NAT e CGNAT — uma NMS que já a conhece lê os pools sem template nosso. Cada pool aparece como uma instância de NAT, com o nome dele, o tipo de filtragem, a faixa de portas, os assinantes e portas em uso agora, as portas recusadas e as faixas de endereços públicos.

Só os números agregados: não há tabela com uma linha por assinante nem por sessão, que numa caixa em produção seriam milhões de linhas. Para os detalhes que a RFC não cobre — capacidade garantida, ocupação, carga por worker — vale a MIB do Prisma, abaixo.

## O VRRP na MIB padrão do IETF: VRRPV3-MIB

A partir da versão 0.144.3 a caixa serve também a **VRRPV3-MIB** (RFC 6527, `.1.3.6.1.2.1.207`) para os roteadores virtuais VRRP do dataplane. Cada roteador é uma linha indexada pelo ifIndex da interface (o mesmo da IF-MIB), pelo VRID e pela família de endereço (1 = IPv4, 2 = IPv6), com o estado (inicializando, backup, master), a prioridade, o MAC virtual, o intervalo de anúncio, os modos preempt e accept e os endereços virtuais.

Para alertar numa troca de master, acompanhe `vrrpv3OperationsStatus`: o dataplane não guarda contadores por roteador, então a tabela de estatísticas e as notificações da RFC não são servidas.

## O LACP na MIB padrão do IEEE: IEEE8023-LAG-MIB

A partir da versão 0.144.4 a caixa serve também a **IEEE8023-LAG-MIB** (IEEE 802.1AX, `.1.2.840.10006.300.43`) para os bundles LACP do dataplane. Cada bundle e cada membro aparecem pelo ifIndex da interface (o mesmo da IF-MIB): o sistema e a prioridade do parceiro, as chaves, a que bundle cada membro está ligado, o estado LACP dos dois lados, os LACPDUs recebidos, enviados e inválidos, e o estado das máquinas de recepção e de mux.

Para alertar quando um membro sai do bundle, acompanhe `dot3adAggPortAttachedAggID` (vai a 0) ou `dot3adAggPortDebugMuxState` (deixa de ser `collectingDistributing`). Os valores administrativos do parceiro, as tabelas de conversação e o DRNI não são servidos: o dataplane não os tem.

## Os endereços das interfaces: IP-MIB

A partir da versão 0.145.1 as tabelas de endereços da **IP-MIB** (`ipAddrTable`, `ipAddressTable` e `ipAddressPrefixTable`) listam os endereços das interfaces do dataplane, IPv4 e IPv6, **com o mesmo ifIndex da IF-MIB**. É por essa ligação que a NMS (o Zabbix, por exemplo) descobre qual endereço está em qual interface. Antes, ela via os endereços das interfaces espelho do Linux, com uma numeração que não batia com nenhuma porta da IF-MIB.

`ipForwarding` e `ipv6IpForwarding` respondem 1 (*forwarding*): a caixa é um roteador. Endereços link-local IPv6 ficam de fora.

## A MIB do Prisma: o CGNAT, os registros de conexão e a caixa

O que **não tem OID padrão** — os pools do CGNAT, os descartes, o transporte dos registros de conexão, a versão que o dataplane está rodando — está na **PRISMA-ROUTING-MIB**, a MIB do próprio produto. A partir da versão 0.143.4 ela é servida sem nenhuma configuração além de ligar o SNMP, e a própria caixa entrega o arquivo pronto, com o número já preenchido:

```
show snmp mib
```

Salve a saída como `PRISMA-ROUTING-MIB.txt` e carregue na NMS: a partir daí, em vez de números, a NMS mostra os nomes — `prPoolUtilisation`, `prLogTransportConnected` e assim por diante.


:::warning
**O número é provisório: 32473.** O Prisma pediu o número dele à IANA, e enquanto ele não chega a MIB fica sob o 32473, que a IANA reserva para uso em documentação e exemplos (RFC 5612) — não é de ninguém, então não se confunde com equipamento de outro fabricante na NMS. Quando o número do produto chegar, a caixa passa a usá-lo depois da atualização, sem nada a configurar, e nada abaixo dele muda de lugar: basta carregar de novo na NMS o arquivo que `show snmp mib` imprimir. Quem preferir pode usar o número da própria organização com `snmp enterprise-number <número>`; `no snmp enterprise-number` volta para o do produto.

:::

### Os totais da caixa

Nos mesmos OIDs de sempre, então uma NMS já configurada continua funcionando:

| OID sob `.1.3.6.1.4.1.<PEN>.1` | Objeto | Significado |
|------------------------------|--------|-------------|
| `.1.0`                       | `prSubscribers` | assinantes segurando um bloco agora |
| `.2.0`                       | `prPortsInUse` | portas alocadas somando todos eles |
| `.3.0`                       | `prPortsHeld` | portas contidas nos blocos atualmente segurados |
| `.4.0`                       | `prSubscribersNearFull` | assinantes em 90% ou mais do bloco |
| `.5.0`                       | `prPortRefusals` | portas recusadas desde que o dataplane subiu |
| `.6.0`                       | `prPacketsDropped` | pacotes que o CGNAT não deixou passar desde que subiu |


:::info
**A partir da versão 0.143.0,** `**.1.0**` **a** `**.4.0**` **chegam como Gauge, e** `**.5.0**` **e** `**.6.0**` **como Counter** — antes os seis chegavam como Counter, e uma NMS que calcula taxa desenhava cada descida como um pico falso de "estouro de contador". Se a sua NMS guardou esses itens como contador, troque para medidor (gauge). E `.5.0` agora é um contador de verdade: soma todas as recusas desde que o dataplane subiu, e não só as dos assinantes que ainda seguram bloco.

:::

### Os pools

Uma linha por pool, em `prPoolTable` (`.10.1.1.<coluna>.<pool>`):

| Objeto | O que traz |
|--------|------------|
| `prPoolName`, `prPoolMode` | o nome e o modo: determinístico ou dinâmico |
| `prPoolBlockSize`, `prPoolInsideAddresses`, `prPoolOutsideAddresses` | o tamanho do bloco e quantos endereços de assinante e públicos o pool tem |
| `prPoolBlocksTotal`, `prPoolCapacity`, `prPoolGuaranteed` | quantos blocos o pool entrega, quantos assinantes ele comporta no teto e quantos ele **garante** em qualquer worker — é por este que se dimensiona |
| `prPoolSubscribers`, `prPoolBlocksHeld`, `prPoolUtilisation` | quantos assinantes e blocos estão em uso agora, e a porcentagem de blocos entregues |
| `prPoolSessions`, `prPoolPortsInUse`, `prPoolSubscribersNearFull` | sessões abertas, portas alocadas e assinantes em 90% ou mais |
| `prPoolRefusals` | portas recusadas desde que o pool foi criado |

Os descartes ficam em `prDropTable` (`.10.2`), uma linha por motivo, com o nó e o motivo nas mesmas palavras de `show cgnat drops`; o total de sessões da caixa, em `prSessions` (`.10.3.0`).

### Os registros de conexão e a caixa

| Objeto | O que traz |
|--------|------------|
| `prLogIpfixEnabled`, `prLogIpfixRecords`, `prLogIpfixDropped` | o IPFIX por UDP: ligado, registros emitidos, e eventos sem coletor para onde ir |
| `prLogTransportTable` (`.11.10`) | os fluxos confiáveis — IPFIX por TCP (1) e syslog (2): ligado, **conectado**, registros, enviados, derramados no arquivo de spill, **perdidos**, e quantas vezes reconectou |
| `prSystemRelease`, `prSystemDataplaneBuild` | a versão instalada e a do dataplane que está rodando |
| `prSystemDataplaneCurrent` | se as duas são a mesma. **Falso depois de uma atualização, até o próximo reboot**: a caixa segue encaminhando com a versão antiga, e um `commit` que precisa da nova é recusado |

### Capacidade, workers, HA, LACP e identidade

A partir da versão 0.143.1:

| Objeto | O que traz |
|--------|------------|
| `prSessionsFit` | quantas sessões ainda cabem na memória do CGNAT — o mesmo número de `show cgnat memory`. Em zero, sessões novas são recusadas |
| `prWorkerTable` | assinantes, sessões e portas de cada thread do dataplane: um worker muito à frente dos outros é o aviso de assinante recusado com bloco sobrando em outro worker |
| `prVrrpTable` | cada roteador VRRP: interface, VR ID, a prioridade anunciada agora e o estado — `master`, `backup`, `init`. Mudança de estado é uma troca de papel |
| `prBundleTable` | cada bundle LACP: quantos membros configurados e quantos **carregando tráfego** |
| `prIdentityEntries`, `prIdentityReadAt` | quantos assinantes a tabela de identidade nomeia, e quando ela foi lida |
| `prRadiusReceived`, `prRadiusApplied`, `prRadiusRefused` | o accounting RADIUS vindo do BNG. Recusa costuma ser segredo diferente entre o BNG e a caixa |

Os números do CGNAT são mantidos pelo próprio dataplane no momento em que mudam, e o agente lê tudo de uma vez: consultar a caixa não custa uma varredura dos assinantes, por maior que ela seja.

## Zabbix: o template pronto

A partir da versão 0.146.0 a caixa entrega um **template para o Zabbix 7.0** que monitora tudo o que está nesta página: as portas do dataplane, os totais, os pools e os workers do CGNAT, os registros de conexão, o VRRP, o LACP, o BGP, o OSPF, o OSPFv3, o IS-IS, o tamanho da FIB, a CPU, a memória e os discos, e os avisos que a caixa envia. Ele sai da própria caixa, já com o número da MIB do Prisma preenchido:

```
show snmp zabbix-template
```

Salve a saída como `prisma-routing-zabbix.json` e importe no Zabbix, em *Coleta de dados* > *Templates* > *Importar*. O arquivo é JSON, que o Zabbix importa do mesmo jeito que YAML. No host, crie uma interface SNMP v2c com uma `snmp community` da caixa, ou v3 com um `snmp user`, e vincule o template **Prisma Routing by SNMP**. SNMPv1 é recusado pela caixa.

Os alarmes já vêm prontos: porta administrativamente ligada e operacionalmente fora, porta acima de 90% da velocidade ou com erros, pool acima de 80% e de 95% dos blocos, assinantes perto de esgotar as portas, portas recusadas, memória do CGNAT acabando, vizinho BGP fora de *established*, vizinho OSPF que não chega a *full*, adjacência IS-IS fora de *up*, troca de estado do VRRP, bundle LACP com menos membros ativos que configurados, a FIB perdendo metade das rotas em uma hora, registros de conexão perdidos ou transporte desconectado, dataplane rodando a versão antiga depois de uma atualização e a caixa sem responder SNMP. Os limites são macros do template, que você ajusta no host: `{$IF.UTIL.MAX}`, `{$PRISMA.POOL.UTIL.WARN}`, `{$PRISMA.POOL.UTIL.CRIT}`, `{$PRISMA.SESSIONS.FIT.WARN}` e as demais. Para uma porta que pode ficar fora sem alarme, use `{$IFCONTROL:"<nome da porta>"}` = 0.

Três escolhas do template:

* **Sem alarme de CPU.** Os núcleos dos workers do dataplane ficam em 100% o tempo todo, por projeto. A carga de cada núcleo é coletada, mas não gera alarme.
* **Um alarme por evento.** O VRRP e os pools geram alarme pela MIB do Prisma, que dá o nome da interface e do pool. As mesmas informações na VRRPV3-MIB e na NATV2-MIB são só coletadas. O BGP gera alarme pela BGP4-MIB para vizinhos IPv4 e pela BGP4V2-MIB só para os IPv6.
* **Porta desligada pelo operador não é porta caída.** O alarme de porta fora só dispara com a porta administrativamente ligada.

Para receber os **avisos** no Zabbix, aponte a caixa para um servidor ou proxy com o receptor de traps do Zabbix: `snmp trap-host <endereço> community <comunidade>`. Cada aviso da tabela da seção seguinte cai no seu próprio item, e o que não for reconhecido cai em um item de sobra.

O template foi importado no Zabbix 7.0 e testado contra uma caixa de laboratório: as descobertas acharam as portas, os pools, os workers, o VRRP e os bundles, os itens receberam valores, e os avisos `linkDown`, `linkUp` e `vrrpStateChange` chegaram cada um no seu item. O SNMPv3 e as tabelas de roteamento não foram testados contra um Zabbix de verdade. As tabelas de roteamento são conferidas contra os daemons pelos testes do produto.

## O que não é servido

* **Nada por assinante nem por sessão.** Isso significaria percorrer a tabela de sessões a cada consulta.
* **Se o par de HA concorda com esta caixa** sobre o mapeamento, os forwards e o resultado do self-test: a VTY mostra, em `show cgnat ha` e `show cgnat forward`, e o self-test fala no console a cada boot.


:::info
O SNMPv3 é configurado e o usuário é criado com acesso somente leitura, mas uma consulta autenticada por v3 **não** é verificada de ponta a ponta pelos testes do produto. Confira a sua com o `snmpget` do fim desta página antes de depender dela.

:::

# Avisos: a caixa conta quando algo muda

A partir da versão 0.143.3, a caixa não espera a próxima consulta: ela **avisa** a NMS quando algo muda, para cada destino configurado:

```
configure
 snmp trap-host 10.0.0.9 community <comunidade>
 snmp trap-host 10.0.0.10 community <comunidade> inform
 commit
 exit
```

Cada destino recebe **trap**, a não ser que diga `inform` — o inform é confirmado pelo receptor e reenviado até ser. A comunidade do destino é tratada como senha: `show running-config` mostra `<set, not shown>`. `no snmp trap-host <endereço>` para de enviar para ele.

| Quando | Aviso |
|--------|-------|
| uma porta do dataplane cai ou volta | `linkDown`, `linkUp` |
| uma sessão BGP cai ou é estabelecida | `bgpBackwardTransition`, `bgpEstablished` |
| um vizinho OSPF muda de estado | `ospfNbrStateChange` |
| um roteador VRRP muda de estado | `vrrpStateChange`, com a interface, o VR ID e o estado |
| um pool chega a 90% dos blocos | `poolNearlyFull`, com o nome do pool |
| portas são recusadas | `portsRefused` |
| um transporte de registros conecta ou cai | `recordsTransportChange` |
| registros de conexão são perdidos | `recordsLost` |
| os membros ativos de um bundle mudam | `bundleMembersChange` |
| uma atualização deixou o dataplane antigo rodando | `dataplaneNotCurrent` |
| disco, memória ou carga passam do limite | os avisos padrão do net-snmp |

Os avisos com nome (`vrrpStateChange` e os demais) chegam como `mteTriggerFired` da DISMAN-EVENT-MIB, com o nome no campo `mteHotTrigger`, e ficam sob o mesmo número da MIB. A caixa confere cada condição a cada 30 ou 60 segundos, então o aviso chega até um minuto depois do evento.

# O que vale colocar em gráfico

| Métrica | Por que |
|---------|---------|
| assinantes em 90% do bloco (`prSubscribersNearFull`) | é o alarme adiantado; quando chega em 100% o cliente já ligou |
| porta recusada (`prPortRefusals`) | qualquer subida é assinante sem conseguir abrir conexão |
| ocupação do pool (`prPoolUtilisation`) acima de 80% | o pool está ficando sem blocos para entregar |
| assinantes do pool (`prPoolSubscribers`) perto de `prPoolGuaranteed` | o pool está perto do que consegue garantir |
| transporte ligado e **não conectado** (`prLogTransportConnected`) | os registros de conexão estão indo para o arquivo de spill |
| registros perdidos (`prLogTransportDropped`, `prLogIpfixDropped`) subindo | é um buraco no log de retenção |
| `prSystemDataplaneCurrent` falso | uma atualização esperando reboot |
| `prSessionsFit` perto de zero | sessões novas vão ser recusadas por falta de memória |
| `prVrrpState` mudando | houve troca de papel no HA |
| `prBundleActiveMembers` menor que `prBundleMembers` | o bundle está rodando com menos pernas do que tem |
| `prRadiusRefused` subindo | o BNG está mandando accounting que a caixa não aceita |
| pacotes não passados (`prPacketsDropped`) | subida súbita costuma ser mudança de configuração ou rota; `prDropTable` diz o motivo |

O mesmo se vê pela VTY, sem NMS:

```
show cgnat top                              quem está mais perto de encher o bloco
show cgnat pool                             quantos estão perto do fim, por faixa de uso
show cgnat drops                            o que não passou, e por quê
show cgnat memory                           quantas sessões ainda cabem na memória do CGNAT
show interface utilization <nome>           quanto está passando na porta, agora
```

`show interface utilization` amostra os contadores do dataplane várias vezes e mostra a taxa em bit/s de cada porta, recebendo e transmitindo, com um pequeno gráfico de barras das amostras daquela execução. Por padrão são 5 amostras a cada 2 segundos; `interval <segundos>` (0,1 a 60) e `count <n>` (1 a 120) mudam isso, e o nome da porta no fim limita a uma só:

```
show interface utilization interval 1 count 30 ens19
```

Onde o dataplane informa a velocidade do enlace, as barras e a porcentagem são da taxa de linha daquela porta. Onde não informa — toda interface virtual —, as barras são escaladas pela amostra mais alta da execução, e o rodapé diz isso em vez de inventar uma porcentagem. Ctrl-C encerra a amostragem antes do fim e mostra o que já foi medido. É a resposta para *"o enlace está cheio?"* sem esperar o gráfico da NMS fechar o intervalo — mas não guarda histórico entre uma execução e outra: histórico é trabalho da NMS.

# Como conferir

Da própria NMS, ou de qualquer máquina que alcance o `listen-address`:

```sh
snmpget -v2c -c <comunidade> <endereço> .1.3.6.1.2.1.123.1.1.1.0
snmpget -v3 -l authPriv -u nms -a SHA -A <passphrase> -x AES -X <passphrase> <endereço> .1.3.6.1.2.1.123.1.1.1.0
```


:::success
O agente respondendo os timeouts da RFC 4008 confirma que ele está de pé; os contadores da MIB do Prisma, sob `.1.3.6.1.4.1.32473.1`, mostram o CGNAT de onde alguém olha.

:::

Se o agente responde nada, verifique primeiro se o `listen-address` é um endereço que a NMS alcança, e depois se o dataplane está vivo (`show version`): com o dataplane parado, silêncio é resposta proposital, não falha do SNMP.

# Ver também

* [Operação e diagnóstico](/doc/operacao-e-diagnostico-A4fcKDVwZt) — os mesmos números pela VTY
* [Quando o bloco de um assinante enche](/doc/quando-o-bloco-de-um-assinante-enche-kb1sJTtCKx) — o que fazer quando `.4.0` e `.5.0` sobem
* [Roteamento, SSH, usuários e SNMP](/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m) — a referência dos comandos `snmp`
* [Timeouts e os pisos das RFCs](/doc/timeouts-e-os-pisos-das-rfcs-GvPsvW3hLf) — os valores que a RFC 4008 expõe
