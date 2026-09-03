"""How a relay in an RDB is paired with an IED in an SCD.

These pin behaviour that already exists. That makes them characterization
tests, so each one names, in its docstring, the production change that would
make it fail -- otherwise a test that passed the moment it was written proves
nothing.

Why they matter: this pairing decides whose signal names get written into whose
diagram. `matchers/relay_scd.py` matches on IP first and falls back to
RID == IED@name, and both identifiers are read out of settings files whose
location depends on the relay family. A wrong pair does not look like a
failure: it produces a full report with every relay matched, and the VB Updater
downstream then copies one bay's descriptions into another bay's relay.

Everything runs against hand-built settings files under `tmp_path`. The 42 MB
`samples/*.rdb` is real but unreadable in a failing assertion, and the shapes
that matter here are two lines of `KEY,"VALUE"`.
"""

from __future__ import annotations

from pathlib import Path

import cfbwrite as cfb
import pytest
from selfiles import match as m
from selfiles.rdb import RelayEntry

from tests import gle_fixtures as fx

# `SET_*.TXT` lines end `KEY,"VALUE"\x1c\r\n` -- the File Separator sits INSIDE
# the line, before the CRLF. Kept in the fixtures because it is what the real
# files hold and what the setting regex has to survive.
FS = "\x1c"


def _settings(**pairs: str) -> str:
    return "".join(f'{k},"{v}"{FS}\r\n' for k, v in pairs.items())


def _relay(extract_dir: Path, name: str, *,
           ip: str | None = None, rid: str | None = None,
           model: str | None = "487E-3",
           entry_ip: str | None = None,
           ip_file: str = "set_p5.txt",
           rid_file: str = "set_g1.txt") -> RelayEntry:
    """Write one relay's settings under `Relays/<name>/` and return its entry.

    The defaults are the 4xx layout (`SET_P5.TXT` / `SET_G1.TXT`), which is
    what the SEL-411L / SEL-451 / SEL-487E profiles declare. The 7xx reads
    `SET_P1.TXT` / `SET_1.TXT` instead -- pass those explicitly.

    `entry_ip` is the IP `parsers/rdb.py` already read, i.e. the fallback
    `_collect_rdb_identifiers` reaches for when the profile lookup comes back
    empty.
    """
    d = extract_dir / "Relays" / name
    d.mkdir(parents=True, exist_ok=True)
    if ip is not None:
        (d / ip_file).write_text(
            _settings(IPADDR=ip, IPADDRE="10.99.99.99"), encoding="latin-1")
    if rid is not None:
        (d / rid_file).write_text(
            _settings(RID=rid, TID="SE TESTE"), encoding="latin-1")
    return RelayEntry(name=name, gles=[], model=model, ip=entry_ip)


#: Two addressed IEDs and one with no `<ConnectedAP>`.
SCD_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL">
  <Communication>
    <SubNetwork name="SUB1">
      <ConnectedAP iedName="QPC1_TR1_UPC1" apName="S1">
        <Address><P type="IP">192.0.2.60</P></Address>
      </ConnectedAP>
      <ConnectedAP iedName="QPC1_LT1_UPC1" apName="S1">
        <Address><P type="IP">192.0.2.61</P></Address>
      </ConnectedAP>
    </SubNetwork>
  </Communication>
  <IED name="QPC1_TR1_UPC1" type="SEL_487E" manufacturer="SEL"/>
  <IED name="QPC1_LT1_UPC1" type="SEL_411L" manufacturer="SEL"/>
  <IED name="QPC1_SEM_IP" type="SEL_751" manufacturer="SEL"/>
</SCL>
"""


@pytest.fixture
def scd_path(tmp_path: Path) -> Path:
    p = tmp_path / "SE_TESTE.scd"
    p.write_text(SCD_BODY, encoding="utf-8")
    return p


@pytest.fixture
def extract_dir(tmp_path: Path) -> Path:
    d = tmp_path / "extracted"
    d.mkdir()
    return d


# -----------------------------------------------------------------------------
# Model normalisation
# -----------------------------------------------------------------------------

class TestNormalizeModel:

    @pytest.mark.parametrize("raw,want", [
        ("SEL-487E-3", "487E"),     # RELAYTYPE in the RDB, with a revision
        ("SEL_487E_A", "487E"),     # IED@type in the SCD, underscores
        ("487E", "487E"),
        ("sel-411l", "411L"),
        ("SEL-311C", "311C"),       # no revision suffix to strip
        ("SEL-2440", "2440"),
        ("", ""),
        (None, ""),
    ])
    def test_both_separators_and_the_revision_suffix_are_absorbed(self, raw, want):
        """The RDB writes `SEL-487E-3` and the SCD writes `SEL_487E_A` for the
        same relay. Fails if either the `_`->`-` fold or the short-suffix trim
        goes -- every matched relay would then be flagged `MISMATCH` and the
        report's one real signal would become noise."""
        assert m._normalize_model(raw) == want

    def test_a_long_suffix_is_not_a_revision_and_survives(self):
        """The trim only fires for an alphanumeric tail of one or two chars.
        Fails if the `len(tail) <= 2` bound loosens: `SEL-411L-2440` would
        collapse to `411L` and stop contradicting anything."""
        assert m._normalize_model("SEL-411L-2440") == "411L-2440"

    def test_missing_data_on_either_side_cannot_contradict(self):
        """`model_consistent` is a claim about a CONFLICT, not about a match.
        Fails if the empty-side guard goes -- an SCD without `type` (legal)
        would mark every relay in the project as mismatched."""
        assert m._model_consistent("487E-3", None) is True
        assert m._model_consistent(None, "SEL_487E") is True
        assert m._model_consistent("411L", "SEL_487E") is False


# -----------------------------------------------------------------------------
# Matching
# -----------------------------------------------------------------------------

class TestMatchByIp:

    def test_the_ip_is_the_primary_key(self, extract_dir, scd_path):
        """Fails if the IP branch is reordered after the RID one -- the RID
        fallback exists for uncommissioned relays and would start winning on
        commissioned ones, where the two identifiers can legitimately differ."""
        relays = [_relay(extract_dir, "TRAFO", ip="192.0.2.60", rid="OUTRO")]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert [(x.rdb_name, x.scd_name, x.matched_by) for x in rep.matched] == [
            ("TRAFO", "QPC1_TR1_UPC1", "ip")]

    def test_a_cidr_suffix_on_the_ipaddr_is_stripped(self, extract_dir, scd_path):
        """QuickSet writes `IPADDR,"192.0.2.60/24"`; the SCD writes the bare
        address. Fails if `read_identifiers` stops splitting on `/` -- no relay
        in a real project would match by IP any more, and every one of them
        would silently fall through to the RID path."""
        relays = [_relay(extract_dir, "TRAFO", ip="192.0.2.60/24")]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert rep.matched[0].ip == "192.0.2.60"

    def test_ipaddre_is_not_ipaddr(self, extract_dir, scd_path):
        """The fixture's `set_p5.txt` holds both keys. Fails if the setting
        regex loosens to a prefix match: the relay would take the alternate
        address and match nothing."""
        relays = [_relay(extract_dir, "TRAFO", ip="192.0.2.60")]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert rep.matched[0].ip == "192.0.2.60"

    def test_the_rdb_loaders_own_ip_is_the_fallback(self, extract_dir, scd_path):
        """`ids.get("ip") or r.ip`. A model with no JSON profile (a SEL-2440
        concentrator) returns no identifiers at all, and the IP `parsers/rdb.py`
        already found is all there is. Fails if the `or r.ip` fallback goes."""
        relays = [_relay(extract_dir, "CONC", model="2440",
                         entry_ip="192.0.2.60")]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert [(x.rdb_name, x.matched_by) for x in rep.matched] == [
            ("CONC", "ip")]


class TestMatchByRid:

    def test_the_rid_matches_the_ied_name_case_insensitively(
            self, extract_dir, scd_path):
        """The engineer types the RID by hand in QuickSet. Fails if the
        `.upper()` on either side goes."""
        relays = [_relay(extract_dir, "TRAFO", rid="qpc1_tr1_upc1")]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert [(x.scd_name, x.matched_by) for x in rep.matched] == [
            ("QPC1_TR1_UPC1", "rid")]

    def test_a_factory_default_ip_falls_through_to_the_rid(
            self, extract_dir, scd_path):
        """The reason the fallback exists: a relay still on `192.168.x.x` has a
        real RID and a meaningless IP. Fails if the RID branch is removed --
        every relay of a not-yet-commissioned panel would report unmatched."""
        relays = [_relay(extract_dir, "TRAFO", ip="192.168.1.2",
                         rid="QPC1_TR1_UPC1")]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert rep.matched[0].matched_by == "rid"

    def test_diverging_ips_on_a_rid_match_are_noted(self, extract_dir, scd_path):
        """The note is the whole value of the fallback: it says 'this relay has
        not been given its project address yet'. Fails if the note goes and the
        match becomes indistinguishable from a clean one."""
        relays = [_relay(extract_dir, "TRAFO", ip="192.168.1.2",
                         rid="QPC1_TR1_UPC1")]
        note = m.compare_relays_to_scd(relays, extract_dir, scd_path).matched[0].notes
        assert any("IPs divergem" in n for n in note)
        assert any("192.168.1.2" in n and "192.0.2.60" in n for n in note)

    def test_a_rid_match_against_an_ied_without_an_ip_is_noted(
            self, extract_dir, scd_path):
        """`QPC1_SEM_IP` has no `<ConnectedAP>`. Fails if that branch goes --
        the pair would look confirmed when neither side can be reached over the
        network."""
        relays = [_relay(extract_dir, "SPARE", ip="192.168.1.9",
                         rid="QPC1_SEM_IP", model="411L")]
        notes = m.compare_relays_to_scd(relays, extract_dir, scd_path).matched[0].notes
        assert any("SCD nao tem IP" in n for n in notes)

    def test_a_7xx_reads_its_identifiers_from_different_files(
            self, extract_dir, scd_path):
        """The 4xx keeps IPADDR in `SET_P5.TXT` and RID in `SET_G1.TXT`; the
        7xx keeps them in `SET_P1.TXT` and `SET_1.TXT`. That split is data --
        `data/relay_models/SEL-751.json` -- and `read_identifiers` obeys it.

        Fails if a profile's `ip_address.file` / `relay_id.file` is edited, or
        if the reader falls back to one hardcoded filename: a whole family
        would report no identifiers and land in `unmatched_rdb`."""
        relays = [_relay(extract_dir, "ALIM", ip="192.0.2.61", model="751",
                         ip_file="set_p1.txt", rid_file="set_1.txt")]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert [(x.scd_name, x.matched_by) for x in rep.matched] == [
            ("QPC1_LT1_UPC1", "ip")]

    def test_a_relay_with_no_readable_ip_is_noted(self, extract_dir, scd_path):
        """Fails if the third note branch goes. Same reasoning: the report has
        to say which half of the identity was missing."""
        relays = [_relay(extract_dir, "TRAFO", rid="QPC1_TR1_UPC1")]
        notes = m.compare_relays_to_scd(relays, extract_dir, scd_path).matched[0].notes
        assert any("nao tem IP" in n for n in notes)

    def test_an_ied_already_matched_by_ip_is_not_matched_again_by_rid(
            self, extract_dir, scd_path):
        """`cand.name not in used_scd_names`. Two relays whose RIDs were
        copy-pasted must not both claim one IED. Fails if that guard goes."""
        relays = [
            _relay(extract_dir, "TRAFO", ip="192.0.2.60", rid="QPC1_TR1_UPC1"),
            _relay(extract_dir, "COPIA", rid="QPC1_TR1_UPC1"),
        ]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert [x.rdb_name for x in rep.matched] == ["TRAFO"]
        assert [u.rdb_name for u in rep.unmatched_rdb] == ["COPIA"]


class TestModelConsistency:

    def test_a_matched_pair_of_the_same_model_is_consistent(
            self, extract_dir, scd_path):
        """`487E-3` in the RDB against `SEL_487E` in the SCD. Fails if
        `_normalize_model` stops absorbing either spelling."""
        relays = [_relay(extract_dir, "TRAFO", ip="192.0.2.60", model="487E-3")]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert rep.matched[0].model_consistent is True
        assert rep.matched[0].notes == ()

    def test_a_model_conflict_still_matches_but_carries_a_note(
            self, extract_dir, scd_path):
        """A conflict is a WARNING, not a rejection: the IP is the identity and
        the model disagreement is what the engineer must go look at. Fails if a
        mismatch starts suppressing the match -- the relay would silently move
        to `unmatched_rdb` and the real problem would disappear."""
        relays = [_relay(extract_dir, "TRAFO", ip="192.0.2.60", model="411L")]
        match = m.compare_relays_to_scd(relays, extract_dir, scd_path).matched[0]
        assert match.model_consistent is False
        assert any("modelo divergente" in n for n in match.notes)


class TestUnmatched:

    def test_a_relay_with_neither_identifier_says_so(self, extract_dir, scd_path):
        """Distinguishes 'the RDB could not be read' from 'the SCD does not
        have this relay'. Fails if `_no_match_reason` loses its first branch."""
        relays = [_relay(extract_dir, "MUDO", ip=None, rid=None)]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert rep.unmatched_rdb[0].reason == "sem IP nem RID legiveis no RDB"

    def test_both_identifiers_missing_from_the_scd_are_both_reported(
            self, extract_dir, scd_path):
        """Fails if the reason collapses to whichever check runs first -- the
        engineer needs to know that BOTH lookups missed, which is what an SCD
        exported before this bay was added looks like."""
        relays = [_relay(extract_dir, "NOVO", ip="192.0.2.99", rid="NAO_EXISTE")]
        reason = m.compare_relays_to_scd(
            relays, extract_dir, scd_path).unmatched_rdb[0].reason
        assert "192.0.2.99" in reason
        assert "NAO_EXISTE" in reason
        assert "; " in reason

    def test_an_identifier_consumed_by_an_earlier_match_says_so(
            self, extract_dir, scd_path):
        """The reason that fires when the RID EXISTS in the SCD but the IED was
        already taken. Fails if that fallback branch goes and the message
        becomes an empty string."""
        relays = [
            _relay(extract_dir, "TRAFO", ip="192.0.2.60", rid="QPC1_TR1_UPC1"),
            _relay(extract_dir, "COPIA", rid="QPC1_TR1_UPC1"),
        ]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert rep.unmatched_rdb[0].reason == (
            "identificadores presentes mas ja consumidos por outro match")

    def test_ieds_nobody_claimed_are_listed_with_their_address(
            self, extract_dir, scd_path):
        """The SCD-only list is how a missing relay in the RDB is spotted.
        Fails if `used_scd_names` stops being consulted (everything would be
        listed) or if the loop is dropped (nothing would be)."""
        relays = [_relay(extract_dir, "TRAFO", ip="192.0.2.60")]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert [(u.scd_name, u.ip, u.scd_type) for u in rep.unmatched_scd] == [
            ("QPC1_LT1_UPC1", "192.0.2.61", "SEL_411L"),
            ("QPC1_SEM_IP", None, "SEL_751"),
        ]

    def test_an_unreadable_scd_warns_once_and_fails_every_relay(
            self, extract_dir, tmp_path):
        """The only `warnings` entry the matcher ever produces. Fails if an
        empty SCD starts being treated as 'no IED matched' -- the report would
        blame each relay individually for a problem that is the file's."""
        rep = m.compare_relays_to_scd(
            [_relay(extract_dir, "TRAFO", ip="192.0.2.60")],
            extract_dir, tmp_path / "nao_existe.scd")
        assert len(rep.warnings) == 1
        assert [u.reason for u in rep.unmatched_rdb] == ["SCD vazio"]
        assert rep.matched == []

    def test_two_rdb_relays_on_one_ip_must_not_both_claim_the_ied(
            self, extract_dir, scd_path):
        """Matching is one-to-one -- `unmatched_scd` is computed from
        `used_scd_names` on that assumption. Was an xfail; the IP branch now
        carries the same guard the RID branch always had.

        The second relay landing in `unmatched_rdb` is the point: a duplicated
        IP inside one RDB is a commissioning error, and the report has to show
        it rather than quietly confirm both."""
        relays = [
            _relay(extract_dir, "TRAFO", ip="192.0.2.60"),
            _relay(extract_dir, "COPIA", ip="192.0.2.60"),
        ]
        rep = m.compare_relays_to_scd(relays, extract_dir, scd_path)
        assert [x.rdb_name for x in rep.matched] == ["TRAFO"]
        assert [u.rdb_name for u in rep.unmatched_rdb] == ["COPIA"]


class TestReport:

    def test_to_dict_is_json_serialisable_and_keeps_the_notes(
            self, extract_dir, scd_path):
        """`to_dict()` is what the web layer ships. Fails if `notes` stays a
        tuple (not JSON) or if a key is renamed under the UI."""
        import json
        relays = [_relay(extract_dir, "TRAFO", ip="192.168.1.2",
                         rid="QPC1_TR1_UPC1")]
        d = m.compare_relays_to_scd(relays, extract_dir, scd_path).to_dict()
        assert set(d) == {"matched", "unmatched_rdb", "unmatched_scd", "warnings"}
        assert isinstance(d["matched"][0]["notes"], list)
        json.dumps(d)

    def test_print_summary_writes_to_stdout(self, extract_dir, scd_path, capsys):
        """Pinned because it is the ONLY output path in this module that does
        not go through a logger (`relay_scd.py` lines ~134-163 call `print`
        directly). A server process has no stdout an operator reads, so this
        belongs behind the logger or a `__main__` guard -- noted, deliberately
        not changed in this phase.

        Fails if the report moves to logging, which is the fix."""
        relays = [_relay(extract_dir, "TRAFO", ip="192.168.1.2",
                         rid="QPC1_TR1_UPC1")]
        m.compare_relays_to_scd(relays, extract_dir, scd_path).print_summary()
        out = capsys.readouterr().out
        assert "RDB <-> SCD match" in out
        assert "QPC1_TR1_UPC1" in out


# -----------------------------------------------------------------------------
# The whole pipeline, RDB file in / report out
# -----------------------------------------------------------------------------

def _stream(name: str, data: bytes) -> cfb.Entry:
    return cfb.Entry(name=name, is_storage=False, size=len(data),
                     read=lambda d=data: d, children=[])


def _storage(name: str, children) -> cfb.Entry:
    return cfb.Entry(name=name, is_storage=True, size=0, read=None,
                     children=list(children))


def test_compare_rdb_to_scd_extracts_and_matches(tmp_path, scd_path):
    """End to end through `process_upload`, on a synthetic Compound File.

    Pins that `base_dir` really redirects the content cache: without it this
    would write into the project's real `cache/rdb/`. Fails if `base_dir`
    stops being forwarded as `cache_root`, or if the relay identifiers stop
    being read from the extracted tree (they are read from disk after
    extraction, not from the OLE streams)."""
    rdb = tmp_path / "SE_TESTE.rdb"
    cfb.write_ole(rdb, [
        _storage("Relays", [
            _storage("QPC1_TR1_UPC1", [
                _stream("SET_P5.TXT",
                        _settings(IPADDR="192.0.2.60/24").encode("latin-1")),
                _stream("SET_G1.TXT",
                        _settings(RID="QPC1_TR1_UPC1").encode("latin-1")),
                _storage("Misc", [
                    _stream("Cfg.txt", b'RELAYTYPE = SEL-487E-3\r\n'),
                    _stream("GL1.gle", fx.SAMPLE_GLE),
                ]),
            ]),
        ]),
    ])
    cache = tmp_path / "cache"
    rep = m.compare_rdb_to_scd(rdb, scd_path, base_dir=cache)
    assert [(x.rdb_name, x.scd_name, x.matched_by, x.model_consistent)
            for x in rep.matched] == [
        ("QPC1_TR1_UPC1", "QPC1_TR1_UPC1", "ip", True)]
    assert cache.is_dir()          # the extraction went to the redirected root
