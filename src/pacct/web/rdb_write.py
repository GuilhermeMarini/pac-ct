"""The one place a tool writes bytes back into an RDB.

An RDB is an OLE Compound File, and ``olefile.write_stream`` will only swap a
stream for one of *exactly* the same size. There are two honest ways to meet
that, and this module owns both:

1. Every replacement happens to keep its stream's size. Copy the RDB and
   ``write_stream`` each one. Everything outside the touched streams stays
   byte-identical.
2. Anything changed size. Rebuild the whole container through
   ``cfbwrite``, which reopens its own output and compares
   every stream against the source before handing it over.

There is deliberately no third path that pads a settings file until it fits.
The VB Updater and the GLE Exporter used to have one -- they collapsed
inter-tag whitespace across the whole ``.gle`` and injected a padding
``<!-- -->`` before ``</editor>`` -- which mutates a protection relay's
settings in a way nobody here has confirmed AcSELerator QuickSet tolerates.
A rebuild has no size constraint, so the padding bought nothing that this
module does not give for free.

Both paths write ATOMICALLY onto the destination: the container is built in a
temporary file beside it and only ``os.replace`` puts it at the final name.
A process kill, OOM or power cut mid-write therefore leaves either the
previous destination or nothing there -- never a file that mixes an old and a
new settings map under a name that looks finished.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

import cfbwrite
import olefile

_logger = logging.getLogger(__name__)

StreamPath = tuple[str, ...]


class RdbWriteError(RuntimeError):
    """The output RDB could not be produced. Nothing was written."""


# -----------------------------------------------------------------------------
# Naming and stream resolution
# -----------------------------------------------------------------------------

def with_suffix_before_ext(path: Path, suffix: str) -> Path:
    """``foo.rdb`` + ``_comments_updated`` -> ``foo_comments_updated.rdb``."""
    path = Path(path)
    return path.with_name(path.stem + suffix + path.suffix)


def resolve_gle_stream_path(extract_dir: Path, gle_fs_path: Path,
                            *, fallback: list[str] | None = None) -> list[str]:
    """Where a GLE lives inside the RDB's OLE storage.

    The OLE tree mirrors the extracted filesystem, so the stream path is just
    the extracted file's path relative to the extraction root -- e.g.
    ``Relays/<relay>/Misc/GL1.gle`` becomes
    ``["Relays", "<relay>", "Misc", "GL1.gle"]``.

    ``fallback`` is returned when ``gle_fs_path`` is not under ``extract_dir``
    at all. The VB Updater passes an optimistic guess there because it knows
    the relay and GLE names; the GLE Exporter passes nothing and gets ``[]``,
    which its caller treats as "stream not located". Neither is a normal path:
    an entry that came out of ``RdbInfo`` is always under the extraction.
    """
    try:
        rel = Path(gle_fs_path).resolve().relative_to(Path(extract_dir).resolve())
    except ValueError:
        return list(fallback) if fallback else []
    return list(rel.parts)


# -----------------------------------------------------------------------------
# The write
# -----------------------------------------------------------------------------

def write_streams(src: Path, dst: Path,
                  streams: dict[StreamPath, bytes], job=None) -> str:
    """Write ``dst`` as ``src`` with ``streams`` replaced. Returns the method.

    ``streams`` maps an OLE path (a tuple of names) to the full new contents
    of that stream. Every path must already exist in ``src``: this replaces
    streams, it never creates them.

    Returns ``"in-place"`` or ``"rebuild"`` so the caller can log which path
    ran -- a rebuild legitimately produces a much smaller file (one corpus RDB
    measured 142.9 MB -> 36.9 MB with no edits at all, because QuickSet never
    compacts), and without the method in the log that looks like data loss.

    Raises ``RdbWriteError`` if anything goes wrong. ``dst`` is untouched in
    that case, whether it was absent or an older RDB.
    """
    src, dst = Path(src), Path(dst)
    if not streams:
        raise RdbWriteError("nenhum stream para gravar.")

    try:
        handle = olefile.OleFileIO(str(src))
        try:
            missing = [p for p in streams if not handle.exists(list(p))]
            if missing:
                raise RdbWriteError(
                    "Stream não encontrado no RDB: "
                    + ", ".join("/".join(p) for p in missing))
            fits = all(handle.get_size(list(p)) == len(data)
                       for p, data in streams.items())
        finally:
            handle.close()

        dst.parent.mkdir(parents=True, exist_ok=True)
        if fits:
            if job:
                job.stage("Copiando o RDB", 30)
            _write_in_place(src, dst, streams, job)
            return "in-place"
        if job:
            job.stage("Reconstruindo o RDB", 40)
        cfbwrite.rebuild(src, dst, dict(streams))
        return "rebuild"
    except RdbWriteError:
        raise
    except cfbwrite.CfbWriteError as e:
        raise RdbWriteError(str(e)) from e
    except Exception as e:                       # olefile, disk full, etc.
        _logger.exception("[rdb_write] falha ao gravar %s", dst)
        raise RdbWriteError(f"Falha ao gravar o RDB: {e}") from e


def copy_only(src: Path, dst: Path) -> None:
    """``dst`` as a byte-for-byte copy of ``src``, atomically.

    For the case where every requested edit turned out to be a no-op -- the
    spreadsheet already matched the RDB. The engineer still gets a file, which
    is what happened before this module existed (the copy came first, and no
    stream was rewritten over it), so the flow does not change under them.
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent),
                                    prefix=f".{dst.name}.", suffix=".rdb-tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _write_in_place(src: Path, dst: Path,
                    streams: dict[StreamPath, bytes], job=None) -> None:
    """Copy ``src`` and ``write_stream`` each replacement, atomically onto ``dst``.

    Mirrors the idiom ``cfbwrite.rebuild`` already uses: the result is built
    in a temp file beside ``dst``, and only ``os.replace`` puts it at the final
    name, after every stream has been written. ``dst`` itself is never opened,
    let alone modified, before that last step.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent),
                                    prefix=f".{dst.name}.", suffix=".rdb-tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(src, tmp)
        if job:
            job.stage("Gravando os streams", 70)
        handle = olefile.OleFileIO(str(tmp), write_mode=True)
        try:
            for parts, data in streams.items():
                handle.write_stream(list(parts), data)
        finally:
            handle.close()
        # mkstemp opens at 0600; the finished RDB is an ordinary file.
        os.chmod(tmp, 0o644)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
