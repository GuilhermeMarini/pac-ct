"""Conectores do GLE: a rede nomeada que o desenho usa no lugar de uma linha.

Quem desenha usa um **conector** pra nao puxar uma linha comprida pela pagina.
No XML isso NAO e' uma aresta: sao dois ou mais `<element type="Connector">`
que compartilham um `<label>`, e nada os liga --

    conn 658:  elemento 667    ->  Connector #723   <label>Cont1</label>
    conn 134:  Connector #206  ->  elemento 517     <label>Cont1</label>

-- de modo que o sinal morre ali e toda linha a jusante da ponta que emite fica
sem cor. O label e' um NOME DE REDE.

Medido nos 418 `.gle` de `rdbs/extracted/`: 107 usam conector, 324 elementos,
110 redes. A forma e' invariante -- **exatamente um receptor por rede, 110 de
110** --, o leque de saida chega a 9 emissores, e 9 redes atravessam pagina
(`LEDGROUND`: acionada em `SCADA`, derivada em `LEDS`).

**Este modulo extrai ESTRUTURA; quem AVALIA e' o JS.** A arvore viaja como
dado pro `evaluatePage`, que a resolve com as mesmas primitivas que ja usa pros
blocos desenhados. Avaliar aqui poria a semantica de NOT/RTRIG/latch em duas
linguagens, e a lista de gotchas deste projeto e' em boa parte o que aconteceu
quando uma regra teve duas copias.

A caminhada para no primeiro elemento com **bit nomeado da Relay Word** -- o
nome de um `SYMBOL`, ou a saida derivada de um bloco (`relay_model
.derived_bit_for`). E' o que faz um `PCN` terminar honestamente: o rele publica
`PCN01Q` direto, nao ha o que simular. Medido, e' tambem o que mantem a equacao
em ~10 bits (mediana 10, maximo 10) em vez de expandir ate as entradas
primarias.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from selfiles.gle import element_info, is_const_symbol_name

_logger = logging.getLogger(__name__)

# Teto de profundidade da caminhada. O corpus nao chega perto (0 truncadas em
# 110), mas realimentacao de latch chegaria, e um extrator que entra em laco
# trava o `build_diagram` inteiro.
MAX_DEPTH = 32

# O ramo que o teto ou a guarda de ciclo cortou. Uma equacao visivelmente
# incompleta e' honesta; uma inventada nao.
CUT = "…"

# Operadores desenhados -> notacao SELOGIC. Medido no corpus: dentro de equacao
# de conector so' aparecem AND (271), OR (262), SYMBOL (1036) e PCN (12) --
# NENHUM bloco aritmetico --, entao `*` e `+` nao colidem com ADD/MULT.
_INFIX = {"AND": " * ", "OR": " + "}

# Modificador de porta -> como o texto o mostra. RTRIG/FTRIG nao sao
# avaliaveis sem historico entre voltas do polling; o `evaluatePage` ja os
# trata como passagem, e o texto diz que houve uma borda ali em vez de fingir
# que nao ha nada.
_MOD_PREFIX = {"NOT": "!", "RTRIG": "↑", "FTRIG": "↓"}


@dataclass(frozen=True)
class ConnectorNet:
    """Uma rede de conector: um acionador, N derivacoes, uma equacao."""
    label: str
    receiver: str          # id do elemento Connector que RECEBE o sinal
    emitters: tuple        # ids dos Connector que EMITEM
    driver_page: str       # safe_page_id onde esta o receptor
    pages: tuple           # safe_page_ids onde ha alguma ponta
    tree: dict             # a expressao, em JSON, pro avaliador do cliente
    bits: frozenset        # folhas nomeadas da Relay Word -- o que polar
    equation: str          # o texto SELOGIC, pra legenda


def _safe_page_id(name: str, fallback: int = 0) -> str:
    """Mesma regra de `gle_pages.list_pages`, pra as chaves baterem."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name or "") or f"page_{fallback}"


def _named_bit(el, relay_model) -> str:
    """O bit da Relay Word que ESTE elemento publica, se publicar algum.

    E' onde a caminhada para: se o rele ja reporta o valor por nome, nao ha
    logica a reconstruir. Cobre o `SYMBOL` nomeado e a saida derivada de um
    bloco com estado (`PLT04`, `PCT03Q`, `PCN01Q`, ...).
    """
    t = el.get("type") or ""
    if t == "SYMBOL":
        name = element_info(el).get("name") or ""
        if not name or is_const_symbol_name(name):
            return ""
        if relay_model is not None and relay_model.is_analog_symbol(name):
            return ""       # analogico tem valor continuo, nao e' bit
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
    """O valor de um SYMBOL que e' literal numerico, ou `None`."""
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
    """O GLE achatado no que a caminhada precisa: elementos, arestas, labels."""

    def __init__(self, gle_root):
        self.el: dict = {}            # id -> element
        self.page_of: dict = {}       # id -> safe_page_id
        self.label_of: dict = {}      # id -> label (so' Connector)
        self.by_label: dict = {}      # label -> [ids], ordem do documento
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
        # Literal do desenho (preset de contador, limiar de comparador). Nao
        # existe na Relay Word -- `collect_bit_names` ja o exclui --, entao
        # nao entra no polling. Mas e' um VALOR: mostra-lo como corte diria
        # que a equacao esta incompleta quando nao esta. Medido: era o unico
        # corte que sobrava no corpus, o preset `6` de um PCN.
        text = el.find("logic_element").get("physical_instance_name") or ""
        return {"op": "CONST", "value": const}, text, frozenset()

    etype = el.get("type") or ""
    if etype == "Connector":
        # Emissor de outra rede: sobe pro receptor DELA. E' o que faz um
        # conector alimentar outro sem caso especial.
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
        # Bloco sem entrada e sem bit nomeado: nao ha o que ler nem o que
        # reconstruir. Cortar e' a resposta honesta.
        return {"op": "CUT"}, CUT, frozenset()
    if len(trees) == 1:
        return trees[0], texts[0], frozenset(bits)
    sep = _INFIX.get(etype)
    if sep is None:
        # Bloco desenhado que nao e' E nem OU e nao publica bit: o cliente
        # decide o que fazer com ele pelo `op`, e o texto mostra a forma.
        return ({"op": etype, "args": trees},
                f"{etype}({', '.join(texts)})", frozenset(bits))
    return ({"op": etype, "args": trees},
            "(" + sep.join(texts) + ")", frozenset(bits))


def _receiver_of(g: _Graph, label: str):
    """O unico Connector do label que RECEBE sinal, ou `None`."""
    got = [eid for eid in g.by_label.get(label, ()) if eid in g.is_sink]
    return got[0] if len(got) == 1 else None


def extract(gle_root, relay_model=None) -> dict:
    """`{label: ConnectorNet}` do GLE inteiro.

    Um label sem receptor -- ou com mais de um -- nao vira rede: fica no log e
    e' ignorado. Nao acontece no corpus (110 de 110 com exatamente um), e
    adivinhar qual ponta aciona pintaria linha por chute.
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
    """As redes com alguma ponta NESTA pagina -- o que a legenda lista e o que
    o polling precisa alimentar."""
    return {label: net for label, net in nets.items()
            if safe_page_id in net.pages}
