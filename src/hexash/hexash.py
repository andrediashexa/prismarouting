#!/usr/bin/env python3
"""
hexash - VTY do HexaRouting.

Uma CLI so para roteamento e CGNAT. Comandos de CGNAT sao traduzidos para o
plugin bpa no VPP; comandos de roteamento sao repassados ao vtysh do FRR com
o contexto preservado.

Por que nao um daemon registrado no vtysh: a lista de daemons do vtysh e
compilada (vtysh_client[]), sem mecanismo de plugin. Registrar um daemon novo
exigiria manter um fork do vtysh pelo resto da vida do produto. Repassar e
mais barato e nao prende a evolucao do FRR.
"""

import os
import re
import shlex
import subprocess

readline = None
import sys

CONFIG_PATH = os.environ.get("HEXA_CONFIG", "/etc/hexarouting/hexarouting.conf")
VPPCTL = os.environ.get("VPPCTL", "vppctl")
VTYSH = os.environ.get("VTYSH", "vtysh")

# Comandos do FRR que abrem um bloco de configuracao. Usados para saber o
# contexto a repetir quando repassamos a proxima linha ao vtysh.
FRR_BLOCKS = ("router ", "interface ", "vrf ", "route-map ", "line ",
              "key chain ", "bfd", "segment-routing", "pbr-map ",
              "address-family ")


# O vppctl devolve 0 mesmo quando o comando falha: quem diz e o texto. Os
# erros do VPP saem como "<comando>: <mensagem>" ou "unknown input ...".
# Substituir isso pela API binaria, que tem codigo de retorno de verdade,
# esta no roadmap; ate la a deteccao e por padrao de texto.
VPP_ERROR = re.compile(r"^(unknown input\b|[a-z][a-z0-9 _-]*: \S)")


# ---------------------------------------------------------------------------
# Arvore de comandos, para completacao e para o "?".
#
# Cada entrada e (descricao, subarvore). Subarvore None encerra o comando;
# uma string no lugar dela e a dica do argumento esperado, mostrada pelo "?"
# como o Cisco faz - "<A.B.C.D/M>" e mais util que uma lista vazia.
#
# Os comandos do FRR nao estao aqui inteiros: sao milhares e mudam a cada
# versao. A arvore cobre os pontos de entrada e o resto e repassado; e
# honesto dizer "daqui para frente quem sabe e o FRR" em vez de fingir uma
# lista que ficaria desatualizada.
# ---------------------------------------------------------------------------

ARG_PREFIX = "<A.B.C.D/M>"
ARG_NUM = "<1-65535>"
ARG_WORD = "<palavra>"

SHOW_CGNAT = {
    "pool": ("pools de CGNAT configurados", None),
    "session": ("sessoes ativas por thread", None),
    "blocks": ("blocos alocados no modo dinamico", None),
    "logging": ("estado da exportacao IPFIX", None),
}

SHOW = {
    "cgnat": ("informacao de CGNAT", SHOW_CGNAT),
    "running-config": ("configuracao em vigor, CGNAT e roteamento", None),
    "ip": ("informacao de IP (FRR)", None),
    "bgp": ("informacao de BGP (FRR)", None),
    "isis": ("informacao de IS-IS (FRR)", None),
    "interface": ("interfaces (FRR)", None),
    "version": ("versao (FRR)", None),
    "logging": ("log do sistema (FRR)", None),
}

CGNAT_POOL = {
    "inside": ("prefixo interno, pode repetir", ARG_PREFIX),
    "outside": ("prefixo publico, pode repetir", ARG_PREFIX),
    "block-size": ("portas por bloco, potencia de dois", "<64-65536>"),
    "ports": ("faixa de portas usavel", "<min> <max>"),
    "policy": ("como espalhar assinantes entre IPs publicos", {
        "interleaved": ("vizinhos em IPs publicos distintos", None),
        "blocked": ("vizinhos no mesmo IP publico", None),
    }),
    "mode": ("como o bloco e atribuido", {
        "deterministic": ("por formula, sem log de alocacao", None),
        "dynamic": ("sob demanda, com log de alocacao", None),
    }),
    "exit": ("aplica o pool e volta", None),
}

CGNAT_CONFIG = {
    "pool": ("cria ou edita um pool", ARG_WORD),
    "interface": ("liga o CGNAT numa interface", "<interface> inside|outside"),
    "timeout": ("tempo de vida das sessoes", {
        "udp": ("segundos", ARG_NUM),
        "icmp": ("segundos", ARG_NUM),
        "tcp-transitory": ("segundos", ARG_NUM),
        "tcp-established": ("segundos", ARG_NUM),
    }),
    "block-holddown": ("segundos que um bloco fica reservado", ARG_NUM),
    "logging": ("exportacao IPFIX de eventos de bloco", {
        "enable": ("liga", None),
        "disable": ("desliga", None),
    }),
}

TREE = {
    "view": {
        "enable": ("entra no modo privilegiado", None),
        "show": ("mostra informacao", SHOW),
        "exit": ("encerra a sessao", None),
    },
    "enable": {
        "configure": ("entra em configuracao", {
            "terminal": ("configura pelo terminal", None)}),
        "show": ("mostra informacao", SHOW),
        "write": ("grava a configuracao", {
            "memory": ("grava em disco", None)}),
        "disable": ("volta ao modo nao privilegiado", None),
        "exit": ("encerra a sessao", None),
    },
    "config": {
        "cgnat": ("configuracao de CGNAT", CGNAT_CONFIG),
        "no": ("remove configuracao", {
            "cgnat": ("remove configuracao de CGNAT", {
                "pool": ("remove um pool", ARG_WORD)})}),
        "router": ("protocolos de roteamento (FRR)", {
            "bgp": ("BGP", "<AS>"),
            "ospf": ("OSPF", None),
            "isis": ("IS-IS", ARG_WORD),
        }),
        "interface": ("configuracao de interface (FRR)", ARG_WORD),
        "exit": ("volta ao modo anterior", None),
    },
    "pool": CGNAT_POOL,
}


def tree_walk(mode, words):
    """
    Onde estamos na arvore depois de `words`.

    Devolve (node, hint): node e o dicionario de opcoes seguintes, ou None
    quando o comando ja terminou; hint e a dica de argumento, se houver.
    """
    node = TREE.get(mode)
    hint = None
    for w in words:
        if not isinstance(node, dict):
            return None, hint
        entry = node.get(w)
        if entry is None:
            # palavra desconhecida: daqui para frente quem sabe e o FRR
            return None, None
        _, sub = entry
        if isinstance(sub, str):
            return None, sub
        node = sub
        hint = None
    return node, hint


class Vpp:
    """Conversa com o plugin bpa pela CLI do VPP."""

    def run(self, cmd):
        try:
            p = subprocess.run([VPPCTL] + shlex.split(cmd),
                               capture_output=True, text=True, timeout=10)
        except FileNotFoundError:
            return 1, f"{VPPCTL} nao encontrado"
        except subprocess.TimeoutExpired:
            return 1, "VPP nao respondeu"
        out = ((p.stdout or "") + (p.stderr or "")).rstrip()
        first = out.split("\n")[0] if out else ""
        return (1 if VPP_ERROR.match(first) else 0), out


class Frr:
    """Repassa ao vtysh, repetindo o contexto de bloco."""

    def run_show(self, cmd):
        return self._exec(["-c", cmd])

    def run_config(self, ctx, cmd):
        args = ["-c", "configure terminal"]
        for c in ctx:
            args += ["-c", c]
        args += ["-c", cmd]
        return self._exec(args)

    def _exec(self, args):
        try:
            p = subprocess.run([VTYSH] + args, capture_output=True,
                               text=True, timeout=15)
        except FileNotFoundError:
            return 1, f"{VTYSH} nao encontrado"
        except subprocess.TimeoutExpired:
            return 1, "vtysh nao respondeu"
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).rstrip()


class Pool:
    def __init__(self, name):
        self.name = name
        self.inside = []
        self.outside = []
        self.block_size = 512
        self.port_min = 1024
        self.port_max = 65535
        self.policy = "interleaved"
        self.mode = "deterministic"

    def vpp_command(self):
        parts = ["bpa pool", self.name]
        for p in self.inside:
            parts += ["inside", p]
        for p in self.outside:
            parts += ["outside", p]
        parts += ["block", str(self.block_size)]
        parts += ["ports", f"{self.port_min}-{self.port_max}"]
        parts += ["policy", self.policy]
        parts += ["mode", self.mode]
        return " ".join(parts)

    def config_lines(self):
        out = [f"cgnat pool {self.name}"]
        for p in self.inside:
            out.append(f" inside {p}")
        for p in self.outside:
            out.append(f" outside {p}")
        out.append(f" block-size {self.block_size}")
        if (self.port_min, self.port_max) != (1024, 65535):
            out.append(f" ports {self.port_min} {self.port_max}")
        out.append(f" policy {self.policy}")
        out.append(f" mode {self.mode}")
        out.append(" exit")
        return out


class Shell:
    def __init__(self):
        self.dp = Vpp()             # dataplane
        self.frr = Frr()
        self.mode = "view"          # view | enable | config | pool
        self.pool = None            # Pool em edicao
        self.frr_ctx = []           # contexto de bloco do FRR
        self.failed = 0

    # ---------------------------------------------------------- prompt

    def prompt(self):
        return {
            "view": "hexarouting> ",
            "enable": "hexarouting# ",
            "config": "hexarouting(config)# ",
            "pool": "hexarouting(config-cgnat-pool)# ",
        }[self.mode]

    # ---------------------------------------------------------- helpers

    def err(self, msg):
        print(f"%% {msg}")
        self.failed += 1

    def vpp(self, cmd, quiet=False):
        """Executa no dataplane. Imprime a saida e devolve 0 se deu certo."""
        rc, out = self.dp.run(cmd)
        if out and not quiet:
            print(out)
        if rc:
            self.failed += 1
        return rc

    # ---------------------------------------------------------- modos

    def do_view(self, line):
        w = line.split()
        if w[0] == "enable":
            self.mode = "enable"
        elif w[0] in ("exit", "quit", "logout"):
            return False
        else:
            self.do_show_or_pass(line)
        return True

    def do_enable(self, line):
        w = line.split()
        if line in ("configure terminal", "conf t", "configure"):
            self.mode = "config"
        elif w[0] in ("exit", "quit", "logout"):
            self.mode = "view"
        elif line in ("write memory", "write", "wr"):
            self.write_memory()
        elif w[0] == "disable":
            self.mode = "view"
        else:
            self.do_show_or_pass(line)
        return True

    def do_config(self, line):
        w = line.split()

        if w[0] in ("exit", "quit", "end"):
            if self.frr_ctx:
                self.frr_ctx.pop()
            else:
                self.mode = "enable"
            return True

        if line.startswith("cgnat pool "):
            self.pool = Pool(w[2])
            self.mode = "pool"
            return True

        if line.startswith("no cgnat pool "):
            self.vpp(f"bpa pool {w[3]} del")
            return True

        if line.startswith("cgnat interface "):
            return self.cgnat_interface(w)

        if line.startswith("cgnat block-holddown "):
            self.vpp(f"bpa block-holddown {w[2]}")
            return True

        if line.startswith("cgnat timeout "):
            # cgnat timeout udp 300  ->  bpa timeouts udp 300
            self.vpp(f"bpa timeouts {w[2]} {w[3]}")
            return True

        if line.startswith("cgnat logging "):
            self.vpp(f"bpa logging {w[2]}")
            return True

        # tudo mais e roteamento: vai para o FRR com o contexto atual
        rc, out = self.frr.run_config(self.frr_ctx, line)
        if out:
            print(out)
        if rc == 0 and line.startswith(FRR_BLOCKS):
            self.frr_ctx.append(line)
        return True

    def cgnat_interface(self, w):
        # cgnat interface <if> <inside|outside> [disable]
        if len(w) < 4:
            self.err("uso: cgnat interface <interface> <inside|outside>")
            return True
        ifname, direction = w[2], w[3]
        if direction not in ("inside", "outside"):
            self.err("direcao deve ser 'inside' ou 'outside'")
            return True
        disable = len(w) > 4 and w[4] == "disable"
        d = "in2out" if direction == "inside" else "out2in"
        cmd = f"set interface bpa {d} {ifname}" + (" del" if disable else "")
        self.vpp(cmd)
        return True

    def do_pool(self, line):
        w = line.split()
        p = self.pool

        if w[0] in ("exit", "end"):
            return self.apply_pool()

        try:
            if w[0] == "inside":
                p.inside.append(w[1])
            elif w[0] == "outside":
                p.outside.append(w[1])
            elif w[0] == "block-size":
                p.block_size = int(w[1])
            elif w[0] == "ports":
                p.port_min, p.port_max = int(w[1]), int(w[2])
            elif w[0] == "policy":
                if w[1] not in ("interleaved", "blocked"):
                    raise ValueError("policy deve ser interleaved ou blocked")
                p.policy = w[1]
            elif w[0] == "mode":
                if w[1] not in ("deterministic", "dynamic"):
                    raise ValueError("mode deve ser deterministic ou dynamic")
                p.mode = w[1]
            else:
                self.err(f"comando desconhecido no modo pool: {w[0]}")
        except (IndexError, ValueError) as e:
            self.err(f"argumento invalido: {e}")
        return True

    def apply_pool(self):
        """
        O pool so vai para o VPP no 'exit'.

        O plugin recebe a definicao inteira num comando so, enquanto a VTY a
        constroi linha a linha. Aplicar a cada linha criaria pools
        incompletos e invalidos; por isso a aplicacao e no fechamento do
        bloco, e um erro deixa o pool fora da configuracao.
        """
        p = self.pool
        self.mode = "config"
        self.pool = None

        if not p.inside or not p.outside:
            self.err(f"pool '{p.name}': faltam prefixos inside/outside; "
                     f"nao aplicado")
            return True

        # Reaplicar um pool existente: remove antes, para a definicao nova
        # substituir a antiga em vez de colidir por nome.
        # Sem estado entre invocacoes, nao da para saber se o pool ja existe:
        # tenta remover e ignora o erro de "nao existe".
        self.dp.run(f"bpa pool {p.name} del")

        rc, out = self.dp.run(p.vpp_command())
        if rc:
            print(out)
            self.err(f"pool '{p.name}' recusado pelo dataplane")
            return True

        return True

    # ---------------------------------------------------------- show

    def do_show_or_pass(self, line):
        m = {
            "show cgnat pool": "show bpa pool",
            "show cgnat session": "show bpa session",
            "show cgnat blocks": "show bpa blocks",
            "show cgnat logging": "show bpa logging",
        }
        if line in m:
            self.vpp(m[line])
            return
        if line in ("show running-config", "show run"):
            self.show_running()
            return
        rc, out = self.frr.run_show(line)
        if out:
            print(out)

    def running_config(self):
        """
        Le a configuracao do dataplane, nao da memoria.

        A VTY roda como processo curto: cada 'hexash -c ...' e um processo
        novo, entao estado em memoria nao sobrevive entre invocacoes. O
        dataplane e a fonte da verdade, do mesmo jeito que no vtysh os
        daemons e que sabem a propria configuracao.
        """
        rc, out = self.dp.run("show bpa config")
        lines = ["!", "! HexaRouting", "!"]
        if rc == 0 and out.strip():
            lines += [l for l in out.split("\n") if l.strip()]
        lines.append("!")
        return lines

    def show_running(self):
        print("\n".join(self.running_config()))
        rc, out = self.frr.run_show("show running-config")
        if out:
            print(out)

    def write_memory(self):
        d = os.path.dirname(CONFIG_PATH)
        try:
            if d:
                os.makedirs(d, exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                f.write("\n".join(self.running_config()) + "\n")
        except OSError as e:
            self.err(f"nao consegui gravar {CONFIG_PATH}: {e}")
            return
        print(f"configuracao do CGNAT gravada em {CONFIG_PATH}")
        rc, out = self.frr._exec(["-c", "write memory"])
        if out:
            print(out)


    # ---------------------------------------------------------- completacao

    def _options(self, line, ending_word):
        """
        Opcoes validas no ponto atual da linha.

        `ending_word` diz se o cursor esta no meio de uma palavra: nesse caso
        a ultima palavra e o prefixo a completar, nao um passo ja dado.
        """
        words = line.split()
        prefix = ""
        if ending_word and words:
            prefix = words[-1]
            words = words[:-1]

        node, hint = tree_walk(self.mode, words)
        if node is None:
            # Fora da arvore: ou o comando terminou, ou dali em diante quem
            # sabe e o FRR. Mostramos a dica de argumento quando existe.
            return {}, hint, prefix
        opts = {k: v[0] for k, v in node.items() if k.startswith(prefix)}
        return opts, hint, prefix

    def complete(self, text, state):
        """Completador do readline."""
        if state == 0:
            line = readline.get_line_buffer()
            ending = bool(line) and not line.endswith(" ")
            opts, hint, _ = self._options(line, ending)
            self._last_opts = opts
            self._last_hint = hint
            self._matches = sorted(opts)
            if not opts and hint:
                # Sem opcoes o readline nao chama o hook de exibicao, e a dica
                # de argumento nunca apareceria. Duas entradas sem prefixo
                # comum forcam a exibicao sem inserir nada na linha - quem
                # imprime e o hook, que mostra so a dica.
                self._matches = [hint, "\u200b"]
        try:
            m = self._matches[state]
        except IndexError:
            return None
        # dica de argumento nao e completacao: nunca entra na linha
        if not self._last_opts:
            return m
        return m + " "

    def display_matches(self, substitution, matches, longest):
        """
        Mostra as opcoes com descricao, no estilo que operador espera.

        O readline sozinho imprime so as palavras em colunas; a descricao ao
        lado e o que torna o "?" util para quem nao decorou a gramatica.
        """
        opts = getattr(self, "_last_opts", {})
        print()
        if opts:
            width = max(len(m) for m in opts)
            for m in sorted(opts):
                print(f"  {m:<{width}}  {opts[m]}")
        hint = getattr(self, "_last_hint", None)
        if hint:
            print(f"  {hint}")
        if not opts and not hint:
            print("  (nenhuma opcao conhecida aqui; o comando segue para o FRR)")
        print()
        # redesenha o prompt com o que ja estava digitado
        sys.stdout.write(self.prompt() + readline.get_line_buffer())
        sys.stdout.flush()

    # ---------------------------------------------------------- loop

    def feed(self, line):
        line = line.split("!")[0].strip()
        line = re.sub(r"\s+", " ", line)
        if not line:
            return True
        handler = {"view": self.do_view, "enable": self.do_enable,
                   "config": self.do_config, "pool": self.do_pool}[self.mode]
        return handler(line)

    def load(self, path):
        if not os.path.exists(path):
            return
        self.mode = "config"
        with open(path) as f:
            for line in f:
                self.feed(line)
        self.mode = "enable"

    def repl(self):
        global readline
        try:
            import readline
        except ImportError:
            readline = None
        if readline:
            readline.set_completer(self.complete)
            readline.set_completer_delims(" \t\n")
            readline.parse_and_bind("tab: complete")
            # "?" mostra as opcoes sem inserir o caractere, como no Cisco
            readline.parse_and_bind('"?": possible-completions')
            # duas tabulacoes ja listam por padrao; o hook so acrescenta a
            # descricao ao lado de cada opcao
            try:
                readline.set_completion_display_matches_hook(
                    self.display_matches)
            except AttributeError:
                pass  # readline antigo: fica so a lista, sem descricao
        while True:
            try:
                line = input(self.prompt())
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not self.feed(line):
                return 0


def main():
    sh = Shell()
    args = sys.argv[1:]

    if args and args[0] == "--load":
        sh.load(args[1] if len(args) > 1 else CONFIG_PATH)
        return sh.failed and 1 or 0

    cmds = []
    while args:
        if args[0] == "-c":
            cmds.append(args[1]); args = args[2:]
        else:
            args = args[1:]

    if cmds:
        sh.mode = "enable"
        for c in cmds:
            sh.feed(c)
        return 1 if sh.failed else 0

    print("HexaRouting VTY. '?' nao implementado ainda; use 'exit' para sair.")
    return sh.repl()


if __name__ == "__main__":
    sys.exit(main())
