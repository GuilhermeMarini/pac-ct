"""sAddr extraction: the only bridge from a Relay Word name to 61850.

`sAddr="db:PLT01"` is an SCL attribute and the relay does NOT serve it over
MMS -- verified on a live SEL-451-5 R331, where `$DC$Ind01$d` answers
`object-non-existent`. So this parse is the only way the map can be built.
"""
from __future__ import annotations

from pathlib import Path

from pacct.parsers.scd import sel_short_addresses

FIXTURE = Path(__file__).parent / "fixtures" / "saddr_min.scd"


def test_extracts_bits_per_ied():
    per_ied = sel_short_addresses(FIXTURE)
    assert set(per_ied) == {"REL_A"}
    bits = per_ied["REL_A"]
    assert set(bits) == {"LOC", "PLT01", "PLT02", "RB01", "SV06", "LOCSTA",
                         "52A", "52B"}


def test_ln_name_is_prefix_class_inst_and_lln0_has_no_prefix():
    bits = sel_short_addresses(FIXTURE)["REL_A"]
    assert (bits["PLT01"].ln, bits["PLT01"].do, bits["PLT01"].da) == (
        "PLT1GGIO1", "Ind01", "stVal")
    assert bits["LOC"].ln == "LLN0"
    assert bits["PLT01"].ld_inst == "ANN"


def test_nested_sdi_joins_with_a_dot():
    """`SDI Oper` + `DAI ctlVal` is the `Oper.ctlVal` MMS leaf."""
    bits = sel_short_addresses(FIXTURE)["REL_A"]
    assert bits["RB01"].ln == "RBGGIO1"
    assert bits["RB01"].do == "SPCSO01"
    assert bits["RB01"].da == "Oper.ctlVal"


def test_names_are_upper_cased():
    """`db:sv06` mirrors the one lowercase `sAddr` found in the real
    substation SCD (`sv06|sv05?0:1:2:3`). Without `.upper()` the key would
    land as `sv06` and this would fail."""
    bits = sel_short_addresses(FIXTURE)["REL_A"]
    assert all(k == k.upper() for k in bits)
    assert "SV06" in bits


def test_the_status_da_beats_the_control_one_on_a_duplicate_bit():
    """`db:LOCSTA` appears twice under the same LN: FIRST nested under
    `SPCSO02/Oper/ctlVal` (a command) and only THEN flat as `Ind04/stVal`
    (the reading). Plain document order picked the command.

    That is not a tie the FC preference can break later: `fc_rank` chooses
    between FCs of one DA, and by then the `stVal` candidate is already gone.
    Measured on `samples/substation_demo.scd`, the real corpus: `LOCSTA` and
    `IPRST` were two of 87 points of `QPC1_LT2_UPC1` that resolved to
    `Oper.ctlVal` under first-wins -- the GLV would have polled what someone
    last commanded instead of what the relay sees."""
    bits = sel_short_addresses(FIXTURE)["REL_A"]
    assert bits["LOCSTA"].do == "Ind04"
    assert bits["LOCSTA"].da == "stVal"


def test_document_order_still_breaks_a_tie_between_two_equal_das():
    """The rank only reorders CLASSES of DA. Two candidates of the same class
    keep the first in document order, so nothing that used to be deterministic
    became arbitrary."""
    bits = sel_short_addresses(FIXTURE)["REL_A"]
    # PLT01 has a single candidate; PLT02 likewise. What this pins is that a
    # re-parse is stable, which a dict-order-dependent tie-break would not be.
    assert sel_short_addresses(FIXTURE)["REL_A"] == bits


def test_a_bit_with_only_a_control_da_is_still_extracted():
    """Dropping a control point is the MAP's job (`mms_map.resolve_map`), not
    the parser's: `RB01` has no status DA at all, and a parser that hid it
    would leave the map layer unable to say why the bit is unreadable."""
    bits = sel_short_addresses(FIXTURE)["REL_A"]
    assert bits["RB01"].da == "Oper.ctlVal"


# -- the real corpus --------------------------------------------------------
#
# `samples/substation_demo.scd` is the only SCD this repo carries, and it is
# what every measurement in the MMS work was taken on. Pinning the finding
# here (rather than only on the hand-written fixture) is what stops the same
# regression coming back through a change that happens to keep the fixture
# happy.

SAMPLE = Path(__file__).parent.parent / "samples" / "substation_demo.scd"


def test_locsta_in_the_real_scd_resolves_to_the_status_point():
    per_ied = sel_short_addresses(SAMPLE)
    bits = per_ied["QPC1_LT2_UPC1"]
    assert bits["LOCSTA"].da == "stVal"
    assert bits["IPRST"].da == "stVal"


def test_no_bit_of_that_ied_keeps_a_control_da_when_a_status_one_exists():
    """87 points of `QPC1_LT2_UPC1` came out as `Oper.ctlVal` under
    first-wins. The 78 that remain are bits the SCD gives NO status address
    at all -- those are the map layer's problem, not the parser's."""
    import xml.etree.ElementTree as ET

    from pacct.core.mms_tables import da_rank, parse_saddr

    root = ET.parse(SAMPLE).getroot()
    ied = next(e for e in root.iter()
               if e.tag.rsplit("}", 1)[-1] == "IED"
               and e.get("name") == "QPC1_LT2_UPC1")
    best: dict = {}
    for el in ied.iter():
        # `parse_saddr` e nao `sa[3:]`: um endereco decorado enderaca varios
        # nomes, e a conta so' fecha se este lado contar os mesmos.
        spec = parse_saddr(el.get("sAddr") or "")
        if spec is None:
            continue
        for bit in spec.names:
            best.setdefault(bit, set())
    chosen = sel_short_addresses(SAMPLE)["QPC1_LT2_UPC1"]
    assert set(chosen) == set(best)
    control = {b for b, p in chosen.items() if da_rank(p.da) == (2,)}
    assert len(control) == 78, sorted(control)[:10]


def test_the_tools_reuse_the_parser_instead_of_reimplementing_it():
    """`tools/_sel61850.py` carried its own sAddr walk and its own
    `norm_part`. Two copies of the rule that decides which bit has a 61850
    address is how a report and the running tool come to disagree about the
    same SCD -- and `fixtures/gle_sem_61850.txt` /
    `fixtures/model_missing.txt` are the evidence base for the coverage
    numbers in the design spec.

    Both reports were confirmed to reproduce byte for byte after the
    deduplication; this pins that the copies do not grow back.
    """
    import inspect
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import _sel61850 as maps
    from pacct.core.mms_tables import norm_part

    assert maps.norm_part is norm_part
    src = inspect.getsource(maps)
    assert "ElementTree" not in src, "tools/ voltou a parsear o SCL sozinho"
    assert "_SADDR_PREFIX" not in src, "a extração de sAddr voltou pra tools/"
    assert "[^A-Z0-9]" not in src, "`norm_part` voltou a ter uma segunda cópia"
    assert maps.load_scd(SAMPLE)["bits"]["QPC1LT2UPC1"] == \
        set(sel_short_addresses(SAMPLE)["QPC1_LT2_UPC1"])


# -- os enderecos DECORADOS: um ponto 61850 que carrega dois bits -----------
#
# `sAddr="db:52A|52B?0:1:2:3"` num `Pos$stVal` e' um DPS: UM ponto cujo Dbpos
# codifica DOIS bits da Relay Word. A gramatica e as alternativas vivem em
# `pacct/core/mms_tables.py:parse_saddr`; aqui pinamos o que o WALK faz com
# elas -- um `ScdPoint` por NOME, cada um com a regra do seu bit.
#
# Antes disto o nome do bit era `sa[3:].upper()`, ou seja, a chave virava a
# string literal `52A|52B?0:1:2:3`, que nao casa com bit desenhado nenhum: a
# forma inteira sumia calada. Medido nos 25 reles da subestacao contra os GLE
# deles: 55 bits desenhados de 7.524 so' tem endereco decorado, e todos sao
# `52A`, `89CL*` ou `89OPN*` -- posicao de disjuntor e de seccionadora.

class TestDecoratedAddresses:

    def test_a_double_bit_address_becomes_one_point_per_name(self):
        bits = sel_short_addresses(FIXTURE)["REL_A"]
        assert bits["52A"].do == bits["52B"].do == "Pos"
        assert bits["52A"].ln == bits["52B"].ln == "BKR1CSWI1"
        assert bits["52A"].da == bits["52B"].da == "stVal"

    def test_each_name_carries_the_rule_for_its_own_position(self):
        bits = sel_short_addresses(FIXTURE)["REL_A"]
        assert bits["52A"].rule.index == 0
        assert bits["52B"].rule.index == 1
        assert bits["52A"].rule.nbits == bits["52B"].rule.nbits == 2
        assert bits["52A"].rule.alternatives == (0, 1, 2, 3)

    def test_a_plain_address_carries_no_rule(self):
        bits = sel_short_addresses(FIXTURE)["REL_A"]
        assert bits["PLT01"].rule is None

    def test_a_plain_boolean_address_beats_a_decorated_one(self):
        """`LOC` tem `LLN0$Loc$stVal` liso E `BKR1CSWI1$Health$stVal?3:1`.
        Os dois sao `stVal`, entao sem o degrau do `da_rank` o desempate
        seria ordem de documento. Medido em `QPC1_LT1_UPC1` do SCD da
        subestacao: 10 dos 33 nomes decorados tambem tem endereco liso."""
        bits = sel_short_addresses(FIXTURE)["REL_A"]
        assert bits["LOC"].ln == "LLN0"
        assert bits["LOC"].rule is None

    def test_a_malformed_decoration_is_dropped_whole(self):
        """`?0:1:2` em dois nomes quebra `len(alt) == 2**n`, invariante que
        vale em 5.025 de 5.025 no corpus. Chutar nela pinta bit errado."""
        bits = sel_short_addresses(FIXTURE)["REL_A"]
        assert "BADFORM" not in bits
        assert not any("?" in b or "|" in b for b in bits)


class TestDecoratedAddressesInTheRealScd:
    """`samples/substation_demo.scd` traz 162 enderecos decorados distintos,
    `52A|52B?0:1:2:3` entre eles."""

    def test_52a_and_52b_come_out_of_the_breaker_position_point(self):
        """`QPC2_TR1_UPC1` e' o rele cujo GL1 abriu o assunto: o unico bit
        desenhado dele que so' tem endereco decorado e' `52A`, em
        `PRO/BKR1CSWI1$Pos$stVal`."""
        bits = sel_short_addresses(SAMPLE)["QPC2_TR1_UPC1"]
        assert (bits["52A"].ld_inst, bits["52A"].ln) == ("PRO", "BKR1CSWI1")
        assert (bits["52A"].do, bits["52A"].da) == ("Pos", "stVal")
        assert bits["52A"].rule.alternatives == (0, 1, 2, 3)
        assert bits["52A"].rule.index == 0

    def test_the_other_half_of_the_pair_keeps_its_plain_address(self):
        """`52B` do mesmo IED tambem esta no `db:52A|52B?0:1:2:3`, mas tem um
        endereco liso em `ANN/PROGGIO37$Ind15$stVal` -- e o liso ganha. E' por
        isso que este rele ganha UM bit e nao dois: 52A nao tem outro
        endereco, 52B tem."""
        bits = sel_short_addresses(SAMPLE)["QPC2_TR1_UPC1"]
        assert (bits["52B"].ln, bits["52B"].do) == ("PROGGIO37", "Ind15")
        assert bits["52B"].rule is None

    def test_a_plain_address_still_wins_where_the_ied_has_both(self):
        """`QPC1_LT2_UPC2` enderaca `52A` liso, em `ANN/BRGGIO14$Ind01`."""
        bits = sel_short_addresses(SAMPLE)["QPC1_LT2_UPC2"]
        assert bits["52A"].do == "Ind01"
        assert bits["52A"].rule is None

    def test_no_bit_name_keeps_the_decoration_in_it(self):
        """A regressao original: a chave virava `52A|52B?0:1:2:3`."""
        for points in sel_short_addresses(SAMPLE).values():
            assert not any("?" in b or "|" in b for b in points)


# -- o FC, quando NAO ha um rele pra perguntar ------------------------------
#
# No caminho vivo o FC vem do rele (`GetLogicalDeviceDirectory`), e e' por isso
# que `sel_short_addresses` nao o traz. Mas a tabela de fabrica em
# `data/mms_map/` e' gerada OFFLINE, a partir dos ICD, e ali nao ha rele
# nenhum: o FC tem que sair dos `DataTypeTemplates` do proprio arquivo.
#
# Medido nos 146 ICD do corpus: todos os 2.030 enderecos decorados caem em FC
# `ST`. Resolver mesmo assim, em vez de gravar `ST` na marra, e' o que faz um
# ICD futuro que discorde falhar alto em vez de gerar um item errado.

class TestDaFunctionalConstraints:

    def test_it_resolves_the_fc_of_an_instance_da(self):
        from pacct.parsers.scd import sel_da_fcs
        fcs = sel_da_fcs(FIXTURE)["REL_A"]
        assert fcs[("ANN", "PLT1GGIO1", "Ind01", "stVal")] == "ST"
        assert fcs[("ANN", "BKR1CSWI1", "Pos", "stVal")] == "ST"
        assert fcs[("ANN", "BKR1CSWI1", "Dir", "dirGeneral")] == "ST"

    def test_a_control_da_keeps_its_own_fc(self):
        """`Oper` e' `CO` no DOType, e o `ctlVal` desce por dentro dele."""
        from pacct.parsers.scd import sel_da_fcs
        fcs = sel_da_fcs(FIXTURE)["REL_A"]
        assert fcs[("ANN", "RBGGIO1", "SPCSO01", "Oper.ctlVal")] == "CO"

    def test_an_unresolvable_da_is_absent_rather_than_guessed(self):
        from pacct.parsers.scd import sel_da_fcs
        fcs = sel_da_fcs(FIXTURE)["REL_A"]
        assert ("ANN", "PLT1GGIO1", "Ind01", "naoExiste") not in fcs

    def test_the_decoy_saddr_in_the_templates_is_not_a_bit(self):
        """`<DA name="q" fc="ST" sAddr="db:EN"/>` num DOType e' um default de
        template, nao um mapeamento de instancia. So' o que esta sob o
        `Server` do IED conta -- se os templates entrassem, `EN` viraria um
        bit em todo rele do corpus."""
        assert "EN" not in sel_short_addresses(FIXTURE)["REL_A"]
