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

def scd(*ieds: bytes) -> bytes:
    return (b'<?xml version="1.0" encoding="UTF-8"?>\r\n'
            b'<SCL xmlns="http://www.iec.ch/61850/2003/SCL">\r\n'
            + b"".join(ieds)
            + b'</SCL>\r\n')


def ied(name: str, *extrefs: bytes) -> bytes:
    return (b'  <IED name="' + name.encode() + b'" type="SEL-411L">\r\n'
            b'    <AccessPoint name="S1">\r\n'
            b'      <Inputs>\r\n'
            + b"".join(extrefs)
            + b'      </Inputs>\r\n'
            b'    </AccessPoint>\r\n'
            b'  </IED>\r\n')


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
