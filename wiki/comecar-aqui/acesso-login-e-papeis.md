# Acesso, login e papéis

```mermaid
flowchart TD
    L["login por console ou SSH"] --> H{"hexash iniciou?"}
    H -->|sim| V["VTY hexarouting&gt;"]
    H -->|não| F["shell, COM o motivo impresso"]
    V -->|"start shell"| S["shell filho — exit volta"]
    B["boot com hexash=off"] --> F2["shell direto, sem VTY"]
```

## Onde você cai ao entrar

| Situação | O que acontece |
|----------|----------------|
| Login por console ou SSH | Cai no `hexash` |
| `start shell` dentro da VTY | Abre um shell como filho; `exit` volta para a VTY |
| `scp`, `rsync`, `git` por SSH | Chegam a um shell real — shell de login invocado com `-c` passa direto |
| `hexash` falha ao iniciar | Cai num shell **com o motivo impresso** |
| Boot com `hexash=off` | Pula a VTY inteiramente; a ISO oferece isso como entrada de menu |

Na **ISO** o console tem autologin — a conta root está travada, não há o que autenticar. Na máquina **instalada** não há autologin e a senha é a definida durante a instalação.

O autologin de console é feito **apenas pela ISO**, e o instalador o remove do alvo. Numa máquina instalada ele significaria que acesso físico ou IPMI equivale a acesso total sem senha — decisão do operador, não padrão do produto.

### `start shell`

Não aparece no completar por TAB nem no `?`. É **oculto, não restrito**: qualquer um que consiga logar pode executá-lo. **Não é fronteira de segurança** — esconder só evita que seja a coisa óbvia a fazer depois de um `?`.

### Por que existe saída de emergência

Apontar o shell de login para uma aplicação cria risco de travamento total: um `hexash` que falhasse ao iniciar deixaria ninguém capaz de alcançar a máquina, nem por console nem por SSH. Por isso há **duas saídas independentes**, e a segunda (`hexash=off` no boot) não depende do `hexash` funcionar.


:::info
`install.sh` **não** troca o shell de login de ninguém, a menos que `HEXA_VTY_LOGIN=1` esteja setado. Numa Debian existente a máquina pode ter outras funções, e trocar o shell do root silenciosamente é o tipo de surpresa que o operador descobre no pior momento.

:::

## Papéis e permissões

Duas capacidades, mapeadas em **grupos do sistema** — não num banco de contas próprio da appliance. Uma appliance que inventa as próprias contas é uma appliance cujas contas ninguém desprovisiona quando alguém sai da empresa.

| Grupo | Pode alterar |
|-------|--------------|
| `hexa-admin` | tudo: roteamento, configuração da própria máquina e CGNAT |
| `hexa-cgnat` | somente configuração de CGNAT |
| nenhum dos dois | somente leitura |

`root` tem as duas, porque root fala com o socket do dataplane diretamente e fingir o contrário seria teatro.

As capacidades são lidas da participação em grupos **no momento em que a VTY inicia**, e nunca de algo que o programa guarde: a resposta precisa ser a que o sistema daria, senão desprovisionar uma conta aqui não a desprovisiona de fato.

Uma conta que o sistema não consegue descrever recebe **nada**. Falhar aberto aqui significaria que um NSS quebrado vira direito total de configuração.

### O que cada mudança exige

| Tipo de mudança | Capacidade exigida |
|-----------------|--------------------|
| Pools, interfaces CGNAT, timeouts, limites, bypass, forwards | `hexa-cgnat`       |
| Roteamento, SSH, contas de usuário, SNMP, MTU, uRPF | `hexa-admin`       |

## Invocando o hexash

```sh
hexash                       # sessão interativa
hexash -c "show cgnat pool"  # um comando e sai
hexash -c "cmd1" -c "cmd2"   # vários comandos em sequência
hexash --load [arquivo]      # aplica um arquivo de configuração salvo
```

`--load` sem argumento usa `/etc/hexarouting/hexarouting.conf`. É o que roda no boot, pela `hexarouting.service`.

O código de saída é **1** se qualquer comando falhou, **0** caso contrário — o que permite usar `hexash -c` em script sem depender de casar padrões de texto na saída.

### Caminhos

| Caminho | Conteúdo |
|---------|----------|
| `/etc/hexarouting/hexarouting.conf` | configuração salva, aplicada no boot |
| `/etc/hexarouting/rollback/` | revisões commitadas (as 20 mais recentes) |

Variáveis de ambiente `HEXA_CONFIG` e `HEXA_ROLLBACK` sobrescrevem esses caminhos.

Em arquivos de configuração, `!` inicia comentário — o resto da linha é ignorado.

# Ver também

* [Primeiros passos](/doc/primeiros-passos-TAWDdSrYG7) — a sequência completa de configuração
* [Roteamento, SSH, usuários e SNMP](/doc/roteamento-ssh-usuarios-e-snmp-LoibxwFM5m) — como criar contas e ajustar o SSH
