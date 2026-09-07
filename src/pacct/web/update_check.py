"""A checagem de versao que a home dispara, com o cache que a torna barata.

Regra 1 do `pacct/update.py` -- "nunca no caminho de boot" -- levada ao
navegador. A home nao consulta o GitHub para ser pintada: ela pinta, e so'
entao o script pede `/update-check`. Uma subestacao sem rota para a internet
ve o menu no mesmo tempo de sempre e simplesmente nao ve aviso nenhum.

O que este modulo acrescenta ao `update.check_latest` sao tres coisas, e nada
alem delas:

1. **Um cache no processo.** Cada aba aberta pediria uma consulta, e um
   escritorio inteiro sai por um NAT so' -- o mesmo orcamento de 60
   requisicoes/hora que fez o `update.py` largar a REST API. Uma resposta
   serve todo mundo ate' envelhecer.
2. **Dois prazos, nao um.** Um acerto vale seis horas: uma release nao sai
   duas vezes na mesma manha. Uma FALHA vale quinze minutos, porque a falha
   mais comum aqui e' "ainda nao pluguei o cabo" -- guardar isso por seis
   horas faria o notebook que acabou de achar rede continuar em silencio ate'
   depois do almoco.
3. **Nenhuma excecao.** `UpdateError` vira o campo `error`. Quem chama e' uma
   rota HTTP que precisa responder de qualquer jeito.

O `lock` cobre a consulta inteira, e nao so' a leitura do cache: dez abas
abertas juntas viram UMA requisicao ao GitHub, e as outras nove esperam por
ela. O custo e' que uma thread do servidor fica presa por ate' `TIMEOUT`
segundos -- aceitavel num `ThreadingHTTPServer` onde o HTML da home ja' foi
entregue antes de o script perguntar qualquer coisa.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from pacct import update
from pacct import version as version_mod
from pacct.paths import PROJECT_ROOT

# Seis horas para um acerto, quinze minutos para uma falha -- ver o item 2 do
# cabecalho. Nao sao configuraveis: nada aqui merece uma chave no config.ini.
OK_TTL = 6 * 3600.0
FAIL_TTL = 15 * 60.0

# O CLI espera 15 s porque quem digitou `--verificar` esta' olhando para a
# resposta. Aqui ninguem esta': o aviso aparece se aparecer.
TIMEOUT = 5.0


@dataclass(frozen=True)
class UpdateStatus:
    """O que a home precisa saber, e nada que ela nao va' mostrar."""

    current: str
    kind: str
    latest: str | None = None
    available: bool = False
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "current": self.current,
            "latest": self.latest,
            "available": self.available,
            "kind": self.kind,
            "error": self.error,
        }


# O cache: uma resposta e o instante em que ela foi obtida. Vive no processo e
# morre com ele, que e' o tempo de vida certo -- um restart ja' e' motivo
# suficiente para perguntar de novo.
_lock = threading.Lock()
_cached: UpdateStatus | None = None
_cached_at: float = 0.0


def reset_cache() -> None:
    """Esquece o que foi consultado. Existe para os testes e para `force`."""
    global _cached, _cached_at
    with _lock:
        _cached = None
        _cached_at = 0.0


def _current_version() -> str:
    """Indireto de proposito: e' o ponto onde o teste fixa a versao."""
    return version_mod.read_version()


def _install_kind() -> str:
    """`versioned`, `portable` ou `checkout` -- decide o conselho do aviso."""
    return update.install_kind(PROJECT_ROOT)


def status(*, force: bool = False, now: float | None = None) -> UpdateStatus:
    """A situacao da versao, do cache ou do GitHub.

    `now` e' injetavel para que o teste dos prazos nao durma seis horas.
    """
    global _cached, _cached_at
    moment = time.monotonic() if now is None else now
    with _lock:
        if _cached is not None and not force:
            ttl = FAIL_TTL if _cached.error else OK_TTL
            if moment - _cached_at < ttl:
                return _cached
        _cached = _ask()
        _cached_at = moment
        return _cached


def _ask() -> UpdateStatus:
    """Uma consulta de verdade. Toda falha vira texto, nenhuma sobe."""
    current = _current_version()
    kind = _install_kind()
    try:
        release = update.check_latest(timeout=TIMEOUT)
    except update.UpdateError as exc:
        return UpdateStatus(current=current, kind=kind, error=str(exc))
    return UpdateStatus(
        current=current, kind=kind, latest=release.version,
        available=update.update_available(release, current),
    )
