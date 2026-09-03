"""As variáveis do desenho que a conexão escolhida NÃO alcança.

O diagrama pinta de indeterminado tudo que não consegue ler, e até aqui um bit
que o relé não serve era indistinguível de um bit que ainda não chegou. São
coisas diferentes na hora de comissionar: um espera, o outro precisa ser
adicionado ao modelo do servidor do IED (MMS) ou não existe nesta Relay Word
(telnet). Quem sabe qual é qual é o TRANSPORTE, e é por isso que a resposta
sai pela costura (`Transport.unreachable`) e não de um `getattr` no privado de
um transporte que o diagrama nem sabe qual é -- o mesmo motivo do
`coverage_for`.

`None` (e nunca `[]`) enquanto não dá pra saber: sem conexão, sem mapa, ou sem
DNA lido, uma lista vazia diria "está tudo alcançável", que é a mentira mais
cara das três.
"""
from __future__ import annotations

import logging
import threading

from pacct.web.glv.diagram import GlvDiagram
from pacct.web.glv.transport import (
    MODE_FAST_METER,
    MODE_TAR,
    MODE_TARGET,
)
from pacct.web.glv.transport.telnet import TelnetTransport

LOG = logging.getLogger("test")


# --- telnet ----------------------------------------------------------------

class _Layout:
    def __init__(self, known=(), not_findable=()):
        self.bit_to_pos = {b.upper(): (0, 0) for b in known}
        self.row_to_names = {}
        self.not_findable = {b.upper() for b in not_findable}


class _Reader:
    def __init__(self, layout):
        self.layout = layout


class _Client:
    def __init__(self, dna=None):
        self.dnaDef = dna
        self.fid = "FID"
        self.devid = "DEV"


def _telnet(mode, *, client=None, reader=None):
    t = TelnetTransport("192.0.2.10", 23, acc_password="OTTER", logger=LOG)
    t.mode = mode
    t.client = client
    t.reader = reader
    return t


def test_a_disconnected_telnet_transport_cannot_tell_yet():
    assert _telnet(MODE_TARGET).unreachable({"PLT01"}) is None


def test_the_relay_word_lists_what_the_discovery_could_not_find():
    """`not_findable` é a lista negra por FID: bits que já foram procurados
    com `TAR <nome>` e não existem nesta Relay Word."""
    t = _telnet(MODE_TARGET,
                client=_Client(),
                reader=_Reader(_Layout(known=["PLT01"], not_findable=["PLT99"])))
    out = t.unreachable({"PLT01", "PLT99"})
    assert out["names"] == ["PLT99"]
    assert out["reason"] == "relay_word"


def test_a_goose_bit_is_unreachable_by_telnet_even_sem_ter_sido_procurado():
    """`prepare_bits` pula `VB*` de propósito -- eles moram em outra região e
    o Fast Message não os traz. Nunca entraram na lista negra, e mesmo assim
    a tela nunca vai pintá-los: é exatamente o caso que manda o usuário pro
    MMS, então tem que aparecer."""
    t = _telnet(MODE_TARGET, client=_Client(),
                reader=_Reader(_Layout(known=["PLT01"])))
    assert t.unreachable({"PLT01", "VB001"})["names"] == ["VB001"]


def test_the_constants_of_the_drawing_are_not_variables():
    """Um GLE liga entradas a `0` e a `12`; não há o que adicionar no relé."""
    t = _telnet(MODE_TARGET, client=_Client(),
                reader=_Reader(_Layout(known=["PLT01"])))
    assert t.unreachable({"PLT01", "0", "12"})["names"] == []


def test_the_names_are_matched_without_looking_at_the_case():
    t = _telnet(MODE_TARGET, client=_Client(),
                reader=_Reader(_Layout(known=["PLT01"])))
    assert t.unreachable({"plt01"})["names"] == []


def test_the_tar_mode_judges_by_the_relay_word_too():
    t = _telnet(MODE_TAR, client=_Client(),
                reader=_Reader(_Layout(known=["LT1"], not_findable=["SV13T"])))
    out = t.unreachable({"LT1", "SV13T"})
    assert out == {"names": ["SV13T"], "reason": "relay_word"}


def test_the_fast_meter_mode_judges_by_the_dna():
    """7xx: não há `AsciiTargetReader`; os digitais são o subconjunto que o
    relé nomeia no DNA, e `*` é uma linha sem nome."""
    t = _telnet(MODE_FAST_METER,
                client=_Client(dna=[["LT01", "LT02", "*", "*"],
                                    ["IN101", "*", "*", "*"]]))
    out = t.unreachable({"LT01", "IN101", "VB001"})
    assert out["names"] == ["VB001"]
    assert out["reason"] == "dna"


def test_a_fast_meter_relay_that_never_answered_the_dna_cannot_tell():
    """Sem DNA, dizer que os 400 bits do desenho estão fora do relé seria
    inventar; a resposta honesta é que ainda não se sabe."""
    t = _telnet(MODE_FAST_METER, client=_Client(dna=[]))
    assert t.unreachable({"LT01"}) is None


def test_unreachable_answers_while_something_holds_the_transport():
    """`prepare_bits` segura o `_lock` do transporte durante a descoberta
    INTEIRA -- ~90 s numa varredura TAR fria de 3xx. E o painel se atualiza
    justamente quando a conexão muda, que é quando essa varredura está
    rodando: pedir o mesmo lock deixaria a requisição pendurada nela, e a
    resposta é só um diagnóstico de dois dicionários que já estão na memória.
    """
    t = _telnet(MODE_TARGET, client=_Client(),
                reader=_Reader(_Layout(known=["PLT01"], not_findable=["PLT99"])))
    out: list = []
    with t._lock:                      # o lock da descoberta, de outra thread
        worker = threading.Thread(
            target=lambda: out.append(t.unreachable({"PLT01", "PLT99"})),
            daemon=True)
        worker.start()
        worker.join(timeout=2.0)
    assert out and out[0]["names"] == ["PLT99"], \
        "a rota ficou esperando a descoberta terminar"


# --- diagrama --------------------------------------------------------------

class _FakeTransport:
    mode = "fake"

    def __init__(self, answer):
        self.answer = answer
        self.asked = None

    def unreachable(self, bits):
        self.asked = set(bits)
        return self.answer


class _FakeLink:
    def __init__(self, transport):
        self.transport = transport


def _diagram(bits, link=None):
    d = GlvDiagram("d1", relay_name="QPC1", gle_name="GL1", gle_path=None,
                   ip="192.0.2.10", port=23, relay_model=None, logger=LOG)
    d.all_wanted_bits = {b.upper() for b in bits}
    d.link = link
    return d


def test_a_disconnected_diagram_says_it_cannot_tell_instead_of_zero():
    """Zero leria como "está tudo no relé" numa tela que ninguém conectou."""
    out = _diagram({"PLT01"}).unreachable()
    assert out["available"] is False
    assert out["names"] == []


def test_the_diagram_asks_about_every_page_and_not_only_the_open_one():
    """A lista existe pra montar o modelo do servidor do IED; página por
    página, o usuário teria que percorrer o desenho inteiro pra saber o que
    falta. (A cobertura da faixa de status continua sendo por página.)"""
    t = _FakeTransport({"names": ["VB001", "PLT99"], "reason": "relay_word"})
    d = _diagram({"PLT01", "PLT99", "VB001"}, link=_FakeLink(t))
    out = d.unreachable()
    assert t.asked == {"PLT01", "PLT99", "VB001"}
    assert out["available"] is True
    assert out["names"] == ["PLT99", "VB001"]      # ordenado
    assert out["count"] == 2
    assert out["total"] == 3
    assert out["reason"] == "relay_word"


def test_a_transport_that_cannot_tell_yet_leaves_the_diagram_unavailable():
    d = _diagram({"PLT01"}, link=_FakeLink(_FakeTransport(None)))
    assert d.unreachable()["available"] is False


# --- rotas -----------------------------------------------------------------
#
# `GET /unreachable` fica FORA do `/values`: a lista e' do diagrama inteiro e
# so' muda quando a conexao muda, entao mandar as centenas de nomes junto de
# um polling de 500 ms seria pagar o pior dos dois lados.

def _handler(tmp_path, diagram, path):
    """Um Handler montado pra exatamente um GET, sem socket nenhum."""
    from pacct.web.glv.handler import GlvDefaults, build_glv_handler
    from pacct.web.session import SessionManager

    sessions = SessionManager(root=tmp_path / "sessions", logger=LOG)
    Handler = build_glv_handler(LOG, sessions, GlvDefaults(port=23))
    sess, _ = sessions.resolve(None)
    h = Handler.__new__(Handler)
    h.session = sess
    h.mount_prefix = "/glv"
    h.path = path
    st = h.sess()
    st.diagrams[diagram.id] = diagram
    st.order.append(diagram.id)
    st.active = diagram.id
    return h


def _capture(h):
    sent: list = []
    h._send = lambda code, body, ctype: sent.append((code, body, ctype))
    h._send_json = lambda code, payload: sent.append(
        (code, __import__("json").dumps(payload), "application/json"))
    return sent


def test_the_route_answers_the_whole_diagram_payload(tmp_path):
    import json

    t = _FakeTransport({"names": ["VB001"], "reason": "relay_word"})
    d = _diagram({"PLT01", "VB001"}, link=_FakeLink(t))
    h = _handler(tmp_path, d, "/unreachable?d=d1")
    sent = _capture(h)
    h.do_GET()
    code, body, ctype = sent[0]
    assert code == 200 and ctype.startswith("application/json")
    assert json.loads(body) == {"available": True, "names": ["VB001"],
                                "count": 1, "total": 2,
                                "reason": "relay_word", "scan_mode": "telnet"}


def test_the_txt_download_carries_one_name_per_line_and_names_the_relay(tmp_path):
    """O arquivo vai ser colado num editor de modelo de IED; o cabecalho diz
    de qual rele e de qual desenho ele saiu, comentado com `#` pra nao virar
    um nome por engano."""
    t = _FakeTransport({"names": ["VB001", "PLT99"], "reason": "relay_word"})
    d = _diagram({"PLT01", "PLT99", "VB001"}, link=_FakeLink(t))
    h = _handler(tmp_path, d, "/unreachable.txt?d=d1")
    wire = _wire(h)
    h.do_GET()
    text = h.wfile.getvalue().decode("utf-8")
    assert wire.code == 200
    assert wire.headers["Content-Type"].startswith("text/plain")
    assert "attachment; filename*=UTF-8''" in wire.headers["Content-Disposition"]
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    assert lines == ["PLT99", "VB001"]
    assert "QPC1" in text and "GL1" in text


class _Wire:
    def __init__(self):
        self.code = None
        self.headers: dict = {}


def _wire(h):
    import io

    w = _Wire()
    h.send_response = lambda code: setattr(w, "code", code)
    h.send_header = lambda k, v: w.headers.__setitem__(k, v)
    h.end_headers = lambda: None
    h.wfile = io.BytesIO()
    return w
