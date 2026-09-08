"""What PAC CT gets out of an IEC 61850 SCD, across two libraries.

The SCD is the *other* half of every cross-check this toolkit does.
`sellib.match` pairs a relay in the RDB with an IED here, the VB Updater
copies descriptions out of `<ExtRef>` into the relay's diagram, and the VLAN
Mapper builds the switch port map out of `<GSE>`. A parse that quietly returns
nothing does not crash -- it produces an empty report that looks like "nothing
to do", which is why the graceful paths are pinned as hard as the happy one.

**This file used to characterise the reader itself**, in four namespace
flavours, across 47 assertions. That reader is not here any more and is not
SELlib's either: `py61850.scl` reads an SCL file as an IEC 61850-6 object
model, for any vendor, and tests every one of those behaviours in its own
suite -- namespace flavours included, since a hand-made SCD that declares none
and a vendor export that declares four are both real. Re-asserting them here
would be a second opinion about somebody else's code that can only ever drift
from it.

What is pinned instead is the seam PAC CT actually stands on, and the parts of
it that are PAC CT's own decisions rather than either library's:

- the join between an IED's identity and its address, which no single section
  of an SCD holds;
- **GSE only** in the VLAN map -- an `SMV` is keyed the same way and is not a
  GOOSE publication;
- subscriptions arriving with SEL's `pubRxStatus` health bit already attached,
  which is the whole reason a vendor library sits on top of the general one;
- and that every one of those answers "" or `{}` for a file that cannot be
  read, on the paths a web route reaches.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from py61850.scl import SclDocument
from sellib.scl.read import sel_goose_rx_status, sel_goose_subscriptions

from pacct.web.glv import vb_source
from pacct.web.vlan_mapper import compute_ied_vlan_rows

#: Shapes copied from `samples/substation_demo.scd`:
#:
#: - the IP does NOT live on the `<IED>`; it is cross-referenced from
#:   `<ConnectedAP iedName=...>` in `<Communication>`;
#: - `<GSE>` sits *inside* that same `<ConnectedAP>`, beside the AP's own
#:   `<Address>`;
#: - an `<SMV>` sits there too, keyed `(ied, ldInst, cbName)` exactly like a
#:   GSE, and is a sampled-value stream rather than a GOOSE publication;
#: - the second AP writes `type="ip"` in lower case, which real exports do;
#: - `QPC1_SPARE` has no ConnectedAP at all -- a relay configured but not yet
#:   given an address;
#: - `QPC1_TR1_UPC1` subscribes twice to the same control block (two intAddrs
#:   of one dataset), once to a second one, and carries a template ExtRef with
#:   no publisher, which is what Architect writes before a link is closed;
#: - the SEL private block declares `VB001` as the health bit of `GCB09`.
_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL"
     xmlns:esel="http://www.selinc.com/2005/SCL">
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
        <SMV ldInst="MU" cbName="SV01">
          <Address>
            <P type="MAC-Address">01-0C-CD-04-00-01</P>
            <P type="VLAN-ID">0FF</P>
          </Address>
        </SMV>
      </ConnectedAP>
      <ConnectedAP iedName="QPC1_LT1_UPC1" apName="S1">
        <Address>
          <P type="ip">192.0.2.61</P>
        </Address>
        <GSE ldInst="PRO" cbName="GCB09">
          <Address>
            <P type="MAC-Address">01-0C-CD-01-00-09</P>
            <P type="VLAN-ID">00B</P>
          </Address>
        </GSE>
        <GSE ldInst="ANN" cbName="GCB10">
          <Address>
            <P type="MAC-Address">01-0C-CD-01-00-10</P>
            <P type="VLAN-ID">00C</P>
          </Address>
        </GSE>
      </ConnectedAP>
    </SubNetwork>
  </Communication>
  <IED name="QPC1_TR1_UPC1" type="SEL_487E" manufacturer="SEL" desc="Trafo 1" configVersion="1.2">
    <Private type="SEL_GooseSubscription">
      <esel:GooseSubscription iedName="QPC1_LT1_UPC1" ldInst="PRO" cbName="GCB09"
                              datSet="GOPB_138" pubRxStatus="VB001" confRev="1"/>
    </Private>
    <AccessPoint name="S1">
      <Server>
        <LDevice inst="ANN">
          <LN0 lnClass="LLN0" inst="" lnType="T_LLN0">
            <Inputs>
              <ExtRef desc="LT1 falha GOOSE" iedName="QPC1_LT1_UPC1" srcLDInst="PRO" srcCBName="GCB09" intAddr="VB001" serviceType="GOOSE"/>
              <ExtRef desc="LT1 abertura" iedName="QPC1_LT1_UPC1" ldInst="ANN" lnClass="GGIO" doName="Ind01" daName="stVal" srcLDInst="PRO" srcCBName="GCB09" intAddr="VB002" serviceType="GOOSE"/>
              <ExtRef desc="LT1 anunciacao" iedName="QPC1_LT1_UPC1" ldInst="ANN" lnClass="GGIO" doName="Ind02" daName="stVal" srcLDInst="ANN" srcCBName="GCB10" intAddr="VB003" serviceType="goose"/>
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
  <DataTypeTemplates>
    <LNodeType id="T_LLN0" lnClass="LLN0"/>
  </DataTypeTemplates>
</SCL>
"""

_SUBSCRIBER = "QPC1_TR1_UPC1"
_PUBLISHER = "QPC1_LT1_UPC1"


@pytest.fixture
def scd_file(tmp_path: Path) -> Path:
    p = tmp_path / "SE_TESTE.scd"
    p.write_text(_BODY, encoding="utf-8")
    return p


@pytest.fixture
def doc(scd_file: Path) -> SclDocument:
    parsed = SclDocument.parse(scd_file)
    assert parsed is not None
    return parsed


def _rows(scd_file: Path) -> dict:
    return {r.ied_name: r for r in compute_ied_vlan_rows(scd_file)}


# -- identity and address are in different sections -------------------------

class TestTheIdentityAddressJoin:
    """No single section of an SCD says both who an IED is and where it is.
    The identity is on `<IED>`, the address on a `<ConnectedAP>` that names it
    from `<Communication>`. Joining them is the consumer's job, and every tool
    here that lists relays does it."""

    def test_a_row_carries_the_identity_and_the_address_together(self, scd_file):
        row = _rows(scd_file)[_SUBSCRIBER]
        assert row.ip == "192.0.2.60"
        assert row.relay_type == "SEL_487E"
        assert row.description == "Trafo 1"

    def test_an_ied_with_no_connected_ap_is_listed_without_an_address(
            self, scd_file):
        """A relay configured but not yet addressed is a real state and is
        exactly what the report exists to show. Dropping it would make an
        unfinished station look finished."""
        row = _rows(scd_file)["QPC1_SPARE"]
        assert row.ip is None
        assert row.ied_name == "QPC1_SPARE"

    def test_the_p_type_is_matched_case_insensitively(self, scd_file):
        """The second ConnectedAP writes `type="ip"` in lower case, which real
        exports do."""
        assert _rows(scd_file)[_PUBLISHER].ip == "192.0.2.61"

    def test_every_ied_gets_a_row(self, scd_file):
        assert set(_rows(scd_file)) == {
            _SUBSCRIBER, _PUBLISHER, "QPC1_SPARE"}


# -- the VLAN map is GOOSE, and only GOOSE ----------------------------------

class TestOnlyGooseReachesTheVlanMap:
    """`ControlBlockAddress` keys a `<GSE>` and an `<SMV>` the same way --
    `(iedName, ldInst, cbName)` -- because that is the triple a subscription
    resolves against either of them by. They are not the same thing: an SMV
    carries sampled values at 4 kHz and no GOOSE subscription ever points at
    one. The filter is this tool's, not the library's, and it is here because
    a mixed station has both: the reference mixed-vendor SCD carries 16 SMV
    control blocks beside its GSEs.
    """

    def test_a_publishers_own_gse_vlans_are_its_tx(self, scd_file):
        row = _rows(scd_file)[_PUBLISHER]
        assert row.tx_vlans == ["00B", "00C"]

    def test_an_smv_vlan_is_not_reported_as_goose(self, scd_file):
        """`SV01` publishes on VLAN 0FF. If the SMV got in, this relay's TX
        list would claim a GOOSE VLAN that carries no GOOSE at all, and the
        switch port map built from it would be wrong."""
        row = _rows(scd_file)[_SUBSCRIBER]
        assert "0FF" not in row.tx_vlans

    def test_a_subscription_resolves_to_the_publishers_vlan(self, scd_file):
        row = _rows(scd_file)[_SUBSCRIBER]
        assert row.rx_vlans == ["00B", "00C"]
        assert row.publishers_by_vlan["00B"] == [_PUBLISHER]


# -- subscriptions arrive with SEL's half already attached ------------------

class TestSubscriptionsCarryTheSelHealthBit:
    """The reason a vendor library sits on top of the general one. `<ExtRef>`
    is standard SCL and says nothing about health; `pubRxStatus` lives in a
    `<Private>` block on the IED and says which bit goes to 1 when the
    publisher stops arriving. Only the two together tell a tool that `VB001`
    is not a signal out of the dataset."""

    def test_a_health_bit_is_named_by_the_private_block(self, doc):
        health = sel_goose_rx_status(doc)[_SUBSCRIBER]
        assert set(health) == {"VB001"}
        assert health["VB001"].src_cb_name == "GCB09"

    def test_the_subscription_carries_the_bit_that_watches_it(self, doc):
        subs = {s.src_cb_name: s for s in sel_goose_subscriptions(doc)[_SUBSCRIBER]}
        assert subs["GCB09"].rx_status_bit == "VB001"
        assert subs["GCB10"].rx_status_bit is None

    def test_two_intaddrs_of_one_control_block_are_one_subscription(self, doc):
        """`VB001` and `VB002` both point at `GCB09`. They are two uses of one
        dataset, and counting them twice would double the RX count the VLAN
        Mapper reports for a switch port."""
        subs = sel_goose_subscriptions(doc)[_SUBSCRIBER]
        assert [s.src_cb_name for s in subs] == ["GCB09", "GCB10"]

    def test_a_non_goose_extref_is_not_a_goose_subscription(self, doc):
        """`RCB01` is a Report control block. It is a real input and py61850
        reports it; what it is not is a GOOSE publication with a VLAN."""
        subs = sel_goose_subscriptions(doc)[_SUBSCRIBER]
        assert "RCB01" not in {s.src_cb_name for s in subs}

    def test_a_template_extref_with_no_publisher_is_not_a_subscription(self, doc):
        """`VB009` names no publisher: a slot Architect wrote and nobody
        wired. 232 of the 256 in a reference SCD look like this."""
        subs = sel_goose_subscriptions(doc)[_SUBSCRIBER]
        assert all(s.publisher_ied for s in subs)

    def test_an_ied_that_subscribes_to_nothing_is_absent(self, doc):
        assert _PUBLISHER not in sel_goose_subscriptions(doc)
        assert "QPC1_SPARE" not in sel_goose_subscriptions(doc)


# -- a file that cannot be read must not take a route down ------------------

class TestAnUnreadableFileIsEmptyAndNeverAnException:
    """Every one of these is reached from a web route with a file a visitor
    uploaded. An exception here is a 500 on a page that should have said "no
    IEDs found"; each of them had the graceful behaviour before the libraries
    split and has to keep it."""

    @pytest.fixture(params=["missing", "malformed", "directory"])
    def unusable(self, request, tmp_path):
        if request.param == "missing":
            return tmp_path / "nao_existe.scd"
        if request.param == "malformed":
            p = tmp_path / "quebrado.scd"
            p.write_text("<SCL><IED name=", encoding="utf-8")
            return p
        return tmp_path

    def test_the_vlan_rows_are_empty(self, unusable):
        assert compute_ied_vlan_rows(unusable) == []

    def test_the_sel_readers_are_empty(self, unusable):
        assert sel_goose_rx_status(unusable) == {}
        assert sel_goose_subscriptions(unusable) == {}

    def test_the_vb_source_map_reports_the_reason_rather_than_raising(
            self, unusable):
        result = vb_source.read(unusable, relay_name="X", ip="")
        assert result.error and result.sources == {}


# -- which IED a diagram belongs to -----------------------------------------

class TestMatchingADiagramToItsIed:
    """`vb_source` picks the IED whose GOOSE map is drawn onto an open
    diagram. Picking the wrong one draws the NEIGHBOURING relay's
    subscriptions, and nothing about that looks wrong on screen."""

    def test_the_ip_is_tried_before_the_name(self, doc):
        """Offline there is no DEVID -- the key the live MMS path matches on,
        read off the relay itself. The IP is the one identifier the visitor
        typed in and that the SCD states outright; the RDB relay name is a
        hint, because an RDB and an SCD routinely spell a bay differently."""
        assert vb_source._match_ied(
            doc, relay_name=_PUBLISHER, ip="192.0.2.60") == (_SUBSCRIBER, "IP")

    def test_the_name_is_matched_case_insensitively(self, doc):
        assert vb_source._match_ied(
            doc, relay_name=_SUBSCRIBER.lower(), ip="") == (_SUBSCRIBER, "nome")

    def test_a_duplicate_address_keeps_the_first_ied(self, tmp_path):
        """Two IEDs on one IP is a configuration error to report elsewhere,
        not one to resolve by silently preferring the later device. First
        wins, which is what the index this replaced did."""
        text = _BODY.replace('<P type="ip">192.0.2.61</P>',
                             '<P type="IP">192.0.2.60</P>')
        p = tmp_path / "dup.scd"
        p.write_text(text, encoding="utf-8")
        parsed = SclDocument.parse(p)
        assert vb_source._match_ied(
            parsed, relay_name="", ip="192.0.2.60") == (_SUBSCRIBER, "IP")

    def test_nothing_matching_picks_nothing(self, doc):
        assert vb_source._match_ied(doc, relay_name="OUTRO", ip="10.0.0.1") \
            == ("", "")

    def test_a_single_ied_document_needs_no_identifier(self, tmp_path):
        p = tmp_path / "solo.scd"
        p.write_text('<?xml version="1.0"?>'
                     '<SCL xmlns="http://www.iec.ch/61850/2003/SCL">'
                     '<IED name="SOLO"/></SCL>', encoding="utf-8")
        parsed = SclDocument.parse(p)
        assert vb_source._match_ied(parsed, relay_name="", ip="") \
            == ("SOLO", "único IED")


def test_an_unreadable_scd_is_logged_and_not_swallowed_in_silence(
        tmp_path, caplog):
    """The failure mode this whole class of test exists for: an empty result
    that looks like "nothing to do". A log line is what tells an engineer the
    difference between a station with no GOOSE and a file that would not
    open."""
    bad = tmp_path / "quebrado.scd"
    bad.write_text("<SCL><IED name=", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="py61850.scl.document"):
        assert compute_ied_vlan_rows(bad) == []
    assert any("quebrado.scd" in r.getMessage() for r in caplog.records)
