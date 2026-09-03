"""The tool catalogue and the home's notes, as plain data.

This used to be HTML written by hand inside `dashboard.py`, which tied the
menu to a single markup -- Folha's. Here it is content only: each direction
reads this list and emits its own structure (a numbered table in Folha,
clipped cards in Caderno, wire-coloured terminal blocks in Régua).

`short` exists because Caderno's mockup uses short labels on its dividers
("Comparador", "Exportador") while Folha uses the full ones; `hint` is the
subtitle only Régua shows, inside the terminal block.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    """Uma ferramenta do toolkit, do ponto de vista do menu e da navegacao."""

    key: str            # o mesmo slug que o handler usa pra se marcar ativo
    group: str          # a key de um Group. OBRIGATORIO: um default silencioso
                        # would put a new tool in the wrong group unseen
    href: str | None  # None = ainda nao existe, aparece desabilitada
    name: str           # nome completo (folha, regua)
    short: str          # nome curto (caderno)
    hint: str           # subtitulo de uma linha (regua)
    does: str           # o que faz, uma frase (todas as direcoes)
    takes: str          # o que precisa receber
    note: int = 0       # 1..4 -> ancora numa nota de rodape, 0 = nenhuma

    @property
    def shipping(self) -> bool:
        return self.href is not None


@dataclass(frozen=True)
class Group:
    """One menu group: the manufacturer a tool serves, or the absence of one.

    The axis is NOT brand by brand -- it is what the tool EATS. A tool that
    reads an SCD (IEC 61850) serves GE and Siemens alike; one that reads a
    QuickSet RDB serves only SEL. That is why `eats` travels with the group:
    it is what turns an empty section into a roadmap.
    """

    key: str      # o slug, usado por Tool.group e pelos testes
    name: str     # nome completo (folha, regua)
    short: str    # nome curto (caderno, capa da regua)
    eats: str     # o que este grupo le, uma linha
    empty: str = ""   # o que dizer quando o grupo nao tem ferramenta


# Group order is menu order, and the 1..9 numbering runs over it: generic
# first, specific after. GE and Siemens are declared and EMPTY on purpose --
# see the 2026-09-01 design note.
GROUPS: list[Group] = [
    Group("geral", "Independentes de fabricante", "Geral",
          "SCD (IEC 61850) — serve qualquer relé"),
    Group("sel", "SEL", "SEL",
          "RDB do AcSELerator QuickSet"),
    Group("ge", "GE", "GE",
          "EnerVista — ajustes .urs",
          "Nenhuma ferramenta ainda. É aqui que a leitura de ajustes do "
          "EnerVista entra."),
    Group("siemens", "Siemens", "Siemens",
          "DIGSI",
          "Nenhuma ferramenta ainda."),
]

GROUP_ORDER: list[str] = [g.key for g in GROUPS]


TOOLS: list[Tool] = [
    # -- independentes de fabricante -----------------------------------------
    Tool("vlan-mapper", "geral", "/vlan-mapper/",
         "VLAN Mapper", "VLAN Mapper",
         "GOOSE por porta",
         "VLANs de GOOSE (subscritas e publicadas) que precisam estar "
         "liberadas na porta do switch.",
         "SCD"),
    Tool("relatorio", "geral", None,
         "Relatório de Comissionamento", "Relatório",
         "em breve",
         "Checklist dos testes funcionais por baia, com fotos e "
         "oscilografias.",
         "—"),
    # -- SEL ------------------------------------------------------------------
    Tool("glv", "sel", "/glv/",
         "Visualizador de Lógica", "Lógica ao vivo",
         "estado do relé sobre o GLE",
         "Estado do relé ao vivo sobre o diagrama GLE do AcSELerator QuickSet.",
         "RDB + telnet", note=1),
    Tool("settings-compare", "sel", "/settings-compare/",
         "Comparador de Ajustes", "Comparador",
         "até 7 relés lado a lado",
         "Até 7 relés da mesma família (3xx/4xx/7xx) lado a lado; detecta "
         "equações equivalentes por álgebra booleana.",
         "RDB", note=3),
    Tool("vb-updater", "sel", "/vb-updater/",
         "VB Updater", "VB Updater",
         "virtual bits GLE ↔ SCD",
         "Compara descrições de Virtual Bits entre o GLE do RDB e o SCD, por "
         "relé/IED.",
         "RDB + SCD"),
    Tool("gle-exporter", "sel", "/gle-exporter/",
         "Exportador de Comentários GLE", "Exportador",
         "Excel, ida e volta",
         "Comentários de porta em Excel, uma aba por GLE; edite e reimporte "
         "para gerar um RDB atualizado.",
         "RDB + XLSX"),
    Tool("dnp-map", "sel", "/dnp-map/",
         "Editor de Mapa DNP", "Mapa DNP",
         "pontos DNP3 do relé",
         "Edita os pontos DNP3 (SET_D) de cada relé do RDB e gera um RDB "
         "novo com as alterações aplicadas.",
         "RDB"),
    Tool("rdb-scd", "sel", None,
         "Comparador RDB ↔ SCD", "RDB ↔ SCD",
         "em breve",
         "Cruza ajustes do RDB com IEDs do SCD por IP e RID; reporta "
         "divergências e itens órfãos de cada lado.",
         "RDB + SCD"),
    Tool("validador", "sel", None,
         "Validador de Ajustes", "Validador",
         "em breve",
         "Valida pickups, tempos e polarizações contra o template do projeto.",
         "RDB"),
]


def tools_of(group: str) -> list[Tool]:
    """A group's tools, in catalogue order. An empty list is a legitimate answer:
    GE and Siemens are declared and have no tool yet."""
    return [t for t in TOOLS if t.group == group]


# Each tool's ordinal, 1..9, in group order. ONE source, and this is it: six
# renderers print this number (Régua's rail and its cards among them), and an
# `enumerate()` per renderer over a list four of them additionally filter is
# exactly how the two sides drift apart.
ORDINAL: dict[str, int] = {t.key: i for i, t in enumerate(TOOLS, start=1)}


# The navigation rail's "Menu" item. Régua does not use it: there the home is
# reached through the "← Menu" in the top bar, as in the mockup.
MENU_ITEM = ("menu", "/", "Menu", "Menu", "todas as ferramentas")

# The project files tab. Like "Menu", it is NOT a tool: it is the way in, the
# only screen that accepts an RDB or an SCD. Kept out of TOOLS on purpose --
# it has no "does" or "takes" to declare, and counting it as a tool would
# spoil the home's numbers.
FILES_ITEM = ("files", "/files/", "Arquivos do Projeto",
              "Arquivos", "RDB e SCD do projeto")

# The home's footnotes. Folha anchors them in the margin column by number;
# Caderno writes them by hand below the content; Régua flattens them into the
# footbar. The text is the same in all three.
NOTES: list[str] = [
    "Exige telnet liberado até o relé. O banner de login é drenado antes da "
    "sessão Fast Message: relés 3xx anunciam quatro linhas e estouram as "
    "cinco tentativas da biblioteca.",
    "Cada visitante tem sessão e diretório próprios. Dois usuários podem "
    "subir <b>projeto.rdb</b> ao mesmo tempo sem se sobrescrever; a sessão "
    "expira depois de 8 h de ociosidade e o diretório dela é apagado.",
    "O comparador exige relés da <b>mesma família</b>: os catálogos de grupos "
    "de 3xx, 4xx e 7xx não são equivalentes.",
    "O Visualizador de Lógica é compartilhado de propósito: ele fala com "
    "<b>um</b> relé físico por vez, então todo mundo vê o mesmo diagrama ao "
    "vivo.",
]

# Chamada de abertura do menu. Uma frase, contada a partir dos dados acima.
SHIPPING = sum(1 for t in TOOLS if t.shipping)
PLANNED = len(TOOLS) - SHIPPING
