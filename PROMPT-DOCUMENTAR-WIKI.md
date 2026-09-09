# Tarefa: documentar todas as features do HexaRouting na wiki

Você vai escrever documentação de uso na wiki pública do HexaRouting. O
repositório está em `/home/hexanetworks/hexarouting` (branch `master`).

## Leia isto primeiro, nesta ordem

1. **`PADRAO-DOCUMENTACAO.md`** (raiz do repo) — como uma página é escrita.
   A **seção 0** define para quem a wiki é: é para quem **usa** a plataforma
   (operador, NOC, quem avalia o produto), **não** para quem a desenvolve. Ela
   lista o que fica de fora. Isso não é sugestão — duas páginas já foram
   apagadas por violar essa regra.
2. **`docs/wiki-audit.md`** — como auditar a wiki contra o repositório, e o
   método de "uma afirmação por vez". Leia a seção 3 (acesso pela API) e a 6
   (o método).
3. **`docs/context.md`** — as invariantes do produto e qual arquivo é dono de
   cada uma. Leia antes de julgar qualquer afirmação.
4. **`wiki/README.md`** — o espelho somente-leitura da wiki dentro do repo.

## O que é autoritativo

**O repositório é autoritativo sobre o produto**: `docs/architecture.md`,
`docs/logging.md`, `docs/routing.md`, `docs/appliance.md`,
`docs/pool-resize.md`, `README.md` e `TESTING.md`. `ref/bpa_model.py` é a
especificação executável do mapeamento.

A wiki é uma camada de uso por cima disso. Onde os dois discordam, **a página
está errada**. Nunca valide uma afirmação contra outra página da wiki.

O repositório muda: confira o `VERSION` e o `CHANGELOG.md` antes de começar, e
documente a versão lançada — não trabalho não commitado na árvore.

## O estado atual da wiki

29 páginas em quatro categorias, numa coleção só (o compartilhamento público do
Outline é por coleção, e a raiz do domínio aponta para esse link único):

```
Começar aqui          Primeiros passos · Dimensionamento e requisitos ·
                      Instalação da appliance · Acesso, login e papéis
Operar                Receitas do dia a dia · Operação e diagnóstico ·
                      Quando o bloco de um assinante enche · Comportamento NAT
                      e compatibilidade · Protocolos e ALGs · Encaminhamento de
                      porta · uRPF e origens forjadas · Ampliar capacidade sem
                      derrubar assinantes · Monitoramento e SNMP · Logging
                      IPFIX e trilha de auditoria
Referência            VTY hexash — guia de uso (+ 5 subpáginas: Comandos show,
                      Configuração de CGNAT, Configuração de pool, commit
                      rollback e save, Roteamento SSH usuários e SNMP) ·
                      Multi-tenant com VRF · Timeouts e os pisos das RFCs
Entender o produto    HexaRouting — visão geral · BPA — conceitos e mapeamento ·
                      Alta disponibilidade
```

A **home** é a descrição da coleção (`collections.update`, campo
`description`), não uma página.

## A tarefa

Documentar as features que ainda não têm cobertura, e completar as que têm
cobertura rasa. Sempre respondendo **como funciona** e **como usar**.

Faça sua própria análise de lacunas — percorra as seções de
`docs/architecture.md`, `docs/routing.md`, `docs/logging.md` e
`docs/appliance.md` e verifique, uma por uma, se a wiki cobre aquilo em
linguagem de uso. Estas são pistas conhecidas, não a lista fechada:

| Lacuna | Onde está a fonte |
|---|---|
| Segredos na configuração: o que é redigido em `show` e por quê | `docs/routing.md` §Secrets |
| Versão do FRR e o que isso implica | `docs/routing.md` §FRR version |
| Erros ICMP e PMTUD | `docs/architecture.md` §TCP and ICMP handling |
| Estado TCP: RST e FIN, e o que o equipamento faz com eles | idem |
| Travamento de acesso pelo SSH e as três defesas contra ele | `docs/routing.md` §The lockout |
| FTP: por que os números de sequência são a parte que morde | `docs/architecture.md` §FTP |
| PPTP: o problema do Call ID | `docs/architecture.md` §PPTP |
| Utilização como distribuição, e o alarme uma vez por travessia | `docs/architecture.md` §Seeing a pool fill |
| O que o self-test de boot checa, e como ler o que ele diz | `docs/appliance.md` §Boot self-test |
| Rotas estáticas | `docs/routing.md` §Static routes |

Antes de criar página nova, pergunte se aquilo é uma **seção** de página
existente. A wiki tem 29 páginas; ela fica pior com 60 páginas finas.

## Como escrever e publicar

Tudo pela **API do Outline**, nunca pelo banco. O `docs/wiki-audit.md` §3
explica; peça ao mantenedor o host e o login.

```
documents.list      lista as páginas e os ids
documents.update    {id, text, publish:true}
documents.create    {title, text, collectionId, parentDocumentId, publish:true}
documents.move      {id, collectionId, parentDocumentId, index}
collections.update  {id, description}   ← a home
```

## Armadilhas já pagas — não repita

**O espelho `wiki/` pode estar desatualizado.** Ele é somente leitura e o
`tools/wiki-pull.sh` o atualiza. Editar arquivo lá não muda nada. Para auditar,
leia o que a wiki diz **hoje**, pela API.

**No espelho, `` `**vppctl**` `` parece markdown quebrado. Não é.** É artefato
do serializador do Outline na exportação; ao vivo renderiza
`<strong><code>vppctl</code></strong>` corretamente. Não "conserte".

**O ida-e-volta do Outline já partiu callouts ao meio**, deixando só a primeira
frase dentro do `:::warning` e o resto fora. Depois de editar uma página com
callout, confira que o bloco contém o pensamento inteiro.

**Renomear uma página muda a URL dela.** Todo link que apontava para a antiga
quebra. Depois de renomear, varra as outras páginas e a home.

**Apagar exige dois passos**: `documents.delete` manda para a lixeira, e só
então `documents.delete` com `permanent:true` remove. Um passo só falha em
silêncio e a página continua lá.

**Nunca invente a saída de um comando.** Os arquivos `lab/*.sh` contêm as
strings que o produto realmente imprime, dentro das asserções — é a fonte
segura para citar saída. Exemplos que já vieram de lá: `FORMULA`,
`past or present`, `DYNAMIC`, `NOW ONLY`, `not inside any configured pool`,
`3/512`, `refusals: ports 0, session cap 6, rate cap 0`.

**Verifique no navegador, não com `curl`.** O HTML servido não prova que a
página renderizou: CSP, chunks e Mermaid só falham no navegador. Diagramas
Mermaid renderizam de fato — há 24 deles na wiki hoje.

## Ao terminar

1. Nenhum link interno quebrado, nem nas páginas nem na home.
2. Todas as páginas conformes ao `PADRAO-DOCUMENTACAO.md`: abertura sem título,
   corpo de um tipo só, fecho com *Como conferir* ou *Ver também*.
3. Atualize `docs/wiki-audit.md` §5 com o que mudou e por quê — o próprio
   documento manda registrar, senão o próximo leitor desfaz.
4. Rode `tools/wiki-pull.sh` para o espelho refletir a wiki.
