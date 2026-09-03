"""The project file library: dedup, kind detection, removal."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from pacct.web.project_files import library
from pacct.web.session import SessionManager


def _entry(sha: str, kind: str = "rdb", name: str = "projeto.rdb",
           size: int = 10, **kw) -> library.FileEntry:
    return library.FileEntry(sha256=sha, kind=kind, display_name=name,
                             size=size, uploaded_at=time.time(), **kw)


# -- kind detection ---------------------------------------------------------

def test_kind_comes_from_the_extension():
    assert library.kind_for("projeto.rdb") == library.KIND_RDB
    assert library.kind_for("subestacao.scd") == library.KIND_SCD
    assert library.kind_for("subestacao.xml") == library.KIND_SCD


def test_kind_detection_ignores_case():
    assert library.kind_for("PROJETO.RDB") == library.KIND_RDB
    assert library.kind_for("Subestacao.SCD") == library.KIND_SCD


def test_unknown_extension_is_not_a_project_file():
    assert library.kind_for("planilha.xlsx") is None
    assert library.kind_for("perfil.zip") is None
    assert library.kind_for("") is None


def test_each_kind_has_its_own_ceiling():
    assert library.max_bytes_for(library.KIND_RDB) == 500 * 1024 * 1024
    assert library.max_bytes_for(library.KIND_SCD) == 200 * 1024 * 1024


# -- dedup ------------------------------------------------------------------

def test_the_same_content_twice_is_one_entry():
    lib = library.FileLibrary()
    first, dup = lib.add(_entry("a" * 64, name="projeto.rdb"))
    assert dup is False
    second, dup = lib.add(_entry("a" * 64, name="copia-do-projeto.rdb"))
    assert dup is True
    assert second is first
    assert len(lib.entries) == 1


def test_the_first_name_wins_on_a_duplicate():
    lib = library.FileLibrary()
    lib.add(_entry("a" * 64, name="projeto.rdb"))
    entry, _ = lib.add(_entry("a" * 64, name="outro-nome.rdb"))
    assert entry.display_name == "projeto.rdb"


def test_same_name_different_content_are_two_entries():
    lib = library.FileLibrary()
    lib.add(_entry("a" * 64, name="projeto.rdb"))
    lib.add(_entry("b" * 64, name="projeto.rdb"))
    assert len(lib.entries) == 2
    assert [e.sha256 for e in lib.list()] == ["a" * 64, "b" * 64]


def test_listing_filters_by_kind():
    lib = library.FileLibrary()
    lib.add(_entry("a" * 64, kind=library.KIND_RDB, name="projeto.rdb"))
    lib.add(_entry("b" * 64, kind=library.KIND_SCD, name="sub.scd"))
    assert [e.display_name for e in lib.list(library.KIND_RDB)] == ["projeto.rdb"]
    assert [e.display_name for e in lib.list(library.KIND_SCD)] == ["sub.scd"]
    assert len(lib.list()) == 2


# -- paths and payload ------------------------------------------------------

def test_the_scd_filename_comes_from_the_hash(tmp_path):
    p = library.scd_path_for(tmp_path, "c" * 64)
    assert p == tmp_path / ("c" * 12 + ".scd")


def test_the_payload_carries_what_the_listing_shows():
    e = _entry("d" * 64, name="projeto.rdb", size=1234)
    e.detail = "12 relés"
    payload = e.to_json()
    assert payload["sha256"] == "d" * 64
    assert payload["short_sha"] == "d" * 12
    assert payload["kind"] == "rdb"
    assert payload["name"] == "projeto.rdb"
    assert payload["size"] == 1234
    assert payload["detail"] == "12 relés"
    # The RdbInfo and the on-disk path never cross to the browser.
    assert "rdb" not in payload
    assert "scd_path" not in payload


# -- removal ----------------------------------------------------------------

def test_removing_an_scd_drops_the_entry_and_the_file(tmp_path):
    lib = library.FileLibrary()
    scd = tmp_path / "abc.scd"
    scd.write_bytes(b"<SCL/>")
    lib.add(_entry("e" * 64, kind=library.KIND_SCD, name="sub.scd",
                   path=scd))
    removed = lib.remove("e" * 64)
    assert removed is not None
    assert removed.display_name == "sub.scd"
    assert lib.entries == {}
    assert not scd.exists()


def test_removing_an_rdb_leaves_the_shared_extraction_alone(tmp_path):
    """cache/rdb/<sha>/ has no owner: it is shared between visitors and swept
    by age. One visitor tidying their project must not delete it."""
    lib = library.FileLibrary()
    extraction = tmp_path / ("f" * 64)
    extraction.mkdir()
    (extraction / "source.rdb").write_bytes(b"x")
    lib.add(_entry("f" * 64, kind=library.KIND_RDB, name="projeto.rdb"))
    lib.remove("f" * 64)
    assert lib.entries == {}
    assert (extraction / "source.rdb").exists()


def test_removing_an_unknown_sha_is_not_an_error():
    lib = library.FileLibrary()
    assert lib.remove("0" * 64) is None


def test_removing_an_scd_whose_file_is_already_gone_is_not_an_error(tmp_path):
    lib = library.FileLibrary()
    lib.add(_entry("9" * 64, kind=library.KIND_SCD, name="sub.scd",
                   path=tmp_path / "nao-existe.scd"))
    assert lib.remove("9" * 64) is not None


# -- one library per visitor, shared by every tool --------------------------

def test_every_tool_in_a_session_sees_the_same_library(tmp_path):
    mgr = SessionManager(root=tmp_path, logger=logging.getLogger("test"))
    sess, _ = mgr.resolve(None)
    # Two different tools asking: same object, or the library is not shared.
    assert library.library_for(mgr, sess) is library.library_for(mgr, sess)


def test_two_visitors_have_two_libraries(tmp_path):
    mgr = SessionManager(root=tmp_path, logger=logging.getLogger("test"))
    a, _ = mgr.resolve(None)
    b, _ = mgr.resolve(None)
    assert library.library_for(mgr, a) is not library.library_for(mgr, b)


def test_the_library_directory_is_not_prefixed_by_a_tool_key(tmp_path):
    """Session.subdir, not SessionHandler.sdir -- the latter prefixes the
    caller's session_key and would hand each tool its own directory."""
    mgr = SessionManager(root=tmp_path, logger=logging.getLogger("test"))
    sess, _ = mgr.resolve(None)
    d = library.files_dir(sess)
    assert d.name == "files"
    assert d.parent == sess.dir
    assert d.is_dir()


# -- the page ---------------------------------------------------------------

def test_the_nav_marker_sits_inside_the_shell_not_the_header():
    """In régua, `.shell` is a two-column grid whose first column IS the nav.
    A marker left in `<header>` collapses the page to about 200px wide."""
    from pacct.web.project_files import handler as pf_handler

    html = pf_handler.LIBRARY_HTML
    shell = html.index('<div class="shell">')
    marker = html.index("<!--NAV:files-->")
    header_end = html.index("</header>")
    assert marker > shell
    assert marker > header_end


def test_the_page_never_bakes_a_nav_at_import_time():
    """The three directions do not share nav markup; resolving the marker
    here would freeze one direction's markup into all three."""
    from pacct.web.project_files import handler as pf_handler

    html = pf_handler.LIBRARY_HTML
    for frozen in ('class="toc"', 'class="strip"', 'class="tabs"'):
        assert frozen not in html


def test_the_handler_owns_the_library_key(tmp_path):
    from pacct.web.project_files import handler as pf_handler

    log = logging.getLogger("test")
    mgr = SessionManager(root=tmp_path, logger=log)
    cls = pf_handler.build_project_files_handler(log, mgr)
    assert cls.session_key == library.LIBRARY_KEY
    assert cls.state_factory is library.FileLibrary


# -- classifying a corrupt/wrong RDB as a client error, not a server one -----

def test_ole_file_error_subclasses_os_error():
    """The trap: an `except OSError` placed before `except OleFileError`
    silently swallows the exact case the latter was written for.

    Imported from the `olefile.olefile` submodule, not the `olefile` package
    itself: this pinned version (0.47) does not re-export `OleFileError` at
    the top level (`__all__` in `olefile/olefile.py` omits it)."""
    from olefile.olefile import OleFileError

    assert issubclass(OleFileError, OSError)


def test_a_non_ole_file_is_a_client_error_not_a_server_error():
    """olefile.NotOleFileError subclasses OSError, so an `except OSError`
    placed before `except OleFileError` silently turns the commonest user
    mistake -- a corrupt or wrong file -- into an HTTP 500."""
    import pytest
    from olefile.olefile import OleFileError
    from selfiles import rdb as rdb_loader

    with pytest.raises(OleFileError):
        rdb_loader.process_upload(b"nao sou um OLE2" * 100, "lixo.rdb")


# -- the shared runtime -----------------------------------------------------

def test_the_runtime_goes_in_before_the_closing_body():
    from pacct.web.project_files import client

    html = "<html><body><p>oi</p></body></html>"
    out = client.inject_library_runtime(html)
    assert out.index("SelLibrary") < out.index("</body>")
    assert out.count("</body>") == 1


def test_the_runtime_survives_a_page_without_a_body_tag():
    from pacct.web.project_files import client

    out = client.inject_library_runtime("<p>oi</p>")
    assert "SelLibrary" in out


def test_the_runtime_refuses_to_define_itself_twice():
    """Every page gets it injected; a tool that also inlined it must not
    clobber a picker that is already mounted."""
    from pacct.web.project_files import client

    assert "if (window.SelLibrary) return;" in client.LIBRARY_JS


def test_the_picker_links_the_tab_relatively():
    """A cross-page link is one of the two things the fetch shim cannot
    reach, so it must be relative and not absolute."""
    from pacct.web.project_files import client

    assert "../files/" in client.LIBRARY_JS
    assert 'href="/files/"' not in client.LIBRARY_JS


# -- what a tool generated, entering the project ----------------------------

def _session(tmp_path):
    mgr = SessionManager(root=tmp_path, logger=logging.getLogger("test"))
    sess, _ = mgr.resolve(None)
    return mgr, sess


def test_a_spreadsheet_is_an_output_kind_and_never_an_upload():
    """`kind_for` is what /upload validates against and must keep refusing a
    spreadsheet; `kind_for_output` is what a tool's own file goes through."""
    from pacct.web.project_files import derived

    assert library.kind_for("planilha.xlsx") is None
    assert derived.kind_for_output("planilha.xlsx") == library.KIND_XLSX
    assert derived.kind_for_output("projeto.rdb") == library.KIND_RDB
    assert derived.kind_for_output("perfil.zip") is None


def test_a_generated_scd_enters_the_library_with_its_origin(tmp_path):
    from pacct.web.project_files import derived

    mgr, sess = _session(tmp_path)
    src = tmp_path / "sub_comments_updated.scd"
    src.write_bytes(b"<SCL><IED name='X'/></SCL>")

    entry, dup, err = derived.adopt(mgr, sess, src, origin="VB Updater")
    assert err == ""
    assert dup is False
    assert entry.kind == library.KIND_SCD
    assert entry.generated is True
    assert entry.origin == "VB Updater"
    # `detail` says what is inside the file; provenance is its own field.
    assert entry.detail == "1 IED(s)"
    assert "gerado" not in entry.detail
    # The bytes landed in the library's own directory, named by the hash.
    assert entry.path.parent == library.files_dir(sess)
    assert entry.path.name.endswith(".scd")
    assert entry.path.read_bytes() == src.read_bytes()
    # And the tools can see it.
    assert library.library_for(mgr, sess).list(library.KIND_SCD) == [entry]


def test_adopting_the_same_output_twice_is_one_entry(tmp_path):
    from pacct.web.project_files import derived

    mgr, sess = _session(tmp_path)
    src = tmp_path / "sub.scd"
    src.write_bytes(b"<SCL><IED name='X'/></SCL>")

    first, dup, _ = derived.adopt(mgr, sess, src, origin="VB Updater")
    second, dup2, _ = derived.adopt(mgr, sess, src, origin="VB Updater")
    assert dup is False and dup2 is True
    assert second is first
    assert len(library.library_for(mgr, sess).entries) == 1


def test_a_generated_spreadsheet_is_kept_but_no_picker_asks_for_it(tmp_path):
    from pacct.web.project_files import derived

    mgr, sess = _session(tmp_path)
    src = tmp_path / "comments.xlsx"
    src.write_bytes(b"PK\x03\x04not-really-a-zip")

    entry, _, err = derived.adopt(mgr, sess, src, origin="GLE Exporter")
    assert err == ""
    assert entry.kind == library.KIND_XLSX
    lib = library.library_for(mgr, sess)
    assert lib.list(library.KIND_RDB) == []
    assert lib.list(library.KIND_SCD) == []
    assert lib.list() == [entry]


def test_an_output_that_cannot_be_adopted_never_raises(tmp_path):
    """Adoption is best-effort: the export that triggered it already
    succeeded, and must not fail because the library refused the file."""
    from pacct.web.project_files import derived

    mgr, sess = _session(tmp_path)
    unknown = tmp_path / "relatorio.txt"
    unknown.write_bytes(b"oi")
    entry, dup, err = derived.adopt(mgr, sess, unknown, origin="X")
    assert entry is None and dup is False and err

    empty = tmp_path / "vazio.scd"
    empty.write_bytes(b"")
    entry, _, err = derived.adopt(mgr, sess, empty, origin="X")
    assert entry is None and err

    entry, _, err = derived.adopt(mgr, sess, tmp_path / "nao-existe.scd",
                                  origin="X")
    assert entry is None and err


def test_the_payload_says_whether_a_file_was_generated():
    e = _entry("d" * 64, kind=library.KIND_SCD, name="sub.scd")
    assert e.to_json()["generated"] is False
    assert e.to_json()["origin"] == ""
    g = _entry("e" * 64, kind=library.KIND_SCD, name="sub.scd",
               origin="DNP Map Editor")
    assert g.to_json()["generated"] is True
    assert g.to_json()["origin"] == "DNP Map Editor"


def test_the_download_source_follows_the_kind(tmp_path):
    """An SCD lives in the session's files/; an RDB lives in the shared
    content cache and has no session path at all."""
    from selfiles.rdb import RdbInfo

    scd = tmp_path / "abc.scd"
    e = _entry("a" * 64, kind=library.KIND_SCD, name="sub.scd", path=scd)
    assert e.file_path() == scd
    assert e.scd_path == scd

    cached = tmp_path / "source.rdb"
    info = RdbInfo(rdb_path=cached, extract_dir=tmp_path, sha256="b" * 64,
                   reused=True, relays=[], display_name="projeto.rdb")
    r = _entry("b" * 64, kind=library.KIND_RDB, name="projeto.rdb", rdb=info)
    assert r.file_path() == cached
    # `scd_path` is for SCDs only -- an RDB answering it would send a tool
    # looking for a file that is not in this session.
    assert r.scd_path is None


# -- re-uploading a file the project already has ----------------------------
#
# The upload writes first and checks for a duplicate after, because the sha256
# is only known once the last byte has arrived. That ordering is fine on its
# own -- `files/<sha12>.scd` is named after the content, so the second write
# rewrites the first byte for byte -- but it means the file the second upload
# "built" and the file the library is already pointing at are ONE FILE. The
# duplicate branch used to delete it, which left the entry listed with nothing
# behind it: the picker still offered the SCD and every tool that resolves
# `scd_path` answered "Arquivo não está mais no projeto".

def _handler_class(tmp_path):
    from pacct.web.project_files.handler import build_project_files_handler
    mgr = SessionManager(root=tmp_path, logger=logging.getLogger("test"))
    return build_project_files_handler(logging.getLogger("test"), mgr)


def test_re_uploading_an_scd_keeps_the_file_the_library_points_at(tmp_path):
    Handler = _handler_class(tmp_path)
    sha = "7" * 64
    scd = library.scd_path_for(tmp_path, sha)
    scd.write_bytes(b"<SCL/>")

    existing = _entry(sha, kind=library.KIND_SCD, name="sub.scd", path=scd)
    # What the second upload of the same bytes builds: the same path, because
    # the name is the hash.
    rebuilt = _entry(sha, kind=library.KIND_SCD, name="sub.scd", path=scd)

    Handler._discard(None, rebuilt, keep=existing)
    assert scd.exists(), "the existing entry's SCD was deleted by a re-upload"


def test_discarding_an_scd_no_entry_owns_still_deletes_it(tmp_path):
    """The other half: a build that never reached the library leaves nothing
    behind. Only a file the library is pointing at is spared."""
    Handler = _handler_class(tmp_path)
    orphan = tmp_path / "abc123456789.scd"
    orphan.write_bytes(b"<SCL/>")
    Handler._discard(None, _entry("8" * 64, kind=library.KIND_SCD,
                                  name="sub.scd", path=orphan))
    assert not orphan.exists()


def test_a_duplicate_rdb_never_touches_the_shared_extraction(tmp_path):
    """An RDB's bytes live in `cache/rdb/<sha>/`, which has no owner. The
    duplicate branch must not reach into it -- deleting it would pull the
    extraction out from under every other visitor holding the same file."""
    Handler = _handler_class(tmp_path)
    cached = tmp_path / ("a" * 64)
    cached.mkdir()
    (cached / "source.rdb").write_bytes(b"x")
    Handler._discard(None, _entry("a" * 64, kind=library.KIND_RDB),
                     keep=_entry("a" * 64, kind=library.KIND_RDB))
    assert (cached / "source.rdb").exists()


def test_the_duplicate_branch_tells_discard_what_to_keep(tmp_path):
    """The guard only works if the call site hands `keep` over. It is one
    keyword on one line and reverting it is silent -- the entry stays on
    screen and only the file goes -- so it is pinned here."""
    from pacct.web.project_files import handler as h
    src = Path(h.__file__).read_text(encoding="utf-8")
    assert "self._discard(entry, keep=existing)" in src


# -- both kinds count the same unit -----------------------------------------
#
# The listing put an RDB's `27 relé(s)` next to an SCD's `30 IED(s)`, in the
# same column of the same table, for two files of the same substation. Two
# words for the device is a question the screen should not be asking: a SEL
# relay in an RDB IS an IED. `detail` says what is inside the file, so it says
# it the same way whatever the file is.

def _minimal_rdb(tmp_path) -> bytes:
    """An RDB with two relays that own a `.gle` and one that does not.

    The odd one out is the point: `RdbInfo.relays` is built from the `.gle`
    streams, so a concentrator with settings and no diagram is not in the
    count -- see the gotcha in docs/ENGINEERING-NOTES.md. The label changed; the population
    it counts did not.
    """
    import cfbwrite as cfb

    from tests import gle_fixtures as fx

    def stream(name, data):
        return cfb.Entry(name=name, is_storage=False, size=len(data),
                         read=lambda d=data: d, children=[])

    def storage(name, children):
        return cfb.Entry(name=name, is_storage=True, size=0, read=None,
                         children=list(children))

    path = tmp_path / "SE_TESTE.rdb"
    cfb.write_ole(path, [
        storage("Relays", [
            storage("QPC1_LT1_UPC1", [
                storage("Misc", [stream("GL1.gle", fx.SAMPLE_GLE)]),
            ]),
            storage("QPC1_LT2_UPC1", [
                storage("Misc", [stream("GL1.gle", fx.SAMPLE_GLE)]),
            ]),
            storage("QPC1_CONC", [
                stream("SET_1.TXT", b'RID,"QPC1_CONC"\x1c\r\n'),
            ]),
        ]),
    ])
    return path.read_bytes()


def test_an_rdbs_detail_counts_ieds_like_an_scds_does(tmp_path):
    from pacct.web.project_files import derived

    mgr, sess = _session(tmp_path)
    rdb_src = tmp_path / "projeto.rdb"
    rdb_src.write_bytes(_minimal_rdb(tmp_path))
    scd_src = tmp_path / "projeto.scd"
    scd_src.write_bytes(b"<SCL><IED name='A'/><IED name='B'/></SCL>")

    rdb_entry, _, err = derived.adopt(mgr, sess, rdb_src, origin="Teste")
    assert err == ""
    scd_entry, _, err = derived.adopt(mgr, sess, scd_src, origin="Teste")
    assert err == ""

    # The two rows of the listing, side by side, in the same unit.
    assert rdb_entry.detail == "2 IED(s)"
    assert scd_entry.detail == "2 IED(s)"
    assert "relé" not in rdb_entry.detail


def test_neither_producer_says_rele_any_more():
    """The upload path builds its entry inside a handler closure, around a
    real request -- cheaper to pin the source than to stand up a POST just to
    read one f-string. The two producers are one line each and drift apart
    silently: the listing is the only place they meet."""
    from pacct.web.project_files import derived
    from pacct.web.project_files import handler as h
    for mod in (h, derived):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "relé(s)" not in src, f"{mod.__name__} still counts relés"
        assert "IED(s)" in src
