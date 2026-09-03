"""Every MMS fixture file must say where it came from.

These fixtures are now a real capture (tools/capture_mms_fixtures.py against
the bench SEL-451-5 R331); they began as a clearly-labelled synthetic stand-in
from tools/synth_mms_fixtures.py, written while the bench was unreachable. The
thing that must never happen is either one being mistaken for the other -- a
stand-in read as live data, or a capture whose relay and FID nobody recorded --
hence this test, which fails loudly if a `provenance` field goes missing or
empty in any of the four files. It asserts presence and not content on
purpose: the tool that writes each kind is what states which kind it is.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "mms"

FIXTURE_FILES = [
    "451_ann_directory.json",
    "451_datadefs.json",
    "451_reads_b64.json",
    "451_expected_stvals.json",
]


@pytest.mark.parametrize("filename", FIXTURE_FILES)
def test_fixture_carries_a_non_empty_provenance(filename):
    path = FIX / filename
    assert path.exists(), f"missing fixture: {filename}"
    data = json.loads(path.read_text())
    assert "provenance" in data, f"{filename} has no top-level 'provenance' field"
    assert isinstance(data["provenance"], str)
    assert data["provenance"].strip(), f"{filename}'s provenance is empty"
