"""A checagem de versao que a home dispara, e o cache que a segura.

O modulo existe por causa da regra 1 do `pacct/update.py` -- nunca no caminho
de boot -- levada ao navegador: a home pinta primeiro e SO' entao pergunta ao
GitHub. O que se testa aqui e' o que sobra dessa decisao:

* uma resposta e' reusada, para que dez abas abertas ao mesmo tempo virem uma
  requisicao e nao dez;
* um erro NAO vira excecao (a home tem que aparecer numa subestacao sem
  internet) e e' guardado por muito menos tempo que um acerto, senao o notebook
  que acabou de achar rede esperaria seis horas para saber;
* o conselho impresso depende do tipo de instalacao -- num clone git
  `--atualizar` e' recusado pelo proprio CLI.
"""
from __future__ import annotations

import pytest

from pacct import update as U
from pacct.web import update_check as uc


class FakeCheck:
    """Um `check_latest` de mentira que conta quantas vezes foi chamado."""

    def __init__(self, version: str | None = None,
                 error: str | None = None,
                 min_python: str | None = None) -> None:
        self.version = version
        self.error = error
        self.min_python = min_python
        self.calls = 0

    def __call__(self, *, timeout: float = 0.0) -> U.Release:
        self.calls += 1
        if self.error is not None:
            raise U.UpdateError(self.error)
        manifest = {"version": self.version, "release": True}
        if self.min_python is not None:
            manifest["min_python"] = self.min_python
        return U.Release(version=self.version or "", tag=f"v{self.version}",
                         notes="", assets=(), manifest=manifest)


@pytest.fixture(autouse=True)
def _clean_cache():
    uc.reset_cache()
    yield
    uc.reset_cache()


@pytest.fixture
def check(monkeypatch):
    """Instala um `check_latest` falso e devolve o contador."""
    def install(version: str | None = None, error: str | None = None,
                min_python: str | None = None):
        fake = FakeCheck(version, error, min_python)
        monkeypatch.setattr(U, "check_latest", fake)
        return fake
    return install


@pytest.fixture(autouse=True)
def _fixed_install(monkeypatch):
    """Sem isto o resultado dependeria da maquina que roda o teste."""
    monkeypatch.setattr(uc, "_current_version", lambda: "1.10.0")
    monkeypatch.setattr(uc, "_install_kind", lambda: "portable")


# -- o que a checagem responde ----------------------------------------------

def test_a_newer_release_is_available(check):
    check("1.11.0")
    st = uc.status()
    assert st.available is True
    assert st.latest == "1.11.0"
    assert st.current == "1.10.0"
    assert st.error is None


def test_the_same_version_is_not_available(check):
    check("1.10.0")
    st = uc.status()
    assert st.available is False
    assert st.latest == "1.10.0"


def test_being_offline_is_an_answer_not_an_exception(check):
    check(error="Sem acesso a' internet para consultar atualizacoes.")
    st = uc.status()
    assert st.available is False
    assert st.latest is None
    assert st.error == "Sem acesso a' internet para consultar atualizacoes."


def test_the_install_kind_travels_with_the_answer(check, monkeypatch):
    check("1.11.0")
    monkeypatch.setattr(uc, "_install_kind", lambda: "checkout")
    assert uc.status().kind == "checkout"


# -- o cache ----------------------------------------------------------------

def test_a_second_visitor_does_not_ask_github_again(check):
    fake = check("1.11.0")
    uc.status(now=1000.0)
    uc.status(now=1000.0)
    uc.status(now=1000.0)
    assert fake.calls == 1


def test_an_answer_is_asked_again_once_it_goes_stale(check):
    fake = check("1.11.0")
    uc.status(now=1000.0)
    uc.status(now=1000.0 + uc.OK_TTL + 1)
    assert fake.calls == 2


def test_a_failure_is_retried_much_sooner_than_a_success(check):
    fake = check(error="sem rede")
    uc.status(now=1000.0)
    uc.status(now=1000.0 + uc.FAIL_TTL + 1)
    assert fake.calls == 2
    assert uc.FAIL_TTL < uc.OK_TTL


def test_a_failure_is_still_cached_for_a_while(check):
    fake = check(error="sem rede")
    uc.status(now=1000.0)
    uc.status(now=1000.0 + uc.FAIL_TTL - 1)
    assert fake.calls == 1


def test_force_ignores_the_cache(check):
    fake = check("1.11.0")
    uc.status(now=1000.0)
    uc.status(now=1000.0, force=True)
    assert fake.calls == 2


# -- o que vai para o navegador ---------------------------------------------

def test_the_payload_carries_every_field_the_banner_reads(check):
    check("1.11.0")
    payload = uc.status().as_dict()
    assert payload == {
        "current": "1.10.0",
        "latest": "1.11.0",
        "available": True,
        "kind": "portable",
        "error": None,
        "blocked": None,
    }


def test_a_release_this_python_cannot_install_is_reported_not_hidden(check):
    """`available` falso e `blocked` preenchido -- a home NAO pode dizer
    "atualizado" quando existe versao nova que esta maquina nao instala.

    `error` continua nulo de proposito: a consulta funcionou. Sao dois estados
    diferentes e a home mostra textos diferentes para cada um.
    """
    check("1.11.0", min_python="99.0")
    st = uc.status()
    assert st.latest == "1.11.0"
    assert st.available is False
    assert st.error is None
    assert st.blocked is not None and "99.0" in st.blocked
    assert st.as_dict()["blocked"] == st.blocked


def test_nothing_is_blocked_when_there_is_nothing_newer(check):
    """Uma versao igual ou mais velha nao e' "bloqueada", e' so' nao-nova --
    senao a home avisaria sobre um Python velho sem ter nada a oferecer."""
    check("1.10.0", min_python="99.0")
    st = uc.status()
    assert st.available is False
    assert st.blocked is None
