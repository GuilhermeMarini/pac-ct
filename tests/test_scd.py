"""What `sellib.scl.read` is allowed to read out of an IEC 61850 SCD.

These pin behaviour that already exists. That makes them characterization
tests, so each one names, in its docstring, the production change that would
make it fail -- otherwise a test that passed the moment it was written proves
nothing.

Why they matter: the SCD is the *other* half of every cross-check this toolkit
does. `matchers/relay_scd.py` pairs a relay in the RDB with an IED here, the
VB Updater copies descriptions out of `<ExtRef>` into the relay's diagram, and
the VLAN Mapper builds the switch port map out of `<GSE>`. All three read
through the four functions below. A parse that quietly returns nothing does not
crash -- it produces an empty report that looks like "nothing to do".

The documents are hand-built. `samples/*.scd` is 22 MB, which makes a failing
assertion unreadable, and the shapes that matter here are three attributes and
two nesting levels deep. Every document is built by `_scd_text()` in four
namespace flavours, because `_strip_ns` / `_iter_local` exist precisely to
survive a new vendor's export and that is the code most likely to break.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from sellib.scl import read as scd

# -----------------------------------------------------------------------------
# Fixture documents
# -----------------------------------------------------------------------------

#: Body of the reference SCD, namespace-free. Shapes copied from
#: `samples/substation_demo.scd`:
#:
#: - the IP does NOT live on the `<IED>`; it is cross-referenced from
#:   `<ConnectedAP iedName=...>` in `<Communication>`;
#: - `<GSE>` sits *inside* that same `<ConnectedAP>`, so anything walking
#:   descendants of a ConnectedAP also walks the GSE's own `<Address>`;
#: - the second AP writes `type="ip"` in lower case, which real exports do;
#: - `QPC1_SPARE` has no ConnectedAP at all -- a relay configured but not yet
#:   given an address;
#: - `QPC1_TR1_UPC1` subscribes twice to the same control block (two intAddrs
#:   of one dataset), once to a second one, and carries a template ExtRef with
#:   no publisher, which is what Architect writes before a link is closed.
_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<SCL>
  <Communication>
    <SubNetwork name="SUB1" type="8-MMS">
      <ConnectedAP iedName="QPC1_TR1_UPC1" apName="S1">
        <Address>
          <P type="IP">192.0.2.60</P>
          <P type="IP-SUBNET">255.255.255.0</P>
        </Address>
        <GSE ldInst="PRO" cbName="GCB01">
          <Address>
            <P type="MAC-Address">01-0C-CD-01-00-01</P>
            <P type="APPID">0001</P>
            <P type="VLAN-ID">00A</P>
            <P type="VLAN-PRIORITY">4</P>
          </Address>
        </GSE>
        <GSE ldInst="ANN" cbName="GCB02">
          <Address>
            <P type="MAC-Address">01-0C-CD-01-00-02</P>
          </Address>
        </GSE>
        <GSE ldInst="ANN" cbName="">
          <Address>
            <P type="MAC-Address">01-0C-CD-01-00-99</P>
          </Address>
        </GSE>
      </ConnectedAP>
      <ConnectedAP iedName="QPC1_LT1_UPC1" apName="S1">
        <Address>
          <P type="ip">192.0.2.61</P>
        </Address>
      </ConnectedAP>
    </SubNetwork>
  </Communication>
  <IED name="QPC1_TR1_UPC1" type="SEL_487E" manufacturer="SEL" desc="Trafo 1" configVersion="1.2">
    <AccessPoint name="S1">
      <Server>
        <LDevice inst="ANN">
          <LN0>
            <Inputs>
              <ExtRef desc="LT1 falha GOOSE" iedName="QPC1_LT1_UPC1" ldInst="ANN" lnClass="GGIO" srcLDInst="PRO" srcCBName="GCB09" intAddr="VB001" serviceType="GOOSE"/>
              <ExtRef desc="LT1 abertura" iedName="QPC1_LT1_UPC1" ldInst="ANN" lnClass="GGIO" srcLDInst="PRO" srcCBName="GCB09" intAddr="VB002" serviceType="GOOSE"/>
              <ExtRef desc="LT1 anunciacao" iedName="QPC1_LT1_UPC1" ldInst="ANN" lnClass="GGIO" srcLDInst="ANN" srcCBName="GCB10" intAddr="VB003" serviceType="goose"/>
              <ExtRef desc="relatorio" iedName="QPC1_LT1_UPC1" srcLDInst="PRO" srcCBName="RCB01" intAddr="VB004" serviceType="Report"/>
              <ExtRef desc="vazio" intAddr="VB009" serviceType="GOOSE"/>
            </Inputs>
          </LN0>
        </LDevice>
      </Server>
    </AccessPoint>
  </IED>
  <IED name="QPC1_LT1_UPC1" type="SEL-411L" manufacturer="SEL"/>
  <IED name="QPC1_SPARE"/>
</SCL>
"""

#: `</?TagName` -- attribute values never contain a `<`, so this only ever
#: rewrites real tags.
_TAG_RE = re.compile(r"(</?)([A-Za-z][A-Za-z0-9]*)")

#: The edition-2 URI. Deliberately NOT the one `scd._SCL_NS` declares: the
#: parser must not care which URI a vendor stamped on the document.
_OTHER_NS = "http://www.iec.ch/61850/2007/SCL"


def _scd_text(flavour: str = "bare") -> str:
    """The same document in four namespace flavours.

    ``bare``     no namespace at all (hand-written / stripped exports)
    ``default``  ``xmlns=`` the standard SCL URI (what Architect writes)
    ``prefixed`` every tag carries an ``scl:`` prefix
    ``other``    ``xmlns=`` a DIFFERENT URI, e.g. an edition-2 export
    """
    if flavour == "bare":
        return _BODY
    if flavour == "default":
        return _BODY.replace("<SCL>", f'<SCL xmlns="{scd._SCL_NS}">', 1)
    if flavour == "other":
        return _BODY.replace("<SCL>", f'<SCL xmlns="{_OTHER_NS}">', 1)
    if flavour == "prefixed":
        head, sep, rest = _BODY.partition("\n")
        body = _TAG_RE.sub(lambda m: m.group(1) + "scl:" + m.group(2), rest)
        return head + sep + body.replace(
            "<scl:SCL>", f'<scl:SCL xmlns:scl="{scd._SCL_NS}">', 1)
    raise AssertionError(f"unknown flavour {flavour!r}")


FLAVOURS = ("bare", "default", "prefixed", "other")


@pytest.fixture
def scd_file(tmp_path: Path) -> Path:
    """The reference document, no namespace."""
    p = tmp_path / "SE_TESTE.scd"
    p.write_text(_scd_text("bare"), encoding="utf-8")
    return p


def _write(tmp_path: Path, text: str, name: str = "doc.scd") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _by_name(ieds: list[scd.IedInfo]) -> dict[str, scd.IedInfo]:
    return {i.name: i for i in ieds}


# -----------------------------------------------------------------------------
# load_scd
# -----------------------------------------------------------------------------

class TestLoadScd:

    def test_reads_every_identifying_attribute_of_an_ied(self, scd_file):
        """`IedInfo` is what the matcher and the VB Updater see of an IED.
        Fails if any of the five attribute names it reads (`type`,
        `manufacturer`, `desc`, `configVersion`, `name`) is renamed or
        dropped."""
        ied = _by_name(scd.load_scd(scd_file))["QPC1_TR1_UPC1"]
        assert ied.relay_type == "SEL_487E"
        assert ied.manufacturer == "SEL"
        assert ied.description == "Trafo 1"
        assert ied.config_version == "1.2"

    def test_the_ip_comes_from_the_communication_section_not_the_ied(self, scd_file):
        """An `<IED>` carries no address; the IP is joined in from
        `<ConnectedAP iedName=...>`. Fails if `_collect_ip_by_ied` stops keying
        by `iedName` -- and then EVERY relay would fall back to the RID match,
        which is the uncommissioned-relay path, not the normal one."""
        ied = _by_name(scd.load_scd(scd_file))["QPC1_TR1_UPC1"]
        assert ied.ip == "192.0.2.60"

    def test_only_the_p_of_type_ip_is_taken_and_the_type_is_upcased(self, scd_file):
        """The first AP also holds an `IP-SUBNET`; the second writes
        `type="ip"` in lower case. Fails if the `.upper()` on the `type`
        attribute goes (the lower-case IED loses its IP) or if the `== "IP"`
        loosens to a prefix match (the subnet mask becomes the address)."""
        ieds = _by_name(scd.load_scd(scd_file))
        assert ieds["QPC1_TR1_UPC1"].ip == "192.0.2.60"
        assert ieds["QPC1_LT1_UPC1"].ip == "192.0.2.61"

    def test_an_ied_with_no_connectedap_has_no_ip(self, scd_file):
        """`QPC1_SPARE` is configured but not addressed. Fails if a missing IP
        starts coming back as `""` -- `index_by_ip` skips on falsy, but
        `_no_match_reason` in the matcher prints whatever it is given."""
        assert _by_name(scd.load_scd(scd_file))["QPC1_SPARE"].ip is None

    def test_a_missing_attribute_is_none_not_empty_string(self, scd_file):
        """`QPC1_SPARE` declares only `name`. Fails if the reads switch to
        `.get(k, "")` -- `_model_consistent` treats empty as 'no data, cannot
        contradict' and None the same way, but `to_dict()` ships both to the
        UI and they render differently."""
        ied = _by_name(scd.load_scd(scd_file))["QPC1_SPARE"]
        assert (ied.relay_type, ied.manufacturer,
                ied.description, ied.config_version) == (None, None, None, None)

    @pytest.mark.parametrize("flavour", FLAVOURS)
    def test_every_namespace_flavour_parses_identically(self, tmp_path, flavour):
        """The whole reason `_strip_ns` / `_iter_local` exist. `other` uses a
        URI that is NOT `scd._SCL_NS`, so this also pins that the URI is
        ignored rather than matched.

        Fails the moment `_iter_local` is replaced by `root.iter("IED")` or by
        a `findall` with the `_NS` prefix map -- three of the four flavours
        would then return an empty list, and an empty list is reported as
        'SCD vazio ou ilegivel', not as a crash."""
        got = scd.load_scd(_write(tmp_path, _scd_text(flavour)))
        assert [(i.name, i.ip, i.relay_type) for i in got] == [
            ("QPC1_TR1_UPC1", "192.0.2.60", "SEL_487E"),
            ("QPC1_LT1_UPC1", "192.0.2.61", "SEL-411L"),
            ("QPC1_SPARE", None, None),
        ]

    def test_document_order_is_preserved(self, scd_file):
        """`load_scd` returns a list, and the VLAN Mapper's tables are rendered
        in that order. Fails if the walk starts sorting or de-duplicating into
        a dict and back."""
        assert [i.name for i in scd.load_scd(scd_file)] == [
            "QPC1_TR1_UPC1", "QPC1_LT1_UPC1", "QPC1_SPARE"]

    def test_a_repeated_ied_name_is_kept_once(self, tmp_path):
        """The `seen` set. Fails if it goes: `index_by_name` would then be
        built from two entries with one key and the matcher's
        `used_scd_names` bookkeeping would count an IED it never matched."""
        doc = _BODY.replace('<IED name="QPC1_SPARE"/>',
                            '<IED name="QPC1_SPARE"/>\n  '
                            '<IED name="QPC1_SPARE" type="SEL-751"/>')
        got = scd.load_scd(_write(tmp_path, doc))
        assert [i.name for i in got].count("QPC1_SPARE") == 1
        assert _by_name(got)["QPC1_SPARE"].relay_type is None   # first wins

    def test_an_ied_without_a_name_is_skipped(self, tmp_path):
        """A nameless IED has no key to match on. Fails if the `if not name`
        guard goes and an entry with `name=None` reaches `index_by_name`, whose
        `.upper()` would raise."""
        doc = _BODY.replace('<IED name="QPC1_SPARE"/>', '<IED type="SEL-751"/>')
        assert [i.name for i in scd.load_scd(_write(tmp_path, doc))] == [
            "QPC1_TR1_UPC1", "QPC1_LT1_UPC1"]

    def test_a_missing_file_returns_an_empty_list(self, tmp_path):
        """Graceful, not fatal: the tools call this on a path the user chose.
        Fails if it starts raising -- a 500 instead of 'SCD vazio ou
        ilegivel'."""
        assert scd.load_scd(tmp_path / "nope.scd") == []

    def test_malformed_xml_returns_an_empty_list(self, tmp_path):
        """Same contract for a truncated upload. Fails if `ET.ParseError` stops
        being caught."""
        assert scd.load_scd(_write(tmp_path, "<SCL><IED name=")) == []

    def test_a_directory_is_not_a_file(self, tmp_path):
        """`is_file()`, not `exists()`. Fails if the guard loosens and
        `ET.parse` gets a directory, which raises `IsADirectoryError` --
        an `OSError`, so it is caught, but only by luck."""
        assert scd.load_scd(tmp_path) == []


# -----------------------------------------------------------------------------
# index_by_ip / index_by_name
# -----------------------------------------------------------------------------

class TestIndexes:

    def test_index_by_ip_skips_ieds_without_one(self, scd_file):
        """Fails if the `if not ied.ip` guard goes: a `None` key would then
        match every relay whose RDB has no readable IPADDR."""
        idx = scd.index_by_ip(scd.load_scd(scd_file))
        assert set(idx) == {"192.0.2.60", "192.0.2.61"}

    def test_a_duplicate_ip_keeps_the_first_ied_and_warns(self, tmp_path, caplog):
        """Two IEDs on one address is a project error the engineer must see.
        Fails if the `continue` becomes an overwrite (last wins, silently) or
        if the warning is dropped."""
        doc = _BODY.replace('<P type="ip">192.0.2.61</P>',
                            '<P type="ip">192.0.2.60</P>')
        ieds = scd.load_scd(_write(tmp_path, doc))
        with caplog.at_level(logging.WARNING, logger="sellib.scl.read"):
            idx = scd.index_by_ip(ieds)
        assert idx["192.0.2.60"].name == "QPC1_TR1_UPC1"
        assert "IP duplicado" in caplog.text

    def test_index_by_name_upcases_the_key(self, scd_file):
        """The matcher looks up `r.rid.upper()`. Fails if this stops upcasing:
        the RID fallback -- the whole uncommissioned-relay path -- would match
        only when the engineer typed the RID in exactly the case Architect
        used."""
        idx = scd.index_by_name(scd.load_scd(scd_file))
        assert "QPC1_TR1_UPC1" in idx
        assert idx["QPC1_TR1_UPC1"].ip == "192.0.2.60"

    def test_index_by_name_does_not_lowercase_the_stored_name(self, scd_file):
        """Only the KEY is upcased. Fails if the value is normalised too --
        `Match.scd_name` is shown on screen and pasted into reports, so it must
        stay as the SCD spells it."""
        idx = scd.index_by_name(scd.load_scd(scd_file))
        assert idx["QPC1_TR1_UPC1"].name == "QPC1_TR1_UPC1"


# -----------------------------------------------------------------------------
# extract_gse_communication_map
# -----------------------------------------------------------------------------

class TestGseMap:

    def test_keys_a_control_block_by_publisher_ldinst_and_cbname(self, scd_file):
        """The triple is the identity of a GOOSE control block; the VLAN Mapper
        joins subscriptions to addresses on it. Fails if `_gse_key` changes
        shape or order -- the join would silently produce zero matches."""
        got = scd.extract_gse_communication_map(scd_file)
        assert set(got) == {
            ("QPC1_TR1_UPC1", "PRO", "GCB01"),
            ("QPC1_TR1_UPC1", "ANN", "GCB02"),
        }

    def test_reads_all_four_address_parameters(self, scd_file):
        """MAC / APPID / VLAN-ID / VLAN-PRIORITY are the switch port map.
        Fails if a `P type` spelling changes -- the row would render blank and
        the engineer would configure the switch from an empty table."""
        gse = scd.extract_gse_communication_map(scd_file)[
            ("QPC1_TR1_UPC1", "PRO", "GCB01")]
        assert gse.mac_address == "01-0C-CD-01-00-01"
        assert gse.appid == "0001"
        assert gse.vlan_id == "00A"
        assert gse.vlan_priority == "4"

    def test_the_vlan_id_stays_a_string(self, scd_file):
        """`00A` is hexadecimal in some exports and decimal in others, and this
        module refuses to guess. Fails if anything here starts calling `int()`
        -- `00A` would raise and `010` would silently become 10."""
        gse = scd.extract_gse_communication_map(scd_file)[
            ("QPC1_TR1_UPC1", "PRO", "GCB01")]
        assert isinstance(gse.vlan_id, str)

    def test_an_absent_parameter_is_none(self, scd_file):
        """GCB02 declares only a MAC. Fails if the missing keys start coming
        back as `""`, which renders as a filled-in-but-blank cell rather than
        an obviously missing one."""
        gse = scd.extract_gse_communication_map(scd_file)[
            ("QPC1_TR1_UPC1", "ANN", "GCB02")]
        assert (gse.appid, gse.vlan_id, gse.vlan_priority) == (None, None, None)

    def test_a_gse_without_a_cbname_is_skipped(self, scd_file):
        """The third `<GSE>` in the fixture has `cbName=""`. Without a control
        block name there is nothing a subscription could join to. Fails if the
        `if not cb_name` guard goes and an unjoinable row appears in the map."""
        got = scd.extract_gse_communication_map(scd_file)
        assert not [k for k in got if k[2] == ""]

    def test_a_p_type_is_matched_case_insensitively(self, tmp_path):
        """The fixture writes `MAC-Address`; the lookup key is
        `MAC-ADDRESS`. Fails if the `.upper()` on the `type` attribute goes --
        every MAC in the map would become None, for every real SCD, because
        that is the spelling Architect uses."""
        gse = scd.extract_gse_communication_map(
            _write(tmp_path, _scd_text("bare")))[("QPC1_TR1_UPC1", "PRO", "GCB01")]
        assert gse.mac_address == "01-0C-CD-01-00-01"

    @pytest.mark.parametrize("flavour", FLAVOURS)
    def test_every_namespace_flavour_yields_the_same_map(self, tmp_path, flavour):
        """Companion to the `load_scd` flavour test; fails the same way."""
        got = scd.extract_gse_communication_map(
            _write(tmp_path, _scd_text(flavour)))
        assert sorted(got) == [("QPC1_TR1_UPC1", "ANN", "GCB02"),
                               ("QPC1_TR1_UPC1", "PRO", "GCB01")]

    def test_a_missing_file_returns_an_empty_map(self, tmp_path):
        """Fails if it starts raising."""
        assert scd.extract_gse_communication_map(tmp_path / "nope.scd") == {}

    def test_malformed_xml_returns_an_empty_map(self, tmp_path):
        """Fails if `ET.ParseError` stops being caught."""
        assert scd.extract_gse_communication_map(_write(tmp_path, "<SCL")) == {}


# -----------------------------------------------------------------------------
# extract_goose_subscriptions_by_ied
# -----------------------------------------------------------------------------

class TestGooseSubscriptions:

    def test_groups_subscriptions_under_the_subscribing_ied(self, scd_file):
        """Keyed by the IED that OWNS the `<ExtRef>`, not by the publisher.
        Fails if the walk starts keying by `ExtRef@iedName` -- publisher and
        subscriber would swap and every VLAN would be assigned to the wrong
        switch port."""
        got = scd.extract_goose_subscriptions_by_ied(scd_file)
        assert set(got) == {"QPC1_TR1_UPC1"}
        assert {s.publisher_ied for s in got["QPC1_TR1_UPC1"]} == {"QPC1_LT1_UPC1"}

    def test_an_ied_with_no_subscriptions_is_absent_from_the_map(self, scd_file):
        """`if subs:` -- the caller iterates the dict and an empty list would
        render an IED with an empty table. Fails if the guard goes."""
        got = scd.extract_goose_subscriptions_by_ied(scd_file)
        assert "QPC1_LT1_UPC1" not in got
        assert "QPC1_SPARE" not in got

    def test_two_intaddrs_of_one_control_block_are_one_subscription(self, scd_file):
        """VB001 and VB002 come from the same dataset over one GOOSE. Fails if
        the `seen` set goes -- a 60-point dataset would appear as 60 identical
        rows in the VLAN table."""
        got = scd.extract_goose_subscriptions_by_ied(scd_file)["QPC1_TR1_UPC1"]
        assert [(s.src_ld_inst, s.src_cb_name) for s in got] == [
            ("PRO", "GCB09"), ("ANN", "GCB10")]

    def test_the_first_occurrence_keeps_its_desc_and_intaddr(self, scd_file):
        """De-duplication keeps the FIRST row, so `desc`/`intAddr` describe one
        arbitrary point of the dataset and are documented as informative. Fails
        if the dedup starts keeping the last -- the label on screen would
        change for no visible reason."""
        first = scd.extract_goose_subscriptions_by_ied(scd_file)["QPC1_TR1_UPC1"][0]
        assert first.desc == "LT1 falha GOOSE"
        assert first.int_addr == "VB001"

    def test_the_service_type_is_matched_case_insensitively(self, scd_file):
        """The fixture's GCB10 row writes `serviceType="goose"`. Fails if the
        `.upper()` goes: exports that lower-case it would come back with no
        subscriptions at all."""
        got = scd.extract_goose_subscriptions_by_ied(scd_file)["QPC1_TR1_UPC1"]
        assert ("ANN", "GCB10") in [(s.src_ld_inst, s.src_cb_name) for s in got]

    def test_a_non_goose_extref_is_ignored(self, scd_file):
        """The `serviceType="Report"` row points at `RCB01`. Fails if the
        filter goes -- report control blocks would be assigned GOOSE VLANs."""
        got = scd.extract_goose_subscriptions_by_ied(scd_file)["QPC1_TR1_UPC1"]
        assert "RCB01" not in [s.src_cb_name for s in got]

    def test_a_template_extref_with_no_publisher_is_ignored(self, scd_file):
        """Architect writes `<ExtRef serviceType="GOOSE">` with no `iedName`
        or `srcCBName` for a link the engineer has not closed yet. Fails if
        the guard goes: those become subscriptions with empty keys and the
        VLAN report claims links that do not exist."""
        got = scd.extract_goose_subscriptions_by_ied(scd_file)["QPC1_TR1_UPC1"]
        assert all(s.publisher_ied and s.src_cb_name for s in got)

    def test_a_subscription_may_have_no_matching_gse(self, scd_file):
        """GCB09/GCB10 are published by an IED whose `<ConnectedAP>` declares
        no `<GSE>`, and the subscriptions are still returned. That is
        deliberate -- an unresolvable subscription is the diagnostic. Fails if
        the extraction starts filtering against the communication map."""
        subs = scd.extract_goose_subscriptions_by_ied(scd_file)["QPC1_TR1_UPC1"]
        comm = scd.extract_gse_communication_map(scd_file)
        assert subs
        assert all((s.publisher_ied, s.src_ld_inst, s.src_cb_name) not in comm
                   for s in subs)

    @pytest.mark.parametrize("flavour", FLAVOURS)
    def test_every_namespace_flavour_yields_the_same_subscriptions(
            self, tmp_path, flavour):
        """Companion to the other two flavour tests; fails the same way."""
        got = scd.extract_goose_subscriptions_by_ied(
            _write(tmp_path, _scd_text(flavour)))
        assert {k: [(s.publisher_ied, s.src_ld_inst, s.src_cb_name)
                    for s in v] for k, v in got.items()} == {
            "QPC1_TR1_UPC1": [("QPC1_LT1_UPC1", "PRO", "GCB09"),
                              ("QPC1_LT1_UPC1", "ANN", "GCB10")]}

    def test_a_missing_file_returns_an_empty_dict(self, tmp_path):
        """Fails if it starts raising."""
        assert scd.extract_goose_subscriptions_by_ied(tmp_path / "nope.scd") == {}

    def test_malformed_xml_returns_an_empty_dict(self, tmp_path):
        """Fails if `ET.ParseError` stops being caught."""
        assert scd.extract_goose_subscriptions_by_ied(
            _write(tmp_path, "<SCL")) == {}


# -----------------------------------------------------------------------------
# _strip_ns / _iter_local, directly
# -----------------------------------------------------------------------------

class TestNamespaceHelpers:

    def test_strip_ns_removes_a_braced_uri(self):
        """`ElementTree` reports `{uri}Local` for any namespaced tag, whether
        the document used a default xmlns or a prefix. Fails if the split
        changes side or separator."""
        assert scd._strip_ns("{http://www.iec.ch/61850/2003/SCL}IED") == "IED"

    def test_strip_ns_leaves_a_bare_tag_alone(self):
        """Fails if the `"}" in tag` guard goes -- `rsplit` on a bare tag is
        harmless, but the guard is what documents that both shapes arrive."""
        assert scd._strip_ns("IED") == "IED"
