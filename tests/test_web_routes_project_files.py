"""Arquivos do Projeto's four routes.

`tests/test_project_files.py` covers `FileLibrary` and `derived.adopt` as
units. This drives the HTTP surface over them: the ceilings, the kind check,
the duplicate that must NOT delete the file it duplicates, and the download
that is keyed by sha256 rather than by a path.

No RDB here. `/upload` of one calls `rdb.process_upload`, which writes into the
process-wide content cache rather than a tmp_path; the RDB half of the library
is covered as a unit in `test_project_files.py`.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from pacct.web.project_files import library as filelib
from pacct.web.project_files.handler import build_project_files_handler
from tests.web_harness import build

_SCD = Path(__file__).parent / "fixtures" / "saddr_min.scd"
_SCD_BYTES = _SCD.read_bytes()


def _harness(tmp_path):
    return build(build_project_files_handler, tmp_path)


def _upload(h, data: bytes, filename: str = "projeto.scd", **kw):
    return h.post("/upload", body=data,
                  headers={"X-Filename": filename,
                           "Content-Length": str(len(data))}, **kw)


# -- the page ---------------------------------------------------------------

def test_the_page_comes_back_as_html(tmp_path):
    r = _harness(tmp_path).get("/")
    assert r.status == 200
    assert r.headers["content-type"].startswith("text/html")


def test_an_unknown_route_is_404(tmp_path):
    assert _harness(tmp_path).get("/nao-existe").status == 404
    assert _harness(tmp_path).post("/nao-existe").status == 404


# -- upload: what is refused before a byte is stored ------------------------

def test_an_empty_upload_is_refused(tmp_path):
    r = _upload(_harness(tmp_path), b"")
    assert r.status == 400
    assert r.json()["ok"] is False


def test_an_extension_the_library_does_not_know_is_refused(tmp_path):
    """`kind_for` does not know `.xlsx` on purpose: a spreadsheet is an
    OUTPUT, never a project input."""
    r = _upload(_harness(tmp_path), b"qualquer coisa", "planilha.xlsx")
    assert r.status == 400
    assert "rdb" in r.json()["error"].lower()


def test_a_file_over_the_ceiling_is_refused_by_content_length(tmp_path):
    """The ceiling is checked against the declared length, BEFORE the body is
    read -- the point is not to allocate what the client asked for."""
    h = _harness(tmp_path)
    too_big = filelib.SCD_MAX_BYTES + 1
    r = h.post("/upload", body=b"x",
               headers={"X-Filename": "grande.scd",
                        "Content-Length": str(too_big)})
    assert r.status == 413


def test_an_scd_with_no_ieds_never_enters_the_library(tmp_path):
    """A file that does not validate leaves no half-entry behind for a tool to
    trip over later."""
    h = _harness(tmp_path)
    r = _upload(h, b"<SCL></SCL>", "vazio.scd")
    assert r.status == 400
    with h.session.lock:
        assert h.library().list() == []


# -- upload: the happy path and the duplicate ------------------------------

def test_uploading_an_scd_puts_it_in_the_library(tmp_path):
    h = _harness(tmp_path)
    r = _upload(h, _SCD_BYTES, "saddr_min.scd")
    assert r.status == 200
    out = r.json()
    assert out["ok"] is True and out["duplicate"] is False
    assert out["entry"]["name"] == "saddr_min.scd"
    assert out["entry"]["kind"] == filelib.KIND_SCD


def test_the_same_bytes_twice_is_one_entry_and_keeps_the_first_name(tmp_path):
    h = _harness(tmp_path)
    _upload(h, _SCD_BYTES, "primeiro.scd")
    r = _upload(h, _SCD_BYTES, "segundo.scd")
    assert r.json()["duplicate"] is True
    assert r.json()["entry"]["name"] == "primeiro.scd"
    with h.session.lock:
        assert len(h.library().list()) == 1


def test_a_duplicate_upload_does_not_delete_the_file_it_duplicates(tmp_path):
    """The bug this guards: `files/<sha12>.scd` is named after the CONTENT, so
    the second upload rewrites the SAME path. `_discard` deleting "what this
    upload built" therefore deleted what the library was pointing at -- the
    entry survived in memory with nothing behind it on disk, and every tool
    answered "Arquivo não está mais no projeto" for a file still on screen.
    """
    h = _harness(tmp_path)
    first = _upload(h, _SCD_BYTES, "primeiro.scd").json()["entry"]
    _upload(h, _SCD_BYTES, "segundo.scd")
    with h.session.lock:
        entry = h.library().get(first["sha256"])
    assert entry is not None
    assert entry.require_scd_path().is_file()


def test_the_stored_name_comes_from_the_hash_not_the_upload(tmp_path):
    h = _harness(tmp_path)
    out = _upload(h, _SCD_BYTES, "nome do usuário.scd").json()["entry"]
    with h.session.lock:
        entry = h.library().get(out["sha256"])
    assert entry.require_scd_path().name == out["sha256"][:12] + ".scd"


def test_two_visitors_have_two_projects(tmp_path):
    h = _harness(tmp_path)
    _upload(h, _SCD_BYTES)
    other, _ = h.sessions.resolve(None)
    lib = filelib.library_for(h.sessions, other)
    with other.lock:
        assert lib.list() == []


# -- download ---------------------------------------------------------------

def test_download_hands_back_the_bytes_that_went_in(tmp_path):
    h = _harness(tmp_path)
    sha = _upload(h, _SCD_BYTES, "saddr_min.scd").json()["entry"]["sha256"]
    r = h.get(f"/download?sha256={sha}")
    assert r.status == 200
    assert r.body == _SCD_BYTES


def test_download_names_the_file_with_rfc_5987(tmp_path):
    """The header is `filename*=UTF-8''` because these names carry accents --
    QuickSet users name their files in Portuguese."""
    h = _harness(tmp_path)
    sha = _upload(h, _SCD_BYTES, "SE Jaguar R0a.scd").json()["entry"]["sha256"]
    r = h.get(f"/download?sha256={sha}")
    disp = r.headers["content-disposition"]
    assert disp.startswith("attachment; filename*=UTF-8''")
    assert "SE%20Jaguar%20R0a.scd" in disp


def test_an_accented_upload_name_keeps_its_accents(tmp_path):
    """`subestação.scd` is shown as `subestação.scd`.

    It used to reach the library as `subesta__o.scd`: the entry's name went
    through `sellib`' `rdb.sanitize_name`, whose allowlist is
    `[^A-Za-z0-9._\\- ]` -- ASCII only. That is the right rule for the one
    caller building a FILESYSTEM path (`dnp_map/export.py`) and the wrong one
    for a name that is only ever shown: the RFC 5987 `filename*=UTF-8''`
    header two tests up exists *because* these names carry accents, and by
    then there were none left to carry.
    """
    h = _harness(tmp_path)
    entry = _upload(h, _SCD_BYTES, "subestação.scd").json()["entry"]
    assert entry["name"] == "subestação.scd"


def test_an_accented_name_survives_the_round_trip_to_the_download_header(tmp_path):
    """Upload to `Content-Disposition` and back, which is the trip the
    stripping used to make pointless."""
    h = _harness(tmp_path)
    sha = _upload(h, _SCD_BYTES, "subestação.scd").json()["entry"]["sha256"]
    disp = h.get(f"/download?sha256={sha}").headers["content-disposition"]
    assert unquote(disp.split("UTF-8''", 1)[1]) == "subestação.scd"


def test_an_upload_name_that_is_a_path_is_stored_as_a_name(tmp_path):
    """The name is not sanitized any more; it is still a NAME. `X-Filename`
    carries whatever the client put in it, and this one goes on to build an
    output filename in the VB Updater."""
    h = _harness(tmp_path)
    entry = _upload(h, _SCD_BYTES, "../../etc/subestação.scd").json()["entry"]
    assert entry["name"] == "subestação.scd"


def test_an_uploaded_rdb_keeps_its_accents(tmp_path, monkeypatch):
    """The RDB is the upload this app sees most, and its name was stripped a
    layer further in than the SCD's: `sellib` sanitizes it inside
    `process_upload_stream`. The name it hands back is the cache's business;
    the one this project shows is not.
    """
    from sellib.rdb import RdbInfo

    from pacct.web.project_files import handler as files_handler

    def fake_stream(source, length, filename, **kw):
        source.read(length)               # the route hands over `rfile`
        return RdbInfo(rdb_path=tmp_path / "source.rdb", extract_dir=tmp_path,
                       sha256="e" * 64, reused=False, relays=[],
                       display_name="subesta__o.rdb")

    monkeypatch.setattr(files_handler.rdb_loader, "process_upload_stream",
                        fake_stream)
    h = _harness(tmp_path)
    entry = _upload(h, b"nao e um OLE de verdade",
                    "subestação.rdb").json()["entry"]
    assert entry["name"] == "subestação.rdb"
    with h.session.lock:
        assert h.library().get(entry["sha256"]).rdb.display_name == "subestação.rdb"


def test_downloading_something_the_project_does_not_have_is_404(tmp_path):
    assert _harness(tmp_path).get("/download?sha256=" + "f" * 64).status == 404


def test_downloading_an_entry_whose_file_is_gone_is_410_not_500(tmp_path):
    """410 and not 404: the project still knows the file, it is the disk that
    no longer has it, and the two are different answers."""
    h = _harness(tmp_path)
    sha = _upload(h, _SCD_BYTES).json()["entry"]["sha256"]
    with h.session.lock:
        h.library().get(sha).require_scd_path().unlink()
    assert h.get(f"/download?sha256={sha}").status == 410


def test_download_takes_a_sha_and_never_a_path(tmp_path):
    """There is nothing to sandbox because there is nothing for the client to
    point anywhere: a path in the query is simply not a sha256 in the
    library."""
    h = _harness(tmp_path)
    _upload(h, _SCD_BYTES)
    assert h.get("/download?sha256=../../etc/passwd").status == 404


# -- remove -----------------------------------------------------------------

def test_removing_an_scd_drops_the_entry_and_the_file(tmp_path):
    h = _harness(tmp_path)
    entry = _upload(h, _SCD_BYTES).json()["entry"]
    with h.session.lock:
        path = h.library().get(entry["sha256"]).require_scd_path()
    r = h.post("/remove", {"sha256": entry["sha256"]})
    assert r.status == 200
    with h.session.lock:
        assert h.library().get(entry["sha256"]) is None
    assert not path.exists()


def test_removing_something_that_is_not_there_is_404(tmp_path):
    assert _harness(tmp_path).post("/remove", {"sha256": "f" * 64}).status == 404
