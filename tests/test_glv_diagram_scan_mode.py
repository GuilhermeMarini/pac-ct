"""`GlvDiagram`'s own share of the scan-mode/period/coverage feature.

`tests/test_glv_scan_mode.py` only exercises `pick_transport`/`DEFAULT_PORTS`
(Tasks 1 and 6). Nothing before this file drove `GlvDiagram.tab()`,
`GlvDiagram.values()`'s per-page coverage, or `GlvDiagram.set_interval_ms`
directly -- this is that coverage, using fakes instead of a real GLE/relay
(as the rest of `pacct/web/glv/` tests already do).
"""
from __future__ import annotations

import logging

from pacct.web.glv.diagram import GlvDiagram
from pacct.web.glv.link import TooManyLinks
from pacct.web.glv.state import LiveState
from pacct.web.glv.transport import SCAN_MMS, SCAN_TELNET

_LOG = logging.getLogger("test")


class _Defaults:
    poll_interval = 0.5
    mms_interval_ms = 100
    acc_password = "OTTER"
    setup_timeout = 60.0


def _diagram(scan_mode=SCAN_TELNET, **kw) -> GlvDiagram:
    return GlvDiagram(
        "d1", relay_name="RELE1", gle_name="GL1", gle_path=None,
        ip="192.0.2.10", port=23, relay_model=None, logger=_LOG,
        scan_mode=scan_mode, **kw,
    )


class _FakeMmsMap:
    def __init__(self, points: dict):
        self.points = points


class _FakeTransport:
    """The SEAM, not the innards: `values()` asks `coverage_for(bits)` and
    knows nothing about a `_map`. It used to reach into `transport._map`
    directly, which is the diagram opening a transport it cannot even name."""

    def __init__(self, mms_map=None, source="scd"):
        self._map = mms_map
        self._source = source

    def coverage_for(self, bits):
        if self._map is None:
            return None
        mapped = sum(1 for b in bits if b.upper() in self._map.points)
        return {"mapped": mapped, "total": len(bits), "source": self._source}


class _TransportWithoutAMap:
    """A telnet transport has no `_map` at all -- and must still answer."""

    def coverage_for(self, bits):
        return None


class _FakeLink:
    """Just enough of `RelayLink` for `tab()`/`values()`/`set_interval_ms`."""

    def __init__(self, transport=None, poll_interval=0.5):
        self.transport = transport or _FakeTransport()
        self.state = LiveState()
        self.owners: set = set()
        self.fid = "FAKE-FID"
        # `RelayLink` sempre tem os dois, e o diagrama pergunta pelos dois: um
        # link que desistiu (a leitura parou sozinha) continua pendurado no
        # `self.link` mas responde `connected = False`, e e' assim que a aba
        # para de dizer LIVE. Um dublê sem isto so' testa o caso feliz.
        self.connected = True
        self.error = ""
        self.key = "192.0.2.10:23"
        self._poll_interval = poll_interval
        self.set_poll_interval_calls: list = []

    def set_wanted_bits(self, owner, bits):
        pass

    @property
    def poll_interval(self) -> float:
        return self._poll_interval

    def set_poll_interval(self, seconds: float) -> bool:
        self.set_poll_interval_calls.append(seconds)
        self._poll_interval = seconds
        return True   # "applies now", for the tests that need that branch


class TestTabAndScanMode:

    def test_tab_reports_the_chosen_scan_mode(self):
        assert _diagram(SCAN_MMS).tab()["scan_mode"] == SCAN_MMS
        assert _diagram(SCAN_TELNET).tab()["scan_mode"] == SCAN_TELNET

    def test_scd_sha_and_scd_path_round_trip(self):
        d = _diagram(SCAN_MMS, scd_sha="abc123", scd_path="/x/y.scd")
        assert d.scd_sha == "abc123"
        assert d.scd_path == "/x/y.scd"


class TestPollInterval:

    def test_mms_diagram_uses_the_configured_mms_default(self):
        d = _diagram(SCAN_MMS)
        assert d._poll_interval(_Defaults()) == 0.1

    def test_telnet_diagram_uses_the_telnet_default(self):
        d = _diagram(SCAN_TELNET)
        assert d._poll_interval(_Defaults()) == 0.5

    def test_a_stored_override_wins_over_either_default(self):
        """The fix for fix-round-1 issue #2: a period requested while the
        diagram had no live link must not be dropped -- `connect()` reads it
        back through here."""
        d = _diagram(SCAN_TELNET)
        d._interval_ms = 250
        assert d._poll_interval(_Defaults()) == 0.25


class TestPerPageCoverage:

    def test_none_with_no_link(self):
        d = _diagram(SCAN_MMS)
        d.bits_per_page = {"pageA": {"PLT01", "PLT02", "PLT03"}}
        assert d.values("pageA")["coverage"] is None

    def test_none_with_a_transport_that_has_no_map(self):
        """Telnet (and MMS before its first `prepare_bits`) has no `_map`:
        the badge must be ABSENT, never a lying zero."""
        d = _diagram(SCAN_MMS)
        d.bits_per_page = {"pageA": {"PLT01", "PLT02", "PLT03"}}
        d.link = _FakeLink(_FakeTransport(mms_map=None))
        assert d.values("pageA")["coverage"] is None

    def test_counts_only_this_pages_bits_not_the_link_wide_union(self):
        """The link's `_map.points` can hold bits from OTHER diagrams on the
        same relay too; the badge must count only `wanted` for THIS page."""
        d = _diagram(SCAN_MMS)
        d.bits_per_page = {"pageA": {"PLT01", "PLT02", "PLT03"}}
        mms_map = _FakeMmsMap({
            "PLT01": object(), "PLT02": object(),
            "SOMETHING_FROM_ANOTHER_DIAGRAM": object(),
        })
        d.link = _FakeLink(_FakeTransport(mms_map=mms_map))
        cov = d.values("pageA")["coverage"]
        assert cov == {"mapped": 2, "total": 3, "source": "scd"}

    def test_the_source_of_the_map_reaches_the_payload(self):
        """`MmsMap.source` was computed and never left the server. The
        headline decision of this feature is "project SCD first"; without the
        source on screen a silent fall back to the factory table looked
        exactly like a successful one."""
        d = _diagram(SCAN_MMS)
        d.bits_per_page = {"pageA": {"PLT01"}}
        mms_map = _FakeMmsMap({"PLT01": object()})
        d.link = _FakeLink(_FakeTransport(mms_map=mms_map, source="tabela"))
        assert d.values("pageA")["coverage"]["source"] == "tabela"

    def test_a_transport_with_no_map_attribute_at_all_still_answers(self):
        """Pins the seam: nothing here may depend on a `_map` existing."""
        d = _diagram(SCAN_MMS)
        d.bits_per_page = {"pageA": {"PLT01"}}
        d.link = _FakeLink(_TransportWithoutAMap())
        assert d.values("pageA")["coverage"] is None


class TestSetIntervalMs:

    def test_telnet_is_refused_and_leaves_the_interval_untouched(self):
        d = _diagram(SCAN_TELNET)
        d.link = _FakeLink(poll_interval=0.5)
        result = d.set_interval_ms(50)
        assert result["status"] == "recusado"
        assert result["interval_ms"] == 500   # unchanged, from the fake link
        assert result["reason"]
        assert d.link.set_poll_interval_calls == []
        assert d._interval_ms is None   # never stored for a refused request

    def test_mms_has_no_floor_and_zero_means_as_fast_as_the_relay_answers(self):
        # Nao ha piso: o que segura o ritmo e' o ciclo da leitura, dentro do
        # transporte (ver test_mms_transport.py). O diagrama repassa o que
        # foi pedido.
        d = _diagram(SCAN_MMS)
        d.link = _FakeLink()
        assert d.set_interval_ms(10)["interval_ms"] == 10
        assert d.set_interval_ms(0)["interval_ms"] == 0
        assert d.link.set_poll_interval_calls == [0.01, 0.0]

    def test_a_negative_period_is_cut_at_zero(self):
        # Unico ajuste que sobrou: periodo negativo nao quer dizer nada e
        # viraria um sleep negativo la' na frente.
        d = _diagram(SCAN_MMS)
        d.link = _FakeLink()
        assert d.set_interval_ms(-50)["interval_ms"] == 0
        assert d.link.set_poll_interval_calls == [0.0]

    def test_mms_with_a_live_link_reports_applied_now(self):
        d = _diagram(SCAN_MMS)
        d.link = _FakeLink()
        result = d.set_interval_ms(250)
        assert result == {"interval_ms": 250, "status": "aplicado", "reason": ""}

    def test_mms_with_no_link_defers_instead_of_dropping_the_request(self):
        """Fix-round-1 issue #2: a diagram still connecting (or already
        disconnected) must not silently discard the request -- it must be
        stored so the eventual `connect()` picks it up."""
        d = _diagram(SCAN_MMS)
        assert d.link is None
        result = d.set_interval_ms(250)
        assert result["status"] == "adiado"
        assert result["interval_ms"] == 250
        assert result["reason"]
        # And it really is retained for the next connect:
        assert d._poll_interval(_Defaults()) == 0.25

    def test_mms_zombie_guard_also_reports_deferred(self):
        """`RelayLink.set_poll_interval` returns False when a wedged poll
        thread blocks an immediate restart; the diagram must surface that as
        "adiado", not claim the change is already live."""
        class ZombieLink(_FakeLink):
            def set_poll_interval(self, seconds):
                self.set_poll_interval_calls.append(seconds)
                return False   # stored, but did not restart anything

        d = _diagram(SCAN_MMS)
        d.link = ZombieLink()
        result = d.set_interval_ms(300)
        assert result["status"] == "adiado"
        assert d.link.set_poll_interval_calls == [0.3]


class _ReapingPool:
    """`LinkPool` as far as `connect_async` uses it: it only releases."""

    def __init__(self):
        self.released: list = []

    def release(self, link, owner):
        self.released.append((link, owner))

    def acquire(self, ip, port, owner, make_transport=None):
        # A reconexao em si nao e' o assunto destes testes: recusar aqui deixa
        # a thread de conexao terminar por um caminho que o diagrama ja trata
        # (`_fail`), em vez de estourar num duble incompleto.
        raise TooManyLinks("sem bancada neste teste")


class TestALinkThatGaveUp:
    """A poll loop can end on its own -- a dropped MMS association does not
    come back -- and `RelayLink._poll_gave_up` marks the link not-connected
    from the polling thread. The diagram is not told: nothing clears
    `self.link`, because releasing it needs the pool.

    So the tab used to keep reading LIVE with "Desconectar" after a
    terminating error. The bits DID go indeterminate (the link clears its own
    `LiveState`), which made it worse to read, not better: a live-looking tab
    over an empty diagram.
    """

    def _gave_up(self):
        d = _diagram(scan_mode=SCAN_MMS)
        link = _FakeLink()
        link.connected = False
        link.error = "MMS: associação caiu"
        d.link = link
        d.status = "live"                  # o que a conexao bem-sucedida pos
        return d, link

    def test_the_tab_stops_saying_live(self):
        d, _ = self._gave_up()
        t = d.tab(_Defaults())
        assert t["connected"] is False, "o botao continuaria 'Desconectar'"
        assert t["status"] == "error"
        assert "associação caiu" in t["error"], "a tela nao diz por que parou"

    def test_the_diagram_agrees_with_its_own_tab(self):
        d, _ = self._gave_up()
        assert d.connected is False
        assert d.values("")["connected"] is False

    def test_connecting_again_releases_the_dead_link_instead_of_no_op(self):
        """The other half, and it cannot be skipped: once `connected` tells
        the truth the button reads "Conectar", and `connect_async` used to
        return early on `self.link is not None` -- so the button did nothing
        and the only way back was Desconectar first.
        """
        d, link = self._gave_up()
        pool = _ReapingPool()

        d.connect_async(pool, _Defaults())

        # `release` e a limpeza do `self.link` acontecem em `connect_async`
        # mesmo, antes de qualquer thread; o desfecho da reconexao e' assunto
        # de outro teste e aqui seria uma corrida.
        assert pool.released == [(link, d.id)], "nao soltou o link morto"
        assert d.link is None, "o link morto continuou pendurado no diagrama"

    def test_a_live_link_is_never_reaped(self):
        """The guard must not fire on a healthy link: a second Conectar on a
        connected diagram is still a no-op, and must not release anything."""
        d = _diagram(scan_mode=SCAN_MMS)
        d.link = _FakeLink()               # connected = True
        d.status = "live"
        pool = _ReapingPool()

        d.connect_async(pool, _Defaults())

        assert pool.released == []
        assert d.status == "live"
