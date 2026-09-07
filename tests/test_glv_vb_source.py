"""Where each VBnnn of an open diagram comes from, read off the project SCD.

A GLE draws `VB023` and nothing else: the block carries no hint of which relay
publishes it, over which GOOSE, or what the bit is called on the other side.
All three are in the SCD, but in two halves that only the SCD writes down
together -- the subscriber's `<ExtRef>` names the point `(ldInst, LN, DO, DA)`
inside the publisher, and the PUBLISHER's `<DAI sAddr="db:BIT">` is what says
that point is `PSV05`. Joining them is what this module does, and it is the
only reason the offline diagram can name a Relay Word bit it never read.

Three shapes come out, and the corpus says there is no fourth: a subscription
to a data attribute, a `pubRxStatus` health bit (a control block and no data
attribute, so no bit exists to name), and a placeholder `<ExtRef>` with no
`iedName` at all -- 232 of the 256 an SEL Architect SCD always writes.
"""

from __future__ import annotations

from pacct.paths import SAMPLES_DIR
from pacct.web.glv import vb_source
from tests import gle_fixtures as fx

# One subscriber and one publisher. The subscriber's VB003 points at
# `ANN/PSVGGIO1/Ind05.stVal`, and the publisher says that address is `PSV05`.
_SUBSCRIBER = "QPC1_LT2_UPC1"
_PUBLISHER = "QPC1_LT2_UPC2"

_SCD_BYTES = fx.scd(
    fx.ied(
        _SUBSCRIBER,
        fx.extref_signal("VB003", "50/62BF LT2 UPC2", ied=_PUBLISHER,
                         prefix="PSV", ln_inst="1", do="Ind05"),
        fx.extref_signal("VB007", "SEM SADDR NO PUBLICADOR", ied=_PUBLISHER,
                         prefix="LT", ln_inst="6", do="Ind06"),
        fx.rx_status_extref("VB101", "FALHA GOOSE LT2 UPC2", cb="GoSB01"),
        fx.extref_placeholder("VB050"),
        private=fx.goose_subscriptions(("GoSB01", "VB101")),
    ),
    fx.saddr_ied(
        _PUBLISHER,
        fx.saddr_point("PSV05", prefix="PSV", ln_inst="1", do="Ind05"),
    ),
    ips={_SUBSCRIBER: "192.0.2.10", _PUBLISHER: "192.0.2.11"},
)


def _scd(tmp_path, data: bytes = b"") -> object:
    p = tmp_path / "projeto.scd"
    p.write_bytes(data or _SCD_BYTES)
    return p


# -- the join ----------------------------------------------------------------

def test_a_signal_vb_reaches_the_publishers_relay_word_bit(tmp_path):
    """The headline. Fails if the ExtRef address is not matched against the
    publisher's `sAddr` -- the panel would then show a 61850 path and never
    the bit name, which is the only half anybody says out loud on site."""
    m = vb_source.read(_scd(tmp_path), relay_name=_SUBSCRIBER, ip="192.0.2.10")
    vb = m.sources["VB003"]
    assert vb.kind == vb_source.SIGNAL
    assert vb.ied == _PUBLISHER
    assert vb.ln == "PSVGGIO1"
    assert vb.bit == "PSV05"
    assert vb.desc == "50/62BF LT2 UPC2"


def test_a_quality_vb_names_its_control_block_and_has_no_bit(tmp_path):
    """A `pubRxStatus` bit receives a subscription's health, not a value: its
    ExtRef names a control block and no data attribute. Fails if it is treated
    as a signal, which would print an address whose last three halves are
    empty and claim a bit that does not exist."""
    m = vb_source.read(_scd(tmp_path), relay_name=_SUBSCRIBER, ip="192.0.2.10")
    vb = m.sources["VB101"]
    assert vb.kind == vb_source.QUALITY
    assert vb.cb == "GoSB01"
    assert vb.bit == ""
    assert vb.do == "" and vb.da == ""


def test_an_extref_without_a_publisher_is_a_placeholder(tmp_path):
    """232 of the 256 in the reference SCD. Fails if absence is reported as a
    subscription to an IED named "" -- the audit tint exists to tell the two
    apart."""
    m = vb_source.read(_scd(tmp_path), relay_name=_SUBSCRIBER, ip="192.0.2.10")
    assert m.sources["VB050"].kind == vb_source.PLACEHOLDER
    assert m.sources["VB050"].ied == ""


def test_a_signal_whose_publisher_has_no_saddr_keeps_the_address(tmp_path):
    """The publisher may simply not map that point to a Relay Word bit. Fails
    if the whole row is dropped: the subscription is real and the IED and the
    logical node are still worth showing -- only the bit is missing."""
    m = vb_source.read(_scd(tmp_path), relay_name=_SUBSCRIBER, ip="192.0.2.10")
    vb = m.sources["VB007"]
    assert vb.kind == vb_source.SIGNAL
    assert vb.ied == _PUBLISHER
    assert vb.ln == "LTGGIO6"
    assert vb.bit == ""


def test_vb_numbers_are_keyed_the_way_the_gle_draws_them(tmp_path):
    """`intAddr="VB7"` and a GLE symbol named `VB007` are the same bit, and the
    browser looks the map up by `data-bit`. Both sides normalise to three
    digits or every lookup misses in silence."""
    assert vb_source.normalise("VB7") == "VB007"
    assert vb_source.normalise("VB007") == "VB007"
    assert vb_source.normalise("VB1234") == "VB1234"
    assert vb_source.normalise("TRIP") == ""
    data = fx.scd(fx.ied("SOLO", fx.extref_signal("VB7", "x", ied="PUB")),
                  fx.saddr_ied("PUB", fx.saddr_point("SV01")))
    m = vb_source.read(_scd(tmp_path, data), relay_name="SOLO", ip="")
    assert "VB007" in m.sources
    assert m.sources["VB007"].bit == "SV01"


# -- which IED of the SCD this relay is ---------------------------------------

def test_the_ied_is_matched_by_ip_before_anything_else(tmp_path):
    """Offline there is no DEVID -- the live MMS path's key -- so the IP the
    visitor typed on the selection screen is the strongest signal there is.
    Fails if the RDB relay name wins: the two files routinely spell the same
    bay differently, and matching the neighbour's IED is worse than matching
    none."""
    m = vb_source.read(_scd(tmp_path), relay_name="NOME QUE NAO EXISTE",
                       ip="192.0.2.10")
    assert m.ied == _SUBSCRIBER
    assert m.matched_by == "IP"
    assert m.sources["VB003"].bit == "PSV05"


def test_the_name_matches_when_there_is_no_ip(tmp_path):
    """A diagram opened against an SCD with no `<Communication>` block still
    has a name to go on."""
    data = fx.scd(fx.ied("SOLO", fx.extref_signal("VB003", "x", ied="PUB")),
                  fx.ied("OUTRO"),
                  fx.saddr_ied("PUB", fx.saddr_point("SV01")))
    m = vb_source.read(_scd(tmp_path, data), relay_name="SOLO", ip="192.0.2.99")
    assert m.ied == "SOLO"
    assert m.matched_by == "nome"


def test_a_single_ied_scd_needs_no_match_at_all(tmp_path):
    """An ICD, or an SCD exported for one bay. There is nothing to confuse it
    with, so refusing on a name mismatch would be pedantry."""
    data = fx.scd(fx.ied("SEJA_QUAL_FOR",
                         fx.extref_signal("VB003", "x", ied="SEJA_QUAL_FOR")))
    m = vb_source.read(_scd(tmp_path, data), relay_name="OUTRA COISA", ip="")
    assert m.ied == "SEJA_QUAL_FOR"
    assert m.matched_by == "único IED"


def test_no_match_says_so_instead_of_guessing(tmp_path):
    """Fails if some IED is picked anyway. Showing the neighbouring relay's
    GOOSE map on this diagram is worse than showing nothing, because nothing
    about it looks wrong."""
    m = vb_source.read(_scd(tmp_path), relay_name="NAO EXISTE", ip="192.0.2.99")
    assert m.sources == {}
    assert m.ied == ""
    assert "2 IEDs" in m.error


def test_an_unreadable_scd_reports_instead_of_raising(tmp_path):
    """The SCD is a file a user uploaded. A diagram whose VB source cannot be
    read is still a diagram."""
    p = tmp_path / "projeto.scd"
    p.write_bytes(b"nao e' xml")
    m = vb_source.read(p, relay_name="X", ip="")
    assert m.sources == {}
    assert m.error


# -- against the real thing ---------------------------------------------------

def test_the_reference_scd_resolves_a_known_bit():
    """The anchor: a real 27-IED substation SCD, not a fixture. `QPC2_TR2_UPC1`
    subscribes VB002 to `QPC1_TR2_UPC2 ANN/PSVGGIO1 Ind05.stVal`, and that
    publisher calls the address `PSV05`.

    Also pins the census the offline panel reports, because those three counts
    are the whole argument for the audit tint: of 256 ExtRefs, 24 have a
    publisher, 8 of them are health bits, and the rest are placeholders.
    """
    m = vb_source.read(SAMPLES_DIR / "substation_demo.scd",
                       relay_name="QPC2_TR2_UPC1", ip="")
    assert m.ied == "QPC2_TR2_UPC1"
    vb = m.sources["VB002"]
    assert (vb.ied, vb.ld, vb.ln, vb.do, vb.da) == (
        "QPC1_TR2_UPC2", "ANN", "PSVGGIO1", "Ind05", "stVal")
    assert vb.bit == "PSV05"

    kinds = [s.kind for s in m.sources.values()]
    assert len(m.sources) == 256
    assert kinds.count(vb_source.QUALITY) == 8
    assert kinds.count(vb_source.SIGNAL) == 16
    assert kinds.count(vb_source.PLACEHOLDER) == 232


# -- the panel prefix is not redundant ----------------------------------------

def test_a_subscribers_publishers_span_several_panels():
    """The premise a shortened IED label would need, and the corpus refuses it.

    The viewer used to draw the publisher as `TR2_UPC2`, stripping a leading
    `QPC<n>_` on the grounds that "o prefixo do painel se repete em todos".
    It does not: a bay subscribes ACROSS panels, so the prefix is the only
    part of the name that says WHICH panel published the bit -- and dropping
    it makes two different relays draw the same label on the same page.

    Measured here rather than asserted: every subscriber in the reference SCD
    draws from three or four panels, and each one has at least one pair that
    collapses onto a single label once the prefix goes.
    """
    import re

    def strip_panel(n: str) -> str:
        return re.sub(r"^QPC\d+_", "", n)

    for relay in ("QPC2_TR2_UPC1", "QPC1_TR1_UPC1", "QPC2_TR1_UPC3"):
        m = vb_source.read(SAMPLES_DIR / "substation_demo.scd",
                           relay_name=relay, ip="")
        publishers = {s.ied for s in m.sources.values() if s.ied}
        panels = {p.split("_")[0] for p in publishers}
        assert len(panels) > 1, f"{relay}: publishers all in one panel {panels}"

        collapsed: dict[str, set[str]] = {}
        for p in publishers:
            collapsed.setdefault(strip_panel(p), set()).add(p)
        ambiguous = {k: v for k, v in collapsed.items() if len(v) > 1}
        assert ambiguous, f"{relay}: no collision, the strip would be safe here"


def test_the_viewer_does_not_shorten_the_publisher_name():
    """No panel-prefix strip in the diagram's JS, ever again.

    The name is drawn over the GLE where room is tight, which is what made
    shortening tempting -- but the test above shows it renders two relays
    identically. If width is a problem the answer is the ellipsis and the
    tooltip, never dropping the half of the name that identifies the panel.

    Grepped rather than exercised because this lives in a template's
    JavaScript, which the suite cannot run (see `docs/ENGINEERING-NOTES.md`).
    """
    import re

    from pacct.paths import GLV_TEMPLATES_DIR

    js = (GLV_TEMPLATES_DIR / "dashboard.html").read_text(encoding="utf-8")
    assert not re.search(r"replace\(\s*/\^QPC", js), (
        "the panel-prefix strip is back in the GLV's VB source layer")
    assert "shortIed" not in js, "shortIed() is back"
