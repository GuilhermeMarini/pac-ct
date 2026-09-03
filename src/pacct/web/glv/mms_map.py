"""Bit da Relay Word -> item MMS, conferido contra o proprio rele.

Duas fontes, nesta ordem: o SCD do projeto (o mapa COMO CONSTRUIDO) e a tabela
de fabrica derivada do ICD. Nenhuma das duas e' descoberta no rele -- o nome do
bit mora no `sAddr` do SCL, que o rele NAO serve por MMS. Nao existe aqui o
equivalente do `TAR <nome>` do telnet, e nunca vai existir.

O FC, esse sim vem do rele: casamos `LN$*$DO$DA` contra o
GetLogicalDeviceDirectory. Isso resolve o FC e confere a entrada de uma vez so.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from pacct.core.mms_tables import (
    da_parts,
    fc_rank,
    is_boolean_status,
    is_enum_do,
    is_enum_status,
)


@dataclass(frozen=True)
class MmsPoint:
    bit: str
    ld: str            # nome completo do logical device, ex. QPC1_TFE_UPC1ANN
    container: str     # LN$FC -- a unidade de leitura
    child: str         # o DO dentro do container
    item: str          # LN$FC$DO$DA
    # O caminho ATE A FOLHA dentro do filho, ex. ("stVal",) ou ("general",).
    # Sem ele o decodificador so' sabia ler `stVal`, e um ponto de ACD/ACT --
    # 43 dos 222 bits enderecaveis do LT2_UPC1 medidos no corpus, o `TRIP`
    # entre eles -- entrava na cobertura e NUNCA era lido. Nao tem default de
    # proposito: um ponto construido sem dizer o que le' e' exatamente o bug.
    leaf: tuple
    # Como tirar ESTE bit do valor do item, quando o item carrega mais de um.
    # `None` num ponto booleano (a maioria), e ai o valor lido e' o bit. Num
    # ponto decorado -- `db:52A|52B?0:1:2:3` num `Pos$stVal` -- e' a `BitRule`
    # que `mms_tables.decode_bit` aplica. O laco de polling nunca faz
    # `int(bool(...))` num ponto com regra: um Dbpos volta da py61850 como a
    # string "10", e `bool("00")` e' True.
    rule: object | None = None


@dataclass(frozen=True)
class Coverage:
    total: int
    mapped: int
    missing: tuple

    @property
    def fraction(self) -> float:
        return (self.mapped / self.total) if self.total else 0.0


@dataclass
class MmsMap:
    points: dict = field(default_factory=dict)
    source: str = ""

    def containers(self) -> dict:
        """`(ld, LN$FC) -> [MmsPoint]` -- como o mapa se espalha pelo rele.

        Ja foi o plano de leitura: agrupar por container era o que fazia o
        polling caber no orcamento quando cada leitura custava uma requisicao
        (30 req / ~180 ms pro diagrama inteiro contra 170 / 739 ms bit a bit,
        medido no 451 da bancada). Com o `read_refs` da py61850 -- uma Read
        nomeando varias folhas -- o custo deixou de ser contado em containers,
        e o plano e' a lista de folhas (ver `transport/mms.py`). Isto aqui
        sobrou como DIAGNOSTICO: quantos `LN$FC` o mapa toca e quais.
        """
        out: dict = {}
        for p in self.points.values():
            out.setdefault((p.ld, p.container), []).append(p)
        return out

    def coverage(self, wanted) -> Coverage:
        wanted = {b.upper() for b in wanted}
        missing = tuple(sorted(b for b in wanted if b not in self.points))
        return Coverage(total=len(wanted), mapped=len(wanted) - len(missing),
                        missing=missing)


def ld_suffixes(lds, suffixes=None) -> dict:
    """`sufixo -> nome completo`.

    Quando `suffixes` e' dado -- os `ld_inst` do SCD e/ou o `ld_suffix` da
    tabela de fallback, ou seja, exatamente os sufixos que quem chama ja'
    sabe que esta' procurando -- casa cada LD por `endswith`, sufixo mais
    longo primeiro. A ordem importa: sem ela, `CON` seria sombreado por
    `ON` sempre que os dois estiverem na lista de sufixos conhecidos.

    Sem `suffixes` -- nenhum chamador hoje usa este caminho, mantido so'
    pra nao devolver lixo -- cai pro prefixo comum entre varios LD, e pra
    um LD so' devolve a identidade: nao ha' um segundo nome pra comparar,
    e adivinhar seria mentir.
    """
    lds = list(lds)
    if not lds:
        return {}
    if suffixes:
        ordered = sorted({s for s in suffixes if s}, key=len, reverse=True)
        out: dict = {}
        for ld in lds:
            for suf in ordered:
                if ld.endswith(suf):
                    out[suf] = ld
                    break
        return out
    if len(lds) == 1:
        return {lds[0]: lds[0]}
    prefix = os.path.commonprefix(lds)
    return {ld[len(prefix):] or ld: ld for ld in lds}


def _fc_index(names) -> dict:
    """`(ln, "do$da_com_$")  -> [fc, ...]` -- um indice do diretorio inteiro,
    montado uma vez por conjunto de nomes.

    Casar contra uma lista fixa de FC (o que a Task 5 original fazia) so'
    encontra os FC que estao naquela lista; o diretorio e' quem manda. `SG`
    (grupo de ajuste, 186 pontos no corpus) nunca estaria em FC_PREFERENCE
    e ainda assim e' um FC real que o rele serve -- essa inversao resolve
    qualquer FC presente no diretorio, catalogado ou nao.
    """
    index: dict = {}
    for name in names:
        parts = name.split("$")
        if len(parts) < 4:
            continue  # sobra so' container (LN$FC) ou LN$FC$DO, sem DA
        ln, fc = parts[0], parts[1]
        rest = "$".join(parts[2:])  # "DO$DA" ou "DO$SDI$DA" achatado
        index.setdefault((ln, rest), []).append(fc)
    return index


def _readable(sp) -> bool:
    """Este `ScdPoint` da' pra ler como bit?

    Sem regra, a folha tem que ser um status booleano. Com regra, um status
    enumerado -- e' o valor do enumerado que carrega os bits. Uma regra nao
    resgata um float nem um comando: a decoracao diz COMO decodificar um
    enumerado, nao transforma o que nao e' leitura em leitura.
    """
    rule = getattr(sp, "rule", None)
    if rule is None:
        # `is_enum_do` e' o que impede um `Pos$stVal` de entrar pelo caminho
        # booleano: o DA sozinho nao separa um SPS de um DPS.
        return is_boolean_status(sp.da) and not is_enum_do(sp.do)
    return is_enum_status(sp.da)


def _rule_from_table(entry):
    """A `BitRule` de uma linha da tabela de fabrica, ou `None`.

    A linha e' JSON, entao a regra chega como lista -- `[alternativas, indice,
    nbits]` -- e volta a ser a mesma `BitRule` que o SCD produz. Uma linha
    malformada e' `None`, ou seja, o ponto cai pro portao booleano em vez de
    virar leitura inventada.
    """
    if len(entry) < 3 or not entry[2]:
        return None
    from pacct.core.mms_tables import BitRule
    try:
        alternatives, index, nbits = entry[2]
        return BitRule(alternatives=tuple(int(a) for a in alternatives),
                       index=int(index), nbits=int(nbits))
    except (TypeError, ValueError):
        return None


def resolve_map(*, wanted, directory, ld_by_suffix, scd_points=None,
                table=None) -> MmsMap:
    """Monta o mapa dos bits pedidos. So entra o que o rele confirma servir."""
    wanted = {b.upper() for b in wanted}
    scd_points = scd_points or {}
    points: dict = {}
    used_scd = used_table = False
    fc_index_by_suffix: dict = {}

    def _fc_candidates(ld_inst: str, ln: str, do: str, da_path: str) -> list:
        idx = fc_index_by_suffix.get(ld_inst)
        if idx is None:
            idx = _fc_index(directory.get(ld_inst) or ())
            fc_index_by_suffix[ld_inst] = idx
        return idx.get((ln, f"{do}${da_path}"), [])

    for bit in wanted:
        sp = scd_points.get(bit)
        # So' entra o que da' pra PINTAR como bit. Sem regra, a folha tem que
        # ser um status BOOLEANO: um `instMag.f` e' float, um `actVal` e'
        # contador, um `Oper.ctlVal` e' COMANDO -- `int(bool(x))` de qualquer
        # um deles nao e' leitura de bit, e' invencao. Fora isso a cobertura
        # mentiria duas vezes: contando o ponto como coberto e deixando o
        # `state.error` travado em "leitura parcial" enquanto tudo esta bem.
        #
        # COM regra (`db:52A|52B?0:1:2:3`), a folha tem que ser um status
        # ENUMERADO -- e' o valor do enumerado que carrega os bits. Exigir a
        # regra e' o portao: `Pos$stVal` tambem passa em `is_boolean_status`,
        # entao um ponto de posicao sem regra entraria pelo caminho booleano e
        # sairia como disjuntor fechado para sempre.
        if sp is not None and _readable(sp):
            ld = ld_by_suffix.get(sp.ld_inst)
            # `sAddr`'s `da` usa '.' pra descer num SDI aninhado (Task 2,
            # ex. "Oper.ctlVal"); MMS junta TODO nivel com '$'. Sem esta
            # troca "RBGGIO1$CO$SPCSO01$Oper.ctlVal" nunca bate contra o
            # diretorio, e o drop seria indistinguivel de "o rele nao serve".
            # Hoje o filtro booleano acima so' deixa passar folha de UM nivel,
            # entao a juncao e' identidade -- ela fica porque quem pode mudar
            # e' o filtro, e nao a regra de como o MMS soletra um nome.
            da_path = "$".join(da_parts(sp.da))
            candidates = _fc_candidates(sp.ld_inst, sp.ln, sp.do,
                                        da_path) if ld else []
            fc = min(candidates, key=fc_rank) if candidates else None
            if fc:
                points[bit] = MmsPoint(
                    bit=bit, ld=ld, container=f"{sp.ln}${fc}", child=sp.do,
                    item=f"{sp.ln}${fc}${sp.do}${da_path}",
                    leaf=da_parts(sp.da), rule=getattr(sp, "rule", None))
                used_scd = True
                continue
        # Um bit cujo endereco no SCD nao e' legivel ainda pode ter um
        # endereco util na tabela de fabrica: cair pra ela e' melhor que
        # descartar o bit por causa da primeira fonte.
        if table is None:
            continue
        entry = table.bits.get(bit)
        if entry is None:
            continue
        # A tabela guarda `[sufixo, item]` ou, num ponto decorado,
        # `[sufixo, item, [alternativas, indice, nbits]]`. As linhas de dois
        # elementos sao a esmagadora maioria e continuam valendo como estao --
        # o terceiro e' opcional pra nao invalidar as tabelas ja publicadas.
        suffix, item = entry[0], entry[1]
        rule = _rule_from_table(entry)
        ld = ld_by_suffix.get(suffix)
        names = directory.get(suffix) or ()
        if not ld or item not in names:
            continue
        parts = item.split("$")
        if len(parts) < 4:
            continue                      # sem DA nao ha' o que ler
        ln, fc, do = parts[0], parts[1], parts[2]
        leaf = tuple(parts[3:])
        if not (is_enum_status(leaf) if rule is not None
                else (is_boolean_status(leaf) and not is_enum_do(do))):
            continue
        points[bit] = MmsPoint(bit=bit, ld=ld, container=f"{ln}${fc}",
                               child=do, item=item, leaf=leaf, rule=rule)
        used_table = True

    source = ("scd+tabela" if used_scd and used_table
              else "scd" if used_scd else "tabela" if used_table else "")
    return MmsMap(points=points, source=source)
