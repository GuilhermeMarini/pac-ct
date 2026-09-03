"""Sessoes por usuario para as ferramentas web.

Antes cada ferramenta guardava a sessao num singleton de modulo (`_state =
_SessionState()`), que e' por PROCESSO, nao por visitante. Com duas pessoas na
mesma ferramenta, o upload da segunda substituia o da primeira sem aviso -- e
como os nomes de rele se repetem entre subestacoes, a primeira podia exportar
dados da outra obra sem perceber.

Aqui cada visitante ganha:

- um id de sessao num cookie (`selsid`), emitido na primeira resposta;
- um estado proprio por ferramenta (`SessionHandler.sess()`);
- um diretorio proprio em `cache/sessions/<sid>/` pros uploads.

O diretorio proprio importa tanto quanto o estado: e' onde ficam os uploads
que sao mesmo desta sessao (o SCD) e TODA saida derivada (o .rdb com os
comentarios atualizados, a planilha). O `/download` de cada ferramenta so
serve de dentro dele, entao um visitante nunca baixa o arquivo gerado por
outro, e a limpeza na expiracao e' um `rmtree` -- sem contar referencias.

Os RDB sao a excecao, e de proposito: eles vao pro cache por conteudo
(`cache/rdb/<sha256>/`, ver `selfiles.rdb_cache`), que e' compartilhado
e read-only pras ferramentas. Dois arquivos iguais sao o mesmo arquivo, entao
guardar uma copia de 40-140 MB por sessao so gastava disco e tempo de
extracao.

O GLV fica de fora de proposito: ele fala com UM rele fisico por vez, entao a
sessao dele e' unica e compartilhada -- varias pessoas podem acompanhar o
mesmo dashboard, mas nao ter cada uma o seu.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from pacct.web import theme as themes
from pacct.web.mount import inject_head
from pacct.web.progress import JobReporter, inject_progress_runtime
from pacct.web.project_files.client import inject_library_runtime

COOKIE_NAME = "selsid"

#: Teto de um corpo JSON de requisicao. Nenhuma rota desta aplicacao manda
#: JSON grande: a maior e' a copia de mapa DNP, com algumas centenas de chaves
#: -- ordens de grandeza abaixo disto. Os arquivos de verdade (RDB de 40-140 MB,
#: XLSX) NAO passam por aqui; entram por `project_files`, em pedacos e com os
#: tetos de `library.py`. Sem um limite, `rfile.read(Content-Length)` aloca o
#: que o cliente disser que vai mandar.
MAX_JSON_BODY = 4 << 20  # 4 MiB

# O sid vira nome de diretorio: aceitar so o alfabeto do token_urlsafe evita
# que um cookie forjado ("../../etc") escape de cache/sessions/.
_SID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

DEFAULT_TTL_SECONDS = 8 * 3600
_SWEEP_INTERVAL_SECONDS = 900


@dataclass
class Session:
    """Estado de um visitante: um objeto por ferramenta + um diretorio."""

    sid: str
    dir: Path
    created: float
    last_seen: float
    # tool_key -> objeto de estado daquela ferramenta (criado sob demanda)
    data: dict[str, Any] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def subdir(self, name: str) -> Path:
        """Diretorio de trabalho da sessao (ex.: "rdbs", "scd", "xlsx")."""
        d = self.dir / name
        d.mkdir(parents=True, exist_ok=True)
        return d


class SessionManager:
    """Cria, encontra e expira sessoes."""

    def __init__(self, root: Path, logger, ttl_seconds: float = DEFAULT_TTL_SECONDS):
        self.root = Path(root)
        self.logger = logger
        self.ttl = ttl_seconds
        # Chamado a cada volta do sweeper, depois de expirar as sessoes. E' por
        # onde o cache de RDB (que nao tem dono nem TTL proprio) e' podado, e
        # por onde os diagramas do GLV de uma sessao expirada sao fechados.
        self.on_sweep: Callable[[], None] | None = None
        self.on_expire: Callable[[Session], None] | None = None
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()

    # -- ciclo de vida ------------------------------------------------------

    def peek(self, cookie_header: str | None) -> Session | None:
        """A sessao do cookie, ou None. NUNCA cria uma.

        E' o que as rotas de infraestrutura usam (`/library`, `/progress`,
        `/theme.css`, `/static/...`): elas nao sao donas de identidade
        nenhuma. Criar sessao ali enchia o servidor de sessoes fantasma --
        uma folha de estilo nao e' um visitante.
        """
        sid = _parse_sid(cookie_header)
        if sid is None:
            return None
        with self._lock:
            sess = self._sessions.get(sid)
            if sess is not None:
                sess.last_seen = time.time()
            return sess

    def resolve(self, cookie_header: str | None,
                path: str = "") -> tuple[Session, bool]:
        """Devolve `(sessao, cookie_novo)` a partir do header Cookie."""
        sid = _parse_sid(cookie_header)
        now = time.time()
        with self._lock:
            if sid is not None:
                sess = self._sessions.get(sid)
                if sess is not None:
                    sess.last_seen = now
                    return sess, False
            # Sem cookie, cookie invalido, ou sessao ja expirada: comeca uma
            # nova. Um sid vindo do cliente nunca e' reaproveitado -- senao
            # daria pra escolher o proprio id e cair na sessao de outro.
            new_sid = secrets.token_urlsafe(18)
            sess = Session(
                sid=new_sid,
                dir=self.root / new_sid,
                created=now,
                last_seen=now,
            )
            self._sessions[new_sid] = sess
            # O MOTIVO vai junto, e nao so o id. "sem cookie" e "cookie
            # desconhecido" sao problemas diferentes: o primeiro e' o
            # navegador que nao guardou (ou nao mandou) o `selsid`, o segundo
            # e' o servidor que reiniciou ou varreu a sessao. Sem essa
            # distincao o log so dizia que uma sessao nasceu, o que nao ajuda
            # ninguem a entender por que o acervo do projeto esvaziou.
            if sid is not None:
                motivo = f"cookie desconhecido ({sid[:8]})"
            elif not cookie_header:
                motivo = "sem cookie"
            else:
                motivo = f"cookie ilegivel: {cookie_header[:200]!r}"
            self.logger.info("[session] nova sessao %s -- %s%s (%d ativa(s))",
                             new_sid[:8], motivo,
                             f" em {path}" if path else "",
                             len(self._sessions))
            return sess, True

    def state(self, sess: Session, key: str, factory: Callable[[], Any]) -> Any:
        """Estado da ferramenta `key` nessa sessao, criado no primeiro acesso."""
        with sess.lock:
            st = sess.data.get(key)
            if st is None:
                st = factory()
                sess.data[key] = st
            return st

    # -- expiracao ----------------------------------------------------------

    def sweep(self) -> int:
        """Remove sessoes ociosas ha mais que o TTL. Devolve quantas sairam."""
        cutoff = time.time() - self.ttl
        with self._lock:
            expired = [s for s in self._sessions.values() if s.last_seen < cutoff]
            for s in expired:
                self._sessions.pop(s.sid, None)
        for s in expired:
            if self.on_expire is not None:
                # Antes do rmtree: a sessao pode ter recursos vivos fora do
                # diretorio dela (conexoes do GLV), e some sem soltar nada.
                try:
                    self.on_expire(s)
                except Exception as e:
                    self.logger.warning("[session] on_expire falhou em %s: %s",
                                        s.sid[:8], e)
            self._discard_dir(s)
            self.logger.info("[session] sessao %s expirada (ociosa ha %.1f h)",
                             s.sid[:8], (time.time() - s.last_seen) / 3600)
        return len(expired)

    def start_sweeper(self, interval: float = _SWEEP_INTERVAL_SECONDS) -> None:
        def loop():
            while not self._stop.wait(interval):
                try:
                    self.sweep()
                except Exception as e:  # nunca derruba a thread do sweeper
                    self.logger.warning("[session] falha no sweep: %s", e)
                hook = self.on_sweep
                if hook is not None:
                    try:
                        hook()
                    except Exception as e:
                        self.logger.warning("[session] falha no on_sweep: %s", e)

        threading.Thread(target=loop, name="session-sweeper", daemon=True).start()

    def shutdown(self) -> None:
        """Para o sweeper e apaga os diretorios de todas as sessoes vivas."""
        self._stop.set()
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            self._discard_dir(s)
        # Restos de execucoes anteriores (kill -9, queda de energia) tambem
        # somem aqui, ja que nenhuma sessao sobrevive ao processo.
        try:
            if self.root.is_dir() and not any(self.root.iterdir()):
                self.root.rmdir()
        except OSError:
            pass

    def purge_root(self) -> None:
        """Limpa `cache/sessions/` inteiro. Chamado no boot: nenhum cookie
        antigo continua valido depois de reiniciar, entao o que ficou no disco
        e' lixo."""
        if not self.root.is_dir():
            return
        n = 0
        for child in self.root.iterdir():
            try:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
                n += 1
            except OSError as e:
                self.logger.warning("[session] nao consegui limpar %s: %s", child, e)
        if n:
            self.logger.info("[session] %d diretorio(s) de sessao antiga removido(s)", n)

    def _discard_dir(self, sess: Session) -> None:
        if sess.dir.is_dir():
            try:
                shutil.rmtree(sess.dir)
            except OSError as e:
                self.logger.warning("[session] nao consegui remover %s: %s",
                                    sess.dir, e)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)


def _parse_sid(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    try:
        jar = SimpleCookie()
        jar.load(cookie_header)
    except Exception:
        return None
    morsel = jar.get(COOKIE_NAME)
    if morsel is None:
        return None
    sid = morsel.value
    return sid if _SID_RE.match(sid) else None


# -----------------------------------------------------------------------------
# Base dos handlers das ferramentas
# -----------------------------------------------------------------------------

class SessionHandler(BaseHTTPRequestHandler):
    """Base comum das ferramentas montadas: sessao, cookie e envio de resposta.

    O dispatcher resolve a sessao e deixa `self.session` / `self._set_cookie`
    na instancia ANTES de trocar `self.__class__` pra ca, entao esses atributos
    ja estao prontos quando `do_GET`/`do_POST` da ferramenta roda.
    """

    # Preenchidos na montagem (`mount.Mount`) e pelo dispatcher.
    mount_prefix: str = ""
    session_key: str = ""
    session: Session | None = None
    # Tema ativo do visitante (cookie `seltheme`), resolvido pelo dispatcher
    # antes de trocar `self.__class__` pra ca. O literal aqui e' so o valor de
    # partida do atributo de classe: quem responde de verdade e' o dispatcher.
    theme: str = themes.DEFAULT_THEME

    def log_message(self, fmt, *args):
        pass  # silencia stderr; cada ferramenta loga o que interessa

    # -- sessao -------------------------------------------------------------

    def sess(self):
        """Estado desta ferramenta na sessao do visitante."""
        return self.server_sessions.state(
            self.session, self.session_key, self.state_factory,
        )

    def sdir(self, name: str) -> Path:
        """Subdiretorio de trabalho da sessao (uploads, saidas)."""
        return self.session.subdir(f"{self.session_key}-{name}")

    def library_entry(self, ref: str, kind: str = ""):
        """O arquivo do acervo do visitante que `ref` nomeia, ou None.

        `ref` pode ser o sha256 inteiro ou o sha CURTO -- as URLs das
        ferramentas carregam o curto (`?rdb=0f5f8eff0d07`) e o acervo e'
        indexado pelo inteiro. A varredura e' sobre os arquivos de UM
        visitante, que sao uma dezena, nao um indice que valha manter.

        Existe pra que escolher um arquivo deixe de ter duas etapas. Cada
        ferramenta guarda os arquivos que ja "adotou" (`st.rdbs`), e as rotas
        respondiam 404 pra qualquer chave que nao estivesse la -- mesmo com o
        arquivo no acervo, na mesma sessao, atras do mesmo cookie. Era o
        resto de quando cada ferramenta tinha o proprio upload: o
        `/select-rdb` e' o rabo daquele handler. Com isto a adocao vira
        cache, nao pre-requisito: a lista da tela e' a do PROJETO, clicar
        numa linha ja mostra os IEDs, e um link com `?rdb=` continua valendo
        depois que a ferramenta esqueceu.
        """
        from pacct.web.project_files import library as filelib

        if not ref or self.session is None:
            return None
        lib = filelib.library_for(self.server_sessions, self.session)
        with self.session.lock:
            entry = lib.get(ref)
            if entry is None:
                for cand in lib.list(kind or None):
                    if cand.short_sha == ref:
                        entry = cand
                        break
        if entry is None:
            return None
        if kind and entry.kind != kind:
            return None
        return entry

    def publish_output(self, path, origin: str, job=None,
                       logger=None) -> dict | None:
        """Poe o arquivo que a ferramenta acabou de gerar no acervo do projeto.

        E' o outro lado do `/upload` de `/files/`: a saida de uma
        ferramenta e' entrada da proxima, e ate aqui o unico caminho entre as
        duas era baixar e subir de novo o mesmo arquivo de 140 MB. Devolve o
        resumo pro JSON da resposta (`project_file`), ou None quando nao deu --
        e nao dar nunca derruba a exportacao: o arquivo foi gerado do mesmo
        jeito e o link de download da propria ferramenta continua valendo.
        """
        from pacct.web.project_files import derived

        entry, duplicate, err = derived.adopt(
            self.server_sessions, self.session, path,
            origin=origin, logger=logger, job=job,
        )
        if entry is None:
            if logger is not None and err:
                logger.warning("[%s] saida nao entrou no projeto: %s",
                               self.session_key, err)
            return None
        out = entry.to_json()
        out["duplicate"] = duplicate
        return out

    # -- resposta -----------------------------------------------------------

    def end_headers(self):
        # O cookie so vai na primeira resposta da sessao; nas seguintes o
        # cliente ja manda o dele de volta.
        cookie = getattr(self, "_set_cookie", None)
        if cookie:
            self.send_header("Set-Cookie", cookie)
            self._set_cookie = None
        super().end_headers()

    def job(self) -> JobReporter:
        """Reporter do job que o cliente abriu pra esta requisicao.

        Vira no-op quando nao veio `X-Job-Id` (chamada sem a barra de
        progresso), entao o handler nao precisa checar nada.
        """
        return JobReporter(self.headers.get("X-Job-Id"))

    def _send(self, code: int, body, ctype: str):
        if isinstance(body, str):
            if ctype.startswith("text/html"):
                body = inject_head(body, self.mount_prefix, self.theme)
                body = inject_progress_runtime(body)
                body = inject_library_runtime(body)
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload), "application/json")

    def _read_json_body(self, max_bytes: int = MAX_JSON_BODY) -> dict:
        """Corpo JSON da requisicao como dict. Sempre um dict: corpo vazio,
        JSON invalido ou JSON que nao e' objeto (uma lista, um numero) viram
        `{}`, entao quem chama nunca leva AttributeError de um `.get()`.

        Um corpo maior que `max_bytes` tambem vira `{}`, e nao e' lido: o
        `Content-Length` vem do cliente, e sem teto ele escolhe quanta memoria
        o servidor aloca."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > max_bytes:
            return {}
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}


def build_cookie(sid: str, ttl_seconds: float) -> str:
    # Sem `Secure`: o toolkit roda em HTTP na rede local da subestacao.
    return (
        f"{COOKIE_NAME}={sid}; Path=/; HttpOnly; SameSite=Lax; "
        f"Max-Age={int(ttl_seconds)}"
    )
