# Monitoramento e SNMP

O que a sua NMS enxerga deste CGNAT, e o que precisa ser feito para que a metade mais útil apareça. Para quem cuida do Zabbix, do LibreNMS ou equivalente.

Todo provedor já tem uma NMS, e é onde alguém de fato olha. Um CGNAT cuja única observabilidade é `vppctl` na caixa é um CGNAT que ninguém acompanha até o cliente ligar.

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

# Somente leitura, duas vezes

O `snmpd.conf` gerado contém `rocommunity` e `rouser`, e **nunca** `rwcommunity`. Além disso, o próprio agente responde a todo SET com `not-writable`.

Configuração pertence à VTY, onde é validada, commitada e reversível. Um SET SNMP que reconfigura um CGNAT é uma parada a um erro de digitação de distância.

# Sem números velhos

Se o dataplane não está respondendo, o agente serve **nada** em vez dos valores que viu por último.


:::info
Um sistema de monitoramento que continua reportando os números de ontem durante uma pane **esconde** a pane; um que reporta uma lacuna a mostra. Por isso a verificação de vida roda a **cada** consulta, antes de o cache ser consultado.

:::

# O que é servido

## Timeouts, pela RFC 4008

`natDefTimeouts` em `.1.3.6.1.2.1.123.1.1` — seis objetos que mapeiam exatamente nos timeouts deste equipamento, então uma NMS existente os lê sem template customizado.

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

# O que vale colocar em gráfico

| Métrica | Por que |
|---------|---------|
| assinantes em 90% do bloco (`.4.0`) | é o alarme adiantado; quando chega em 100% o cliente já ligou |
| porta recusada (`.5.0`) | qualquer valor diferente de zero é assinante sem conseguir abrir conexão |
| portas alocadas ÷ portas seguradas (`.2.0` / `.3.0`) | a taxa de ocupação real do pool |
| pacotes não passados (`.6.0`) | subida súbita costuma ser mudança de configuração ou rota |

O mesmo se vê pela VTY, sem NMS:

```
show cgnat top                              quem está mais perto de encher o bloco
show cgnat drops                            o que não passou, e por quê
show interface utilization <nome>           quanto está passando na porta, agora
```

`show interface utilization` amostra os contadores duas ou mais vezes e mostra a taxa em bit/s, com histórico — é a resposta para *"o enlace está cheio?"* sem esperar o gráfico da NMS fechar o intervalo. Onde o dataplane informa a velocidade do enlace, a porcentagem é da taxa de linha daquela porta.

# Como conferir

Da própria NMS, ou de qualquer máquina autorizada:

```sh
snmpget -v2c -c <comunidade> <endereço> .1.3.6.1.2.1.123.1.1.1.0
```


:::success
O agente respondendo os timeouts da RFC 4008 confirma que ele está de pé. Se o PEN estiver configurado, os seis contadores acima também respondem — e aí o CGNAT está visível de onde alguém olha.

:::

Se o agente responde nada, verifique se o dataplane está vivo: silêncio é resposta proposital, não falha do SNMP.

# Ver também

* [Operação e diagnóstico](/doc/operacao-e-diagnostico-A4fcKDVwZt) — os mesmos números pela VTY
* [Roteamento, SSH, usuários e SNMP](/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m) — a referência dos comandos `snmp`
* [Timeouts e os pisos das RFCs](/doc/timeouts-e-os-pisos-das-rfcs-GvPsvW3hLf) — os valores que a RFC 4008 expõe
