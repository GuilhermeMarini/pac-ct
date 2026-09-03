"""Resolving a Relay Word name to an MMS item.

The FC is deliberately NOT parsed out of the SCL type templates. `sAddr` sits
on a DAI but the functional constraint lives on the DA inside the DOType, so
the ordinary route means walking DataTypeTemplates. The relay already publishes
every fully-qualified name (12 735 for the 451's ANN alone), so matching
`LN$*$DO$DA` against GetLogicalDeviceDirectory yields the FC *and* verifies the
entry in one pass. Where a DO/DA exists under two FCs -- `LocSta` is at both CO
and ST on the 487E -- ST wins, then MX.

Fixtures under tests/fixtures/mms/ are a REAL capture (see their own
`provenance` field): SEL-451-5 R331 at 203.0.113.61, taken with
tools/capture_mms_fixtures.py. They replaced the synthetic stand-in the branch
was written against, and the suite passed unchanged across the substitution --
which is the whole reason the assertions here stay on behaviour (what
resolve_map does with a name the relay confirms or does not confirm) and never
on the directory's exact size or full membership.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from pacct.parsers.scd import ScdPoint
from pacct.web.glv.mms_map import ld_suffixes, resolve_map

FIX = Path(__file__).parent / "fixtures" / "mms"
IED = "QPC1_TFE_UPC1"


@pytest.fixture(scope="module")
def directory():
    data = json.loads((FIX / "451_ann_directory.json").read_text())
    return set(data["directory"])


def test_ld_suffix_comes_from_the_common_prefix():
    """The IED name is not given to us; it is the prefix every LD shares."""
    lds = [f"{IED}ANN", f"{IED}CFG", f"{IED}PRO"]
    assert ld_suffixes(lds) == {"ANN": f"{IED}ANN",
                                "CFG": f"{IED}CFG",
                                "PRO": f"{IED}PRO"}


def test_scd_point_gets_its_fc_from_the_relay(directory):
    scd = {"PLT01": ScdPoint(bit="PLT01", ld_inst="ANN", ln="PLT1GGIO1",
                             do="Ind01", da="stVal")}
    m = resolve_map(wanted={"PLT01"}, directory={"ANN": directory},
                    ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
    p = m.points["PLT01"]
    assert p.item == "PLT1GGIO1$ST$Ind01$stVal"
    assert p.container == "PLT1GGIO1$ST"
    assert p.child == "Ind01"
    assert p.ld == f"{IED}ANN"


def test_a_point_the_relay_does_not_serve_is_dropped(directory):
    scd = {"NOPE": ScdPoint(bit="NOPE", ld_inst="ANN", ln="NOSUCHGGIO9",
                            do="Ind01", da="stVal")}
    m = resolve_map(wanted={"NOPE"}, directory={"ANN": directory},
                    ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
    assert "NOPE" not in m.points
    assert m.coverage({"NOPE"}).missing == ("NOPE",)


def test_table_fills_in_what_the_scd_lacks(directory):
    class FakeTable:
        bits = {"ALT01": ("ANN", "ALT1GGIO1$ST$Ind01$stVal")}

    m = resolve_map(wanted={"ALT01"}, directory={"ANN": directory},
                    ld_by_suffix={"ANN": f"{IED}ANN"},
                    scd_points={}, table=FakeTable())
    assert m.points["ALT01"].item == "ALT1GGIO1$ST$Ind01$stVal"
    assert m.source == "tabela"


def test_scd_wins_over_the_table_for_the_same_bit(directory):
    class FakeTable:
        bits = {"PLT01": ("ANN", "WRONGGGIO1$ST$Ind01$stVal")}

    scd = {"PLT01": ScdPoint(bit="PLT01", ld_inst="ANN", ln="PLT1GGIO1",
                             do="Ind01", da="stVal")}
    m = resolve_map(wanted={"PLT01"}, directory={"ANN": directory},
                    ld_by_suffix={"ANN": f"{IED}ANN"},
                    scd_points=scd, table=FakeTable())
    assert m.points["PLT01"].item == "PLT1GGIO1$ST$Ind01$stVal"


def test_containers_group_the_points_for_the_read_plan(directory):
    scd = {f"PLT{i:02d}": ScdPoint(bit=f"PLT{i:02d}", ld_inst="ANN",
                                   ln="PLT1GGIO1", do=f"Ind{i:02d}",
                                   da="stVal")
           for i in range(1, 5)}
    m = resolve_map(wanted=set(scd), directory={"ANN": directory},
                    ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
    groups = m.containers()
    assert list(groups) == [(f"{IED}ANN", "PLT1GGIO1$ST")]
    assert len(groups[(f"{IED}ANN", "PLT1GGIO1$ST")]) == 4


def test_coverage_counts_only_what_was_asked_for(directory):
    scd = {"PLT01": ScdPoint(bit="PLT01", ld_inst="ANN", ln="PLT1GGIO1",
                             do="Ind01", da="stVal")}
    m = resolve_map(wanted={"PLT01", "T10_LED"}, directory={"ANN": directory},
                    ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
    cov = m.coverage({"PLT01", "T10_LED"})
    assert (cov.total, cov.mapped, cov.missing) == (2, 1, ("T10_LED",))
    assert cov.fraction == 0.5


def test_a_control_point_is_dropped_instead_of_being_polled():
    """`Oper.ctlVal` is a COMMAND: it is what someone last told the relay to
    do, not what the relay sees. Reading it as a bit and painting it green is
    a fabrication, and counting it as covered makes the page badge overstate
    what the screen can show. The relay serves the name -- the drop is the
    map's judgement, not the directory's."""
    scd = {"RB01": ScdPoint(bit="RB01", ld_inst="ANN", ln="RBGGIO1",
                            do="SPCSO01", da="Oper.ctlVal")}
    names = {"RBGGIO1$CO$SPCSO01$Oper$ctlVal"}
    m = resolve_map(wanted={"RB01"}, directory={"ANN": names},
                    ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
    assert "RB01" not in m.points
    assert m.coverage({"RB01"}).missing == ("RB01",)


def test_a_measurement_or_a_setting_is_dropped_too():
    """`instMag.f` is a float and `setVal` is a setting. `int(bool(x))` of
    either is not a bit reading. Measured on the LT2_UPC1 corpus: 4 of the
    222 addressable bits point at `instMag.f`, and before this they were
    counted as covered and then never read -- which also pinned
    `state.error` at "leitura parcial" on a perfectly healthy link."""
    scd = {"IA": ScdPoint(bit="IA", ld_inst="ANN", ln="MMXU1",
                          do="A", da="instMag.f"),
           "MLTLEV": ScdPoint(bit="MLTLEV", ld_inst="ANN", ln="LLN0",
                              do="MltLev", da="setVal")}
    names = {"MMXU1$MX$A$instMag$f", "LLN0$SG$MltLev$setVal"}
    m = resolve_map(wanted={"IA", "MLTLEV"}, directory={"ANN": names},
                    ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
    assert m.points == {}


def test_an_acd_general_point_resolves_and_carries_its_leaf():
    """43 of the 222 addressable bits of the real LT2_UPC1 -- `TRIP` among
    them, drawn on five pages -- are ACD/ACT points whose boolean leaf is
    `general`, not `stVal`. The decoder reads `MmsPoint.leaf`, so the leaf
    has to survive the map."""
    scd = {"TRIP": ScdPoint(bit="TRIP", ld_inst="PRO", ln="PTRC1",
                            do="Op", da="general")}
    names = {"PTRC1$ST$Op$general"}
    m = resolve_map(wanted={"TRIP"}, directory={"PRO": names},
                    ld_by_suffix={"PRO": f"{IED}PRO"}, scd_points=scd)
    p = m.points["TRIP"]
    assert p.item == "PTRC1$ST$Op$general"
    assert p.child == "Op" and p.leaf == ("general",)


def test_an_fc_outside_fc_preference_still_resolves_when_the_relay_serves_it():
    """The old `_resolve_fc` looped over FC_PREFERENCE, so it could only
    ever match those six FCs -- but the module's own docstring claims
    `LN$*$DO$DA`, a wildcard, and the real corpus has 186 SG (setting-group)
    points. A relay that confirms a point under an uncatalogued FC must not
    be reported missing just because that FC never made the fixed list. It is
    the FC that is under test here, not the DA -- the DA still has to be a
    boolean status, or the point is not a bit."""
    scd = {"MLTLEV": ScdPoint(bit="MLTLEV", ld_inst="ANN", ln="LLN0",
                              do="MltLev", da="stVal")}
    names = {"LLN0$SG$MltLev$stVal"}
    m = resolve_map(wanted={"MLTLEV"}, directory={"ANN": names},
                    ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
    assert m.points["MLTLEV"].item == "LLN0$SG$MltLev$stVal"


def test_the_table_drops_a_non_boolean_item_the_same_way():
    """Both sources go through the same filter. The shipped tables carry
    5 076 `instMag$f` items and 794 `Oper$ctlVal` ones; none of them is a
    bit, and the fallback path must not smuggle them in."""
    class FakeTable:
        bits = {"IA": ("ANN", "MMXU1$MX$A$instMag$f"),
                "ALT01": ("ANN", "ALT1GGIO1$ST$Ind01$stVal")}

    names = {"MMXU1$MX$A$instMag$f", "ALT1GGIO1$ST$Ind01$stVal"}
    m = resolve_map(wanted={"IA", "ALT01"}, directory={"ANN": names},
                    ld_by_suffix={"ANN": f"{IED}ANN"},
                    scd_points={}, table=FakeTable())
    assert set(m.points) == {"ALT01"}
    assert m.points["ALT01"].leaf == ("stVal",)


def test_a_bit_the_scd_addresses_only_as_a_control_falls_back_to_the_table():
    """Dropping the SCD candidate must not drop the BIT: the factory table
    may still name a readable address for it. 78 bits of `QPC1_LT2_UPC1`
    have `Oper.ctlVal` as their only SCD address."""
    class FakeTable:
        bits = {"RB01": ("ANN", "RBGGIO1$ST$SPCSO01$stVal")}

    scd = {"RB01": ScdPoint(bit="RB01", ld_inst="ANN", ln="RBGGIO1",
                            do="SPCSO01", da="Oper.ctlVal")}
    names = {"RBGGIO1$CO$SPCSO01$Oper$ctlVal", "RBGGIO1$ST$SPCSO01$stVal"}
    m = resolve_map(wanted={"RB01"}, directory={"ANN": names},
                    ld_by_suffix={"ANN": f"{IED}ANN"},
                    scd_points=scd, table=FakeTable())
    assert m.points["RB01"].item == "RBGGIO1$ST$SPCSO01$stVal"
    assert m.source == "tabela"


def test_the_inverted_lookup_still_ranks_st_ahead_of_co():
    """The inversion (index the directory, then rank candidates) must keep
    the same outcome as the old fixed-list walk when both a reading and a
    control point are genuinely present: state before control, always."""
    scd = {"RB01": ScdPoint(bit="RB01", ld_inst="ANN", ln="RBGGIO1",
                            do="SPCSO01", da="stVal")}
    names = {"RBGGIO1$CO$SPCSO01$stVal", "RBGGIO1$ST$SPCSO01$stVal"}
    m = resolve_map(wanted={"RB01"}, directory={"ANN": names},
                    ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
    assert m.points["RB01"].item == "RBGGIO1$ST$SPCSO01$stVal"


def test_ld_suffixes_is_correct_for_a_single_ld():
    """`os.path.commonprefix` on a single-element list has nothing to diff
    against, so the old implementation fell back to the WHOLE name as its
    own "suffix" -- `ld_suffixes(["QPC1_TFE_UPC1ANN"])` returned
    `{"QPC1_TFE_UPC1ANN": "QPC1_TFE_UPC1ANN"}`, not `{"ANN": ...}`. The
    caller always knows which suffixes it is looking for (the SCD's
    ld_inst values, or the fallback table's ld_suffix values), so matching
    by `endswith` against that known set is correct no matter how many LDs
    came back."""
    lds = [f"{IED}ANN"]
    assert ld_suffixes(lds, suffixes={"ANN", "CFG", "CON", "MET", "PRO"}) == \
        {"ANN": f"{IED}ANN"}


def test_ld_suffixes_longest_match_wins_so_con_is_not_shadowed_by_on():
    """Two LDs sharing more of their name than just the IED prefix broke the
    old commonprefix logic outright -- `ld_suffixes(["ABCCFG", "ABCCON"])`
    returned `{"FG": "ABCCFG", "ON": "ABCCON"}` because `os.path.commonprefix`
    ate the shared leading "ABCC". Even with the true suffixes supplied,
    trying the shortest first would let "ON" claim "ABCCON" before "CON"
    gets a chance, so the match must try the longest candidate first."""
    lds = ["ABCCFG", "ABCCON"]
    assert ld_suffixes(lds, suffixes={"CFG", "CON", "ON"}) == \
        {"CFG": "ABCCFG", "CON": "ABCCON"}


# -- the real corpus, end to end --------------------------------------------
#
# `samples/LT2_UPC1_R1e_GL1.gle.xml` and `samples/substation_demo.scd` are a
# real 411L and its own IED, both tracked. The directory is synthesised from
# the SCD's own points (every candidate offered under both ST and CO), so the
# "relay" confirms everything and what is left is purely the map's judgement.

SAMPLES = Path(__file__).parent.parent / "samples"


@pytest.fixture(scope="module")
def lt2():
    from pacct.core.mms_tables import da_parts
    from pacct.parsers.gle import parse_gle
    from pacct.parsers.scd import sel_short_addresses
    from pacct.web.glv.gle_pages import collect_bits_per_page

    doc = parse_gle(SAMPLES / "LT2_UPC1_R1e_GL1.gle.xml")
    pages = {pg: {b.upper() for b in bits}
             for pg, bits in collect_bits_per_page(doc).items()}
    points = sel_short_addresses(SAMPLES / "substation_demo.scd")["QPC1_LT2_UPC1"]
    directory: dict = {}
    for p in points.values():
        da = "$".join(da_parts(p.da))
        directory.setdefault(p.ld_inst, set()).update(
            {f"{p.ln}$ST${p.do}${da}", f"{p.ln}$CO${p.do}${da}"})
    ld_by_suffix = {suf: f"IEDX{suf}" for suf in directory}
    return pages, points, directory, ld_by_suffix


def _resolved(lt2, wanted):
    _pages, points, directory, ld_by_suffix = lt2
    return resolve_map(wanted=wanted, directory=directory,
                       ld_by_suffix=ld_by_suffix, scd_points=points)


def test_the_page_badge_counts_only_bits_the_screen_can_actually_show(lt2):
    """`SCADA_1` of the real 411L: 48 drawn bits, 35 with an sAddr. Three of
    those 35 address a float (`instMag.f`), so the badge used to promise
    35/48 for a page that could only ever show 32 -- and only 18 before the
    decoder learned to read a leaf other than `stVal`."""
    pages, *_ = lt2
    wanted = pages["SCADA_1"]
    cov = _resolved(lt2, wanted).coverage(wanted)
    assert (cov.total, cov.mapped) == (48, 32)


def test_the_whole_diagram_recovers_the_acd_points(lt2):
    """385 drawn bits, 222 with an sAddr: 175 `stVal`, 43 `general`, 4
    `instMag.f`. The 43 are the ones a `stVal`-only decoder threw away.

    The 7 above 218 are the DECORATED ones -- `Pos$stVal` of four
    disconnector CSWI nodes -- which no `sa[3:]` key could ever match.
    """
    pages, *_ = lt2
    wanted = set().union(*pages.values())
    m = _resolved(lt2, wanted)
    cov = m.coverage(wanted)
    assert (cov.total, cov.mapped) == (385, 225)
    leaves = collections.Counter(p.leaf for p in m.points.values())
    assert leaves == {("stVal",): 182, ("general",): 43}


def test_the_disconnector_positions_are_what_the_decoration_recovers(lt2):
    """Os 7 bits que so' o endereco decorado enderaca neste 411L. `89CL0n` e
    `89OPN0n` sao os dois contatos auxiliares da MESMA seccionadora, entao
    saem do MESMO item -- e' um `Pos$stVal` so', lido uma vez, com uma regra
    por bit."""
    pages, *_ = lt2
    m = _resolved(lt2, set().union(*pages.values()))
    decorated = {b: p for b, p in m.points.items() if p.rule is not None}
    assert set(decorated) == {"89CL01", "89CL02", "89CL03",
                              "89OPN01", "89OPN02", "89OPN03", "89OPN04"}
    assert decorated["89CL01"].item == decorated["89OPN01"].item \
        == "DC1CSWI1$ST$Pos$stVal"
    assert decorated["89CL01"].rule.index == 0
    assert decorated["89OPN01"].rule.index == 1


def test_the_pair_costs_one_leaf_in_the_read_plan_not_two(lt2):
    """O plano de leitura e' de FOLHAS. Dois bits no mesmo item sao UM nome
    pedido, nao dois -- e' por isso que a decoracao nao encarece o ciclo."""
    pages, *_ = lt2
    m = _resolved(lt2, set().union(*pages.values()))
    pair = {"89CL01", "89OPN01"}
    assert len({m.points[b].item for b in pair}) == 1


def test_trip_itself_is_mapped_and_reads_general(lt2):
    """`TRIP` is drawn on five pages and is `...$Op$general` -- it was counted
    as covered and could never be read."""
    m = _resolved(lt2, {"TRIP"})
    assert m.points["TRIP"].leaf == ("general",)
    assert m.points["TRIP"].item.endswith("$general")


# -- pontos DECORADOS: um item MMS que carrega dois bits --------------------
#
# `sAddr="db:52A|52B?0:1:2:3"` num `Pos$stVal` e' um DPS: o Dbpos codifica os
# dois contatos auxiliares do disjuntor. O `ScdPoint` chega aqui com a `rule`
# que diz como tirar cada bit do valor, e o portao desta camada e' o que
# impede o mesmo `Pos$stVal` de entrar SEM regra -- caso em que a py61850
# devolveria a string "10", `bool("00")` seria True, e todo disjuntor sairia
# pintado fechado.

class TestDecoratedPoints:

    def _point(self, bit, index, alternatives=(0, 1, 2, 3), nbits=2,
               do="Pos", da="stVal"):
        from pacct.core.mms_tables import BitRule
        return ScdPoint(bit=bit, ld_inst="PRO", ln="BKR1CSWI1", do=do, da=da,
                        rule=BitRule(alternatives=alternatives, index=index,
                                     nbits=nbits))

    def test_both_bits_resolve_to_the_same_item_with_their_own_rule(self):
        scd = {"52A": self._point("52A", 0), "52B": self._point("52B", 1)}
        m = resolve_map(wanted={"52A", "52B"},
                        directory={"PRO": {"BKR1CSWI1$ST$Pos$stVal"}},
                        ld_by_suffix={"PRO": f"{IED}PRO"}, scd_points=scd)
        assert m.points["52A"].item == m.points["52B"].item == \
            "BKR1CSWI1$ST$Pos$stVal"
        assert m.points["52A"].rule.index == 0
        assert m.points["52B"].rule.index == 1

    def test_an_enum_da_without_a_rule_is_still_refused(self):
        """Este e' o portao. `Pos$stVal` passa em `is_boolean_status`, entao
        sem exigir a regra ele entraria pelo caminho booleano -- e um Dbpos
        lido com `int(bool(...))` e' um disjuntor sempre fechado."""
        scd = {"52A": ScdPoint(bit="52A", ld_inst="PRO", ln="BKR1CSWI1",
                               do="Pos", da="stVal")}
        m = resolve_map(wanted={"52A"},
                        directory={"PRO": {"BKR1CSWI1$ST$Pos$stVal"}},
                        ld_by_suffix={"PRO": f"{IED}PRO"}, scd_points=scd)
        assert m.points == {}

    def test_a_dirgeneral_point_needs_a_rule_and_then_resolves(self):
        """`dirGeneral` nao esta em `BOOLEAN_STATUS_DAS`, entao antes disto
        ele nao entrava de jeito nenhum."""
        scd = {"32GF": self._point("32GF", 0, alternatives=(0, 1), nbits=1,
                                   do="Dir", da="dirGeneral")}
        m = resolve_map(wanted={"32GF"},
                        directory={"PRO": {"BKR1CSWI1$ST$Dir$dirGeneral"}},
                        ld_by_suffix={"PRO": f"{IED}PRO"}, scd_points=scd)
        assert m.points["32GF"].item == "BKR1CSWI1$ST$Dir$dirGeneral"

    def test_a_plain_boolean_point_carries_no_rule(self):
        scd = {"PLT01": ScdPoint(bit="PLT01", ld_inst="ANN", ln="PLT1GGIO1",
                                 do="Ind01", da="stVal")}
        m = resolve_map(wanted={"PLT01"},
                        directory={"ANN": {"PLT1GGIO1$ST$Ind01$stVal"}},
                        ld_by_suffix={"ANN": f"{IED}ANN"}, scd_points=scd)
        assert m.points["PLT01"].rule is None

    def test_a_rule_never_rescues_a_float_or_a_control(self):
        """A decoracao diz como decodificar um ENUMERADO. Ela nao transforma
        um `instMag.f` nem um `Oper.ctlVal` em leitura de bit."""
        scd = {"IA": self._point("IA", 0, alternatives=(0, 1), nbits=1,
                                 do="A", da="instMag.f"),
               "RB01": self._point("RB01", 0, alternatives=(0, 1), nbits=1,
                                   do="SPCSO01", da="Oper.ctlVal")}
        m = resolve_map(wanted={"IA", "RB01"},
                        directory={"PRO": {"BKR1CSWI1$MX$A$instMag$f",
                                           "BKR1CSWI1$CO$SPCSO01$Oper$ctlVal"}},
                        ld_by_suffix={"PRO": f"{IED}PRO"}, scd_points=scd)
        assert m.points == {}


def test_the_breaker_position_reaches_the_map_on_the_real_relay(lt2):
    """Fim a fim no corpus: `QPC2_TR1_UPC1` -- o rele cujo GL1 abriu o
    assunto -- so' enderaca `52A` pelo ponto decorado."""
    from pacct.core.mms_tables import da_parts
    from pacct.parsers.scd import sel_short_addresses

    points = sel_short_addresses(SAMPLES / "substation_demo.scd")["QPC2_TR1_UPC1"]
    directory: dict = {}
    for p in points.values():
        directory.setdefault(p.ld_inst, set()).add(
            f"{p.ln}$ST${p.do}${'$'.join(da_parts(p.da))}")
    m = resolve_map(wanted={"52A"}, directory=directory,
                    ld_by_suffix={s: f"X{s}" for s in directory},
                    scd_points=points)
    assert m.points["52A"].item == "BKR1CSWI1$ST$Pos$stVal"
    assert m.points["52A"].rule.alternatives == (0, 1, 2, 3)
