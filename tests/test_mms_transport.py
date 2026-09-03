"""O loop de polling MMS, movido a bytes gravados de um rele.

A leitura e' UMA Read nomeando todas as folhas da pagina (`read_refs` da
py61850). O que os testes aqui defendem e' o que essa mudanca poe em risco:
um acesso que falha volta `{"error": ...}` NO LUGAR do valor, e
`int(bool({...}))` e' 1 -- um bit ligado que ninguem leu, na tela de quem esta
comissionando.

Os fixtures em `tests/fixtures/mms/` sao uma captura REAL do SEL-451-5 R331 da
bancada (ver o `provenance` deles e `test_mms_fixtures_provenance.py`),
embrulhados em `{"provenance": ..., "<chave>": payload}`. Eles sao ANTERIORES
ao lote: guardam a resposta de uma Read de container inteiro mais a definicao
que descreve a ordem dos filhos. `_capture_values` anda essa definicao uma vez
e vira a tabela `item -> valor` que o `read_refs` de verdade devolve -- o
mesmo valor pelos dois caminhos, que e' o que a bancada mediu no 751 (1053 de
1055 bits identicos; os 2 restantes oscilam entre duas voltas do MESMO
caminho). Nada aqui fixa tamanho de fixture nem quais containers ele traz.
"""
from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path

import pytest

# `py61850` e' a unica dependencia do projeto que nao vem do PyPI (ver
# requirements.txt). Sem esta guarda o modulo inteiro deixa de ser COLETAVEL
# numa maquina sem ela, e a suite toda para -- inclusive as centenas de testes
# que nao tem nada com MMS.
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
    """A mesma caminhada posicional que o rele descreve na definicao."""
    structure = (node_type.get("structure")
                 if isinstance(node_type, dict) else None)
    if structure and isinstance(value, (list, tuple)):
        # strict=False on purpose: this walker mimics what py61850 hands
        # back, and a short value is one of the shapes under test.
        return {child["name"]: _walk(child.get("type"), v)
                for child, v in zip(structure, value, strict=False)}
    return value


def _capture_values(defs, reads):
    """`LN$FC$DO$folha -> valor`, tirado da captura por container.

    Este passo mora aqui, e nao no `transport/mms.py`, porque e' a CAPTURA que
    e' por container: o polling nao le mais container nenhum. Andar a
    definicao uma vez transforma os bytes gravados na tabela que o `read_refs`
    de verdade devolve, sem inventar valor nenhum.
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
    """Guarda-corpo dos fixtures: sem folha booleana gravada, todo teste
    abaixo passaria lendo `{"error": ...}` de um capture vazio."""
    defs, reads, expected = recorded
    values = _capture_values(defs, reads)
    assert values, "a captura nao decodificou nenhuma folha"
    for container, children in expected.items():
        for child, stval in children.items():
            assert values[f"{container}${child}$stVal"] == stval


class FakeClient:
    """Responde `read_refs` a partir da captura; guarda o que foi pedido.

    Um item que a captura nao traz volta `{"error": ...}` NO LUGAR do valor,
    que e' como um rele responde um nome que ele nao serve: `read_refs` so'
    levanta excecao quando o servico inteiro falha.
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
    # um LOTE por volta, nomeando os quatro bits -- nao uma requisicao por
    # bit e nem uma por container: e' o ponto todo do `read_refs`.
    assert t._client.reads_made >= 1
    assert [item for _, item in t._client.asked[0]] == [
        f"PLT1GGIO1$ST$Ind{i:02d}$stVal" for i in range(1, 5)]


def test_poll_asks_only_for_the_bits_the_open_page_wants(recorded):
    """`wanted_bits` estreita o plano; vazio quer dizer o mapa inteiro.

    Mesma regra do `poll_loop_tar`. O filtro agora e' por BIT e nao por
    container: com a leitura por `LN$FC`, pedir um bit arrastava junto os
    outros 30 do container dele, e nenhum deles estava na tela.
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
    # e nao foi so' o payload que ficou menor: o pedido tambem
    assert [item for _, item in t._client.asked[0]] == [
        "PLT1GGIO1$ST$Ind01$stVal", "PLT1GGIO1$ST$Ind02$stVal"]


def test_poll_says_so_when_the_relay_refuses_every_point(recorded):
    """Leitura parcial nao pode parecer "o rele nao sabe".

    Mesma regra do `poll_loop_tar`: um bit que nao veio some do payload e o
    diagrama o pinta indeterminado, que em comissionamento e' outra coisa.
    """
    defs, reads, _ = recorded
    t = _plt_transport(defs, reads)
    t._client.unreadable = tuple(p.item for p in t._plan)

    state = LiveState()
    state.digitals = {"PLT01": 1}       # a leitura anterior, que já não vale
    _run_until_read(t, state)
    snap = state.snapshot()
    assert snap["digitals"] == {}, "manteve na tela um valor que não foi lido"
    assert "leitura parcial: 0/4" in snap["error"]


def _polarised(defs, reads):
    """Um container gravado com uma folha booleana VERDADEIRA e uma FALSA.

    Varrido da captura em vez de fixado: uma recaptura mantem os nomes e troca
    todos os valores. As duas polaridades sao exatamente o que os testes
    abaixo precisam -- uma leitura que devolvesse a mesma coisa pra tudo (o
    `{"error": ...}` virando 1, por exemplo) da' 1 em todo bit, e so' um bit
    cujo valor verdadeiro e' 0 separa isso de uma leitura correta.
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
    """A mesma tabela com a folha booleana chamada `new` em vez de `old`.

    E' a forma de um ponto de ACD/ACT -- cujo booleano e' `general` e nao
    `stVal` -- sobre valores que o rele mandou de verdade. 43 dos 222 bits
    enderecaveis do LT2_UPC1 rastreado tem essa forma, o `TRIP` entre eles.
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
    """O `zip` entre os pontos e o que o `read_refs` devolveu.

    E' o contrato do `read_refs`: uma resposta por par pedido, na ordem em que
    foram pedidos. Se ele deslizar -- ou se alguem filtrar a lista de valores
    e nao a de pontos -- os bits trocam de valor entre si, calados. Por isso
    as duas polaridades: um deslize num container quase todo ligado devolve
    1 pra tudo e passaria despercebido.
    """
    defs, reads, _ = recorded
    container, on, off = _polarised(defs, reads)
    points = {"BIT_ON": _point("BIT_ON", container, on, ("stVal",)),
              "BIT_OFF": _point("BIT_OFF", container, off, ("stVal",))}

    state = LiveState()
    _run_until_read(_transport_with(points, defs, reads), state)
    assert state.snapshot()["digitals"] == {"BIT_ON": 1, "BIT_OFF": 0}


def test_poll_never_paints_a_failed_access_as_a_bit_on(recorded):
    """O risco que o lote traz: `{"error": ...}` no LUGAR do valor.

    Um nome que o rele nao serve nao levanta excecao -- vem um dict na
    posicao dele. `int(bool({...}))` e' 1: um bit LIGADO que ninguem leu, na
    tela de quem esta comissionando. Tem que sumir do payload, que e' como o
    diagrama pinta indeterminado.
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
    """Um bit cujo booleano e' `general` e' lido, e um ponto que pede uma folha
    que o rele nao serve fica FORA do payload.

    As duas metades importam: a primeira e' o caso 43-de-222 que a leitura por
    folha existe pra cobrir, e a segunda e' o que mantem "nao consegui ler"
    diferente de "o rele diz zero" -- um bit ausente e' pintado indeterminado,
    nunca 0.
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
    # Sem piso: o que se pede e' o que vale, inclusive 0.
    assert t.effective_interval(0.005, last_cycle=0.001) == pytest.approx(0.005)
    assert t.effective_interval(0.0, last_cycle=0.0) == 0.0
    # a page that costs more than the period: the loop runs flat out
    assert t.effective_interval(0.100, last_cycle=0.250) == pytest.approx(0.250)
    assert t.effective_interval(0.0, last_cycle=0.250) == pytest.approx(0.250)
    # Um periodo negativo nunca vira um sleep negativo.
    assert t.effective_interval(-1.0, last_cycle=0.0) == 0.0


def test_period_zero_never_puts_two_reads_on_the_link_at_once(recorded):
    """A guarda que substituiu o piso: pedir 0 ms nao afoga a comunicacao.

    O laco e' sincrono e le UMA vez por volta, entao a proxima requisicao so'
    parte depois da resposta anterior -- no maximo uma leitura em voo, e no
    maximo uma por RTT, por mais rapido que se peca.
    """
    defs, reads, _ = recorded
    t = _plt_transport(defs, reads)
    client = t._client
    rtt = 0.02                       # o "RTT" deste rele de mentira
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
    # +1 pela volta que ja estava a caminho quando o cronometro comecou.
    assert client.reads_made <= elapsed / rtt + 1, (
        f"{client.reads_made} leituras em {elapsed:.3f}s -- mais de uma por RTT")


def test_a_cycle_that_reads_nothing_does_not_spin(recorded):
    """A pagina aberta nao tem nenhum bit mapeado: nao ha resposta do rele pra
    esperar, entao periodo 0 seria um laco quente queimando CPU sem tocar na
    rede. E' o unico periodo minimo que sobrou (`IDLE_INTERVAL`)."""
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
        # Tudo que o transporte PERGUNTOU ao rele, em ordem. E' o que prova
        # que o `prepare_bits` deixou de falar com ele.
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
    # nao e' erro de protocolo: a py61850 respondeu tudo. Dizer o contrario
    # manda o usuario procurar rede onde o problema e' arquivo de projeto.
    assert not isinstance(e.value, Iec61850Error)


def test_prepare_bits_never_stops_the_reader_because_it_never_asks_the_relay(
        fake_relay):
    """O `pause` existe pelo contrato do `Transport` e nao e' usado aqui.

    O cliente da py61850 e' um socket e um contador de invoke, entao duas
    threads nele embaralham as respostas -- por isso um segundo diagrama
    parava o leitor pra buscar a estrutura dos containers novos. Com a leitura
    por folha nao ha' estrutura a buscar: o mapa sai do diretorio ja' lido no
    connect, e o leitor nem fica sabendo que outro diagrama entrou.
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
    # ja resolvido: segue sem pausa e sem requisicao
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
    from pacct.core import mms_tables
    from pacct.core.mms_tables import is_boolean_status

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
    """A recusa que sumiu, e o que ficou no lugar dela.

    Enquanto o polling lia container, `prepare_bits` pedia a ESTRUTURA de cada
    `LN$FC` e precisava recusar o diagrama quando o firmware nao descrevia
    nenhuma -- senao o diagrama subia LIVE com tudo indeterminado e sem erro
    na tela. Lendo por folha nao existe esse pedido nem esse modo de falhar: o
    que entra no mapa e' so' o que o proprio diretorio do rele nomeou (ver
    `resolve_map`), e o que sobra e' a recusa por cobertura zero.
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
    # o que o diagrama A pediu segue mapeado -- a recusa de B nao desfaz A
    assert t.coverage_for({"PLT01"})["mapped"] == 1
    assert t.coverage_for({"NAO_EXISTE_1"})["mapped"] == 0


def test_a_second_diagram_reaches_the_next_cycle_without_a_reconnect(fake_relay):
    """Nada pausa e a thread de polling nunca reinicia, entao ela tem que ver
    o plano NOVO: se o plano for capturado antes do laco, os bits de B ficam
    indeterminados ate' alguem reconectar."""
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
        # nada a perguntar ao rele: o plano novo sai do diretorio ja lido
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
    # sem tabela pra essa peca e sem SCD
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

    # (a) nenhum SCD escolhido
    t = MmsTransport("192.0.2.10", 102, relay_model=Model(), logger=None)
    t.connect()
    with pytest.raises(MmsSetupError) as e:
        t.prepare_bits(["PLT01"])
    sem_scd = str(e.value)
    assert "nenhum SCD do projeto foi associado" in sem_scd
    assert "Informe o SCD" in sem_scd

    # (b) um SCD escolhido que nao diz nada sobre este IED
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


# ---- o que falta no modelo do servidor do IED ------------------------------
#
# Um bit sem ponto no mapa nunca vai ser lido, e a tela so' dizia
# "indeterminado" -- o mesmo que ela diz pra um bit que ainda nao chegou.
# Quem pode dizer que a diferenca e' "adicione no modelo do IED" e' o
# transporte, que e' quem tem o mapa.

def test_unreachable_names_the_bits_the_relay_does_not_serve(fake_relay):
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    t.connect()
    t.prepare_bits(["PLT01", "PLT02"])
    out = t.unreachable({"PLT01", "PLT02", "NAO_EXISTE"})
    assert out["names"] == ["NAO_EXISTE"]
    assert out["reason"] == "mms"


def test_unreachable_cannot_tell_before_there_is_a_map():
    """Sem mapa, `[]` diria "o IED serve tudo" -- e' o oposto do que se sabe."""
    t = MmsTransport("192.0.2.10", 102, relay_model=None, logger=None)
    assert t.unreachable({"PLT01"}) is None


# -- pontos decorados: um item que carrega dois bits ------------------------
#
# `db:52A|52B?0:1:2:3` num `Pos$stVal`: o Dbpos codifica os dois contatos
# auxiliares do disjuntor. A py61850 devolve um BIT-STRING como a STRING
# "10", e `bool("00")` e' True -- ler isso com `int(bool(...))` pinta TODO
# disjuntor como fechado, sempre.

def _dbpos_transport(value, alternatives=(0, 1, 2, 3), nbits=2):
    """Dois bits (`52A`, `52B`) no mesmo item, que responde `value`."""
    from pacct.core.mms_tables import BitRule
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
    """Dbpos 1 (`"01"`), aberto. Sem a regra os dois sairiam 1, porque
    `bool("01")` e `bool("10")` sao os dois True."""
    t = _dbpos_transport("01")
    state = LiveState()
    _run_until_read(t, state)
    assert state.snapshot()["digitals"] == {"52A": 0, "52B": 1}


def test_poll_never_paints_a_dbpos_as_a_bit_on_by_truthiness():
    """Dbpos 0 (`"00"`, intermediate): os dois contatos abertos. Este e' o
    caso que `int(bool(valor))` acertaria por acaso em 52B e erraria em 52A --
    e' a prova de que a leitura passa pela regra e nao pela verdade do
    Python."""
    t = _dbpos_transport("00")
    state = LiveState()
    _run_until_read(t, state)
    assert state.snapshot()["digitals"] == {"52A": 0, "52B": 0}


def test_poll_leaves_a_value_outside_the_alternatives_indeterminate():
    """Um Dbpos 3 (bad-state) contra um ponto `?1:2` nao e' 0 nem 1: o bit
    some do payload e o desenho o pinta indeterminado, como um acesso que
    falha. E o `state.error` tem que dizer que a leitura foi parcial."""
    t = _dbpos_transport("11", alternatives=(1, 2), nbits=1)
    state = LiveState()
    _run_until_read(t, state)
    snap = state.snapshot()
    assert snap["digitals"] == {}
    assert "parcial" in snap["error"]


def test_poll_asks_for_a_shared_item_once_and_not_once_per_bit():
    """Os dois bits saem do MESMO `LN$FC$DO$DA`. Pedir o nome duas vezes no
    lote gasta banda do TPDU sem trazer nada: o plano e' de FOLHAS, e a folha
    e' uma so'."""
    t = _dbpos_transport("10")
    state = LiveState()
    _run_until_read(t, state)
    assert [item for _, item in t._client.asked[0]] == \
        ["BKR1CSWI1$ST$Pos$stVal"]


def test_a_plain_boolean_point_is_untouched_by_the_rule_path(recorded):
    """A regra e' opcional e o caminho booleano nao mudou: `rule is None`
    continua lendo `int(bool(valor))`."""
    defs, reads, expected = recorded
    t = _plt_transport(defs, reads)
    assert all(p.rule is None for p in t._plan)
    state = LiveState()
    _run_until_read(t, state)
    assert state.snapshot()["digitals"]["PLT01"] == \
        int(bool(expected["PLT1GGIO1$ST"]["Ind01"]))


# ---- relogio de parede que salta -------------------------------------------

class _JumpyClock:
    """Um `time` cujo relogio de PAREDE salta, como o do WSL.

    Medido na maquina de desenvolvimento: `time.time()` do WSL 82,5 s atras do
    relogio do Windows, e o log do GLV com carimbos ANDANDO PRA TRAS. Quem
    mede duracao com esse relogio le um ciclo negativo, e
    `sleep_for = periodo - ciclo` vira 82 s de sono -- a "leitura travada" com
    o Wireshark limpo. O monotonico nao salta; e' com ele que o laco conta.
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
    # Com o relogio de parede na conta, o salto de -82,5 s viraria um ciclo
    # negativo e o laco dormiria 82 s: UMA leitura em 0,4 s.
    assert t._client.reads_made >= 10, (
        f"{t._client.reads_made} leituras em 0,4 s a 10 ms -- o salto do "
        f"relogio parou o laco")


def test_the_age_on_screen_comes_from_the_monotonic_clock(recorded):
    """A tela recebe `age` pronto, e nao um carimbo pra subtrair do relogio
    DELA: o navegador roda no Windows e o servidor no WSL."""
    state = LiveState()
    assert state.snapshot()["age"] is None      # nada lido ainda
    with state.lock:
        state.mark_updated()
    snap = state.snapshot()
    assert snap["age"] is not None and snap["age"] < 1.0
    # Um relogio de parede em qualquer epoca nao mexe na idade.
    state.last_update_ts = 0.0
    assert state.snapshot()["age"] < 1.0
    state.clear()
    assert state.snapshot()["age"] is None
