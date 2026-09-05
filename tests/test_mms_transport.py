"""The MMS polling loop, driven by bytes recorded from a relay.

The read is ONE Read naming every leaf of the page (py61850's `read_refs`).
What the tests here defend is what that change puts at risk: a failed access
comes back as `{"error": ...}` IN PLACE of the value, and `int(bool({...}))`
is 1 -- a bit painted on that nobody read, on the screen of the person doing
the commissioning.

The fixtures in `tests/fixtures/mms/` are a REAL capture from the bench
SEL-451-5 R331 (see their `provenance` and `test_mms_fixtures_provenance.py`),
wrapped in `{"provenance": ..., "<key>": payload}`. They are OLDER than the
batch: they hold the answer to a whole-container Read plus the definition that
describes the order of the children. `_capture_values` walks that definition
once and turns it into the `item -> value` table the real `read_refs` returns
-- the same value by both paths, which is what the bench measured on the 751
(1053 of 1055 bits identical; the remaining 2 oscillate between two turns of
the SAME path). Nothing here fixes the fixture's size nor which containers it
brings.
"""
from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path

import pytest

# `py61850` is the only project dependency that does not come from PyPI (see
# requirements.txt). Without this guard the whole module stops being
# COLLECTABLE on a machine without it, and the entire suite stops -- including
# the hundreds of tests that have nothing to do with MMS.
pytest.importorskip("py61850")

from py61850.errors import Iec61850Error, MmsError  # noqa: E402
from py61850.mms import pdu  # noqa: E402

from pacct.web.glv.state import LiveState  # noqa: E402
from pacct.web.glv.transport.mms import (  # noqa: E402
    IDLE_INTERVAL,
    MmsSetupError,
    MmsTransport,
)

FIX = Path(__file__).parent / "fixtures" / "mms"


def _load(name, key):
    return json.loads((FIX / name).read_text(encoding="utf-8"))[key]


@pytest.fixture(scope="module")
def recorded():
    return (_load("451_datadefs.json", "datadefs"),
            _load("451_reads_b64.json", "reads"),
            _load("451_expected_stvals.json", "expected"))


@pytest.fixture(scope="module")
def ann_directory():
    return _load("451_ann_directory.json", "directory")


def _walk(node_type, value):
    """The same positional walk the relay describes in the definition."""
    structure = (node_type.get("structure")
                 if isinstance(node_type, dict) else None)
    if structure and isinstance(value, (list, tuple)):
        # strict=False on purpose: this walker mimics what py61850 hands
        # back, and a short value is one of the shapes under test.
        return {child["name"]: _walk(child.get("type"), v)
                for child, v in zip(structure, value, strict=False)}
    return value


def _capture_values(defs, reads):
    """`LN$FC$DO$leaf -> value`, taken from the per-container capture.

    This step lives here, and not in `transport/mms.py`, because it is the
    CAPTURE that is per container: the polling no longer reads any container.
    Walking the definition once turns the recorded bytes into the table the
    real `read_refs` returns, without inventing any value.
    """
    out = {}
    for container, definition in defs.items():
        raw = pdu.decode_read_response(base64.b64decode(reads[container]))
        if not raw or not isinstance(raw[0], (list, tuple)):
            continue
        # strict=False: the capture is real traffic, and a response with
        # fewer children than the definition is a shape this decodes, not a
        # fixture bug to raise on.
        decoded = {child["name"]: _walk(child.get("type"), value)
                   for child, value in zip(definition["type"]["structure"],
                                           raw[0], strict=False)}
        for do, node in decoded.items():
            if isinstance(node, dict):
                for leaf, value in node.items():
                    if not isinstance(value, dict):
                        out[f"{container}${do}${leaf}"] = value
            else:
                out[f"{container}${do}"] = node
    return out


def test_the_capture_carries_the_leaves_the_poll_asks_for(recorded):
    """Guard-rail for the fixtures: with no boolean leaf recorded, every test
    below would pass reading `{"error": ...}` from an empty capture."""
    defs, reads, expected = recorded
    values = _capture_values(defs, reads)
    assert values, "a captura nao decodificou nenhuma folha"
    for container, children in expected.items():
        for child, stval in children.items():
            assert values[f"{container}${child}$stVal"] == stval


class FakeClient:
    """Answers `read_refs` from the capture; records what was asked for.

    An item the capture does not bring comes back as `{"error": ...}` IN PLACE
    of the value, which is how a relay answers a name it does not serve:
    `read_refs` only raises when the whole service fails.
    """

    def __init__(self, defs, reads):
        self.values = _capture_values(defs, reads)
        self.asked = []                 # um item por lote pedido
        self.raise_on_read = None
        self.unreadable = ()

    def read_refs(self, refs):
        refs = list(refs)
        self.asked.append(refs)
        if self.raise_on_read is not None:
            raise self.raise_on_read
        return [{"error": "object-non-existent"}
                if item in self.unreadable or item not in self.values
                else self.values[item] for _, item in refs]

    @property
    def reads_made(self):
        return len(self.asked)

    def close(self):
        pass


def _plt_transport(defs, reads, n=4):
    from pacct.web.glv.mms_map import MmsMap, MmsPoint

    points = {f"PLT{i:02d}": MmsPoint(bit=f"PLT{i:02d}", ld="LD",
                                      container="PLT1GGIO1$ST",
                                      child=f"Ind{i:02d}",
                                      item=f"PLT1GGIO1$ST$Ind{i:02d}$stVal",
                                      leaf=("stVal",))
              for i in range(1, n + 1)}
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t._client = FakeClient(defs, reads)
    t._map = MmsMap(points=points, source="scd")
    t._plan = _published_plan(t._map)  # o que `prepare_bits` publicaria
    return t


def _published_plan(mms_map):
    return tuple(sorted(mms_map.points.values(), key=lambda p: (p.ld, p.item)))


def _run_until_read(t, state, interval=0.01):
    stop = threading.Event()
    th = threading.Thread(target=t.poll, args=(state, interval, stop, None),
                          daemon=True)
    th.start()
    for _ in range(200):
        if state.snapshot()["ts"]:
            break
        threading.Event().wait(0.01)
    stop.set()
    th.join(timeout=3.0)
    assert not th.is_alive(), "a thread de polling ignorou o stop"
    return stop


def test_poll_writes_bits_into_live_state_and_honours_stop(recorded):
    defs, reads, expected = recorded
    t = _plt_transport(defs, reads)
    state = LiveState()
    _run_until_read(t, state)

    snap = state.snapshot()["digitals"]
    assert snap["PLT01"] == int(bool(expected["PLT1GGIO1$ST"]["Ind01"]))
    assert set(snap) == {"PLT01", "PLT02", "PLT03", "PLT04"}
    # Every mapped bit against its own recorded value, and not just PLT01:
    # PLT01 is True in the recording, so a poll that wrote the whole child
    # struct instead of its boolean leaf would pass the line above -- and
    # every other assertion in this file -- while turning EVERY bit into 1.
    for i in range(1, 5):
        bit, child = f"PLT{i:02d}", f"Ind{i:02d}"
        assert snap[bit] == int(bool(expected["PLT1GGIO1$ST"][child])), bit
    # one BATCH per turn, naming the four bits -- not one request per bit
    # and not one per container: that is the whole point of `read_refs`.
    assert t._client.reads_made >= 1
    assert [item for _, item in t._client.asked[0]] == [
        f"PLT1GGIO1$ST$Ind{i:02d}$stVal" for i in range(1, 5)]


def test_poll_asks_only_for_the_bits_the_open_page_wants(recorded):
    """`wanted_bits` narrows the plan; empty means the whole map.

    Same rule as `poll_loop_tar`. The filter is now per BIT and not per
    container: with the `LN$FC` read, asking for one bit dragged in the other
    30 of its container, and none of them was on the screen.
    """
    defs, reads, _ = recorded
    from pacct.web.glv.mms_map import MmsPoint

    t = _plt_transport(defs, reads)
    t._map.points["IN101"] = MmsPoint(bit="IN101", ld="LD",
                                      container="IN1XGGIO1$ST", child="Ind01",
                                      item="IN1XGGIO1$ST$Ind01$stVal",
                                      leaf=("stVal",))
    t._plan = _published_plan(t._map)

    state = LiveState()
    state.set_wanted_bits({"PLT01", "PLT02"})
    _run_until_read(t, state)

    snap = state.snapshot()["digitals"]
    assert "IN101" not in snap, "leu um bit que a pagina aberta nao pediu"
    assert set(snap) == {"PLT01", "PLT02"}
    # and it was not only the payload that got smaller: the request too
    assert [item for _, item in t._client.asked[0]] == [
        "PLT1GGIO1$ST$Ind01$stVal", "PLT1GGIO1$ST$Ind02$stVal"]


def test_poll_says_so_when_the_relay_refuses_every_point(recorded):
    """A partial read must not look like "the relay does not know".

    Same rule as `poll_loop_tar`: a bit that did not arrive disappears from
    the payload and the diagram paints it indeterminate, which when
    commissioning is another thing.
    """
    defs, reads, _ = recorded
    t = _plt_transport(defs, reads)
    t._client.unreadable = tuple(p.item for p in t._plan)

    state = LiveState()
    state.digitals = {"PLT01": 1}       # the previous reading, now stale
    _run_until_read(t, state)
    snap = state.snapshot()
    assert snap["digitals"] == {}, "manteve na tela um valor que não foi lido"
    assert "leitura parcial: 0/4" in snap["error"]


def _polarised(defs, reads):
    """A recorded container with one TRUE boolean leaf and one FALSE.

    Swept from the capture instead of hardcoded: a recapture keeps the names
    and changes all the values. The two polarities are exactly what the tests
    below need -- a read that returned the same thing for everything (the
    `{"error": ...}` becoming 1, for example) gives 1 on every bit, and only a
    bit whose true value is 0 separates that from a correct read.
    """
    values = _capture_values(defs, reads)
    for container in defs:
        prefix = container + "$"
        on = [k for k, v in values.items()
              if k.startswith(prefix) and k.endswith("$stVal") and v is True]
        off = [k for k, v in values.items()
               if k.startswith(prefix) and k.endswith("$stVal") and v is False]
        if on and off:
            return (container,
                    on[0].split("$")[-2], off[0].split("$")[-2])
    raise AssertionError(
        "nenhum container gravado tem uma folha booleana verdadeira E uma "
        "falsa; sem as duas polaridades estes testes nao distinguem uma "
        "leitura correta de 'tudo 1'. Recapture com "
        "tools/capture_mms_fixtures.py contra um rele com bits nos dois "
        "estados.")


def _renamed_leaf(values, old, new):
    """The same table with the boolean leaf called `new` instead of `old`.

    It is the shape of an ACD/ACT point -- whose boolean is `general` and not
    `stVal` -- over values the relay really sent. 43 of the 222 addressable
    bits of the tracked LT2_UPC1 have that shape, `TRIP` among them.
    """
    return {(k[:-len(old)] + new if k.endswith("$" + old) else k): v
            for k, v in values.items()}


def _transport_with(points, defs, reads, leaf_as=None):
    from pacct.web.glv.mms_map import MmsMap

    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t._client = FakeClient(defs, reads)
    if leaf_as is not None:
        t._client.values = _renamed_leaf(t._client.values, "stVal", leaf_as)
    t._map = MmsMap(points=points, source="scd")
    t._plan = _published_plan(t._map)
    return t


def _point(bit, container, child, leaf):
    from pacct.web.glv.mms_map import MmsPoint

    return MmsPoint(bit=bit, ld="LD", container=container, child=child,
                    item=f"{container}${child}${leaf[0] if leaf else ''}",
                    leaf=leaf)


def test_poll_keeps_each_value_with_the_bit_that_asked_for_it(recorded):
    """The `zip` between the points and what `read_refs` returned.

    It is `read_refs`'s contract: one answer per pair asked for, in the order
    they were asked. If it slips -- or if someone filters the list of values
    and not the list of points -- the bits swap values with each other,
    silently. Hence the two polarities: a slip in a container that is almost
    all on returns 1 for everything and would go unnoticed.
    """
    defs, reads, _ = recorded
    container, on, off = _polarised(defs, reads)
    points = {"BIT_ON": _point("BIT_ON", container, on, ("stVal",)),
              "BIT_OFF": _point("BIT_OFF", container, off, ("stVal",))}

    state = LiveState()
    _run_until_read(_transport_with(points, defs, reads), state)
    assert state.snapshot()["digitals"] == {"BIT_ON": 1, "BIT_OFF": 0}


def test_poll_never_paints_a_failed_access_as_a_bit_on(recorded):
    """The risk the batch brings: `{"error": ...}` IN PLACE of the value.

    A name the relay does not serve does not raise -- a dict comes in its
    position. `int(bool({...}))` is 1: a bit ON that nobody read, on the
    screen of the person doing the commissioning. It has to disappear from the
    payload, which is how the diagram paints it indeterminate.
    """
    defs, reads, _ = recorded
    container, on, off = _polarised(defs, reads)
    points = {"BIT_ON": _point("BIT_ON", container, on, ("stVal",)),
              "RECUSADO": _point("RECUSADO", container, off, ("stVal",))}
    t = _transport_with(points, defs, reads)
    t._client.unreadable = (points["RECUSADO"].item,)

    state = LiveState()
    _run_until_read(t, state)
    snap = state.snapshot()
    assert snap["digitals"] == {"BIT_ON": 1}
    assert "RECUSADO" not in snap["digitals"]
    assert "leitura parcial: 1/2" in snap["error"]


def test_poll_reads_a_general_leaf_and_leaves_the_wrong_leaf_indeterminate(
        recorded):
    """A bit whose boolean is `general` is read, and a point asking for a leaf
    the relay does not serve stays OUT of the payload.

    Both halves matter: the first is the 43-of-222 case the per-leaf read
    exists to cover, and the second is what keeps "I could not read it"
    different from "the relay says zero" -- an absent bit is painted
    indeterminate, never 0.
    """
    defs, reads, _ = recorded
    container, on, off = _polarised(defs, reads)
    points = {"TRIP": _point("TRIP", container, on, ("general",)),
              "TRIP_OFF": _point("TRIP_OFF", container, off, ("general",)),
              "PEDE_STVAL": _point("PEDE_STVAL", container, on, ("stVal",))}

    state = LiveState()
    _run_until_read(_transport_with(points, defs, reads, leaf_as="general"),
                    state)
    snap = state.snapshot()
    assert snap["digitals"] == {"TRIP": 1, "TRIP_OFF": 0}
    assert "leitura parcial: 2/3" in snap["error"]


def test_poll_reports_a_relay_error_and_stops(recorded):
    defs, reads, _ = recorded
    t = _plt_transport(defs, reads)
    t._client.raise_on_read = MmsError("object-non-existent")

    state = LiveState()
    stop = threading.Event()
    th = threading.Thread(target=t.poll, args=(state, 0.01, stop, None),
                          daemon=True)
    th.start()
    th.join(timeout=3.0)
    assert not th.is_alive(), "o loop continuou depois de um erro do rele"
    assert "object-non-existent" in state.snapshot()["error"]


def test_effective_period_has_no_floor_but_never_beats_the_cycle():
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    # No floor: what is asked for is what counts, 0 included.
    assert t.effective_interval(0.005, last_cycle=0.001) == pytest.approx(0.005)
    assert t.effective_interval(0.0, last_cycle=0.0) == 0.0
    # a page that costs more than the period: the loop runs flat out
    assert t.effective_interval(0.100, last_cycle=0.250) == pytest.approx(0.250)
    assert t.effective_interval(0.0, last_cycle=0.250) == pytest.approx(0.250)
    # A negative period never becomes a negative sleep.
    assert t.effective_interval(-1.0, last_cycle=0.0) == 0.0


def test_period_zero_never_puts_two_reads_on_the_link_at_once(recorded):
    """The guard that replaced the floor: asking 0 ms does not flood the link.

    The loop is synchronous and reads ONCE per turn, so the next request only
    leaves after the previous answer -- at most one read in flight, and at
    most one per RTT, however fast it is asked for.
    """
    defs, reads, _ = recorded
    t = _plt_transport(defs, reads)
    client = t._client
    rtt = 0.02                       # this pretend relay's "RTT"
    answer = client.read_refs
    lock = threading.Lock()
    inflight = [0]
    overlapped = []

    def slow(refs):
        with lock:
            inflight[0] += 1
            if inflight[0] > 1:
                overlapped.append(inflight[0])
        try:
            time.sleep(rtt)
            return answer(refs)
        finally:
            with lock:
                inflight[0] -= 1

    client.read_refs = slow
    state = LiveState()
    stop = threading.Event()
    th = threading.Thread(target=t.poll, args=(state, 0.0, stop, None),
                          daemon=True)
    t0 = time.time()
    th.start()
    stop.wait(0.30)
    stop.set()
    th.join(timeout=3.0)
    elapsed = time.time() - t0

    assert not th.is_alive(), "a thread de polling ignorou o stop"
    assert not overlapped, "duas leituras em voo no mesmo link"
    assert client.reads_made >= 2, "o laco nem chegou a repetir a leitura"
    # +1 for the turn that was already on its way when the clock started.
    assert client.reads_made <= elapsed / rtt + 1, (
        f"{client.reads_made} leituras em {elapsed:.3f}s -- mais de uma por RTT")


def test_a_cycle_that_reads_nothing_does_not_spin(recorded):
    """The open page has no mapped bit: there is no relay answer to wait for,
    so period 0 would be a hot loop burning CPU without touching the network.
    It is the only minimum period left (`IDLE_INTERVAL`)."""
    defs, reads, _ = recorded
    t = _plt_transport(defs, reads)
    state = LiveState()
    with state.lock:
        state.wanted_bits = {"NAO_ESTA_NO_MAPA"}
    stop = threading.Event()
    th = threading.Thread(target=t.poll, args=(state, 0.0, stop, None),
                          daemon=True)
    th.start()
    stop.wait(0.30)
    stop.set()
    th.join(timeout=3.0)

    assert not th.is_alive(), "a thread de polling ignorou o stop"
    rounds = t._client.reads_made
    assert rounds <= 0.30 / IDLE_INTERVAL + 2, (
        f"{rounds} voltas em 0,3 s sem ler nada -- laco quente")


# ---- connect / prepare_bits ------------------------------------------------

class FakeMmsClient:
    """Enough of `py61850.MmsClient` to drive connect/prepare_bits offline."""

    def __init__(self, host, port=102, timeout=10, *, lds, directory, defs,
                 reads=None,
                 sw_rev="FID=SEL-451-5-R331-V1-Z033014-D20250919"):
        self.host, self.port = host, port
        self.lds, self.directory, self.defs = lds, directory, defs
        self.reads = dict(reads or {})
        self.sw_rev = sw_rev
        self.connected = False
        self.closed = False
        self.values = _capture_values(defs, self.reads) if self.reads else {}
        # Everything the transport ASKED the relay, in order. It is what
        # proves `prepare_bits` stopped talking to it.
        self.calls = []

    def connect(self):
        self.connected = True

    def close(self):
        self.closed = True

    def get_server_directory(self):
        return list(self.lds)

    def get_logical_device_directory(self, ld):
        self.calls.append(("directory", ld))
        return list(self.directory.get(ld, ()))

    def read_value(self, ld, item):
        self.calls.append(("read_value", item))
        if item.endswith("swRev"):
            return self.sw_rev
        return None

    def read_refs(self, refs):
        refs = list(refs)
        self.calls.append(("read_refs", len(refs)))
        return [self.values.get(item, {"error": "object-non-existent"})
                for _, item in refs]


@pytest.fixture
def fake_relay(monkeypatch, ann_directory, recorded):
    defs, reads, _ = recorded
    lds = ["QPC1_TFE_UPC1ANN", "QPC1_TFE_UPC1CFG", "QPC1_TFE_UPC1CON"]
    directory = {lds[0]: ann_directory, lds[1]: [], lds[2]: []}
    made = []

    def factory(host, port=102, timeout=10):
        c = FakeMmsClient(host, port, timeout, lds=lds, directory=directory,
                          defs=defs, reads=reads)
        made.append(c)
        return c

    monkeypatch.setattr("pacct.web.glv.transport.mms.MmsClient", factory)
    return made


def test_connect_strips_the_fid_prefix_and_names_the_device(fake_relay):
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t.connect()
    assert t.fid == "SEL-451-5-R331-V1-Z033014-D20250919"
    assert not t.fid.startswith("FID=")
    assert t.devid == "QPC1_TFE_UPC1"
    assert fake_relay[0].connected


class FakeJob:
    """The shape `SessionHandler.job()` hands a handler."""

    def __init__(self):
        self.stages = []

    def stage(self, text, pct=None):
        self.stages.append((text, pct))

    def fraction(self, text, done, total):
        self.stages.append((text, 100.0 * done / total))


def test_connect_reports_its_stages_through_the_job(fake_relay):
    """The job is not decoration: `fraction(text, done, total)` is not
    `fraction(pct)`, and calling it wrong only shows up at connect time on a
    real relay."""
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    job = FakeJob()
    t.connect(job=job)
    assert job.stages
    assert all(p is None or 0.0 <= p <= 100.0 for _, p in job.stages)


def test_prepare_bits_maps_the_wanted_bits_through_the_shipped_table(fake_relay):
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t.connect()
    n = t.prepare_bits(["PLT01", "PLT02"])
    assert n == 2
    assert set(t._map.points) == {"PLT01", "PLT02"}
    p = t._map.points["PLT01"]
    assert p.ld == "QPC1_TFE_UPC1ANN"
    assert p.container == "PLT1GGIO1$ST"
    cov = t.coverage_for({"PLT01", "PLT02"})
    assert cov == {"mapped": 2, "total": 2, "source": "tabela"}


def test_prepare_bits_refuses_a_diagram_it_cannot_read(fake_relay):
    """Zero coverage fails loudly. A live diagram with nothing on it is worse
    than a clear refusal."""
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t.connect()
    with pytest.raises(MmsSetupError) as e:
        t.prepare_bits(["NAO_EXISTE_1", "NAO_EXISTE_2"])
    assert "nenhum" in str(e.value).lower()
    # not a protocol error: py61850 answered everything. Saying otherwise
    # sends the user hunting the network when the problem is a project file.
    assert not isinstance(e.value, Iec61850Error)


def test_prepare_bits_never_stops_the_reader_because_it_never_asks_the_relay(
        fake_relay):
    """`pause` exists because of the `Transport` contract and is not used here.

    py61850's client is a socket and an invoke counter, so two threads on it
    scramble the answers -- which is why a second diagram used to stop the
    reader to fetch the structure of the new containers. With the per-leaf
    read there is no structure to fetch: the map comes from the directory
    already read at connect, and the reader never even learns that another
    diagram came in.
    """
    import contextlib

    entered = []

    @contextlib.contextmanager
    def pause():
        entered.append(1)
        yield

    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t.connect()
    talked = len(t._client.calls)
    assert t.prepare_bits(["PLT01"], pause=pause) == 1
    assert entered == [], "parou o leitor sem ter o que perguntar ao rele"
    assert len(t._client.calls) == talked, "falou com o rele no prepare_bits"
    # already resolved: it goes on with no pause and no request
    assert t.prepare_bits(["PLT01"], pause=pause) == 0
    assert entered == []
    assert len(t._client.calls) == talked


def _transport_over(monkeypatch, lds, directory, defs):
    def factory(host, port=102, timeout=10):
        return FakeMmsClient(host, port, timeout, lds=lds, directory=directory,
                             defs=defs)

    monkeypatch.setattr("pacct.web.glv.transport.mms.MmsClient", factory)
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t.connect()
    return t


def test_prepare_bits_resolves_the_only_logical_device_by_its_suffix(
        monkeypatch, ann_directory, recorded):
    """`ld_suffixes` must be given the suffixes the map sources name.

    Without them it falls back to a common-prefix split, and with a single LD
    there is no second name to compare, so it returns the identity -- the
    table's `ANN` then matches nothing and the whole diagram is unaddressable.
    """
    defs, _, _ = recorded
    t = _transport_over(monkeypatch, ["QPC1_TFE_UPC1ANN"],
                        {"QPC1_TFE_UPC1ANN": ann_directory}, defs)
    t.prepare_bits(["PLT01"])
    assert t._ld_by_suffix.get("ANN") == "QPC1_TFE_UPC1ANN"
    assert t._map.points["PLT01"].ld == "QPC1_TFE_UPC1ANN"


def test_prepare_bits_disambiguates_logical_devices_that_share_a_prefix(
        monkeypatch, recorded):
    """Two LDs that share more than the IED name (`ABCCFG` / `ABCCON`).

    The common prefix eats the `C`, so the fallback splits them as `FG` / `ON`
    and the table's `CON` group resolves to no device at all.
    """
    from sellib.scl import mms_tables
    from sellib.scl.mms_tables import is_boolean_status

    defs, _, _ = recorded
    table = mms_tables.lookup("451")
    con_items = sorted({item for suf, item in table.bits.values()
                        if suf == "CON"})
    t = _transport_over(monkeypatch, ["ABCCFG", "ABCCON"],
                        {"ABCCON": con_items, "ABCCFG": []}, defs)
    # A boolean status point: the CON logical device is mostly controls, and
    # a control is dropped from the map on purpose (it is a command, not a
    # reading), so picking just any CON bit would resolve to nothing.
    bit = next(b for b, (suf, item) in table.bits.items()
               if suf == "CON" and is_boolean_status(item.split("$")[3:]))
    t.prepare_bits([bit])
    assert t._ld_by_suffix.get("CON") == "ABCCON"
    assert t._map.points[bit].ld == "ABCCON"


def test_close_drops_the_client(fake_relay):
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t.connect()
    client = fake_relay[0]
    t.close()
    assert client.closed and t._client is None
    assert t.coverage_for({"PLT01"}) is None


# ---- what makes a diagram live-but-blank -----------------------------------

def test_the_map_only_carries_names_the_relay_itself_listed(fake_relay):
    """The refusal that went away, and what took its place.

    While the polling read containers, `prepare_bits` asked for the STRUCTURE
    of each `LN$FC` and had to refuse the diagram when the firmware described
    none -- otherwise the diagram came up LIVE with everything indeterminate
    and no error on the screen. Reading per leaf, neither that request nor
    that failure mode exists: what enters the map is only what the relay's own
    directory named (see `resolve_map`), and what is left is the refusal for
    zero coverage.
    """
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t.connect()
    t.prepare_bits(["PLT01", "PLT02"])
    servidos = set(t._client.directory["QPC1_TFE_UPC1ANN"])
    assert {p.item for p in t._plan} <= servidos
    assert t._plan, "o plano publicado nao pode ficar vazio depois de mapear"


def test_the_refusal_is_judged_on_the_bits_this_diagram_asked_for(fake_relay):
    """`_wanted` never shrinks, so judging the union lets a second diagram
    that is 100% unaddressable ride in on the first one's coverage."""
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t.connect()
    t.prepare_bits(["PLT01"])                   # diagrama A: mapeia
    with pytest.raises(MmsSetupError):
        t.prepare_bits(["NAO_EXISTE_1", "NAO_EXISTE_2"])   # diagrama B: nada
    # what diagram A asked for stays mapped -- B's refusal does not undo A
    assert t.coverage_for({"PLT01"})["mapped"] == 1
    assert t.coverage_for({"NAO_EXISTE_1"})["mapped"] == 0


def test_a_second_diagram_reaches_the_next_cycle_without_a_reconnect(fake_relay):
    """Nothing pauses and the polling thread never restarts, so it has to see
    the NEW plan: if the plan is captured before the loop, B's bits stay
    indeterminate until somebody reconnects."""
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t.connect()
    t.prepare_bits(["PLT01"])

    state = LiveState()
    stop = threading.Event()
    th = threading.Thread(target=t.poll, args=(state, 0.01, stop, None),
                          daemon=True)
    th.start()
    try:
        for _ in range(200):
            if "PLT01" in state.snapshot()["digitals"]:
                break
            threading.Event().wait(0.01)
        assert "PLT01" in state.snapshot()["digitals"]

        before = len(t._client.calls)
        assert t.prepare_bits(["PLT02"]) == 1
        # nothing to ask the relay: the new plan comes from the directory
        # already read
        assert len(t._client.calls) == before

        for _ in range(200):
            if "PLT02" in state.snapshot()["digitals"]:
                break
            threading.Event().wait(0.01)
    finally:
        stop.set()
        th.join(timeout=3.0)
    assert "PLT02" in state.snapshot()["digitals"], \
        "a thread seguiu lendo o plano da primeira chamada"


def test_the_watchdog_deadline_ends_at_the_association(fake_relay):
    """`RelayLink._connect_with_watchdog` reads `setup_done` off the transport.

    Without it the 60 s deadline covers the directory sweep too, and tripping
    there tells the user the relay did not answer -- pointing at the network
    when the cost is the size of the directory.
    """
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    assert not t.setup_done.is_set()
    t.connect()
    assert t.setup_done.is_set()


def test_setup_done_stays_clear_when_the_association_fails(monkeypatch):
    def factory(host, port=102, timeout=10):
        raise MmsError("association failed, MMS tag 0xa3")

    monkeypatch.setattr("pacct.web.glv.transport.mms.MmsClient", factory)
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    with pytest.raises(MmsError):
        t.connect()
    assert not t.setup_done.is_set()


# ---- which IED of the project SCD is this relay ----------------------------
#
# The name comes from the RDB (`GlvDiagram.relay_name`) and the SCD is another
# tool's file: they are not obliged to spell the relay the same way. Returning
# `{}` on a name miss made the two heuristics below dead code on the live
# path, and one name mismatch silently degraded the branch's headline
# decision -- "project SCD first; the SCD is the as-built truth" -- to the
# factory table, with only a log line.

def _t(ied_name=None, devid=""):
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None,
                     ied_name=ied_name)
    t.devid = devid
    return t


def test_an_exact_ied_name_still_wins():
    t = _t("QPC1_LT2_UPC1")
    by_ied = {"QPC1_LT2_UPC1": {"PLT01": 1}, "OUTRO": {"PLT99": 1}}
    assert t._points_for_ied(by_ied) == {"PLT01": 1}


def test_an_ied_name_that_misses_falls_through_to_the_single_ied_scd():
    t = _t("NOME_DO_RDB")
    by_ied = {"NOME_DO_SCD": {"PLT01": 1}}
    assert t._points_for_ied(by_ied) == {"PLT01": 1}
    assert t.ied_name == "NOME_DO_SCD"


def test_an_ied_name_that_misses_falls_through_to_the_devid_match():
    """The DEVID is the common prefix of the relay's own LD names, so it is
    the relay speaking for itself -- a better witness than the RDB's label."""
    t = _t("NOME_DO_RDB", devid="QPC1_LT2_UPC1")
    by_ied = {"QPC1_LT2_UPC1": {"PLT01": 1}, "QPC1_TR1_UPC1": {"PLT99": 1}}
    assert t._points_for_ied(by_ied) == {"PLT01": 1}
    assert t.ied_name == "QPC1_LT2_UPC1"


def test_an_ied_that_is_in_the_scd_but_carries_nothing_is_a_verdict():
    """Present-and-empty is not a miss. Falling through there would hand the
    diagram the NEIGHBOURING relay's map, which is worse than no map."""
    t = _t("QPC1_LT2_UPC1")
    by_ied = {"QPC1_LT2_UPC1": {}, "QPC1_TR1_UPC1": {"PLT99": 1}}
    assert t._points_for_ied(by_ied) == {}


def test_the_devid_match_is_a_prefix_and_not_a_substring():
    """`name in devid` would let an IED called `TR1` claim the DEVID
    `QPC1_TR1_UPC1` -- and equally `QPC2_TR1_UPC1`, the relay next to it.
    While this heuristic was unreachable that never showed; now that the RDB
    name is only a hint, it runs."""
    t = _t("NOME_DO_RDB", devid="QPC1_TR1_UPC1")
    by_ied = {"TR1": {"X": 1}, "QPC1_TR1_UPC1": {"Y": 1}}
    assert t._points_for_ied(by_ied) == {"Y": 1}


def test_nothing_matches_and_nothing_is_borrowed():
    t = _t("NOME_DO_RDB", devid="NADA_A_VER")
    by_ied = {"QPC1_LT2_UPC1": {"X": 1}, "QPC1_TR1_UPC1": {"Y": 1}}
    assert t._points_for_ied(by_ied) == {}


def test_no_table_and_no_scd_refuses_by_naming_the_model(fake_relay):
    """The spec asks for a refusal "naming the model". Falling through to the
    zero-coverage refusal instead says "no bit has an MMS address ON THIS
    RELAY", which sends the user hunting the network for a problem that is a
    missing file."""
    class Model:
        model = "SEL-9999"

    t = MmsTransport("192.0.2.10", 102, relay_model=Model(), logger=None)
    t.connect()
    with pytest.raises(MmsSetupError) as e:
        t.prepare_bits(["PLT01"])
    assert "SEL-9999" in str(e.value)
    assert "SCD" in str(e.value)


def test_without_a_model_the_refusal_falls_back_to_the_fid(fake_relay):
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t.connect()
    t.fid = "SEL-9999-1-R100-V0-Z000000-D20250101"
    # no table for this part and no SCD
    with pytest.raises(MmsSetupError) as e:
        t.prepare_bits(["PLT01"])
    assert t.fid in str(e.value)


def test_the_refusal_says_WHICH_of_the_two_files_is_missing(fake_relay, tmp_path):
    """Two different faults, two different places to go.

    The message used to say "nenhum SCD do projeto foi associado a este IED"
    in both cases -- including to a user who had just associated one. What is
    missing then is not the SCD but an `sAddr` for this IED inside it, and
    being told to go and attach the file that is already attached sends them
    looking for the wrong thing.
    """
    class Model:
        model = "SEL-9999"                 # sem tabela de fabrica

    # (a) no SCD chosen
    t = MmsTransport("192.0.2.10", 102, relay_model=Model(), logger=None)
    t.connect()
    with pytest.raises(MmsSetupError) as e:
        t.prepare_bits(["PLT01"])
    sem_scd = str(e.value)
    assert "nenhum SCD do projeto foi associado" in sem_scd
    assert "Informe o SCD" in sem_scd

    # (b) a chosen SCD that says nothing about this IED
    scd = tmp_path / "subestacao.scd"
    scd.write_text("<SCL></SCL>", encoding="utf-8")
    t2 = MmsTransport("192.0.2.10", 102, relay_model=Model(), logger=None,
                      scd_path=scd, ied_name="QPC1_TFE_UPC1")
    t2.connect()
    with pytest.raises(MmsSetupError) as e:
        t2.prepare_bits(["PLT01"])
    com_scd = str(e.value)
    assert "subestacao.scd" in com_scd, "não diz QUAL SCD ele leu"
    assert "sAddr" in com_scd
    assert "nenhum SCD do projeto foi associado" not in com_scd, \
        "continua dizendo que não há SCD para quem acabou de escolher um"
    assert "SEL-9999" in com_scd          # a recusa ainda nomeia o modelo


# ---- what is missing from the IED's server model ---------------------------
#
# A bit with no point in the map will never be read, and the screen only said
# "indeterminate" -- the same thing it says for a bit that has not arrived
# yet. The one that can say the difference is "add it to the IED model" is the
# transport, which is the one that has the map.

def test_unreachable_names_the_bits_the_relay_does_not_serve(fake_relay):
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t.connect()
    t.prepare_bits(["PLT01", "PLT02"])
    out = t.unreachable({"PLT01", "PLT02", "NAO_EXISTE"})
    assert out["names"] == ["NAO_EXISTE"]
    assert out["reason"] == "mms"


def test_unreachable_cannot_tell_before_there_is_a_map():
    """With no map, `[]` would say "the IED serves everything" -- the
    opposite of what is known."""
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    assert t.unreachable({"PLT01"}) is None


# -- decorated points: one item that carries two bits -----------------------
#
# `db:52A|52B?0:1:2:3` on a `Pos$stVal`: the Dbpos encodes the breaker's two
# auxiliary contacts. py61850 returns a BIT-STRING as the STRING "10", and
# `bool("00")` is True -- reading that with `int(bool(...))` paints EVERY
# breaker as closed, always.

def _dbpos_transport(value, alternatives=(0, 1, 2, 3), nbits=2):
    """Two bits (`52A`, `52B`) on the same item, which answers `value`."""
    from sellib.scl.mms_tables import BitRule

    from pacct.web.glv.mms_map import MmsMap, MmsPoint

    item = "BKR1CSWI1$ST$Pos$stVal"
    points = {
        name: MmsPoint(bit=name, ld="LD", container="BKR1CSWI1$ST",
                       child="Pos", item=item, leaf=("stVal",),
                       rule=BitRule(alternatives=alternatives, index=i,
                                    nbits=nbits))
        for i, name in enumerate(("52A", "52B")[:nbits])
    }

    class OnePointClient:
        def __init__(self):
            self.asked = []

        def read_refs(self, refs):
            refs = list(refs)
            self.asked.append(refs)
            return [value for _ in refs]

        def close(self):
            pass

    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t._client = OnePointClient()
    t._map = MmsMap(points=points, source="scd")
    t._plan = _published_plan(t._map)
    return t


def test_poll_splits_a_dbpos_into_the_two_auxiliary_contacts():
    """Dbpos 2 (`"10"`) e' o disjuntor FECHADO: 52A=1, 52B=0."""
    t = _dbpos_transport("10")
    state = LiveState()
    _run_until_read(t, state)
    assert state.snapshot()["digitals"] == {"52A": 1, "52B": 0}


def test_poll_reads_the_open_breaker_as_the_other_way_round():
    """Dbpos 1 (`"01"`), open. Without the rule both would come out 1, because
    `bool("01")` and `bool("10")` are both True."""
    t = _dbpos_transport("01")
    state = LiveState()
    _run_until_read(t, state)
    assert state.snapshot()["digitals"] == {"52A": 0, "52B": 1}


def test_poll_never_paints_a_dbpos_as_a_bit_on_by_truthiness():
    """Dbpos 0 (`"00"`, intermediate): both contacts open. This is the case
    `int(bool(value))` would get right by accident on 52B and wrong on 52A --
    it is the proof that the reading goes through the rule and not through
    Python's truthiness."""
    t = _dbpos_transport("00")
    state = LiveState()
    _run_until_read(t, state)
    assert state.snapshot()["digitals"] == {"52A": 0, "52B": 0}


def test_poll_leaves_a_value_outside_the_alternatives_indeterminate():
    """A Dbpos 3 (bad-state) against a `?1:2` point is neither 0 nor 1: the
    bit disappears from the payload and the drawing paints it indeterminate,
    like a failed access. And `state.error` has to say the read was partial."""
    t = _dbpos_transport("11", alternatives=(1, 2), nbits=1)
    state = LiveState()
    _run_until_read(t, state)
    snap = state.snapshot()
    assert snap["digitals"] == {}
    assert "parcial" in snap["error"]


def test_poll_asks_for_a_shared_item_once_and_not_once_per_bit():
    """Both bits come out of the SAME `LN$FC$DO$DA`. Asking for the name twice
    in the batch spends TPDU bandwidth and brings nothing: the plan is of
    LEAVES, and the leaf is only one."""
    t = _dbpos_transport("10")
    state = LiveState()
    _run_until_read(t, state)
    assert [item for _, item in t._client.asked[0]] == \
        ["BKR1CSWI1$ST$Pos$stVal"]


def test_a_plain_boolean_point_is_untouched_by_the_rule_path(recorded):
    """The rule is optional and the boolean path did not change: `rule is
    None` still reads `int(bool(value))`."""
    defs, reads, expected = recorded
    t = _plt_transport(defs, reads)
    assert all(p.rule is None for p in t._plan)
    state = LiveState()
    _run_until_read(t, state)
    assert state.snapshot()["digitals"]["PLT01"] == \
        int(bool(expected["PLT1GGIO1$ST"]["Ind01"]))


# ---- a wall clock that jumps -----------------------------------------------

class _JumpyClock:
    """A `time` whose WALL clock jumps, like WSL's.

    Measured on the development machine: WSL's `time.time()` 82,5 s behind the
    Windows clock, and the GLV log with timestamps GOING BACKWARDS. Whoever
    measures a duration with that clock reads a negative cycle, and
    `sleep_for = period - cycle` becomes 82 s of sleep -- the "stuck reading"
    with a clean Wireshark. The monotonic one does not jump; it is the one the
    loop counts with.
    """

    def __init__(self, jump=-82.5, after=3):
        self._n = 0
        self._jump, self._after = jump, after
        self.monotonic = time.monotonic
        self.sleep = time.sleep

    def time(self):
        self._n += 1
        return 1_000_000.0 + (self._jump if self._n > self._after else 0.0)


def test_a_wall_clock_jump_does_not_stall_the_poll_loop(recorded, monkeypatch):
    defs, reads, _ = recorded
    t = _plt_transport(defs, reads)
    monkeypatch.setattr("pacct.web.glv.transport.mms.time", _JumpyClock())
    monkeypatch.setattr("pacct.web.glv.state.time", _JumpyClock())

    state = LiveState()
    stop = threading.Event()
    th = threading.Thread(target=t.poll, args=(state, 0.01, stop, None),
                          daemon=True)
    th.start()
    stop.wait(0.40)
    stop.set()
    th.join(timeout=3.0)

    assert not th.is_alive(), "a thread de polling ignorou o stop"
    # With the wall clock in the sum, the -82,5 s jump would become a
    # negative cycle and the loop would sleep 82 s: ONE read in 0,4 s.
    assert t._client.reads_made >= 10, (
        f"{t._client.reads_made} leituras em 0,4 s a 10 ms -- o salto do "
        f"relogio parou o laco")


def test_the_age_on_screen_comes_from_the_monotonic_clock(recorded):
    """The screen receives `age` ready-made, and not a timestamp to subtract
    from ITS own clock: the browser runs on Windows and the server on WSL."""
    state = LiveState()
    assert state.snapshot()["age"] is None      # nada lido ainda
    with state.lock:
        state.mark_updated()
    snap = state.snapshot()
    assert snap["age"] is not None and snap["age"] < 1.0
    # A wall clock at any epoch does not affect the age.
    state.last_update_ts = 0.0
    assert state.snapshot()["age"] < 1.0
    state.clear()
    assert state.snapshot()["age"] is None
