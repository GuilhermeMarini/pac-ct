"""Per-visitor sessions for the web tools.

Each tool used to keep its session in a module-level singleton (`_state =
_SessionState()`), which is per PROCESS, not per visitor. With two people in
the same tool, the second one's upload replaced the first's without warning --
and since relay names repeat across substations, the first could export
another job's data without noticing.

Here every visitor gets:

- a session id in a cookie (`selsid`), issued on the first response;
- their own state per tool (`SessionHandler.sess()`);
- their own directory under `cache/sessions/<sid>/` for uploads.

The directory matters as much as the state: it holds the uploads that really
are this session's (the SCD) and EVERY derived output (the .rdb with updated
comments, the spreadsheet). Each tool's `/download` serves only from inside
it, so a visitor can never download a file another one generated, and cleaning
up on expiry is one `rmtree` -- no reference counting.

RDBs are the exception, on purpose: they go to the content-addressed cache
(see `selfiles.rdb_cache`), which is shared and read-only to the tools. Two
identical files are the same file, so keeping a 40-140 MB copy per session
only cost disk and extraction time.

The GLV stays out of this deliberately: it talks to ONE physical relay at a
time, so its session is single and shared -- several people can watch the same
diagram, but they cannot each have their own.
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

#: Ceiling on a JSON request body. No route in this application sends large
#: JSON: the biggest is the DNP map copy, a few hundred keys -- orders of
#: magnitude below this. The real files (a 40-140 MB RDB, an XLSX) do NOT come
#: through here; they enter through `project_files`, in chunks and under
#: `library.py`'s own ceilings. Without a limit,
#: `rfile.read(Content-Length)` allocates whatever the client claims it will
#: send.
MAX_JSON_BODY = 4 << 20  # 4 MiB

# The sid becomes a directory name: accepting only token_urlsafe's alphabet
# is what stops a forged cookie ("../../etc") escaping the sessions
# directory.
_SID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

DEFAULT_TTL_SECONDS = 8 * 3600
_SWEEP_INTERVAL_SECONDS = 900


@dataclass
class Session:
    """One visitor's state: an object per tool, plus a directory."""

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
        # Called on every sweeper turn, after the sessions have expired. It
        # is how the RDB cache (which has no owner and no TTL of its own) gets
        # pruned, and how an expired session's GLV diagrams get closed.
        self.on_sweep: Callable[[], None] | None = None
        self.on_expire: Callable[[Session], None] | None = None
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()

    # -- ciclo de vida ------------------------------------------------------

    def peek(self, cookie_header: str | None) -> Session | None:
        """The cookie's session, or None. NEVER creates one.

        This is what the infrastructure routes use (`/library`, `/progress`,
        `/theme.css`, `/static/...`): none of them owns any identity. Creating
        a session there filled the server with phantom ones -- a stylesheet is
        not a visitor.
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
            # No cookie, an invalid one, or a session already expired: start
            # a new one. A sid coming from the client is never reused -- that
            # would let someone choose their own id and land in another
            # visitor's session.
            new_sid = secrets.token_urlsafe(18)
            sess = Session(
                sid=new_sid,
                dir=self.root / new_sid,
                created=now,
                last_seen=now,
            )
            self._sessions[new_sid] = sess
            # The REASON travels with the id, not just the id. "no cookie"
            # and "unknown cookie" are different problems: the first is a
            # browser that did not keep (or did not send) the `selsid`, the
            # second is a server that restarted or swept the session. Without
            # that distinction the log only said a session was born, which
            # helps nobody understand why the project's files emptied out.
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
        """The state of tool `key` in this session, created on first access."""
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
                # Before the rmtree: a session may hold live resources
                # outside its own directory (GLV connections), and would
                # otherwise vanish without releasing any of them.
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
        """Stop the sweeper and delete the directories of every live session."""
        self._stop.set()
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            self._discard_dir(s)
        # Leftovers from earlier runs (kill -9, a power cut) go here too,
        # since no session outlives the process.
        try:
            if self.root.is_dir() and not any(self.root.iterdir()):
                self.root.rmdir()
        except OSError:
            pass

    def purge_root(self) -> None:
        """Wipe the whole sessions directory. Called at start-up: no old cookie
        is still valid after a restart, so whatever is on disk is rubbish."""
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
    """The common base of the mounted tools: session, cookie and response.

    The dispatcher resolves the session and leaves `self.session` and
    `self._set_cookie` on the instance BEFORE swapping `self.__class__` to
    this one, so those attributes are already in place when the tool's
    `do_GET`/`do_POST` runs.
    """

    # Filled in at mount time (`mount.Mount`) and by the dispatcher.
    mount_prefix: str = ""
    session_key: str = ""
    session: Session | None = None
    # The visitor's active theme (the `seltheme` cookie), resolved by the
    # dispatcher before it swaps `self.__class__` to this class. The literal
    # here is only the class attribute's starting value; the dispatcher is what
    # actually answers.
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
        """The file in this visitor's library that `ref` names, or None.

        `ref` may be the full sha256 or the SHORT one -- the tools' URLs carry
        the short form (`?rdb=0f5f8eff0d07`) while the library is indexed by
        the full one. The scan covers ONE visitor's files, a dozen of them,
        which is not worth an index of its own.

        It exists so that choosing a file stopped being a two-step affair.
        Each tool used to keep the files it had "adopted" (`st.rdbs`), and its
        routes answered 404 for any key not in there -- even with the file in
        the library, in the same session, behind the same cookie. That was the
        last stone of the era when every tool had its own upload:
        `/select-rdb` is the tail of that handler. With this, adoption is a
        cache rather than a prerequisite: the list on screen is the PROJECT's,
        clicking a row goes straight to the IEDs, and a `?rdb=` link keeps
        working after the tool has forgotten.
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
        """Put the file a tool has just produced into the project's library.

        This is the other half of `/files/`'s `/upload`: one tool's output is
        the next one's input, and until this existed the only path between
        them was downloading and re-uploading the same 140 MB file. Returns the
        summary for the response JSON (`project_file`), or None when it did not
        work -- and failing here never breaks the export: the file was produced
        all the same, and the tool's own download link still works.
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
        # The cookie goes out only on the session's first response; after
        # that the client sends its own back.
        cookie = getattr(self, "_set_cookie", None)
        if cookie:
            self.send_header("Set-Cookie", cookie)
            self._set_cookie = None
        super().end_headers()

    def job(self) -> JobReporter:
        """The reporter for the job the client opened for this request.

        Becomes a no-op when no `X-Job-Id` arrived (a call made without the
        progress bar), so a handler never has to check.
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
        """The request's JSON body as a dict -- always a dict. An empty body,
        invalid JSON, or JSON that is not an object (a list, a number) all
        become `{}`, so a caller never takes an AttributeError off a `.get()`.

        A body larger than `max_bytes` also becomes `{}`, and is not read: the
        `Content-Length` comes from the client, and without a ceiling the
        client chooses how much memory the server allocates."""
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
