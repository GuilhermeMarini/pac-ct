"""A home: o menu, o carimbo de versao e o aviso de versao nova.

A home nao e' uma ferramenta -- nao tem `sessions` na fabrica -- mas as rotas
dela se dirigem pelo mesmo `tests/web_harness.py`. O que importa aqui e' que a
versao chega pintada no HTML (sem uma segunda requisicao para algo que nunca
muda dentro de um processo) e que `/update-check` responde JSON sem NUNCA
propagar uma falha de rede: a home tem que abrir numa subestacao sem internet.
"""
from __future__ import annotations

import pacct
from pacct import update as U
from pacct.web import update_check as uc
from pacct.web.dashboard import build_home_handler
from tests.web_harness import build


def _harness(tmp_path):
    return build(lambda logger, sessions: build_home_handler(logger), tmp_path)


def _fake_check(monkeypatch, version: str | None = None,
                error: str | None = None):
    def check_latest(*, timeout: float = 0.0):
        if error is not None:
            raise U.UpdateError(error)
        return U.Release(version=version or "", tag=f"v{version}", notes="",
                         assets=())
    monkeypatch.setattr(U, "check_latest", check_latest)
    uc.reset_cache()


# -- o carimbo de versao -----------------------------------------------------

def test_the_home_prints_the_running_version(tmp_path):
    r = _harness(tmp_path).get("/")
    assert r.status == 200
    assert f"v{pacct.__version__}" in r.text


def test_the_version_marker_is_resolved_not_served_raw(tmp_path):
    """O `<!--VERSION-->` e' um marcador; se ele sair no HTML, ninguem ve nada."""
    assert "<!--VERSION-->" not in _harness(tmp_path).get("/").text


# -- /update-check -----------------------------------------------------------

def test_update_check_answers_json(tmp_path, monkeypatch):
    _fake_check(monkeypatch, "999.0.0")
    r = _harness(tmp_path).get("/update-check")
    assert r.status == 200
    assert r.headers["content-type"].startswith("application/json")
    payload = r.json()
    assert payload["available"] is True
    assert payload["latest"] == "999.0.0"
    assert payload["current"] == pacct.__version__


def test_update_check_survives_having_no_internet(tmp_path, monkeypatch):
    _fake_check(monkeypatch, error="Sem acesso a' internet.")
    r = _harness(tmp_path).get("/update-check")
    assert r.status == 200
    assert r.json()["available"] is False
    assert r.json()["error"] == "Sem acesso a' internet."


def test_update_check_is_not_asked_on_the_render_path(tmp_path, monkeypatch):
    """Regra 1 no navegador: pintar a home nao consulta o GitHub."""
    calls = []

    def check_latest(*, timeout: float = 0.0):
        calls.append(timeout)
        raise U.UpdateError("nao deveria ter sido chamado")

    monkeypatch.setattr(U, "check_latest", check_latest)
    uc.reset_cache()
    _harness(tmp_path).get("/")
    assert calls == []


def test_an_unknown_home_route_is_404(tmp_path):
    assert _harness(tmp_path).get("/nao-existe").status == 404
