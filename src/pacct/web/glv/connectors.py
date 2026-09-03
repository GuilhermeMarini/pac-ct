"""GLE connectors: the named network the drawing uses in place of a line.

Whoever draws the logic uses a **connector** so as not to drag a long line
across the page. In the XML that is NOT an edge: it is two or more
`<element type="Connector">` sharing one `<label>`, with nothing to link
them --

    conn 658:  element 667     ->  Connector #723   <label>Cont1</label>
    conn 134:  Connector #206  ->  element 517      <label>Cont1</label>

-- so the signal dies there and every line downstream of the emitting end is
left with no colour. The label is a NETWORK NAME.

Measured across the 418 `.gle` of `rdbs/extracted/`: 107 use a connector, 324
elements, 110 networks. The shape is invariant -- **exactly one receiver per
network, 110 of 110** --, fan-out reaches 9 emitters, and 9 networks cross
pages (`LEDGROUND`: driven in `SCADA`, derived in `LEDS`).

**This module extracts STRUCTURE; the JS is what EVALUATES.** The tree travels
as data to `evaluatePage`, which resolves it with the same primitives it
already uses for the drawn blocks. Evaluating here would put the semantics of
NOT/RTRIG/latch in two languages, and this project's gotcha list is in good
part what happened when one rule had two copies.

The walk stops at the first element with a **named Relay Word bit** -- the name
of a `SYMBOL`, or a block's derived output (`relay_model.derived_bit_for`).
That is what makes a `PCN` end honestly: the relay publishes `PCN01Q` directly,
there is nothing to simulate. Measured, it is also what keeps the equation at
~10 bits (median 10, maximum 10) instead of expanding to the primary inputs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from selfiles.gle import element_info, is_const_symbol_name

_logger = logging.getLogger(__name__)

# Depth ceiling for the walk. The corpus does not come close (0 truncated in
# 110), but latch feedback would, and an extractor that goes into a loop locks
# up the whole `build_diagram`.
MAX_DEPTH = 32

# The branch the ceiling or the cycle guard cut. A visibly incomplete equation
# is honest; an invented one is not.
CUT = "…"

# Drawn operators -> SELOGIC notation. Measured in the corpus: inside a
# connector equation only AND (271), OR (262), SYMBOL (1036) and PCN (12)
# appear -- NO arithmetic block --, so `*` and `+` do not collide with
# ADD/MULT.
_INFIX = {"AND": " * ", "OR": " + "}

# Gate modifier -> how the text shows it. RTRIG/FTRIG are not evaluable
# without history between polling turns; `evaluatePage` already treats them as
# pass-through, and the text says there was an edge there instead of
# pretending there is nothing.
_MOD_PREFIX = {"NOT": "!", "RTRIG": "↑", "FTRIG": "↓"}


@dataclass(frozen=True)
class ConnectorNet:
    """A connector network: one driver, N derivations, one equation."""
    label: str
    receiver: str          # id do elemento Connector que RECEBE o sinal
    emitters: tuple        # ids dos Connector que EMITEM
    driver_page: str       # the safe_page_id the receiver sits on
    pages: tuple           # safe_page_ids onde ha alguma ponta
    tree: dict             # a expressao, em JSON, pro avaliador do cliente
    bits: frozenset        # folhas nomeadas da Relay Word -- o que polar
    equation: str          # the SELOGIC text, for the legend


def _safe_page_id(name: str, fallback: int = 0) -> str:
    """Same rule as `gle_pages.list_pages`, so the keys match."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name or "") or f"page_{fallback}"


def _named_bit(el, relay_model) -> str:
    """The Relay Word bit THIS element publishes, if it publishes one.

    It is where the walk stops: if the relay already reports the value by
    name, there is no logic to reconstruct. Covers the named `SYMBOL` and the
    derived output of a block with state (`PLT04`, `PCT03Q`, `PCN01Q`, ...).
    """
    t = el.get("type") or ""
    if t == "SYMBOL":
        name = element_info(el).get("name") or ""
        if not name or is_const_symbol_name(name):
            return ""
        if relay_model is not None and relay_model.is_analog_symbol(name):
            return ""       # an analogue is continuous, not a bit
        return name.upper()
    if relay_model is None:
        return ""
    le = el.find("logic_element")
    if le is None:
        return ""
    try:
        instance = int(le.get("physical_instance_number") or 0)
    except ValueError:
        instance = 0
    bit = relay_model.derived_bit_for(
        t, instance, le.get("physical_instance_name") or "")
    return (bit or "").upper()


def _const_value(el):
    """The value of a SYMBOL that is a numeric literal, or `None`."""
    if (el.get("type") or "") != "SYMBOL":
        return None
    name = element_info(el).get("name") or ""
    if not name or not is_const_symbol_name(name):
        return None
    try:
        return float(name)
    except ValueError:
        return None


class _Graph:
    """The GLE flattened to what the walk needs: elements, edges, labels."""

    def __init__(self, gle_root):
        self.el: dict = {}            # id -> element
        self.page_of: dict = {}       # id -> safe_page_id
        self.label_of: dict = {}      # id -> label (so' Connector)
        self.by_label: dict = {}      # label -> [ids], in document order
        self.incoming: dict = {}      # id -> [(src_id, sink_port)]
        self.is_source: set = set()   # ids que aparecem como origem
        self.is_sink: set = set()     # ids que aparecem como destino
        for i, page in enumerate(gle_root.findall(".//page")):
            safe = _safe_page_id(page.get("name", ""), i)
            for el in page.findall(".//element"):
                eid = el.get("id") or ""
                self.el[eid] = el
                self.page_of[eid] = safe
                if el.get("type") == "Connector":
                    label = (el.findtext("label") or "").strip()
                    self.label_of[eid] = label
                    self.by_label.setdefault(label, []).append(eid)
            for conn in page.findall(".//connection"):
                src = conn.find("source_port")
                dst = conn.find("sink_port")
                if src is None or dst is None:
                    continue
                sid = src.get("element_id") or ""
                did = dst.get("element_id") or ""
                try:
                    port = int(dst.get("port_number") or 0)
                except ValueError:
                    port = 0
                self.incoming.setdefault(did, []).append((sid, port))
                self.is_source.add(sid)
                self.is_sink.add(did)

    def input_mod(self, eid: str, port: int) -> str:
        el = self.el.get(eid)
        if el is None:
            return ""
        return element_info(el).get("input_mods", {}).get(port, "") or ""


def _wrap(text: str, mod: str) -> str:
    return f"{_MOD_PREFIX[mod]}{text}" if mod in _MOD_PREFIX else text


def _wrap_tree(node: dict, mod: str) -> dict:
    if mod == "NOT":
        return {"op": "NOT", "args": [node]}
    if mod in ("RTRIG", "FTRIG"):
        return {"op": mod, "args": [node]}
    return node


def _walk(g: _Graph, eid: str, relay_model, seen: frozenset, depth: int):
    """`(arvore, texto, bits)` da logica que chega ate `eid`."""
    if depth > MAX_DEPTH or eid in seen:
        return {"op": "CUT"}, CUT, frozenset()
    el = g.el.get(eid)
    if el is None:
        return {"op": "CUT"}, CUT, frozenset()
    seen = seen | {eid}

    bit = _named_bit(el, relay_model)
    if bit:
        return {"op": "BIT", "name": bit}, bit, frozenset({bit})

    const = _const_value(el)
    if const is not None:
        # Literal from the drawing (counter preset, comparator threshold).
        # It does not exist in the Relay Word -- `collect_bit_names` already
        # excludes it --, so it never enters the polling. But it is a VALUE:
        # showing it as a cut would say the equation is incomplete when it is
        # not. Measured: it was the only cut left in the corpus, the preset
        # `6` of a PCN.
        text = el.find("logic_element").get("physical_instance_name") or ""
        return {"op": "CONST", "value": const}, text, frozenset()

    etype = el.get("type") or ""
    if etype == "Connector":
        # Emitter of another network: climb to ITS receiver. That is what
        # lets one connector feed another with no special case.
        peer = _receiver_of(g, g.label_of.get(eid, ""))
        if peer is None or not g.incoming.get(peer):
            return {"op": "CUT"}, CUT, frozenset()
        src, port = g.incoming[peer][0]
        tree, text, bits = _walk(g, src, relay_model, seen, depth + 1)
        mod = g.input_mod(peer, port)
        return _wrap_tree(tree, mod), _wrap(text, mod), bits

    args = sorted(g.incoming.get(eid, []), key=lambda sp: sp[1])
    trees, texts, bits = [], [], set()
    for src, port in args:
        tree, text, b = _walk(g, src, relay_model, seen, depth + 1)
        mod = g.input_mod(eid, port)
        trees.append(_wrap_tree(tree, mod))
        texts.append(_wrap(text, mod))
        bits |= b
    if not trees:
        # Block with no input and no named bit: there is nothing to read and
        # nothing to reconstruct. Cutting is the honest answer.
        return {"op": "CUT"}, CUT, frozenset()
    if len(trees) == 1:
        return trees[0], texts[0], frozenset(bits)
    sep = _INFIX.get(etype)
    if sep is None:
        # Drawn block that is neither AND nor OR and publishes no bit: the
        # client decides what to do with it from the `op`, and the text shows
        # the shape.
        return ({"op": etype, "args": trees},
                f"{etype}({', '.join(texts)})", frozenset(bits))
    return ({"op": etype, "args": trees},
            "(" + sep.join(texts) + ")", frozenset(bits))


def _receiver_of(g: _Graph, label: str):
    """O unico Connector do label que RECEBE sinal, ou `None`."""
    got = [eid for eid in g.by_label.get(label, ()) if eid in g.is_sink]
    return got[0] if len(got) == 1 else None


def extract(gle_root, relay_model=None) -> dict:
    """`{label: ConnectorNet}` for the whole GLE.

    A label with no receiver -- or with more than one -- does not become a
    network: it goes to the log and is ignored. It does not happen in the
    corpus (110 of 110 with exactly one), and guessing which end drives it
    would paint a line on a hunch.
    """
    g = _Graph(gle_root)
    nets: dict = {}
    for label, ids in g.by_label.items():
        receivers = [eid for eid in ids if eid in g.is_sink]
        if len(receivers) != 1:
            _logger.info(
                "[glv] conector %r ignorado: %d receptor(es) entre %d ponta(s)"
                " -- sem um acionador unico nao da' pra dizer de onde vem o "
                "sinal", label, len(receivers), len(ids))
            continue
        receiver = receivers[0]
        emitters = tuple(eid for eid in ids
                         if eid in g.is_source and eid != receiver)
        feed = g.incoming.get(receiver) or ()
        if not feed:
            continue
        src, port = feed[0]
        tree, text, bits = _walk(g, src, relay_model, frozenset({receiver}), 0)
        mod = g.input_mod(receiver, port)
        tree, text = _wrap_tree(tree, mod), _wrap(text, mod)
        pages = tuple(sorted({g.page_of.get(eid, "") for eid in ids}))
        nets[label] = ConnectorNet(
            label=label, receiver=receiver, emitters=emitters,
            driver_page=g.page_of.get(receiver, ""), pages=pages,
            tree=tree, bits=bits, equation=text)
    return nets


def nets_on_page(nets: dict, safe_page_id: str) -> dict:
    """The networks with an end ON THIS page -- what the legend lists and what
    the polling has to feed."""
    return {label: net for label, net in nets.items()
            if safe_page_id in net.pages}
