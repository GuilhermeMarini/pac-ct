"""sAddr extraction: the only bridge from a Relay Word name to 61850.

`sAddr="db:PLT01"` is an SCL attribute and the relay does NOT serve it over
MMS -- verified on a live SEL-451-5 R331, where `$DC$Ind01$d` answers
`object-non-existent`. So this parse is the only way the map can be built.
"""
from __future__ import annotations

from pathlib import Path

from selfiles.scl.read import sel_short_addresses

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

    from selfiles.scl.mms_tables import da_rank, parse_saddr

    root = ET.parse(SAMPLE).getroot()
    ied = next(e for e in root.iter()
               if e.tag.rsplit("}", 1)[-1] == "IED"
               and e.get("name") == "QPC1_LT2_UPC1")
    best: dict = {}
    for el in ied.iter():
        # `parse_saddr` and not `sa[3:]`: a decorated address reaches
        # several names, and the count only adds up if this side counts the
        # same ones.
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
    from selfiles.scl.mms_tables import norm_part

    import _sel61850 as maps

    assert maps.norm_part is norm_part
    src = inspect.getsource(maps)
    assert "ElementTree" not in src, "tools/ voltou a parsear o SCL sozinho"
    assert "_SADDR_PREFIX" not in src, "a extração de sAddr voltou pra tools/"
    assert "[^A-Z0-9]" not in src, "`norm_part` voltou a ter uma segunda cópia"
    assert maps.load_scd(SAMPLE)["bits"]["QPC1LT2UPC1"] == \
        set(sel_short_addresses(SAMPLE)["QPC1_LT2_UPC1"])


# -- the DECORATED addresses: one 61850 point that carries two bits ---------
#
# `sAddr="db:52A|52B?0:1:2:3"` on a `Pos$stVal` is a DPS: ONE point whose
# Dbpos encodes TWO bits of the Relay Word. The grammar and the alternatives
# live in `pacct/core/mms_tables.py:parse_saddr`; here we pin what the WALK
# does with them -- one `ScdPoint` per NAME, each with its own bit's rule.
#
# Before this the bit's name was `sa[3:].upper()`, that is, the key became the
# literal string `52A|52B?0:1:2:3`, which matches no drawn bit at all: the
# whole form vanished silently. Measured on the substation's 25 relays against
# their GLEs: 55 drawn bits out of 7.524 have only a decorated address, and
# all of them are `52A`, `89CL*` or `89OPN*` -- breaker and disconnector
# position.

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
        """`LOC` has a plain `LLN0$Loc$stVal` AND `BKR1CSWI1$Health$stVal?3:1`.
        Both are `stVal`, so without the `da_rank` step the tie-break would be
        document order. Measured on `QPC1_LT1_UPC1` of the substation's SCD:
        10 of the 33 decorated names also have a plain address."""
        bits = sel_short_addresses(FIXTURE)["REL_A"]
        assert bits["LOC"].ln == "LLN0"
        assert bits["LOC"].rule is None

    def test_a_malformed_decoration_is_dropped_whole(self):
        """`?0:1:2` on two names breaks `len(alt) == 2**n`, an invariant that
        holds in 5.025 of 5.025 on the corpus. Guessing at it paints the wrong
        bit."""
        bits = sel_short_addresses(FIXTURE)["REL_A"]
        assert "BADFORM" not in bits
        assert not any("?" in b or "|" in b for b in bits)


class TestDecoratedAddressesInTheRealScd:
    """`samples/substation_demo.scd` traz 162 enderecos decorados distintos,
    `52A|52B?0:1:2:3` entre eles."""

    def test_52a_and_52b_come_out_of_the_breaker_position_point(self):
        """`QPC2_TR1_UPC1` is the relay whose GL1 opened the subject: its only
        drawn bit that has nothing but a decorated address is `52A`, on
        `PRO/BKR1CSWI1$Pos$stVal`."""
        bits = sel_short_addresses(SAMPLE)["QPC2_TR1_UPC1"]
        assert (bits["52A"].ld_inst, bits["52A"].ln) == ("PRO", "BKR1CSWI1")
        assert (bits["52A"].do, bits["52A"].da) == ("Pos", "stVal")
        assert bits["52A"].rule.alternatives == (0, 1, 2, 3)
        assert bits["52A"].rule.index == 0

    def test_the_other_half_of_the_pair_keeps_its_plain_address(self):
        """`52B` of the same IED is also in `db:52A|52B?0:1:2:3`, but it has a
        plain address on `ANN/PROGGIO37$Ind15$stVal` -- and the plain one
        wins. That is why this relay gains ONE bit and not two: 52A has no
        other address, 52B has."""
        bits = sel_short_addresses(SAMPLE)["QPC2_TR1_UPC1"]
        assert (bits["52B"].ln, bits["52B"].do) == ("PROGGIO37", "Ind15")
        assert bits["52B"].rule is None

    def test_a_plain_address_still_wins_where_the_ied_has_both(self):
        """`QPC1_LT2_UPC2` enderaca `52A` liso, em `ANN/BRGGIO14$Ind01`."""
        bits = sel_short_addresses(SAMPLE)["QPC1_LT2_UPC2"]
        assert bits["52A"].do == "Ind01"
        assert bits["52A"].rule is None

    def test_no_bit_name_keeps_the_decoration_in_it(self):
        """The original regression: the key became `52A|52B?0:1:2:3`."""
        for points in sel_short_addresses(SAMPLE).values():
            assert not any("?" in b or "|" in b for b in points)


# -- the FC, when there is NO relay to ask ----------------------------------
#
# On the live path the FC comes from the relay (`GetLogicalDeviceDirectory`),
# which is why `sel_short_addresses` does not bring it. But the factory table
# in `data/mms_map/` is generated OFFLINE, from the ICDs, and there is no
# relay there: the FC has to come out of the file's own `DataTypeTemplates`.
#
# Measured on the corpus's 146 ICDs: all 2.030 decorated addresses fall on FC
# `ST`. Resolving anyway, instead of hardcoding `ST`, is what makes a future
# ICD that disagrees fail loudly instead of generating a wrong item.

class TestDaFunctionalConstraints:

    def test_it_resolves_the_fc_of_an_instance_da(self):
        from selfiles.scl.read import sel_da_fcs
        fcs = sel_da_fcs(FIXTURE)["REL_A"]
        assert fcs[("ANN", "PLT1GGIO1", "Ind01", "stVal")] == "ST"
        assert fcs[("ANN", "BKR1CSWI1", "Pos", "stVal")] == "ST"
        assert fcs[("ANN", "BKR1CSWI1", "Dir", "dirGeneral")] == "ST"

    def test_a_control_da_keeps_its_own_fc(self):
        """`Oper` is `CO` on the DOType, and `ctlVal` descends inside it."""
        from selfiles.scl.read import sel_da_fcs
        fcs = sel_da_fcs(FIXTURE)["REL_A"]
        assert fcs[("ANN", "RBGGIO1", "SPCSO01", "Oper.ctlVal")] == "CO"

    def test_an_unresolvable_da_is_absent_rather_than_guessed(self):
        from selfiles.scl.read import sel_da_fcs
        fcs = sel_da_fcs(FIXTURE)["REL_A"]
        assert ("ANN", "PLT1GGIO1", "Ind01", "naoExiste") not in fcs

    def test_the_decoy_saddr_in_the_templates_is_not_a_bit(self):
        """`<DA name="q" fc="ST" sAddr="db:EN"/>` on a DOType is a template
        default, not an instance mapping. Only what is under the IED's
        `Server` counts -- if the templates got in, `EN` would become a bit on
        every relay of the corpus."""
        assert "EN" not in sel_short_addresses(FIXTURE)["REL_A"]
