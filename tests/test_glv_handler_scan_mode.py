"""Handler-level wiring of scan_mode/scd_sha through `/diagrams`,
`/diagrams/batch` and `/period` -- the surface Task 7 actually built.

`tests/test_glv_scan_mode.py` only pins `pick_transport`/`DEFAULT_PORTS`
(Tasks 1 and 6); nothing before this file drove `_create_diagram`,
`_create_diagrams_batch` or the `/period` route themselves. These tests call
`Handler.do_POST` directly against an instance built with `__new__` (no real
socket): every response in the routes exercised here goes through
`self._send_json`, which is overridden per-instance to record `(code,
payload)` instead of touching `BaseHTTPRequestHandler`'s wire machinery. A
real `SessionManager`/`Session` and a real (byte-for-byte minimal) GLE file on
disk back everything else, so `_resolve_gle` and `_resolve_scd_path` run for
real.
"""
from __future__ import annotations

import io
import json
import logging

from pacct.parsers.rdb import GleEntry, RdbInfo, RelayEntry
from pacct.web.glv.handler import GlvDefaults, build_glv_handler
from pacct.web.glv.transport import DEFAULT_PORTS, SCAN_MMS, SCAN_TELNET
from pacct.web.project_files import library as filelib
from pacct.web.session import SessionManager

_GLE = (b'<?xml version="1.0" encoding="utf-8"?>\r\n'
        b'<editor version="1.0"><page name="GL1"><elements /></page></editor>\r\n')


def _make_handler(tmp_path, **default_overrides):
    """`(Handler class, sessions, defaults, session, sha256-of-a-real-SCD)`."""
    sessions = SessionManager(root=tmp_path / "sessions",
                              logger=logging.getLogger("test"))
    defaults = GlvDefaults(**{"port": 23, **default_overrides})
    Handler = build_glv_handler(logging.getLogger("test"), sessions, defaults)
    sess, _ = sessions.resolve(None)

    gle_path = tmp_path / "GL1.gle"
    gle_path.write_bytes(_GLE)
    relay = RelayEntry(name="QPC1", gles=[
        GleEntry(name="GL1", filename="GL1.gle",
                rel_path="Relays/QPC1/Misc/GL1.gle", fs_path=gle_path),
    ], model=None, ip=None)
    info = RdbInfo(rdb_path=tmp_path / "proj.rdb", extract_dir=tmp_path,
                   sha256="a" * 64, reused=True, relays=[relay],
                   display_name="proj.rdb")

    scd_path = tmp_path / "proj.scd"
    scd_path.write_bytes(b"<SCL/>")
    scd_sha = "b" * 64
    lib = filelib.library_for(sessions, sess)
    lib.add(filelib.FileEntry(sha256=scd_sha, kind=filelib.KIND_SCD,
                              display_name="proj.scd", size=6, path=scd_path))

    return Handler, sessions, defaults, sess, info, scd_sha


def _new_request(Handler, sess, path: str, body: dict) -> tuple:
    """A `Handler` instance wired for exactly one POST, with `_send_json`
    replaced by a recorder instead of the real HTTP write path."""
    h = Handler.__new__(Handler)
    h.session = sess
    h.mount_prefix = ""
    h.path = path
    payload = json.dumps(body).encode("utf-8")
    h.headers = {"Content-Length": str(len(payload))}
    h.rfile = io.BytesIO(payload)
    sent: list = []
    h._send_json = lambda code, data: sent.append((code, data))
    return h, sent


class TestCreateDiagramScanMode:

    def test_default_scan_mode_is_telnet_on_port_23(self, tmp_path):
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(tmp_path)
        h, sent = _new_request(Handler, sess, "/diagrams",
                               {"relay": "QPC1", "gle": "GL1",
                                "ip": "192.0.2.10"})
        h.sess().rdb = info
        h.do_POST()
        assert sent and sent[0][0] == 200
        st = h.sess()
        d = st.diagrams[sent[0][1]["id"]]
        assert d.scan_mode == SCAN_TELNET
        assert d.port == DEFAULT_PORTS[SCAN_TELNET] == 23
        assert d.scd_path is None

    def test_a_telnet_diagram_uses_the_config_ini_port_not_a_hardcoded_23(
            self, tmp_path):
        """`[tcp] port` exists because a substation can sit behind a terminal
        server or a port-forward (`port = 2001`). Deriving the port from
        `DEFAULT_PORTS.get(scan_mode, defaults.port)` silently overrode it with
        23 for the one mode that was supposed to honour it -- while an UNKNOWN
        mode, falling through to the default, still got it right."""
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(
            tmp_path, port=2001)
        h, sent = _new_request(Handler, sess, "/diagrams",
                               {"relay": "QPC1", "gle": "GL1",
                                "ip": "192.0.2.10",
                                "scan_mode": SCAN_TELNET})
        h.sess().rdb = info
        h.do_POST()
        d = h.sess().diagrams[sent[0][1]["id"]]
        assert d.scan_mode == SCAN_TELNET
        assert d.port == 2001

    def test_the_batch_route_honours_the_config_ini_port_the_same_way(
            self, tmp_path):
        """Two call sites, one rule -- the batch open must not disagree with
        the single open about which port a telnet diagram gets."""
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(
            tmp_path, port=2001)
        h, sent = _new_request(Handler, sess, "/diagrams/batch",
                               {"items": [{"relay": "QPC1", "gle": "GL1",
                                           "ip": "192.0.2.10",
                                           "scan_mode": SCAN_TELNET},
                                          {"relay": "QPC1", "gle": "GL1",
                                           "ip": "192.0.2.10",
                                           "scan_mode": SCAN_MMS}]})
        h.sess().rdb = info
        h.do_POST()
        assert sent and sent[0][0] == 200, sent
        st = h.sess()
        ids = sent[0][1]["ids"]
        assert [st.diagrams[i].port for i in ids] == [2001, 102]

    def test_an_mms_diagram_still_goes_to_102_whatever_the_config_says(
            self, tmp_path):
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(
            tmp_path, port=2001)
        h, sent = _new_request(Handler, sess, "/diagrams",
                               {"relay": "QPC1", "gle": "GL1",
                                "ip": "192.0.2.10", "scan_mode": SCAN_MMS})
        h.sess().rdb = info
        h.do_POST()
        assert h.sess().diagrams[sent[0][1]["id"]].port == 102

    def test_mms_scan_mode_lands_on_port_102_and_resolves_the_scd(self, tmp_path):
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(tmp_path)
        h, sent = _new_request(Handler, sess, "/diagrams",
                               {"relay": "QPC1", "gle": "GL1",
                                "ip": "192.0.2.10", "scan_mode": SCAN_MMS,
                                "scd_sha": scd_sha})
        h.sess().rdb = info
        h.do_POST()
        assert sent and sent[0][0] == 200
        st = h.sess()
        d = st.diagrams[sent[0][1]["id"]]
        assert d.scan_mode == SCAN_MMS
        assert d.port == DEFAULT_PORTS[SCAN_MMS] == 102
        assert d.scd_sha == scd_sha
        assert d.scd_path is not None
        assert d.scd_path.name == "proj.scd"

    def test_an_unresolvable_scd_sha_degrades_to_none_without_failing(self, tmp_path):
        """A sha that is not (or no longer) in the project's library is not
        fatal -- the MMS transport still has the factory table as a second
        source."""
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(tmp_path)
        h, sent = _new_request(Handler, sess, "/diagrams",
                               {"relay": "QPC1", "gle": "GL1",
                                "ip": "192.0.2.10", "scan_mode": SCAN_MMS,
                                "scd_sha": "not-a-real-sha"})
        h.sess().rdb = info
        h.do_POST()
        assert sent and sent[0][0] == 200
        st = h.sess()
        d = st.diagrams[sent[0][1]["id"]]
        assert d.scd_path is None


class TestCreateDiagramsBatchMixedModes:

    def test_a_batch_mixing_telnet_and_mms_lands_each_on_its_own_port(self, tmp_path):
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(tmp_path)
        h, sent = _new_request(Handler, sess, "/diagrams/batch", {
            "items": [
                {"relay": "QPC1", "gle": "GL1", "ip": "192.0.2.10",
                 "scan_mode": SCAN_TELNET},
                {"relay": "QPC1", "gle": "GL1", "ip": "192.0.2.10",
                 "scan_mode": SCAN_MMS, "scd_sha": scd_sha},
            ],
        })
        h.sess().rdb = info
        h.job = lambda: _NullJob()
        h.do_POST()
        assert sent and sent[0][0] == 200
        ids = sent[0][1]["ids"]
        assert len(ids) == 2
        st = h.sess()
        telnet_d, mms_d = st.diagrams[ids[0]], st.diagrams[ids[1]]
        assert telnet_d.scan_mode == SCAN_TELNET and telnet_d.port == 23
        assert mms_d.scan_mode == SCAN_MMS and mms_d.port == 102
        assert mms_d.scd_path is not None and mms_d.scd_path.name == "proj.scd"

    def test_an_item_without_scan_mode_falls_back_to_the_process_default(self, tmp_path):
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(
            tmp_path, scan_mode=SCAN_MMS)
        h, sent = _new_request(Handler, sess, "/diagrams/batch", {
            "items": [{"relay": "QPC1", "gle": "GL1", "ip": "192.0.2.10"}],
        })
        h.sess().rdb = info
        h.job = lambda: _NullJob()
        h.do_POST()
        st = h.sess()
        d = st.diagrams[sent[0][1]["ids"][0]]
        assert d.scan_mode == SCAN_MMS
        assert d.port == 102


class _NullJob:
    def fraction(self, *a, **k):
        pass

    def finish(self, *a, **k):
        pass


class TestPeriodRoute:

    def _diagram_id(self, h, st, **diagram_kw):
        from pacct.web.glv.diagram import GlvDiagram
        d = GlvDiagram("d1", relay_name="QPC1", gle_name="GL1", gle_path=None,
                       ip="192.0.2.10", port=102, relay_model=None,
                       logger=logging.getLogger("test"), **diagram_kw)
        st.diagrams[d.id] = d
        st.order.append(d.id)
        st.active = d.id
        return d

    def test_rejects_a_body_without_interval_ms(self, tmp_path):
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(tmp_path)
        h, sent = _new_request(Handler, sess, "/period?d=d1", {})
        self._diagram_id(h, h.sess(), scan_mode=SCAN_MMS)
        h.do_POST()
        assert sent == [(400, {"error": "intervalo inválido"})]

    def test_rejects_a_non_numeric_interval_ms(self, tmp_path):
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(tmp_path)
        h, sent = _new_request(Handler, sess, "/period?d=d1",
                               {"interval_ms": "not-a-number"})
        self._diagram_id(h, h.sess(), scan_mode=SCAN_MMS)
        h.do_POST()
        assert sent == [(400, {"error": "intervalo inválido"})]

    def test_404s_for_an_unknown_diagram(self, tmp_path):
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(tmp_path)
        h, sent = _new_request(Handler, sess, "/period?d=nope",
                               {"interval_ms": 200})
        h.sess()   # create the (empty) session state
        h.do_POST()
        assert sent == [(404, {"error": "diagrama não encontrado"})]

    def test_a_valid_request_forwards_to_set_interval_ms_and_returns_its_result(self, tmp_path):
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(tmp_path)
        h, sent = _new_request(Handler, sess, "/period?d=d1",
                               {"interval_ms": 250})
        self._diagram_id(h, h.sess(), scan_mode=SCAN_MMS)
        h.do_POST()
        assert len(sent) == 1
        code, payload = sent[0]
        assert code == 200
        # No live link on this diagram -> deferred, per GlvDiagram.set_interval_ms.
        assert payload["interval_ms"] == 250
        assert payload["status"] == "adiado"
        assert payload["reason"]

    def test_a_telnet_diagram_is_refused_through_the_real_route(self, tmp_path):
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(tmp_path)
        h, sent = _new_request(Handler, sess, "/period?d=d1",
                               {"interval_ms": 50})
        self._diagram_id(h, h.sess(), scan_mode=SCAN_TELNET)
        h.do_POST()
        assert len(sent) == 1
        code, payload = sent[0]
        assert code == 200
        assert payload["status"] == "recusado"


class TestScanModeDefaultFromConfig:
    """`GlvDefaults.scan_mode` existed and was never read from `config.ini`:
    the field defaulted to "telnet" and nothing else ever wrote it, so the
    knob was there and did nothing."""

    def _cfg(self, **web):
        import configparser
        cfg = configparser.ConfigParser()
        cfg["web"] = {k: str(v) for k, v in web.items()}
        return cfg

    def test_the_config_value_reaches_the_defaults(self):
        from pacct.web.dashboard import _glv_scan_mode
        log = logging.getLogger("test")
        assert _glv_scan_mode(self._cfg(glv_scan_mode="mms"), log) == SCAN_MMS
        assert _glv_scan_mode(self._cfg(glv_scan_mode="TELNET"),
                              log) == SCAN_TELNET

    def test_no_key_at_all_is_telnet(self):
        from pacct.web.dashboard import _glv_scan_mode
        assert _glv_scan_mode(self._cfg(), logging.getLogger("test")) == \
            SCAN_TELNET

    def test_an_unknown_value_warns_and_falls_back(self, caplog):
        """Silently keeping a bogus value would show a radio ticked on a mode
        that does not exist, while `pick_transport` quietly used telnet."""
        from pacct.web.dashboard import _glv_scan_mode
        with caplog.at_level(logging.WARNING):
            got = _glv_scan_mode(self._cfg(glv_scan_mode="goose"),
                                 logging.getLogger("test"))
        assert got == SCAN_TELNET
        assert "goose" in caplog.text

    def test_a_diagram_opened_with_no_scan_mode_takes_the_default(self,
                                                                  tmp_path):
        Handler, sessions, defaults, sess, info, scd_sha = _make_handler(
            tmp_path, scan_mode=SCAN_MMS)
        h, sent = _new_request(Handler, sess, "/diagrams",
                               {"relay": "QPC1", "gle": "GL1",
                                "ip": "192.0.2.10"})
        h.sess().rdb = info
        h.do_POST()
        d = h.sess().diagrams[sent[0][1]["id"]]
        assert d.scan_mode == SCAN_MMS and d.port == 102
