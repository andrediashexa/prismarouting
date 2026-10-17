# Operar

O que fazer com a máquina já em produção: tarefas do dia a dia, diagnóstico quando algo quebra, e a guarda de registros que a lei exige.

| Página | Responde |
|--------|----------|
| [Receitas do dia a dia](/s/wiki/doc/receitas-do-dia-a-dia-IcocgdbXW2) | Tarefas concretas, com os comandos na ordem |
| [Operação e diagnóstico](/s/wiki/doc/operacao-e-diagnostico-A4fcKDVwZt) | Por onde começar quando alguém reclama, e a ordem de boot |
| [A caixa subiu e não encaminha](/s/wiki/doc/a-caixa-subiu-e-nao-encaminha-MJT8B3E4jW) | O que o autoteste diz, onde a caixa conta por que o dataplane parou, e como voltar a encaminhar |
| [Quando o bloco de um assinante enche](/s/wiki/doc/quando-o-bloco-de-um-assinante-enche-kb1sJTtCKx) | As três recusas, blocos estendidos e limites por assinante |
| [Comportamento NAT e compatibilidade](/s/wiki/doc/comportamento-nat-e-compatibilidade-8eYzcDTnmJ) | Se jogo, câmera e peer-to-peer funcionam atrás deste CGNAT |
| [Protocolos e ALGs](/s/wiki/doc/protocolos-e-algs-vZjaCcHaN7) | O que atravessa sozinho, o que precisa de ALG e o que não é suportado |
| [Encaminhamento de porta](/s/wiki/doc/encaminhamento-de-porta-epIZfdmzU3) | Abrir uma porta de entrada sem vender um endereço público |
| [uRPF e origens forjadas](/s/wiki/doc/urpf-e-origens-forjadas-1Mb4LB6Dhf) | A defesa que impede o log de ficar confiantemente errado |
| [Ampliar capacidade sem derrubar assinantes](/s/wiki/doc/ampliar-capacidade-sem-derrubar-assinantes-rWQkaj9r1u) | O caminho que custa uma parada e o que não custa |
| [Levar a caixa para uma versão nova](/s/wiki/doc/levar-a-caixa-para-uma-versao-nova-C09kO2kU8X) | `system upgrade`, o pacote à mão, e quando a reinstalação é inevitável |
| [Montar um par ativo/standby](/s/wiki/doc/montar-um-par-ativostandby-CkNb7AvtnG) | Duas caixas, VRRP nas duas portas, o arquivo do par e como testar a troca |
| [Monitoramento e SNMP](/s/wiki/doc/monitoramento-e-snmp-YGn85RpUAb) | O que a sua NMS enxerga, e o que falta para enxergar tudo |
| [Logging IPFIX e trilha de auditoria](/s/wiki/doc/logging-ipfix-e-trilha-de-auditoria-lREEjJu1Bq) | Para onde os registros vão (IPFIX ou syslog), o que carregam, e como provar que a trilha não foi editada |
| [Onde vão os registros: coletor, transporte e guarda](/s/wiki/doc/onde-vao-os-registros-coletor-transporte-e-guarda-0IG3JJOswC) | Por que a caixa exporta e não guarda, qual transporte escolher, o que o coletor precisa manter e o que acontece quando ele cai |

# As três regras que evitam incidente


1. **Mudar o mapeamento de um pool derruba todas as sessões dele.** Prefixo, `block-size`, `ports`, `policy`, `mode`, `reserve` ou VRF: o pool é reconstruído. Quando assinantes de um pool determinístico mudariam de endereço ou de bloco, o `commit` recusa e mostra quantos, até ouvir `commit remap`; nos demais casos — pool dinâmico, troca de VRF, troca de modo — ele aplica sem perguntar.
2. **Mudança remota vai com** `**commit confirmed**`**.** Se cortar o próprio acesso, a máquina se recupera sozinha — quem desfaz é o equipamento, não a sua sessão, então sair da VTY ou perder o SSH não cancela nada. Só um novo `commit` confirma.
3. `**commit**` **não é** `**save**`**.** O primeiro faz valer agora; o segundo faz sobreviver ao boot. Depois de atualizar a versão, `save` antes de reiniciar: a caixa pode recusar o arquivo salvo na forma antiga e subir sem configuração.

# Ver também

* [Comandos show](/s/wiki/doc/comandos-show-YlNji1X5W4) — a referência dos comandos de diagnóstico
* [Configuração de pool](/s/wiki/doc/configuracao-de-pool-btyS572Ih6) — quais campos derrubam sessão e quais não
