"""O catalogo de ferramentas e as notas da home, como dados puros.

Antes isto era HTML escrito a mao dentro de `dashboard.py`, o que amarrava o
menu a uma unica marcacao -- a da folha. Aqui e' so conteudo: cada direcao le
esta lista e emite a estrutura dela (tabela numerada na folha, fichas com
clipe no caderno, bornes com cor de fio na regua).

`short` existe porque o mockup do caderno usa rotulos curtos nas divisorias
("Comparador", "Exportador") e a folha usa os completos; `hint` e' o subtitulo
que so a regua mostra, dentro do borne.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    """Uma ferramenta do toolkit, do ponto de vista do menu e da navegacao."""

    key: str            # o mesmo slug que o handler usa pra se marcar ativo
    group: str          # a key de um Group. OBRIGATORIO: um default silencioso
                        # poe a ferramenta nova no grupo errado sem ninguem ver
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
    """Um grupo do menu: o fabricante que a ferramenta serve, ou a ausencia de
    um.

    O eixo NAO e' marca por marca -- e' o que a ferramenta come. Quem le SCD
    (IEC 61850) serve GE e Siemens igual, quem le RDB do QuickSet so serve SEL.
    Por isso `eats` viaja junto: e' o que transforma uma secao vazia em roteiro.
    """

    key: str      # o slug, usado por Tool.group e pelos testes
    name: str     # nome completo (folha, regua)
    short: str    # nome curto (caderno, capa da regua)
    eats: str     # o que este grupo le, uma linha
    empty: str = ""   # o que dizer quando o grupo nao tem ferramenta


# A ordem dos grupos e' a ordem do menu, e a numeracao 1..9 corre por cima
# dela: do generico ao especifico. GE e Siemens entram declarados e VAZIOS de
# proposito -- ver o design de 2026-09-01.
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
    """As ferramentas de um grupo, na ordem do catalogo. Lista vazia e' uma
    resposta legitima: GE e Siemens estao declarados e ainda sem ferramenta."""
    return [t for t in TOOLS if t.group == group]


# O ordinal de cada ferramenta, 1..9, na ordem dos grupos. UMA fonte, e e'
# esta: seis renderizadores imprimem este numero (a tira e as fichas da regua
# entre eles), e um `enumerate()` por renderizador sobre uma lista que quatro
# deles ainda filtram e' exatamente como os dois lados se separam.
ORDINAL: dict[str, int] = {t.key: i for i, t in enumerate(TOOLS, start=1)}


# O item "Menu" da tira de navegacao. A regua nao o usa: la a home se alcanca
# pelo "← Menu" da barra superior, como no mockup.
MENU_ITEM = ("menu", "/", "Menu", "Menu", "todas as ferramentas")

# A aba dos arquivos do projeto. Como o "Menu", NAO e' uma ferramenta: e' a
# superficie de entrada, a unica tela que aceita um RDB ou um SCD. Fora de
# TOOLS de proposito -- ela nao tem "does" nem "takes" pra declarar, e contar
# como ferramenta estragaria os numeros da home.
FILES_ITEM = ("files", "/files/", "Arquivos do Projeto",
              "Arquivos", "RDB e SCD do projeto")

# As notas de rodape da home. A folha as ancora na coluna de margem pelo
# numero; o caderno as escreve a mao embaixo do conteudo; a regua as achata na
# footbar. O texto e' o mesmo nas tres.
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
