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
| `snmp enterprise-number` | liga a metade dos números de operação (ver abaixo) | essa metade fica escura |
| `snmp community` | uma comunidade SNMPv2c, somente leitura | só o v3 consulta |
| `snmp-user` | um usuário SNMPv3, com `auth <sha\|md5>` e `priv <aes\|des>` | só o v2c consulta |

O `commit` recusa um agente ligado sem nenhuma comunidade e sem nenhum usuário v3: seria um agente que ninguém consegue consultar. E o agente é conferido pelo **efeito**: o arquivo é escrito, o serviço reinicia, e se ele não subir com a configuração nova a caixa devolve a anterior e diz isso no `commit`.

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

## Os números que o operador realmente acompanha

Esses **não têm OID padrão**. Vivem sob o Private Enterprise Number da sua própria organização:

| OID sob `.1.3.6.1.4.1.<PEN>.1` | Significado |
|------------------------------|-------------|
| `.1.0`                       | assinantes segurando um bloco agora |
| `.2.0`                       | portas alocadas somando todos eles |
| `.3.0`                       | portas contidas nos blocos atualmente segurados |
| `.4.0`                       | assinantes em 90% ou mais do bloco |
| `.5.0`                       | vezes que um assinante teve porta recusada |
| `.6.0`                       | pacotes que o CGNAT não deixou passar |


:::warning
**Até** `**snmp enterprise-number**` **ser configurado, essa metade fica escura.** Não há padrão e não pode haver: um OID sob o PEN de outra organização é uma mentira sobre a quem o objeto pertence, e colide no dia em que aquela organização usar a mesma subárvore. O registro na IANA é gratuito, leva de dias a semanas, e o pedido nomeia a empresa — então é do responsável pela organização, não algo que o software possa fazer sozinho.

:::


:::info
**Os seis objetos da subárvore do PEN chegam com o tipo Counter, e só um deles se comporta como contador.** `.1.0` a `.4.0` são fotografias do momento, que sobem e descem. `.5.0` soma as recusas dos assinantes que seguram bloco **agora**: quando um assinante solta o último bloco, as recusas dele saem da soma, e o número desce. `.6.0` soma os descartes desde que o dataplane subiu, e zera quando ele reinicia. Configure a NMS para guardar o valor lido, e não a diferença por segundo — senão uma descida vira um pico falso de "estouro de contador".

:::

## O que não é servido

* **Nada por assinante nem por sessão.** Isso significaria percorrer a tabela de sessões a cada consulta.


:::info
O SNMPv3 é configurado e o usuário é criado com acesso somente leitura, mas uma consulta autenticada por v3 **não** é verificada de ponta a ponta pelos testes do produto. Confira a sua com o `snmpget` do fim desta página antes de depender dela.

:::

# O que vale colocar em gráfico

| Métrica | Por que |
|---------|---------|
| assinantes em 90% do bloco (`.4.0`) | é o alarme adiantado; quando chega em 100% o cliente já ligou |
| porta recusada (`.5.0`) | qualquer valor diferente de zero é assinante sem conseguir abrir conexão |
| portas alocadas ÷ portas seguradas (`.2.0` / `.3.0`) | a taxa de ocupação real dos blocos entregues |
| pacotes não passados (`.6.0`) | subida súbita costuma ser mudança de configuração ou rota |

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
O agente respondendo os timeouts da RFC 4008 confirma que ele está de pé. Se o PEN estiver configurado, os seis contadores acima também respondem — e aí o CGNAT está visível de onde alguém olha.

:::

Se o agente responde nada, verifique primeiro se o `listen-address` é um endereço que a NMS alcança, e depois se o dataplane está vivo (`show version`): com o dataplane parado, silêncio é resposta proposital, não falha do SNMP.

# Ver também

* [Operação e diagnóstico](/doc/operacao-e-diagnostico-A4fcKDVwZt) — os mesmos números pela VTY
* [Quando o bloco de um assinante enche](/doc/quando-o-bloco-de-um-assinante-enche-kb1sJTtCKx) — o que fazer quando `.4.0` e `.5.0` sobem
* [Roteamento, SSH, usuários e SNMP](/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m) — a referência dos comandos `snmp`
* [Timeouts e os pisos das RFCs](/doc/timeouts-e-os-pisos-das-rfcs-GvPsvW3hLf) — os valores que a RFC 4008 expõe
