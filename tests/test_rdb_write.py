"""Writing a GLE stream back into an RDB.

Two halves:

* `TestWriteStreams` pins `pacct.web.rdb_write.write_streams`, the two-pass
  writer both tools move onto in Phase 1C/1D. It is new code, so these are
  ordinary tests.
* `TestGrowingAComment` is the RED phase for that migration. Each test is
  `xfail(strict=True)` and describes damage the CURRENT `_fit_xml_to_size`
  path does. Flip the marker off in the phase that fixes it -- do not delete.

Everything runs against a synthetic Compound File built here, not against
`samples/*.rdb`: a 42 MB fixture makes a failing assertion unreadable and the
suite slow, and `ole_rebuild.write_ole` already produces containers `olefile`
accepts (see `test_ole_rebuild.py`).
"""

from __future__ import annotations

from pathlib import Path

import olefile
import pytest

from pacct.parsers import ole_rebuild as ore
from pacct.web import rdb_write
from tests import gle_fixtures as fx

RELAY = "QPC1_LT1_UPC1"
GLE_PARTS = ("Relays", RELAY, "Misc", "GL1.gle")
OTHER_PARTS = ("Relays", RELAY, "SET_1.TXT")

#: A second GLE, so "did the writer touch a stream it was not asked to?" is a
#: question the tests can actually ask.
SECOND_PARTS = ("Relays", RELAY, "Misc", "GL2.gle")
SECOND_GLE = fx.gle(fx.symbol_element("100", "VB900", out_comment="OUTRA PAGINA"))

SETTINGS = b'[1]\r\nRID,"QPC1 LT1 UPC1"\x1c\r\nTID,"SE TESTE"\x1c\r\n'


def _stream(name: str, data: bytes) -> ore.Entry:
    return ore.Entry(name=name, is_storage=False, size=len(data),
                     read=lambda d=data: d, children=[])


def _storage(name: str, children) -> ore.Entry:
    return ore.Entry(name=name, is_storage=True, size=0, read=None,
                     children=list(children))


@pytest.fixture
def rdb(tmp_path: Path) -> Path:
    """A minimal RDB: one relay, two GLE pages and a settings file."""
    path = tmp_path / "SE_TESTE.rdb"
    ore.write_ole(path, [
        _storage("Relays", [
            _storage(RELAY, [
                _stream("SET_1.TXT", SETTINGS),
                _storage("Misc", [
                    _stream("GL1.gle", fx.SAMPLE_GLE),
                    _stream("GL2.gle", SECOND_GLE),
                ]),
            ]),
        ]),
    ])
    return path


def _read(path: Path, parts: tuple[str, ...]) -> bytes:
    handle = olefile.OleFileIO(str(path))
    try:
        return handle.openstream(list(parts)).read()
    finally:
        handle.close()


# -----------------------------------------------------------------------------

class TestWriteStreams:

    def test_a_same_size_replacement_takes_the_in_place_path(self, rdb, tmp_path):
        """Same length in, same length out: no need to rebuild the container."""
        original = _read(rdb, GLE_PARTS)
        swapped = original.replace(b"TR1 UPC1 FALHA GOOSE",
                                   b"TR2 UPC2 FALHA GOOSE")
        assert len(swapped) == len(original)

        out = tmp_path / "out.rdb"
        method = rdb_write.write_streams(rdb, out, {GLE_PARTS: swapped})

        assert method == "in-place"
        assert _read(out, GLE_PARTS) == swapped

    def test_a_longer_replacement_takes_the_rebuild_path(self, rdb, tmp_path):
        """The case `write_stream` cannot do and `_fit_xml_to_size` used to
        force. A rebuild has no size constraint, so nothing has to be padded
        or whitespace-collapsed."""
        original = _read(rdb, GLE_PARTS)
        longer = original.replace(
            b"<comment>TR1 UPC1 FALHA GOOSE</comment>",
            b"<comment>TR1 UPC1 FALHA DE COMUNICACAO GOOSE ENTRE UPC1 E UPC2</comment>")
        assert len(longer) > len(original)

        out = tmp_path / "out.rdb"
        method = rdb_write.write_streams(rdb, out, {GLE_PARTS: longer})

        assert method == "rebuild"
        assert _read(out, GLE_PARTS) == longer

    def test_a_shorter_replacement_also_rebuilds(self, rdb, tmp_path):
        original = _read(rdb, GLE_PARTS)
        shorter = original.replace(b"<comment>TR1 UPC1 FALHA GOOSE</comment>",
                                   b"<comment />")
        assert len(shorter) < len(original)

        out = tmp_path / "out.rdb"
        method = rdb_write.write_streams(rdb, out, {GLE_PARTS: shorter})

        assert method == "rebuild"
        assert _read(out, GLE_PARTS) == shorter

    @pytest.mark.parametrize("grow", [False, True])
    def test_every_other_stream_survives_byte_identical(self, rdb, tmp_path, grow):
        """The guarantee that matters most. An RDB holds every relay in the
        substation; editing one GLE page must not perturb another relay's
        settings by a single byte.

        Parametrised over both paths on purpose -- the rebuild path
        reconstructs the whole container, so it is the one where a regression
        would show up."""
        original = _read(rdb, GLE_PARTS)
        if grow:
            new = original.replace(b"FALHA GOOSE", b"FALHA GOOSE MUITO LONGA")
        else:
            new = original.replace(b"TR1 UPC1", b"TR2 UPC2")

        out = tmp_path / "out.rdb"
        rdb_write.write_streams(rdb, out, {GLE_PARTS: new})

        assert _read(out, OTHER_PARTS) == SETTINGS
        assert _read(out, SECOND_PARTS) == SECOND_GLE

    def test_writes_several_streams_in_one_pass(self, rdb, tmp_path):
        """A batch apply touches many relays at once; it must be one write,
        not N sequential ones each able to fail halfway."""
        gle1 = _read(rdb, GLE_PARTS).replace(b"TR1 UPC1", b"TR9 UPC9")
        gle2 = _read(rdb, SECOND_PARTS).replace(b"OUTRA PAGINA",
                                                b"OUTRA PAGINA BEM MAIOR")
        out = tmp_path / "out.rdb"
        rdb_write.write_streams(rdb, out, {GLE_PARTS: gle1, SECOND_PARTS: gle2})

        assert _read(out, GLE_PARTS) == gle1
        assert _read(out, SECOND_PARTS) == gle2

    def test_a_stream_that_does_not_exist_is_refused(self, rdb, tmp_path):
        out = tmp_path / "out.rdb"
        with pytest.raises(rdb_write.RdbWriteError, match="não encontrado"):
            rdb_write.write_streams(rdb, out, {("Relays", "NAO_EXISTE"): b"x"})

    def test_a_refused_write_leaves_no_output_behind(self, rdb, tmp_path):
        """Nothing half-written, and nothing for `publish_output()` to adopt
        into the project as if it were a finished RDB."""
        out = tmp_path / "out.rdb"
        with pytest.raises(rdb_write.RdbWriteError):
            rdb_write.write_streams(rdb, out, {("Nope",): b"x"})
        assert not out.exists()

    def test_an_empty_replacement_set_is_refused(self, rdb, tmp_path):
        out = tmp_path / "out.rdb"
        with pytest.raises(rdb_write.RdbWriteError):
            rdb_write.write_streams(rdb, out, {})
        assert not out.exists()

    def test_a_crash_midway_leaves_an_existing_output_untouched(
            self, rdb, tmp_path, monkeypatch):
        """The power-cut case. `out.rdb` already holds a good export from an
        earlier run; a failure during the next one must not replace it with a
        file that mixes the two.

        Same sabotage idiom as `test_dnp_map_export.py`."""
        out = tmp_path / "out.rdb"
        out.write_bytes(b"UM RDB ANTERIOR, PERFEITAMENTE BOM")

        gle1 = _read(rdb, GLE_PARTS).replace(b"TR1 UPC1", b"TR2 UPC2")
        gle2 = _read(rdb, SECOND_PARTS).replace(b"OUTRA", b"AQUEL")

        real = olefile.OleFileIO.write_stream
        calls = {"n": 0}

        def flaky(self, stream, data):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disco cheio")
            return real(self, stream, data)

        monkeypatch.setattr(olefile.OleFileIO, "write_stream", flaky)

        with pytest.raises(rdb_write.RdbWriteError):
            rdb_write.write_streams(rdb, out, {GLE_PARTS: gle1,
                                               SECOND_PARTS: gle2})
        assert out.read_bytes() == b"UM RDB ANTERIOR, PERFEITAMENTE BOM"

    def test_refuses_to_write_onto_the_source(self, rdb):
        """Rebuilding a file onto itself would read from what it is
        overwriting. `ole_rebuild` already refuses; this checks the refusal
        survives the wrapper."""
        longer = _read(rdb, GLE_PARTS).replace(b"FALHA", b"FALHA LONGA")
        with pytest.raises(rdb_write.RdbWriteError):
            rdb_write.write_streams(rdb, rdb, {GLE_PARTS: longer})


class TestPathHelpers:

    def test_resolves_a_gle_path_relative_to_the_extraction(self, tmp_path):
        extract = tmp_path / "extracted"
        gle = extract / "Relays" / RELAY / "Misc" / "GL1.gle"
        gle.parent.mkdir(parents=True)
        gle.write_bytes(b"x")
        assert rdb_write.resolve_gle_stream_path(extract, gle) == list(GLE_PARTS)

    def test_a_path_outside_the_extraction_returns_the_fallback(self, tmp_path):
        """The VB Updater's optimistic guess. Not a normal path -- a GLE that
        came out of `RdbInfo` is always under the extraction."""
        got = rdb_write.resolve_gle_stream_path(
            tmp_path / "extracted", tmp_path / "elsewhere" / "GL1.gle",
            fallback=["Relays", RELAY, "Misc", "GL1.gle"])
        assert got == list(GLE_PARTS)

    def test_a_path_outside_the_extraction_with_no_fallback_is_empty(self, tmp_path):
        got = rdb_write.resolve_gle_stream_path(
            tmp_path / "extracted", tmp_path / "elsewhere" / "GL1.gle")
        assert got == []

    def test_suffix_goes_before_the_extension(self):
        got = rdb_write.with_suffix_before_ext(
            Path("/tmp/SE EXEMPLO.rdb"), "_comments_updated")
        assert got.name == "SE EXEMPLO_comments_updated.rdb"


# -----------------------------------------------------------------------------
# RED for Phase 1C / 1D
# -----------------------------------------------------------------------------

class TestNoPaddingPathCameBack:
    """`_fit_xml_to_size` is gone, and must stay gone.

    It existed to meet `olefile.write_stream`'s exact-size constraint by
    collapsing inter-tag whitespace across the whole `.gle` and padding with an
    XML comment before `</editor>` -- mutating a protection relay's settings in
    a way nobody has confirmed AcSELerator QuickSet tolerates.

    These replace the two `xfail`s that used to sit here. They fixed nothing on
    their own; what they described is now asserted positively, at the level a
    user actually reaches, by `test_the_rest_of_the_document_is_untouched` and
    `TestVbUpdaterApply::test_a_longer_description_now_succeeds`. What is left
    to guard is that the shortcut is not reintroduced when someone next meets
    the size constraint.
    """

    @pytest.mark.parametrize("module", ["vb_updater", "gle_exporter"])
    def test_the_module_has_no_size_fitting_helper(self, module):
        import importlib
        mod = importlib.import_module(f"pacct.web.{module}")
        assert not hasattr(mod, "_fit_xml_to_size")

    @pytest.mark.parametrize("module", ["vb_updater", "gle_exporter"])
    def test_the_module_does_not_call_write_stream_itself(self, module):
        """Every RDB write goes through `rdb_write`, which verifies and writes
        atomically. A tool reaching for `olefile` directly is the shape the
        padding path grew out of."""
        import importlib
        import inspect
        src = inspect.getsource(importlib.import_module(f"pacct.web.{module}"))
        assert "write_stream(" not in src
        assert "OleFileIO" not in src


# -----------------------------------------------------------------------------
# The GLE Exporter's orchestrator, end to end (Phase 1C)
# -----------------------------------------------------------------------------

class TestApplyXlsxUpdatesToRdb:
    """`apply_xlsx_updates_to_rdb` after the two-pass migration.

    The RDB is synthetic and so is the extraction beside it -- the exporter
    reads the GLE from the extracted file and writes to the matching OLE
    stream, so both have to exist and agree.
    """

    @pytest.fixture
    def project(self, rdb, tmp_path):
        from pacct.parsers.rdb import GleEntry, RdbInfo, RelayEntry

        extract = tmp_path / "extracted"
        misc = extract / "Relays" / RELAY / "Misc"
        misc.mkdir(parents=True)
        (misc / "GL1.gle").write_bytes(fx.SAMPLE_GLE)
        (misc / "GL2.gle").write_bytes(SECOND_GLE)

        def entry(name: str) -> GleEntry:
            return GleEntry(
                name=name, filename=f"{name}.gle",
                rel_path=f"Relays/{RELAY}/Misc/{name}.gle",
                fs_path=misc / f"{name}.gle")

        return RdbInfo(
            rdb_path=rdb, extract_dir=extract, sha256="0" * 64, reused=False,
            relays=[RelayEntry(name=RELAY, gles=[entry("GL1"), entry("GL2")],
                               model="411L", ip=None)],
            display_name="SE_TESTE.rdb")

    def test_a_longer_comment_now_succeeds_instead_of_being_squeezed(
            self, project, tmp_path):
        """The case `_fit_xml_to_size` had to buy bytes back for. It rebuilds
        now, so nothing is collapsed or padded."""
        from pacct.web.gle_exporter import apply_xlsx_updates_to_rdb

        out = tmp_path / "out.rdb"
        result = apply_xlsx_updates_to_rdb(
            rdb_info=project,
            xlsx_updates={(RELAY, "GL1"): {
                "542": {("output", 0): "UMA DESCRICAO CONSIDERAVELMENTE MAIS LONGA"}}},
            output_path=out)

        assert result["ok"] is True
        assert result["method"] == "rebuild"
        written = _read(out, GLE_PARTS)
        assert b"UMA DESCRICAO CONSIDERAVELMENTE MAIS LONGA" in written

    def test_the_rest_of_the_document_is_untouched(self, project, tmp_path):
        """The `xfail` in TestGrowingAComment, now as a passing assertion at
        the level a user actually reaches."""
        from pacct.web.gle_exporter import apply_xlsx_updates_to_rdb

        out = tmp_path / "out.rdb"
        apply_xlsx_updates_to_rdb(
            rdb_info=project,
            xlsx_updates={(RELAY, "GL1"): {
                "542": {("output", 0): "MUITO MAIS LONGA QUE A ORIGINAL"}}},
            output_path=out)

        written = _read(out, GLE_PARTS)

        def slice_of(raw: bytes) -> bytes:
            start = raw.index(b'<element id="544"')
            return raw[start:raw.index(b"</element>", start)]

        assert slice_of(written) == slice_of(fx.SAMPLE_GLE)
        assert b"<!--" not in written
        assert _read(out, SECOND_PARTS) == SECOND_GLE

    def test_one_bad_selection_writes_nothing_at_all(self, project, tmp_path):
        """All-or-nothing. Before, the good selection was already in the output
        file, the response still said ok, and that half-applied RDB went into
        the project library indistinguishable from a complete one."""
        from pacct.web.gle_exporter import apply_xlsx_updates_to_rdb

        out = tmp_path / "out.rdb"
        result = apply_xlsx_updates_to_rdb(
            rdb_info=project,
            xlsx_updates={
                (RELAY, "GL1"): {"542": {("output", 0): "ISSO DARIA CERTO"}},
                (RELAY, "GL9"): {"1": {("output", 0): "ESSE GLE NAO EXISTE"}},
            },
            output_path=out)

        assert result["ok"] is False
        assert result["failed"] == 1
        assert not out.exists()

    def test_a_no_op_import_still_hands_back_the_rdb(self, project, tmp_path):
        """The spreadsheet matched the RDB exactly. Nothing to rewrite, but the
        engineer still gets a file -- which is what happened before, when the
        copy came first and no stream was written over it."""
        from pacct.web.gle_exporter import apply_xlsx_updates_to_rdb

        out = tmp_path / "out.rdb"
        result = apply_xlsx_updates_to_rdb(
            rdb_info=project,
            xlsx_updates={(RELAY, "GL1"): {
                "542": {("output", 0): "TR1 UPC1 FALHA GOOSE"}}},  # already this
            output_path=out)

        assert result["ok"] is True
        assert result["method"] == "copy"
        assert out.read_bytes() == rdb_bytes(project.rdb_path)


def rdb_bytes(path: Path) -> bytes:
    return Path(path).read_bytes()


# -----------------------------------------------------------------------------
# The VB Updater's orchestrators, end to end (Phase 1D)
# -----------------------------------------------------------------------------

class TestVbUpdaterApply:
    """`update_rdb_with_scd_descs` and its batch twin after the migration."""

    @pytest.fixture
    def extracted(self, tmp_path) -> Path:
        misc = tmp_path / "extracted" / "Relays" / RELAY / "Misc"
        misc.mkdir(parents=True)
        (misc / "GL1.gle").write_bytes(fx.SAMPLE_GLE)
        (misc / "GL2.gle").write_bytes(SECOND_GLE)
        return tmp_path / "extracted"

    @pytest.fixture
    def scd(self, tmp_path) -> Path:
        path = tmp_path / "estacao.scd"
        path.write_bytes(fx.scd(fx.ied(
            "QPC1_UPC2",
            fx.extref("VB105", desc="DESCRICAO NOVA E BEM MAIS COMPRIDA"),
            fx.extref("VB042", desc=""),          # spare -> "reserva"
        )))
        return path

    def test_a_longer_description_now_succeeds(self, rdb, extracted, scd, tmp_path):
        """The SCD description is longer than the GLE comment it replaces --
        the case `_fit_xml_to_size` had to buy bytes back for."""
        from pacct.web.vb_updater import update_rdb_with_scd_descs

        out = tmp_path / "out.rdb"
        result = update_rdb_with_scd_descs(
            rdb_path=rdb, extract_dir=extracted, relay_name=RELAY,
            gle_name="GL1", gle_fs_path=extracted / "Relays" / RELAY / "Misc" / "GL1.gle",
            scd_path=scd, ied_name="QPC1_UPC2", output_path=out)

        assert result["ok"] is True
        assert result["method"] == "rebuild"
        written = _read(out, GLE_PARTS)
        assert b"DESCRICAO NOVA E BEM MAIS COMPRIDA" in written
        assert b"<!--" not in written

    def test_an_empty_scd_desc_becomes_reserva(self, rdb, extracted, scd, tmp_path):
        """A VB declared as an ExtRef with no description is a spare slot.
        Leaving the old comment would describe a signal that is gone."""
        from pacct.web.vb_updater import update_rdb_with_scd_descs

        out = tmp_path / "out.rdb"
        result = update_rdb_with_scd_descs(
            rdb_path=rdb, extract_dir=extracted, relay_name=RELAY,
            gle_name="GL1", gle_fs_path=extracted / "Relays" / RELAY / "Misc" / "GL1.gle",
            scd_path=scd, ied_name="QPC1_UPC2", output_path=out)

        assert result["stats"]["vbs_in_scd_renamed_to_reserva"] == 1
        assert b"<comment>reserva</comment>" in _read(out, GLE_PARTS)

    def test_an_ied_with_no_extrefs_writes_nothing(self, rdb, extracted, scd, tmp_path):
        from pacct.web.vb_updater import update_rdb_with_scd_descs

        out = tmp_path / "out.rdb"
        result = update_rdb_with_scd_descs(
            rdb_path=rdb, extract_dir=extracted, relay_name=RELAY,
            gle_name="GL1", gle_fs_path=extracted / "Relays" / RELAY / "Misc" / "GL1.gle",
            scd_path=scd, ied_name="NAO_EXISTE", output_path=out)

        assert result["ok"] is False
        assert not out.exists()

    def test_batch_writes_every_selection_in_one_go(self, rdb, extracted, scd,
                                                    tmp_path):
        from pacct.web.vb_updater import update_rdb_with_scd_descs_batch

        misc = extracted / "Relays" / RELAY / "Misc"
        out = tmp_path / "out.rdb"
        result = update_rdb_with_scd_descs_batch(
            rdb_path=rdb, extract_dir=extracted, scd_path=scd,
            selections=[
                {"relay": RELAY, "ied": "QPC1_UPC2", "gle_name": "GL1",
                 "gle_fs_path": misc / "GL1.gle"},
                {"relay": RELAY, "ied": "QPC1_UPC2", "gle_name": "GL2",
                 "gle_fs_path": misc / "GL2.gle"},
            ],
            output_path=out)

        assert result["ok"] is True
        assert result["succeeded"] == 2
        assert b"DESCRICAO NOVA E BEM MAIS COMPRIDA" in _read(out, GLE_PARTS)

    def test_batch_with_one_bad_selection_writes_nothing_and_says_so(
            self, rdb, extracted, scd, tmp_path):
        """THE regression this phase exists for.

        Before: the good selection was already in the output RDB, the response
        said `ok: True`, and `publish_output()` put that half-applied file into
        the project library where nothing distinguishes it from a complete
        one. An engineer would take it to the substation."""
        from pacct.web.vb_updater import update_rdb_with_scd_descs_batch

        misc = extracted / "Relays" / RELAY / "Misc"
        out = tmp_path / "out.rdb"
        result = update_rdb_with_scd_descs_batch(
            rdb_path=rdb, extract_dir=extracted, scd_path=scd,
            selections=[
                {"relay": RELAY, "ied": "QPC1_UPC2", "gle_name": "GL1",
                 "gle_fs_path": misc / "GL1.gle"},
                {"relay": RELAY, "ied": "IED_INEXISTENTE", "gle_name": "GL2",
                 "gle_fs_path": misc / "GL2.gle"},
            ],
            output_path=out)

        assert result["ok"] is False
        assert result["succeeded"] == 1
        assert result["failed"] == 1
        assert not out.exists()
        # The per-selection detail still reaches the screen, so the user can
        # see WHICH one failed rather than just that something did.
        assert [r["ok"] for r in result["results"]] == [True, False]
