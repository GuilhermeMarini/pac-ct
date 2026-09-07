"""Small, real-shaped GLE and SCD fragments for the byte-surgery tests.

Hand-written rather than sliced out of `samples/`: the real files are ~1 MB
each, which makes a failing assertion unreadable. The shapes here are copied
verbatim from `samples/LT3_UPC1_GL1.gle.xml` and
`samples/substation_demo.scd`, including the details that
matter to the regexes:

- an `<element id="N" type="SYMBOL">` wrapping a `<logic_element>` -- the GLE
  Exporter matches the outer one, the VB Updater the inner one;
- TWO `<ports>` blocks per element, the first self-closing (`<ports />`) when
  the symbol has no inputs. The exporter reads "first block = input, second =
  output" positionally, so a fixture with only one block tests the wrong thing;
- both comment spellings, `<comment />` and `<comment>TEXT</comment>`;
- latin-1 accented text, because QuickSet writes latin-1 while declaring
  utf-8 in the header.
"""

from __future__ import annotations

# The XML declaration says utf-8 and the bytes are latin-1. That is not a bug
# in the fixture -- it is what AcSELerator QuickSet writes, and the reason
# every writer in this codebase encodes with latin-1.
GLE_HEAD = (
    b'<?xml version="1.0" encoding="utf-8"?>\r\n'
    b'<editor version="1.0">\r\n'
    b'  <page name="GL1">\r\n'
    b'    <elements>\r\n'
)

GLE_TAIL = (
    b'    </elements>\r\n'
    b'  </page>\r\n'
    b'</editor>\r\n'
)


def symbol_element(eid: str, name: str, *, in_comment: str | None = None,
                   out_comment: str | None = None) -> bytes:
    """One `<element type="SYMBOL">` with an input and an output ports block.

    ``None`` for a comment writes the self-closing `<comment />` spelling;
    a string writes `<comment>TEXT</comment>`. Text is encoded latin-1, like
    the rest of a real GLE.
    """
    def block(index: int, comment: str | None) -> bytes:
        if comment is None:
            body = b'<comment />'
        else:
            body = b'<comment>' + comment.encode("latin-1") + b'</comment>'
        return (b'                <ports>\r\n'
                b'                  <port index="' + str(index).encode() + b'">\r\n'
                b'                    ' + body + b'\r\n'
                b'                  </port>\r\n'
                b'                </ports>\r\n')

    return (
        b'            <element id="' + eid.encode() + b'" type="SYMBOL" '
        b'left="282" top="108" locked="False">\r\n'
        b'              <logic_element type="SYMBOL" physical_instance_number="0" '
        b'physical_instance_name="' + name.encode("latin-1") + b'" alias="">\r\n'
        b'                <comment />\r\n'
        + block(0, in_comment)
        + block(0, out_comment)
        + b'              </logic_element>\r\n'
        b'            </element>\r\n'
    )


def gle(*elements: bytes) -> bytes:
    """A whole GLE document around the given elements."""
    return GLE_HEAD + b"".join(elements) + GLE_TAIL


#: The document most tests use.
#:
#: - 542 / VB105: an output comment, no input comment. The input port is
#:   therefore `<comment />`, which `_GLE_PORT_COMMENT_RE` does NOT match --
#:   so a substitution lands on the OUTPUT. That asymmetry is real and is
#:   pinned in `test_gle_bytes.py`.
#: - 543 / VB007: both ports self-closing. Zero-padded on purpose: the writer
#:   looks the name up as `VB7`.
#: - 544 / TMB1A: a Relay Word symbol, not a virtual bit. Must never be touched.
#: - 545 / VB042: an output comment, so the zero-padding lookup can be tested
#:   on a symbol the writer can actually reach.
SAMPLE_GLE = gle(
    symbol_element("542", "VB105", out_comment="TR1 UPC1 FALHA GOOSE"),
    symbol_element("543", "VB007", out_comment=None),
    symbol_element("544", "TMB1A", out_comment="DISJUNTOR 52A POSIÇÃO"),
    symbol_element("545", "VB042", out_comment="RESERVA"),
)


# -----------------------------------------------------------------------------
# SCD
# -----------------------------------------------------------------------------

def scd(*ieds: bytes, ips: dict[str, str] | None = None) -> bytes:
    """An SCL document around the given IEDs.

    `ips` adds the `<Communication>` block, `{ied_name: ip}`. It is separate
    from `ied()` because that is where an address actually lives in SCL -- on
    the `<ConnectedAP>` under a `<SubNetwork>`, not inside the IED -- and
    because it is what the RDB x SCD matcher pairs on: it matches by IP or
    RID and NEVER by name, two files routinely naming the same bay
    differently. An SCD built without `ips` is deliberately unmatchable,
    which is the case `unmatched_rdb` exists to report.
    """
    comm = b""
    if ips:
        aps = b"".join(
            b'      <ConnectedAP iedName="' + n.encode() + b'" apName="S1">\r\n'
            b'        <Address>\r\n'
            b'          <P type="IP">' + ip.encode() + b'</P>\r\n'
            b'        </Address>\r\n'
            b'      </ConnectedAP>\r\n'
            for n, ip in ips.items())
        comm = (b'  <Communication>\r\n'
                b'    <SubNetwork name="W01">\r\n'
                + aps
                + b'    </SubNetwork>\r\n'
                b'  </Communication>\r\n')
    return (b'<?xml version="1.0" encoding="UTF-8"?>\r\n'
            b'<SCL xmlns="http://www.iec.ch/61850/2003/SCL" '
            b'xmlns:esel="http://www.selinc.com/2005/SCL">\r\n'
            + comm
            + b"".join(ieds)
            + b'</SCL>\r\n')


def ied(name: str, *extrefs: bytes, private: bytes = b"") -> bytes:
    """One `<IED>`. `private` is the SEL private block, if any -- see
    `goose_subscriptions`."""
    return (b'  <IED name="' + name.encode() + b'" type="SEL-411L">\r\n'
            + private
            + b'    <AccessPoint name="S1">\r\n'
            b'      <Inputs>\r\n'
            + b"".join(extrefs)
            + b'      </Inputs>\r\n'
            b'    </AccessPoint>\r\n'
            b'  </IED>\r\n')


def goose_subscriptions(*subs: tuple[str, str]) -> bytes:
    """The `<Private type="SEL_GooseSubscription">` block SEL Architect
    regenerates on every save.

    Each `(cb_name, pub_rx_status)` becomes one `<esel:GooseSubscription>`;
    an empty `pub_rx_status` writes no attribute, which is a real
    subscription whose health simply is not mapped to a bit.
    """
    body = b""
    for cb, rx in subs:
        attr = b' pubRxStatus="' + rx.encode() + b'"' if rx else b""
        body += (b'      <esel:GooseSubscription iedName="QPC1_UPC2" '
                 b'ldInst="ANN" cbName="' + cb.encode() + b'" '
                 b'datSet="GOPB_138"' + attr + b' confRev="1" />\r\n')
    return (b'    <Private type="SEL_GooseSubscription">\r\n'
            + body
            + b'    </Private>\r\n')


def rx_status_extref(vb: str, desc: str | None = None,
                     cb: str = "GoSB00") -> bytes:
    """The `<ExtRef>` twin of a `pubRxStatus` bit.

    Publisher and control block filled in, and NO `doName`/`daName`: there is
    no data attribute to point at, because the bit carries the subscription's
    health rather than a value out of its dataset. Also no `serviceType`,
    which is the shape all 202 of `samples/substation_demo.scd` carry.
    """
    head = b'        <ExtRef '
    if desc is not None:
        head += b'desc="' + desc.encode("utf-8") + b'" '
    return (head + b'iedName="QPC1_UPC2" srcLDInst="ANN" srcLNClass="LLN0" '
            b'srcCBName="' + cb.encode() + b'" '
            b'intAddr="' + vb.encode() + b'" />\r\n')


def extref(vb: str, desc: str | None = None) -> bytes:
    """An `<ExtRef intAddr="VBnnn">`, with or without a `desc` attribute.

    A real SCD writes `desc` FIRST and `intAddr` late in the attribute list
    (see the sample), so that is the order here -- the insert path looks for
    `intAddr=` and appends after it, which only exercises the interesting case
    when `desc` is genuinely absent rather than merely later.
    """
    head = b'        <ExtRef '
    if desc is not None:
        head += b'desc="' + desc.encode("utf-8") + b'" '
    return (head + b'iedName="QPC1_UPC2" ldInst="ANN" lnClass="GGIO" '
            b'intAddr="' + vb.encode() + b'" serviceType="GOOSE" />\r\n')


def extref_signal(vb: str, desc: str | None = None, *,
                  ied: str = "PUB", src_ld: str = "CFG",
                  src_ln: str = "LLN0", cb: str = "GoSB00",
                  ld: str = "ANN", prefix: str = "", ln_class: str = "GGIO",
                  ln_inst: str = "1", do: str = "Ind01",
                  da: str = "stVal") -> bytes:
    """An `<ExtRef>` that subscribes to a real data attribute.

    Unlike `extref`, this one carries the whole path: `ldInst`, `prefix`,
    `lnClass`, `lnInst`, `doName` and `daName` name the point INSIDE the
    publisher, which is what `vb_source` matches against that publisher's
    `sAddr` to reach the Relay Word bit. `srcLDInst`/`srcLNClass`/`srcCBName`
    name the control block that carries it.
    """
    head = b'        <ExtRef '
    if desc is not None:
        head += b'desc="' + desc.encode("utf-8") + b'" '
    return (head
            + b'iedName="' + ied.encode() + b'" '
            b'srcLDInst="' + src_ld.encode() + b'" '
            b'srcLNClass="' + src_ln.encode() + b'" '
            b'srcCBName="' + cb.encode() + b'" '
            b'ldInst="' + ld.encode() + b'" '
            + (b'prefix="' + prefix.encode() + b'" ' if prefix else b"")
            + b'lnClass="' + ln_class.encode() + b'" '
            b'lnInst="' + ln_inst.encode() + b'" '
            b'doName="' + do.encode() + b'" '
            b'daName="' + da.encode() + b'" '
            b'intAddr="' + vb.encode() + b'" serviceType="GOOSE" />\r\n')


def saddr_point(bit: str, *, ld: str = "ANN", prefix: str = "",
                ln_class: str = "GGIO", ln_inst: str = "1",
                do: str = "Ind01", da: str = "stVal") -> tuple:
    """One `sAddr="db:<bit>"` of a publisher, addressed the way SCL spells it."""
    return (ld, prefix, ln_class, ln_inst, do, da, bit)


def saddr_ied(name: str, *points: tuple) -> bytes:
    """A publisher `<IED>`: the `<DAI sAddr="db:BIT">` side of the join.

    This is the half `sel_short_addresses` reads. The subscriber's `<ExtRef>`
    names `(ldInst, prefix+lnClass+lnInst, doName, daName)` and the bit is
    whatever `sAddr` that same address carries here -- the SCD is the only
    place the two are written down together.
    """
    by_ln: dict = {}
    for ld, prefix, ln_class, ln_inst, do, da, bit in points:
        by_ln.setdefault((ld, prefix, ln_class, ln_inst), []).append((do, da, bit))
    lds: dict = {}
    for (ld, prefix, ln_class, ln_inst), dais in by_ln.items():
        body = b"".join(
            b'            <DOI name="' + do.encode() + b'">'
            b'<DAI name="' + da.encode() + b'" sAddr="db:' + bit.encode() + b'"/>'
            b'</DOI>\r\n'
            for do, da, bit in dais)
        lds.setdefault(ld, b"")
        lds[ld] += (b'          <LN lnType="T1" lnClass="' + ln_class.encode()
                    + b'" inst="' + ln_inst.encode() + b'"'
                    + (b' prefix="' + prefix.encode() + b'"' if prefix else b"")
                    + b'>\r\n' + body + b'          </LN>\r\n')
    devices = b"".join(
        b'        <LDevice inst="' + ld.encode() + b'">\r\n' + body
        + b'        </LDevice>\r\n'
        for ld, body in lds.items())
    return (b'  <IED name="' + name.encode() + b'" type="SEL-411L">\r\n'
            b'    <AccessPoint name="S1">\r\n'
            b'      <Server>\r\n'
            + devices
            + b'      </Server>\r\n'
            b'    </AccessPoint>\r\n'
            b'  </IED>\r\n')


def extref_placeholder(vb: str) -> bytes:
    """An `<ExtRef intAddr="VBnnn">` with no publisher.

    The slot exists and nothing was wired into it. SEL Architect writes all
    256 of them whether or not the engineer used any, so this is the shape 232
    of the reference SCD's ExtRefs have -- `extref` is NOT this, because it
    fills `iedName` in.
    """
    return (b'        <ExtRef intAddr="' + vb.encode() + b'" '
            b'serviceType="GOOSE" />\r\n')
