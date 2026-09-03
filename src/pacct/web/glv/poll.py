"""Threads de polling do GLV: uma por conexao com rele.

Tres modos, um por familia (o `fast_read` do JSON do modelo decide qual):

  `poll_loop`            4xx -- Fast Meter + `VIEW 1:TARGET` pipelinados
  `poll_loop_fastmeter`  7xx -- digitais dentro dos bancos A5D1 (AG95-10)
  `poll_loop_tar`        3xx -- analogicos do A5D1, digitais por `TAR <linha>`

Sairam de `dashboard.py` sem uma linha de mudanca. Todas recebem
`(client, [reader,] state, interval, logger, stop_event)` e escrevem no
LiveState do RelayLink que as subiu.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import warnings

from pacct.paths import PROJECT_ROOT

# `selprotopy/` mora em PROJECT_ROOT, fora do pacote `pacct/`.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Python 3.13+ removeu `telnetlib` da stdlib. `selprotopy` faz `import
# telnetlib` direto, entao o shim precisa vir ANTES do import dele.
from pacct.compat import ensure_telnetlib

ensure_telnetlib()


import selprotopy  # noqa: F401,E402

warnings.filterwarnings("ignore", category=DeprecationWarning, module="telnetlib")

# `commands` e `telnetlib` sao REEXPORTADOS daqui: o transporte telnet os
# importa deste modulo, e nao da origem, de proposito. Este arquivo e' o unico
# que garante a ORDEM -- `ensure_telnetlib()` roda acima, antes de qualquer
# `import telnetlib` -- e quem importar telnetlib direto no 3.13+ recebe
# ImportError. Sao usados por `pacct.web.glv.transport.telnet`; o `noqa: F401`
# diz ao ruff que nao estao sobrando.
import telnetlib  # noqa: E402,F401

from selprotopy.client.base import SELClient  # noqa: E402
from selprotopy.protocol import commands  # noqa: E402,F401
from selprotopy.protocol import parser as sel_parser  # noqa: E402

from pacct.core.relay_conn import channel_for  # noqa: E402
from pacct.core.target_region import (  # noqa: E402
    TARGET_REGION_BYTES,
    AsciiTargetReader,
    target_bytes_from_stream,
)
from pacct.web.glv.state import LiveState  # noqa: E402

# --- Tempos do loop -------------------------------------------------------
#
# Todos medidos na bancada de Exemplo (203.0.113.x), com os reles reais
# que cada modo atende: 411L-A-R133 e 451-5-R331 e 487E-3-R323 (4xx),
# 751-R402 (7xx), 311C-1-R509 (3xx). Nao sao numeros escolhidos a esmo, e
# mexer neles sem um rele na frente e' mexer as cegas.

# Teto pra drenar o que sobrou de uma resposta ASCII anterior antes de mandar
# o proximo Fast Meter. So entra quando o buffer esta sujo (`_buffer_clean`
# falso), o que na pratica e' a primeira volta depois de um comando ASCII.
DRAIN_DEADLINE_S = 0.3

# Quanto esperar pela resposta de um round-trip Fast Meter antes de desistir
# da volta. Medido: a resposta A5D1 completa chega em ~40-90 ms nos 4xx e
# ~25 ms no 751; 3 s e' folga de uma ordem de grandeza, nao um valor tipico.
RESPONSE_DEADLINE_S = 3.0

# Depois que o frame Fast Meter ja chegou, quanto tempo de silencio no socket
# basta pra concluir que o resto (o TARGET pipelinado) nao vem mais.
IDLE_AFTER_FM_S = 0.15

# Mesmo silencio, pros modos que nao pipelinam nada depois do FM.
IDLE_NO_PIPELINE_S = 0.5

# Espera maxima em UMA chamada ao selector. O loop tem deadlines proprios; o
# teto existe so pra que um `stop_event` disparado no meio de uma volta nao
# fique preso ate o deadline inteiro.
SELECT_SLICE_S = 0.1


class FirstTimeLog:
    """Diagnostics worth exactly once per CONNECTION, not once per process.

    These lines exist to be read against a relay: "which analog channels does
    this firmware actually expose" is how you check a profile's
    `analog_name_aliases`, and the FM parse/timeout warnings say what the relay
    on the other end is doing wrong. All four used to be flags hung on the
    poll functions themselves (`poll_loop._logged_an_keys = True`), which is
    per PROCESS -- so the first relay connected after a restart got the
    diagnostics and every relay after it got silence. The GLV opens N diagrams
    over N relays; that made the logs useless exactly when they were needed.

    One of these lives on each `RelayLink`, so it is scoped to one telnet to
    one relay. `ensure_bits()` stops and restarts the polling thread when a
    second diagram joins, and that deliberately does NOT re-log -- it is the
    same connection to the same relay. A genuine reconnect builds a new
    `RelayLink` and therefore a new log.
    """

    __slots__ = ("_logger", "_seen")

    def __init__(self, logger: logging.Logger):
        self._logger = logger
        self._seen: set[str] = set()

    def _first(self, key: str) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    def info(self, key: str, msg: str) -> None:
        if self._first(key):
            self._logger.info(msg)

    def warning(self, key: str, msg: str) -> None:
        if self._first(key):
            self._logger.warning(msg)


def poll_loop(client: SELClient, reader: AsciiTargetReader,
              state: LiveState, interval: float, logger: logging.Logger,
              stop_event: threading.Event | None = None,
              once: FirstTimeLog | None = None):
    """Le Fast Meter + TARGET em loop, atualizando o LiveState.

    Se `stop_event` for fornecido, o loop termina assim que ele for sinalizado
    (usado quando o usuario clica em "Trocar GLE" para voltar a landing page).
    """
    # Sem um `once` do RelayLink (chamada direta, teste), o loop ganha o
    # dele -- o escopo passa a ser a thread, que ainda e' melhor que o
    # processo.
    if once is None:
        once = FirstTimeLog(logger)
    # Um canal por conexao: o `AsciiTargetReader` pega o MESMO objeto, e e'
    # atraves dele que os dois combinam quem precisa drenar o buffer.
    ch = channel_for(client)
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        t0 = time.monotonic()
        try:
            # Drain rapido
            ch.drain(DRAIN_DEADLINE_S)

            ch.mark_dirty()
            # Pipeline: FM + VIEW 1:TARGET juntos
            ch.send_fast_meter()
            ch.send_target_region()

            buf = b""
            fm_frame = None
            target_bytes = None
            wait_deadline = time.monotonic() + RESPONSE_DEADLINE_S
            last_data = time.monotonic()
            search_start = 0
            while time.monotonic() < wait_deadline:
                chunk = ch.read_available()
                if chunk:
                    buf += chunk
                    last_data = time.monotonic()
                # FM
                if fm_frame is None:
                    idx = buf.find(ch.fm_marker, search_start)
                    if idx >= 0 and len(buf) - idx >= 3:
                        dl = buf[idx + 2]
                        if dl < 100:
                            search_start = idx + 2
                        elif len(buf) - idx >= dl:
                            fm_frame = bytes(buf[idx:idx + dl])
                # TARGET
                if target_bytes is None:
                    target_bytes = target_bytes_from_stream(buf,
                                                            TARGET_REGION_BYTES)
                if fm_frame is not None and target_bytes is not None:
                    break
                if not chunk and time.monotonic() - last_data > IDLE_AFTER_FM_S \
                        and fm_frame:
                    break
                if not chunk:
                    ch.wait_readable(min(
                        SELECT_SLICE_S,
                        max(0.0, wait_deadline - time.monotonic())))

            ch.mark_clean()

            # Parseia FORA do lock: `fast_meter_block` desempacota o frame
            # inteiro e o TARGET vira ate 3.4 mil bits: com isso dentro do
            # `with state.lock`, todo `/values` do navegador esperava a parse
            # em vez de esperar uma copia de dict.
            new_analogs = None
            new_digitals = None
            parse_error = ""
            if fm_frame is not None:
                try:
                    fm_data = sel_parser.fast_meter_block(
                        fm_frame,
                        ch.fast_meter_definition,
                        ch.dna_definition,
                        verbose=False,
                    )
                    new_analogs = fm_data.get("analogs", {})
                    # Log one-shot dos nomes que o firmware expoe no FM
                    # (pra batear contra analog_name_aliases do relay model).
                    keys = list(new_analogs.keys())
                    once.info(
                        "an_keys",
                        f"[poll] 1a leitura FM: {len(keys)} analog channels: "
                        f"{keys}",
                    )
                except Exception as e:
                    parse_error = f"FM parse: {e}"

            if target_bytes is not None:
                new_digitals = {}
                for row_idx, names in reader.layout.row_to_names.items():
                    if row_idx >= len(target_bytes):
                        continue
                    byte_val = target_bytes[row_idx]
                    for i, nm in enumerate(names):
                        if nm != "*":
                            new_digitals[nm] = int(bool(byte_val & (1 << (7 - i))))

            with state.lock:
                state.error = parse_error
                if new_analogs is not None:
                    state.analogs = new_analogs
                if new_digitals is not None:
                    state.digitals = new_digitals
                state.mark_updated()

        except Exception as e:
            with state.lock:
                state.error = f"poll: {e}"
            logger.warning(f"poll erro: {e}")

        elapsed = time.monotonic() - t0
        sleep_for = max(0.0, interval - elapsed)
        if stop_event is not None:
            if stop_event.wait(timeout=sleep_for):
                return
        else:
            time.sleep(sleep_for)


def poll_loop_fastmeter(client: SELClient, state: LiveState, interval: float,
                          logger: logging.Logger,
                          stop_event: threading.Event | None = None,
                          once: FirstTimeLog | None = None):
    """Loop de polling para reles que carregam digitals dentro da resposta
    Fast Meter (SEL-7xx: 751/787/etc.). Implementa AG95-10 fielmente -- um
    unico round-trip A5D1 traz analogs + N banks de 8 digital bits.

    Diferencas vs `poll_loop` (4xx):
      - Sem comando paralelo `VIEW 1:TARGET` (a 7xx nao expoe regiao TARGET
        via Fast Message).
      - Digitals vem de `fm_data['digitals']` (montado por
        `selprotopy.parser.fast_meter_block` a partir de
        `numdigitalbank`/`digitaloffset` + `dnaDef`).
      - Sem AsciiTargetReader -- a Relay Word exposta eh o subset configurado
        no rele (BNA/DNA) e ja vem pronta com nomes->valor 0/1.
    """
    # Sem um `once` do RelayLink (chamada direta, teste), o loop ganha o
    # dele -- o escopo passa a ser a thread, que ainda e' melhor que o
    # processo.
    if once is None:
        once = FirstTimeLog(logger)
    ch = channel_for(client)
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        t0 = time.monotonic()
        try:
            # Drain residual ASCII (mesma logica do poll_loop)
            ch.drain(DRAIN_DEADLINE_S)

            ch.mark_dirty()
            ch.send_fast_meter()

            buf = b""
            fm_frame = None
            wait_deadline = time.monotonic() + RESPONSE_DEADLINE_S
            search_start = 0
            last_data = time.monotonic()
            while time.monotonic() < wait_deadline:
                chunk = ch.read_available()
                if chunk:
                    buf += chunk
                    last_data = time.monotonic()
                idx = buf.find(ch.fm_marker, search_start)
                if idx >= 0 and len(buf) - idx >= 3:
                    dl = buf[idx + 2]
                    if dl < 8:
                        # Length byte invalido -- continua procurando
                        search_start = idx + 2
                    elif len(buf) - idx >= dl:
                        fm_frame = bytes(buf[idx:idx + dl])
                        break
                if (not chunk
                        and time.monotonic() - last_data > IDLE_NO_PIPELINE_S):
                    # Sem dados nem prompt; encerra essa volta
                    break
                if not chunk:
                    ch.wait_readable(min(
                        SELECT_SLICE_S,
                        max(0.0, wait_deadline - time.monotonic())))

            ch.mark_clean()

            # Parse fora do lock -- ver a nota em `poll_loop`.
            new_analogs = None
            new_digitals = None
            err = ""
            if fm_frame is not None:
                try:
                    fm_data = sel_parser.fast_meter_block(
                        fm_frame,
                        ch.fast_meter_definition,
                        ch.dna_definition,
                        verbose=False,
                    )
                    new_analogs = fm_data.get("analogs", {})
                    # selprotopy.fast_meter_block devolve digitals como
                    # bool (via int_to_bool_list). O JS do dashboard usa
                    # `v === 0 || v === 1` (strict), entao precisamos
                    # serializar como int -- senao tudo vira indeterminado.
                    raw_digitals = fm_data.get("digitals", {})
                    new_digitals = {
                        k: int(bool(v)) for k, v in raw_digitals.items()
                    }
                    # Diagnostic uma vez no startup: tamanho do bloco
                    # recebido, primeiros 6 bits parseados.
                    sample = list(new_digitals.items())[:6]
                    once.info(
                        "fm_first",
                        f"[poll] 1a leitura FM ok: frame={len(fm_frame)}B, "
                        f"{len(new_digitals)} digitals, "
                        f"{len(new_analogs)} analogs. sample: {sample}",
                    )
                except Exception as e:
                    err = f"FM parse: {e}"
                    once.warning(
                        "fm_parse_err",
                        f"[poll] FM parse falhou (frame {len(fm_frame)}B): {e} | "
                        f"primeiros 32 bytes hex: {fm_frame[:32].hex(' ')}",
                    )
            else:
                err = "FM timeout"
                once.warning(
                    "fm_timeout",
                    f"[poll] FM timeout: nenhum frame A5D1 recebido em "
                    f"{RESPONSE_DEADLINE_S:.0f}s. "
                    f"buffer recebido ({len(buf)}B): {bytes(buf[:64]).hex(' ')}...",
                )

            with state.lock:
                state.error = err
                if new_analogs is not None:
                    state.analogs = new_analogs
                if new_digitals is not None:
                    state.digitals = new_digitals
                state.mark_updated()

        except Exception as e:
            with state.lock:
                state.error = f"poll: {e}"
            logger.warning(f"poll erro (fastmeter): {e}")

        elapsed = time.monotonic() - t0
        sleep_for = max(0.0, interval - elapsed)
        if stop_event is not None:
            if stop_event.wait(timeout=sleep_for):
                return
        else:
            time.sleep(sleep_for)


def _read_fast_meter_analogs(client: SELClient, logger: logging.Logger,
                             timeout: float = 3.0):
    """Um round-trip A5D1; devolve `(analogs, erro)`.

    Compartilhado pelos modos que tiram SO os analogicos do Fast Meter.
    """
    ch = channel_for(client)
    ch.drain(DRAIN_DEADLINE_S)

    ch.mark_dirty()
    ch.send_fast_meter()

    buf = b""
    fm_frame = None
    wait_deadline = time.monotonic() + timeout
    search_start = 0
    last_data = time.monotonic()
    while time.monotonic() < wait_deadline:
        chunk = ch.read_available()
        if chunk:
            buf += chunk
            last_data = time.monotonic()
        idx = buf.find(ch.fm_marker, search_start)
        if idx >= 0 and len(buf) - idx >= 3:
            dl = buf[idx + 2]
            if dl < 8:
                search_start = idx + 2
            elif len(buf) - idx >= dl:
                fm_frame = bytes(buf[idx:idx + dl])
                break
        if not chunk and time.monotonic() - last_data > IDLE_NO_PIPELINE_S:
            break
        if not chunk:
            ch.wait_readable(min(
                SELECT_SLICE_S, max(0.0, wait_deadline - time.monotonic())))
    ch.mark_clean()

    if fm_frame is None:
        return {}, "FM timeout"
    try:
        fm_data = sel_parser.fast_meter_block(
            fm_frame, ch.fast_meter_definition, ch.dna_definition, verbose=False,
        )
        return fm_data.get("analogs", {}), ""
    except Exception as e:
        return {}, f"FM parse: {e}"


def poll_loop_tar(client: SELClient, reader: AsciiTargetReader,
                  state: LiveState, interval: float, logger: logging.Logger,
                  stop_event: threading.Event | None = None,
                  once: FirstTimeLog | None = None):
    """Loop de polling da familia 3xx (SEL-311C/311L).

    Nem o caminho 4xx nem o 7xx servem aqui (medido num SEL-311C-1-R509):

      - `VIEW 1:TARGET` / `MAP 1 TARGET BL` -> "Invalid Command".
      - A5D1 anuncia numdigitalbank=111, mas o bloco DNA vem com 111 linhas de
        "*": nenhum bit nomeado, entao a metade digital do Fast Meter e'
        indecifravel.

    Sobra: analogicos do A5D1 (10 canais, esses funcionam) + digitais por
    `TAR <linha>` ASCII, 8 bits nomeados por round-trip.

    Cada `TAR` custa ~200ms NO RELE -- pipelinar varios nao ajuda (medido:
    2.81s sequencial vs 2.53s pipelinado pra 13 linhas). Por isso lemos apenas
    os bits da pagina aberta (`state.wanted_bits`), e nao o diagrama inteiro:
    a pagina mais pesada do GLE de exemplo tem 46 bits em 13 linhas (~2.6s),
    contra 41 linhas (~8s) se lessemos tudo.
    """
    logged_first = False
    prev_wanted: set[str] | None = None
    # Sem um `once` do RelayLink (chamada direta, teste), o loop ganha o
    # dele -- o escopo passa a ser a thread, que ainda e' melhor que o
    # processo.
    if once is None:
        once = FirstTimeLog(logger)
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        t0 = time.monotonic()
        try:
            analogs, err = _read_fast_meter_analogs(client, logger)

            with state.lock:
                wanted = sorted(state.wanted_bits)
            digitals = {}
            if wanted:
                raw = reader.read_via_tar_rows(wanted)
                digitals = {k: v for k, v in raw.items() if v is not None}

            # Leitura parcial precisa aparecer: um bit que nao foi lido some
            # do payload e o diagrama o pinta como indeterminado. Melhor dizer
            # que a leitura falhou do que deixar parecer "estado desconhecido
            # do rele" -- em comissionamento os dois significam coisas bem
            # diferentes.
            readable = [b for b in wanted if b in reader.layout.bit_to_pos]
            page_changed = prev_wanted is not None and set(wanted) != prev_wanted
            if (readable and len(digitals) < len(readable)
                    and not page_changed and prev_wanted is not None):
                # Na primeira volta apos trocar de pagina o conjunto pedido
                # mudou no meio do caminho (e bits novos ainda passam por
                # descoberta), entao "faltando" ali e' transicao, nao falha.
                err = (f"{err} " if err else "") + (
                    f"leitura parcial: {len(digitals)}/{len(readable)} bits"
                )
            prev_wanted = set(wanted)

            with state.lock:
                state.error = err
                if analogs:
                    state.analogs = analogs
                if wanted:
                    state.digitals = digitals
                state.mark_updated()

            if not logged_first and (analogs or digitals):
                rows = len({reader.layout.bit_to_pos[b][0]
                            for b in wanted if b in reader.layout.bit_to_pos})
                logger.info(
                    f"[poll] 1a leitura TAR ok: {len(analogs)} analogs, "
                    f"{len(digitals)}/{len(wanted)} digitais em {rows} linha(s), "
                    f"{time.monotonic() - t0:.2f}s"
                )
                logged_first = True

        except Exception as e:
            with state.lock:
                state.error = f"poll: {e}"
            logger.warning(f"poll erro (tar): {e}")

        elapsed = time.monotonic() - t0
        sleep_for = max(0.0, interval - elapsed)
        if stop_event is not None:
            if stop_event.wait(timeout=sleep_for):
                return
        else:
            time.sleep(sleep_for)

