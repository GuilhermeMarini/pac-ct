"""A aba lembra a pagina que estava aberta.

Trocar de diagrama nao recarrega a pagina: o cliente busca `/meta?d=` e
re-renderiza a faixa de paginas e o viewer com `meta.initial`. Enquanto
`initial` era sempre a pagina inicial do GLE, quem estava na pagina 7 de um
GLE de 12 voltava pra primeira a cada ida e volta entre dois reles -- e o
mesmo acontecia depois de um F5. A pagina aberta e' estado do diagrama, que
mora no servidor como a propria lista de diagramas.
"""
from __future__ import annotations

import logging

from pacct.web.glv.diagram import GlvDiagram
from pacct.web.glv.transport import SCAN_TELNET

_LOG = logging.getLogger("test")


class _Defaults:
    poll_interval = 0.5
    mms_interval_ms = 100
    acc_password = "OTTER"
    setup_timeout = 60.0


def _diagram(pages=("Capa", "SCADA", "LEDS", "TRIP")) -> GlvDiagram:
    d = GlvDiagram(
        "d1", relay_name="RELE1", gle_name="GL1", gle_path=None,
        ip="192.0.2.10", port=23, relay_model=None, logger=_LOG,
        scan_mode=SCAN_TELNET,
    )
    # `build_diagram` monta os dois juntos: um id seguro por pagina em
    # `pages_meta` e o SVG dela em `svgs`.
    d.pages_meta = [[p, p] for p in pages]
    d.svgs = {p: f"<svg id='{p}'/>" for p in pages}
    return d


# -- o padrao de quem nunca abriu nada --------------------------------------

def test_a_diagram_nobody_opened_yet_starts_on_the_second_page():
    """A primeira pagina de um GLE do QuickSet e' capa/indice em quase todo
    arquivo do corpo. Comportamento inalterado -- e' o que `initial` sempre
    fez, agora com nome."""
    d = _diagram()
    assert d.open_page == ""
    assert d.default_page() == "SCADA"
    assert d.meta(_Defaults())["initial"] == "SCADA"


def test_a_single_page_gle_opens_that_page():
    d = _diagram(pages=("Unica",))
    assert d.default_page() == "Unica"
    assert d.meta(_Defaults())["initial"] == "Unica"


def test_a_gle_with_no_page_at_all_answers_empty_instead_of_raising():
    d = _diagram(pages=())
    assert d.default_page() == ""
    assert d.meta(_Defaults())["initial"] == ""


# -- lembrar ----------------------------------------------------------------

def test_meta_comes_back_on_the_page_that_was_open():
    d = _diagram()
    d.remember_page("TRIP")
    assert d.meta(_Defaults())["initial"] == "TRIP"


def test_the_last_page_opened_is_the_one_remembered():
    d = _diagram()
    d.remember_page("LEDS")
    d.remember_page("TRIP")
    assert d.meta(_Defaults())["initial"] == "TRIP"


def test_the_first_page_can_be_remembered_too():
    """`open_page` nao pode ser falsy-testado contra a inicial: a capa e' uma
    escolha legitima, e cair no `default_page()` a devolveria como SCADA."""
    d = _diagram()
    d.remember_page("Capa")
    assert d.open_page == "Capa"
    assert d.meta(_Defaults())["initial"] == "Capa"


def test_two_diagrams_remember_their_own_page():
    """A memoria e' por diagrama. Duas abas no mesmo rele, cada uma numa
    pagina, e' exactamente o caso que a faixa de abas existe pra servir."""
    a, b = _diagram(), _diagram()
    a.remember_page("SCADA")
    b.remember_page("TRIP")
    assert a.meta(_Defaults())["initial"] == "SCADA"
    assert b.meta(_Defaults())["initial"] == "TRIP"


# -- o que NAO se lembra ----------------------------------------------------

def test_a_page_the_gle_does_not_have_is_not_remembered():
    """`remember_page` roda com o que veio na URL de `/pages/<id>`. Um id que
    nao e' pagina deste GLE nao pode virar a proxima `initial`."""
    d = _diagram()
    d.remember_page("SCADA")
    d.remember_page("../../etc/passwd")
    d.remember_page("")
    assert d.open_page == "SCADA"
    assert d.meta(_Defaults())["initial"] == "SCADA"


def test_a_remembered_page_that_vanished_falls_back_to_the_default():
    """O diagrama pode ser reconstruido sobre outro GLE. Abrir vazio seria
    pior que abrir na inicial."""
    d = _diagram()
    d.remember_page("TRIP")
    d.pages_meta = [["Capa", "Capa"], ["OUTRA", "OUTRA"]]
    d.svgs = {"Capa": "<svg/>", "OUTRA": "<svg/>"}
    assert d.meta(_Defaults())["initial"] == "OUTRA"


# -- a rota que anota ------------------------------------------------------
#
# `GET /pages/<safe_id>` e' o UNICO caminho pro SVG, e passa por ele tanto o
# clique na faixa de paginas quanto a navegacao da busca de variaveis. E' por
# isso que a anotacao mora la, e nao num endpoint novo: nao existe abrir uma
# pagina sem buscar o desenho dela.

def _handler_with_diagram(tmp_path):
    """`(handler_instance_factory, diagram)` -- um GLV real com um diagrama
    de tres paginas, sem socket. Mesmo padrao de
    `tests/test_glv_handler_scan_mode.py`: instancia via `__new__` e `_send`
    trocado por um gravador."""
    from pacct.web.glv.handler import GlvDefaults, build_glv_handler
    from pacct.web.session import SessionManager

    sessions = SessionManager(root=tmp_path / "sessions", logger=_LOG)
    Handler = build_glv_handler(_LOG, sessions, GlvDefaults(port=23))
    sess, _ = sessions.resolve(None)

    d = _diagram(pages=("Capa", "SCADA", "LEDS"))
    # O estado da ferramenta e' criado pelo proprio handler; pega-se por ele.
    probe = Handler.__new__(Handler)
    probe.session = sess
    probe.mount_prefix = ""
    st = probe.sess()
    st.diagrams[d.id] = d
    st.order.append(d.id)
    st.active = d.id

    def request(path):
        h = Handler.__new__(Handler)
        h.session = sess
        h.mount_prefix = ""
        h.path = path
        h.headers = {}
        sent: list = []
        h._send = lambda code, body, ctype: sent.append((code, ctype))
        h._send_json = lambda code, data: sent.append((code, data))
        h.do_GET()
        return sent

    return request, d


def test_fetching_a_pages_svg_is_what_records_the_open_page(tmp_path):
    request, d = _handler_with_diagram(tmp_path)
    sent = request("/pages/LEDS?d=d1")
    assert sent and sent[0][0] == 200 and sent[0][1] == "image/svg+xml"
    assert d.open_page == "LEDS"
    assert d.meta()["initial"] == "LEDS"


def test_a_404_page_records_nothing(tmp_path):
    request, d = _handler_with_diagram(tmp_path)
    request("/pages/SCADA?d=d1")
    sent = request("/pages/NAO_EXISTE?d=d1")
    assert sent and sent[0][0] == 404
    assert d.open_page == "SCADA"
