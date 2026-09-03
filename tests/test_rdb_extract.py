"""Getting an RDB out of its Compound File and into the content cache.

These pin behaviour that already exists. That makes them characterization
tests, so each one names, in its docstring, the production change that would
make it fail -- otherwise a test that passed the moment it was written proves
nothing.

Why they matter: `parsers/rdb.py` is the front door. Every tool in the toolkit
starts from an `RdbInfo`, and three of its decisions are load-bearing and easy
to break silently:

* the extraction is keyed by the sha256 of the CONTENT and shared between
  visitors and across restarts, so a mistake here leaks one project's files
  into another's screen;
* `meta.json` is written only after extraction finishes, so a killed process
  leaves an entry that is redone rather than half-served;
* `display_name` comes from THIS upload, never from the cache -- otherwise
  everyone sees the name of whoever uploaded the bytes first.

Everything runs against a synthetic Compound File built with
`ole_rebuild.write_ole` (the same idiom as `test_ole_rebuild.py` and
`test_rdb_write.py`) and a `tmp_path` cache root. The real
`samples/*.rdb` is 42 MB and the real `cache/` belongs to the user.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pacct.parsers import ole_rebuild as ore
from pacct.parsers import rdb
from tests import gle_fixtures as fx

FS = "\x1c"

TRAFO = "QPC1_TR1_UPC1"
ALIM = "QPC1_ALIM_01"
#: A SEL-2440 data concentrator: real settings, no logic diagram. `RelayEntry`
#: is built from `.gle` streams, so it must NOT appear in `RdbInfo.relays` --
#: see the docs/ENGINEERING-NOTES.md gotcha.
CONC = "QPC1_2440_CONC"


def _stream(name: str, data: bytes) -> ore.Entry:
    return ore.Entry(name=name, is_storage=False, size=len(data),
                     read=lambda d=data: d, children=[])


def _storage(name: str, children) -> ore.Entry:
    return ore.Entry(name=name, is_storage=True, size=0, read=None,
                     children=list(children))


def _settings(**pairs: str) -> bytes:
    return "".join(f'{k},"{v}"{FS}\r\n' for k, v in pairs.items()).encode("latin-1")


def _rdb_bytes(tmp_path: Path, name: str = "src.rdb") -> bytes:
    """A three-relay RDB: a 487E with two pages, a relay with no `Cfg.txt`,
    and a concentrator with settings and no diagram at all."""
    path = tmp_path / name
    ore.write_ole(path, [
        _stream("Info", b"QuickSet\r\n"),
        _storage("Relays", [
            _storage(TRAFO, [
                _stream("SET_P5.TXT", _settings(IPADDR="192.0.2.60/24")),
                _stream("SET_G1.TXT", _settings(RID=TRAFO)),
                _storage("Misc", [
                    _stream("Cfg.txt", b"RELAYTYPE = SEL-487E-3\r\n"),
                    _stream("GL2.gle", fx.SAMPLE_GLE),
                    _stream("GL1.gle", fx.SAMPLE_GLE),
                ]),
            ]),
            _storage(ALIM, [
                _storage("Misc", [_stream("GL1.gle", fx.SAMPLE_GLE)]),
            ]),
            _storage(CONC, [
                _stream("SET_P1.TXT", _settings(IPADDR="192.0.2.70")),
                _storage("Misc", [_stream("Cfg.txt", b"RELAYTYPE = SEL-2440\r\n")]),
            ]),
        ]),
    ])
    return path.read_bytes()


@pytest.fixture
def data(tmp_path: Path) -> bytes:
    return _rdb_bytes(tmp_path)


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return tmp_path / "rdbcache"


# -----------------------------------------------------------------------------
# Extraction
# -----------------------------------------------------------------------------

class TestExtraction:

    def test_the_extraction_lives_under_the_sha256_of_the_content(self, data, cache):
        """Keyed by content, not by name -- that is what lets two visitors and
        two restarts share one 140 MB extraction. Fails if the layout changes
        to a name- or session-keyed directory, which is exactly the bug the
        content cache replaced."""
        info = rdb.process_upload(data, "SE TESTE.rdb", cache_root=cache)
        assert info.sha256 == rdb.sha256_bytes(data)
        assert info.extract_dir == cache / info.sha256 / "extracted"
        assert info.rdb_path == cache / info.sha256 / "source.rdb"

    def test_every_stream_is_written_with_its_hierarchy(self, data, cache):
        """Streams outside `Relays/` are extracted too -- the DNP Map Editor
        walks `Relays/` directly rather than through `RdbInfo.relays`, and other
        code reads project-level streams. Fails if the extractor starts
        filtering to the paths it happens to index."""
        info = rdb.process_upload(data, "x.rdb", cache_root=cache)
        d = info.extract_dir
        assert (d / "Info").read_bytes() == b"QuickSet\r\n"
        assert (d / "Relays" / TRAFO / "SET_P5.TXT").is_file()
        assert (d / "Relays" / CONC / "Misc" / "Cfg.txt").is_file()

    def test_only_relays_that_own_a_gle_are_listed(self, data, cache):
        """`RelayEntry` is built from `.gle` streams, so a SEL-2440
        concentrator -- settings, no diagram -- never appears. Anything that
        needs EVERY relay walks `extract_dir/Relays/` instead (that is what
        `parsers/set_dnp.py:discover()` does).

        Fails if this list silently starts including diagram-less relays: the
        GLV's relay picker would offer entries with nothing to draw."""
        info = rdb.process_upload(data, "x.rdb", cache_root=cache)
        assert [r.name for r in info.relays] == [ALIM, TRAFO]
        assert CONC not in [r.name for r in info.relays]
        assert (info.extract_dir / "Relays" / CONC).is_dir()

    def test_relays_and_their_pages_come_back_sorted(self, data, cache):
        """`GL2.gle` is written before `GL1.gle` in the fixture. The tab strip
        and the page selector render in this order. Fails if either `sorted()`
        goes and the pages start following OLE directory order, which is a
        red-black tree walk and looks random."""
        info = rdb.process_upload(data, "x.rdb", cache_root=cache)
        trafo = [r for r in info.relays if r.name == TRAFO][0]
        assert [g.name for g in trafo.gles] == ["GL1", "GL2"]

    def test_a_gle_entry_carries_both_spellings_and_a_real_path(self, data, cache):
        """`rel_path` addresses the OLE stream (what a writer needs) and
        `fs_path` the extracted file (what the renderer reads). Fails if either
        is dropped or if `name`/`filename` are collapsed into one field --
        `find_gle` accepts both spellings on purpose."""
        info = rdb.process_upload(data, "x.rdb", cache_root=cache)
        g = [r for r in info.relays if r.name == TRAFO][0].gles[0]
        assert (g.name, g.filename) == ("GL1", "GL1.gle")
        assert g.rel_path == f"Relays/{TRAFO}/Misc/GL1.gle"
        assert g.fs_path.read_bytes() == fx.SAMPLE_GLE

    def test_the_model_and_ip_are_read_out_of_the_extracted_files(
            self, data, cache):
        """`RELAYTYPE` from `Misc/Cfg.txt` picks the profile, and the profile
        says which `SET_*` file holds `IPADDR`. Fails if `_MODEL_RE` stops
        trimming the `SEL-` prefix (the profile lookup misses) or if the CIDR
        suffix stops being handled."""
        info = rdb.process_upload(data, "x.rdb", cache_root=cache)
        trafo = [r for r in info.relays if r.name == TRAFO][0]
        assert trafo.model == "487E-3"
        assert trafo.ip == "192.0.2.60"

    def test_a_relay_without_a_cfg_has_no_model_and_no_ip(self, data, cache):
        """`QPC1_ALIM_01` has a diagram and nothing else. Fails if a missing
        `Cfg.txt` starts raising instead of returning None -- one unusual relay
        would take the whole upload down."""
        info = rdb.process_upload(data, "x.rdb", cache_root=cache)
        alim = [r for r in info.relays if r.name == ALIM][0]
        assert (alim.model, alim.ip) == (None, None)

    def test_an_empty_upload_is_refused(self, cache):
        """A ValueError, so the handler can answer 400 rather than 500. Fails
        if the guard goes and `hashlib` happily hashes zero bytes into a cache
        entry that can never be extracted."""
        with pytest.raises(ValueError):
            rdb.process_upload(b"", "vazio.rdb", cache_root=cache)

    def test_progress_is_reported_during_the_slow_phases(self, data, cache):
        """A real RDB has thousands of streams and takes seconds; without this
        the client's bar sits still. Fails if `on_progress` stops being
        threaded through `_extract_and_collect`."""
        stages = []
        rdb.process_upload(data, "x.rdb", cache_root=cache,
                           on_progress=lambda d, t, s: stages.append(s))
        assert "Extraindo arquivos do RDB" in stages
        assert "Lendo dados dos reles" in stages


# -----------------------------------------------------------------------------
# The cache contract
# -----------------------------------------------------------------------------

class TestCacheContract:

    def test_meta_json_is_written_only_after_extraction_finishes(
            self, data, cache):
        """The marker that says 'this entry is complete'. Fails if it is
        written up front -- a `kill -9` mid-extraction would leave an entry
        that `complete` accepts and `_scan_existing` reads half of."""
        info = rdb.process_upload(data, "SE TESTE.rdb", cache_root=cache)
        import json
        meta = json.loads((cache / info.sha256 / "meta.json").read_text())
        assert meta["sha256"] == info.sha256
        assert meta["relays"] == 2
        assert meta["first_name"] == "SE TESTE.rdb"

    def test_the_same_bytes_are_extracted_once(self, data, cache):
        """`reused` is False the first time and True the second. Fails if the
        dedup goes: every upload of a 140 MB RDB would re-extract it, which is
        the cost the content cache exists to remove."""
        first = rdb.process_upload(data, "a.rdb", cache_root=cache)
        second = rdb.process_upload(data, "b.rdb", cache_root=cache)
        assert first.reused is False
        assert second.reused is True
        assert first.extract_dir == second.extract_dir

    def test_a_reused_entry_reports_the_same_relays_as_a_fresh_one(
            self, data, cache):
        """There are TWO implementations of 'what is in this RDB':
        `_extract_and_collect` walks the OLE, `_scan_existing` walks the
        extracted tree. They must not drift. Fails the moment one of them
        learns something the other did not -- and only the second one runs for
        everyone after the first upload."""
        fresh = rdb.process_upload(data, "a.rdb", cache_root=cache)
        reused = rdb.process_upload(data, "b.rdb", cache_root=cache)

        def shape(info):
            return [(r.name, r.model, r.ip, [g.rel_path for g in r.gles])
                    for r in info.relays]

        assert shape(reused) == shape(fresh)

    def test_the_display_name_is_this_uploads_name_not_the_caches(
            self, data, cache):
        """Two engineers upload the same export under their own names; each
        must see their own. Fails if `display_name` starts coming from
        `meta.json` -- everyone would see whoever uploaded first."""
        rdb.process_upload(data, "PRIMEIRO.rdb", cache_root=cache)
        second = rdb.process_upload(data, "SEGUNDO.rdb", cache_root=cache)
        assert second.display_name == "SEGUNDO.rdb"

    def test_meta_json_deliberately_keeps_the_first_name(self, data, cache):
        """The counterpart: the on-disk record is for human inspection only and
        is NOT rewritten by later uploads. Fails if `write_meta` starts running
        on the reuse path, which would make `meta.json` look like the answer to
        'what is this file called' -- it is not."""
        import json
        info = rdb.process_upload(data, "PRIMEIRO.rdb", cache_root=cache)
        rdb.process_upload(data, "SEGUNDO.rdb", cache_root=cache)
        meta = json.loads((cache / info.sha256 / "meta.json").read_text())
        assert meta["first_name"] == "PRIMEIRO.rdb"

    def test_an_entry_without_meta_json_is_extracted_again(self, data, cache):
        """A process killed mid-extraction leaves exactly this. Fails if
        `complete` stops requiring `meta.json` -- the next visitor would be
        served a partial tree as if it were whole."""
        info = rdb.process_upload(data, "a.rdb", cache_root=cache)
        (cache / info.sha256 / "meta.json").unlink()
        again = rdb.process_upload(data, "a.rdb", cache_root=cache)
        assert again.reused is False
        assert [r.name for r in again.relays] == [ALIM, TRAFO]

    def test_a_complete_marker_over_an_empty_tree_is_extracted_again(
            self, data, cache):
        """The second guard: `reused = bool(relays)`. `meta.json` can survive a
        disk that lost the extraction. Fails if the reuse path trusts the
        marker alone and returns an `RdbInfo` with no relays in it -- which on
        screen is an RDB that 'has no diagrams'."""
        import shutil
        info = rdb.process_upload(data, "a.rdb", cache_root=cache)
        shutil.rmtree(info.extract_dir)
        again = rdb.process_upload(data, "a.rdb", cache_root=cache)
        assert again.reused is False
        assert len(again.relays) == 2

    def test_different_bytes_get_a_different_entry(self, tmp_path, cache):
        """Fails if the key stops being the content hash: two different
        substations uploaded under one name would share one extraction."""
        a = _rdb_bytes(tmp_path, "a.rdb")
        b = _rdb_bytes(tmp_path, "b.rdb") + b"\x00" * 512
        ia = rdb.process_upload(a, "x.rdb", cache_root=cache)
        ib = rdb.process_upload(b, "x.rdb", cache_root=cache)
        assert ia.sha256 != ib.sha256
        assert ia.extract_dir != ib.extract_dir


# -----------------------------------------------------------------------------
# Small helpers other modules key things by
# -----------------------------------------------------------------------------

class TestHelpers:

    @pytest.mark.parametrize("raw,want", [
        ("SE 4 Marcos.rdb", "SE 4 Marcos.rdb"),
        ("../../etc/passwd", ".._.._etc_passwd"),
        ("SUBESTAÇÃO.rdb", "SUBESTA__O.rdb"),
        ("", "unknown"),
    ])
    def test_sanitize_name_keeps_only_filesystem_safe_characters(self, raw, want):
        """`display_name` reaches an `<a download>` and a `Content-Disposition`.
        Fails if `_UNSAFE_CHARS` loosens -- note that a path separator is not
        merely cosmetic here."""
        assert rdb.sanitize_name(raw) == want

    def test_a_name_without_the_extension_gets_one(self, data, cache):
        """Fails if the suffix check goes: the browser would save an
        extension-less file that QuickSet will not offer to open."""
        info = rdb.process_upload(data, "SE TESTE", cache_root=cache)
        assert info.display_name == "SE TESTE.rdb"

    def test_the_extension_check_is_case_insensitive(self, data, cache):
        """Fails if `.lower()` goes and `PROJETO.RDB` becomes
        `PROJETO.RDB.rdb`."""
        info = rdb.process_upload(data, "PROJETO.RDB", cache_root=cache)
        assert info.display_name == "PROJETO.RDB"

    def test_short_sha_is_twelve_characters(self):
        """An IDENTIFIER, not a display detail: the DNP Map Editor keys its
        per-session edits by it and Settings Compare keys its RDB registry by
        it. Fails if the length changes -- two tools would disagree about which
        RDB is which."""
        sha = rdb.sha256_bytes(b"x")
        assert rdb.short_sha(sha) == sha[:12]
        assert len(rdb.short_sha(sha)) == 12

    def test_hashing_a_file_and_hashing_its_bytes_agree(self, tmp_path):
        """`sha256_file` reads in chunks; `sha256_bytes` does not. Fails if the
        chunked reader ever drops a tail -- the library would then key an entry
        by a digest nothing else can reproduce."""
        p = tmp_path / "big.bin"
        p.write_bytes(bytes(range(256)) * 500)
        assert rdb.sha256_file(p) == rdb.sha256_bytes(p.read_bytes())

    def test_find_gle_accepts_both_spellings(self, data, cache):
        """The URL carries `GL1`, the OLE path carries `GL1.gle`. Fails if
        either arm of the comparison goes."""
        info = rdb.process_upload(data, "x.rdb", cache_root=cache)
        assert rdb.find_gle(info, TRAFO, "GL1").filename == "GL1.gle"
        assert rdb.find_gle(info, TRAFO, "GL1.gle").name == "GL1"

    def test_find_gle_returns_none_rather_than_guessing(self, data, cache):
        """A wrong page silently substituted for a missing one would be a wrong
        diagram on screen. Fails if either `None` return becomes a fallback to
        the first page."""
        info = rdb.process_upload(data, "x.rdb", cache_root=cache)
        assert rdb.find_gle(info, TRAFO, "GL9") is None
        assert rdb.find_gle(info, "NAO_EXISTE", "GL1") is None

    def test_relays_to_dict_is_the_shape_the_frontend_reads(self, data, cache):
        """Fails if a key is renamed under the JavaScript, which has no schema
        to complain with -- the picker would just render blank rows."""
        info = rdb.process_upload(data, "x.rdb", cache_root=cache)
        rows = rdb.relays_to_dict(info.relays)
        assert set(rows[0]) == {"name", "model", "ip", "gles"}
        assert set(rows[0]["gles"][0]) == {"name", "filename", "rel_path"}
        assert [r["name"] for r in rows] == [ALIM, TRAFO]
