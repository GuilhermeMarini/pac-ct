"""Edit state of the DNP map editor."""

from __future__ import annotations

import threading

from selfiles import dnp_map as set_dnp

from pacct.web.dnp_map import model
from tests.dnp_fixtures import SAMPLE_411L


def _fresh():
    return model.DnpMapState(), threading.RLock()


# Two AI points, each with its own scale and deadband -- enough to prove a
# swap carries them along instead of leaving them at the old index.
SAMPLE_TWO_AI = (
    b"[INFO]\r\n"
    b"RELAYTYPE=SEL-411L-A\r\n"
    b"FID=SEL-411L-A-RXXX-VX-Z022004-DXXXXXXXX\r\n"
    b"BFID=SLBT-4XX-R300-V0-Z001002-D20200229\r\n"
    b"PARTNO=0411LAX6X5C7DDXH5D474XX\r\n"
    b"[D1]\r\n"
    b'AI_1,"IAMAG"\x1c\r\n'
    b'AI_SCA1,"1.0"\x1c\r\n'
    b'AI_DBD1,"0.5"\x1c\r\n'
    b'AI_2,"IBMAG"\x1c\r\n'
    b'AI_SCA2,"2.0"\x1c\r\n'
    b'AI_DBD2,"0.25"\x1c\r\n'
)

# AI_1 has a scale; AI_2 has neither AI_SCA2 nor AI_DBD2 lines at all -- the
# file simply has no slot to hold a scale at that index.
SAMPLE_AI_ONE_SIDED_SCALE = (
    b"[INFO]\r\n"
    b"RELAYTYPE=SEL-411L-A\r\n"
    b"FID=SEL-411L-A-RXXX-VX-Z022004-DXXXXXXXX\r\n"
    b"BFID=SLBT-4XX-R300-V0-Z001002-D20200229\r\n"
    b"PARTNO=0411LAX6X5C7DDXH5D474XX\r\n"
    b"[D1]\r\n"
    b'AI_1,"IAMAG"\x1c\r\n'
    b'AI_SCA1,"1.0"\x1c\r\n'
    b'AI_DBD1,"0.5"\x1c\r\n'
    b'AI_2,"IBMAG"\x1c\r\n'
)


def test_recording_an_edit_stores_only_the_difference():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_411L)
    n = model.record_edits(st, lock, "abc", "R1", "D1",
                           {"BI_1": "PSV22", "BI_2": "IN205"}, original)
    # BI_1 already was PSV22: not an edit.
    assert n == 1
    assert model.edits_for(st, "abc", "R1", "D1") == {"BI_2": "IN205"}


def test_editing_back_to_the_original_clears_the_edit():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_411L)
    model.record_edits(st, lock, "abc", "R1", "D1", {"BI_2": "IN205"}, original)
    model.record_edits(st, lock, "abc", "R1", "D1", {"BI_2": ""}, original)
    assert model.edits_for(st, "abc", "R1", "D1") == {}


def test_apply_edits_produces_the_edited_bytes():
    original = set_dnp.parse(SAMPLE_411L)
    edited = model.apply_edits(original, {"BI_2": "IN205"})
    assert b'BI_2,"IN205"\x1c\r\n' in edited.serialize()
    # The original was not mutated.
    assert b'BI_2,""\x1c\r\n' in original.serialize()


def test_apply_edits_ignores_a_key_the_file_does_not_have():
    original = set_dnp.parse(SAMPLE_411L)
    edited = model.apply_edits(original, {"BI_999": "IN205"})
    assert edited.serialize() == original.serialize()


def test_swap_exchanges_two_values():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_411L)
    model.swap(st, lock, "abc", "R1", "D1", "BI_1", "BI_2", original)
    assert model.edits_for(st, "abc", "R1", "D1") == {
        "BI_1": "", "BI_2": "PSV22",
    }


def test_swap_uses_pending_edits_not_just_the_original():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_411L)
    model.record_edits(st, lock, "abc", "R1", "D1", {"BI_2": "IN205"}, original)
    model.swap(st, lock, "abc", "R1", "D1", "BI_1", "BI_2", original)
    assert model.edits_for(st, "abc", "R1", "D1") == {
        "BI_1": "IN205", "BI_2": "PSV22",
    }


def test_swap_moves_scale_and_deadband_with_an_ai_point():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_TWO_AI)
    model.swap(st, lock, "abc", "R1", "D1", "AI_1", "AI_2", original)
    assert model.edits_for(st, "abc", "R1", "D1") == {
        "AI_1": "IBMAG", "AI_SCA1": "2.0", "AI_DBD1": "0.25",
        "AI_2": "IAMAG", "AI_SCA2": "1.0", "AI_DBD2": "0.5",
    }


def test_swap_of_a_bi_point_is_unaffected_by_scale_handling():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_411L)
    model.swap(st, lock, "abc", "R1", "D1", "BI_1", "BI_2", original)
    assert model.edits_for(st, "abc", "R1", "D1") == {
        "BI_1": "", "BI_2": "PSV22",
    }


def test_swap_with_only_one_side_scaled_leaves_the_scale_where_it_is():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_AI_ONE_SIDED_SCALE)
    model.swap(st, lock, "abc", "R1", "D1", "AI_1", "AI_2", original)
    # The values swap, but AI_2 has no AI_SCA2/AI_DBD2 line to receive a
    # scale, so AI_SCA1/AI_DBD1 are left untouched rather than invented.
    assert model.edits_for(st, "abc", "R1", "D1") == {
        "AI_1": "IBMAG", "AI_2": "IAMAG",
    }


def test_swapping_twice_returns_to_the_original_state():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_TWO_AI)
    model.swap(st, lock, "abc", "R1", "D1", "AI_1", "AI_2", original)
    model.swap(st, lock, "abc", "R1", "D1", "AI_1", "AI_2", original)
    assert model.edits_for(st, "abc", "R1", "D1") == {}


def test_dirty_summary_lists_relays_with_pending_edits():
    st, lock = _fresh()
    original = set_dnp.parse(SAMPLE_411L)
    model.record_edits(st, lock, "abc", "R1", "D1", {"BI_2": "IN205"}, original)
    model.record_edits(st, lock, "abc", "R2", "D3", {"BI_1": "LOP"}, original)
    summary = model.dirty_summary(st, "abc", lock)
    assert summary == [
        {"relay": "R1", "sessions": {"D1": 1}, "total": 1},
        {"relay": "R2", "sessions": {"D3": 1}, "total": 1},
    ]


def test_dirty_summary_is_empty_for_an_untouched_rdb():
    st, lock = _fresh()
    assert model.dirty_summary(st, "abc", lock) == []


def _setd(section: str, mindist: str, bi1: str, bi2: str) -> bytes:
    """A SET_D fixture in the shape of SAMPLE_411L, with MINDIST/BI_1/BI_2
    free to vary so tests can make a source and a target genuinely differ.
    """
    return (
        b"[INFO]\r\n"
        b"RELAYTYPE=SEL-411L-A\r\n"
        b"FID=SEL-411L-A-RXXX-VX-Z022004-DXXXXXXXX\r\n"
        b"BFID=SLBT-4XX-R300-V0-Z001002-D20200229\r\n"
        b"PARTNO=0411LAX6X5C7DDXH5D474XX\r\n"
        + f"[{section}]\r\n".encode("latin-1")
        + f'MINDIST,"{mindist}"'.encode("latin-1") + b"\x1c\r\n"
        + b'MAXDIST,"10000.0"\x1c\r\n'
        + f'BI_1,"{bi1}"'.encode("latin-1") + b"\x1c\r\n"
        + f'BI_2,"{bi2}"'.encode("latin-1") + b"\x1c\r\n"
        + b'AI_1,"IAMAG"\x1c\r\n'
        + b'AI_SCA1,"1.0"\x1c\r\n'
        + b'AI_DBD1,"0.5"\x1c\r\n'
        + b'CO_1,""\x1c\r\n'
        + b'CO_DBD1,""\x1c\r\n'
    )


def test_copy_session_copies_points_but_not_read_only_extras():
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    # D2's MINDIST differs from the source's -- it must stay untouched, since
    # MINDIST configures the DNP session, not the map.
    d2 = set_dnp.parse(_setd("D2", "2.0", "OTHER", ""))
    # D3 already reads exactly like the source: it should gain no edits.
    d3 = set_dnp.parse(_setd("D3", "1.0", "PSV22", ""))
    parsed = {"D1": src, "D2": d2, "D3": d3}

    touched = model.copy_session(st, lock, "abc", "R1", "D1", ["D2", "D3"], parsed)

    assert touched == 1
    assert model.edits_for(st, "abc", "R1", "D2") == {"BI_1": "PSV22"}
    assert model.edits_for(st, "abc", "R1", "D3") == {}


def test_copy_session_copies_the_source_pending_edits_not_its_disk_values():
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    d2 = set_dnp.parse(_setd("D2", "1.0", "PSV22", ""))
    parsed = {"D1": src, "D2": d2}

    model.record_edits(st, lock, "abc", "R1", "D1", {"BI_1": "IN205"}, src)
    model.copy_session(st, lock, "abc", "R1", "D1", ["D2"], parsed)

    assert model.edits_for(st, "abc", "R1", "D2") == {"BI_1": "IN205"}


def test_copy_session_does_not_modify_the_source_session():
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    d2 = set_dnp.parse(_setd("D2", "1.0", "OTHER", ""))
    parsed = {"D1": src, "D2": d2}

    model.record_edits(st, lock, "abc", "R1", "D1", {"BI_2": "IN205"}, src)
    before = model.edits_for(st, "abc", "R1", "D1")
    model.copy_session(st, lock, "abc", "R1", "D1", ["D2"], parsed)

    assert model.edits_for(st, "abc", "R1", "D1") == before


# A file with only BI_1 -- fewer points than `_setd`, to make a copy that does
# not line up measurable instead of hypothetical.
def _setd_curto(section: str, bi1: str) -> bytes:
    return (
        b"[INFO]\r\n"
        b"RELAYTYPE=SEL-411L-A\r\n"
        + f"[{section}]\r\n".encode("latin-1")
        + f'BI_1,"{bi1}"'.encode("latin-1") + b"\x1c\r\n"
    )


def test_copy_to_another_relay_records_the_edits_under_that_relay():
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    outra_d1 = set_dnp.parse(_setd("D1", "2.0", "OTHER", ""))
    outra_d2 = set_dnp.parse(_setd("D2", "2.0", "PSV22", ""))
    parsed = {"R1": {"D1": src},
              "R2": {"D1": outra_d1, "D2": outra_d2}}

    outcomes = model.copy_map_to(st, lock, "abc", "R1", "D1", src, "abc",
                                 [("R2", "D1"), ("R2", "D2")], parsed)

    assert [(o.relay, o.session, o.touched) for o in outcomes] == [
        ("R2", "D1", 1), ("R2", "D2", 0),
    ]
    assert model.edits_for(st, "abc", "R2", "D1") == {"BI_1": "PSV22"}
    # R2/D2 already read like the source: no edits, and nothing dirty.
    assert model.edits_for(st, "abc", "R2", "D2") == {}
    assert model.dirty_summary(st, "abc", lock) == [
        {"relay": "R2", "sessions": {"D1": 1}, "total": 1},
    ]


def test_copy_to_another_relay_carries_the_source_pending_edits():
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    alvo = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    parsed = {"R1": {"D1": src}, "R2": {"D1": alvo}}

    model.record_edits(st, lock, "abc", "R1", "D1", {"BI_1": "IN205"}, src)
    model.copy_map_to(st, lock, "abc", "R1", "D1", src, "abc",
                      [("R2", "D1")], parsed)

    assert model.edits_for(st, "abc", "R2", "D1") == {"BI_1": "IN205"}


def test_copy_to_another_relay_does_not_touch_the_source():
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    alvo = set_dnp.parse(_setd("D1", "1.0", "OTHER", ""))
    parsed = {"R1": {"D1": src}, "R2": {"D1": alvo}}

    model.record_edits(st, lock, "abc", "R1", "D1", {"BI_2": "IN205"}, src)
    before = model.edits_for(st, "abc", "R1", "D1")
    model.copy_map_to(st, lock, "abc", "R1", "D1", src, "abc",
                      [("R2", "D1")], parsed)

    assert model.edits_for(st, "abc", "R1", "D1") == before


def test_copy_to_another_relay_does_not_copy_read_only_extras():
    st, lock = _fresh()
    # The target's MINDIST differs from the source's; it configures the DNP
    # session, not the map, and must survive the copy untouched.
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    alvo = set_dnp.parse(_setd("D1", "9.0", "OTHER", ""))
    parsed = {"R1": {"D1": src}, "R2": {"D1": alvo}}

    model.copy_map_to(st, lock, "abc", "R1", "D1", src, "abc",
                      [("R2", "D1")], parsed)

    assert "MINDIST" not in model.edits_for(st, "abc", "R2", "D1")


def test_copy_counts_the_points_that_did_not_line_up():
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    curto = set_dnp.parse(_setd_curto("D1", "OTHER"))
    parsed = {"R1": {"D1": src}, "R2": {"D1": curto}}

    (o,) = model.copy_map_to(st, lock, "abc", "R1", "D1", src, "abc",
                             [("R2", "D1")], parsed)

    # BI_1 landed; BI_2, AI_1, AI_SCA1, AI_DBD1, CO_1 and CO_DBD1 had nowhere
    # to go, and the target has no point the source lacks.
    assert (o.touched, o.missing, o.extra) == (1, 6, 0)
    assert model.edits_for(st, "abc", "R2", "D1") == {"BI_1": "PSV22"}


def test_copy_counts_the_target_points_the_source_never_mentions():
    st, lock = _fresh()
    curto = set_dnp.parse(_setd_curto("D1", "PSV22"))
    alvo = set_dnp.parse(_setd("D1", "1.0", "OTHER", ""))
    parsed = {"R1": {"D1": curto}, "R2": {"D1": alvo}}

    (o,) = model.copy_map_to(st, lock, "abc", "R1", "D1", curto, "abc",
                             [("R2", "D1")], parsed)

    assert (o.touched, o.missing, o.extra) == (1, 0, 6)


def test_copy_skips_the_source_session_and_an_unknown_target():
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    alvo = set_dnp.parse(_setd("D1", "1.0", "OTHER", ""))
    parsed = {"R1": {"D1": src}, "R2": {"D1": alvo}}

    outcomes = model.copy_map_to(
        st, lock, "abc", "R1", "D1", src, "abc",
        [("R1", "D1"), ("R9", "D1"), ("R2", "D9"), ("R2", "D1")], parsed)

    assert [(o.relay, o.session) for o in outcomes] == [("R2", "D1")]


def test_copy_into_a_different_rdb_records_under_that_rdb():
    """The destination RDB is not the source's: the edits belong to the
    destination's own pending list and export, and the source RDB stays
    clean."""
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    alvo = set_dnp.parse(_setd("D1", "1.0", "OTHER", ""))

    (o,) = model.copy_map_to(st, lock, "semana-passada", "R1", "D1", src,
                             "hoje", [("R1", "D1")], {"R1": {"D1": alvo}})

    assert (o.relay, o.session, o.touched) == ("R1", "D1", 1)
    assert model.edits_for(st, "hoje", "R1", "D1") == {"BI_1": "PSV22"}
    # Same relay name and same session name in the other RDB: it is only the
    # rdb key that tells them apart, and the source must not be written.
    assert model.edits_for(st, "semana-passada", "R1", "D1") == {}
    assert model.dirty_summary(st, "semana-passada", lock) == []
    assert model.dirty_summary(st, "hoje", lock) == [
        {"relay": "R1", "sessions": {"D1": 1}, "total": 1},
    ]


def test_the_same_relay_and_session_in_another_rdb_is_a_real_target():
    """The skip that protects the source is keyed on the rdb too. Copying
    R1/D1 onto R1/D1 of a DIFFERENT RDB is the normal "same substation, newer
    file" case, and must not be mistaken for copying onto itself."""
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    mesmo_nome = set_dnp.parse(_setd("D1", "1.0", "OUTRO", ""))

    outcomes = model.copy_map_to(st, lock, "a", "R1", "D1", src, "b",
                                 [("R1", "D1")], {"R1": {"D1": mesmo_nome}})

    assert [(o.relay, o.session, o.touched) for o in outcomes] == [
        ("R1", "D1", 1),
    ]


def test_copying_a_subset_leaves_the_other_points_alone():
    """O passo 2 do assistente manda as chaves marcadas; o que ficou de fora
    fica como esta no destino -- uma copia nunca apaga ponto."""
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", "TRIP"))
    alvo = set_dnp.parse(_setd("D1", "1.0", "OUTRO", "OUTRO2"))

    (o,) = model.copy_map_to(st, lock, "abc", "R1", "D1", src, "abc",
                             [("R2", "D1")], {"R2": {"D1": alvo}},
                             point_keys={"BI_1"})

    assert model.edits_for(st, "abc", "R2", "D1") == {"BI_1": "PSV22"}
    assert o.touched == 1
    # BI_2 nao foi pedido: nao e' desencontro, e' escolha de quem copiou.
    assert (o.missing, o.extra) == (0, 0)


def test_a_chosen_point_carries_its_scale_and_deadband():
    """A escala e' atributo da grandeza mapeada, nao um ponto a parte: marcar
    AI_1 leva AI_SCA1/AI_DBD1 junto, como o arraste do editor."""
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    alvo = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    # o destino difere so' na escala do AI_1
    alvo.set_value("AI_SCA1", "9.9")

    (o,) = model.copy_map_to(st, lock, "abc", "R1", "D1", src, "abc",
                             [("R2", "D1")], {"R2": {"D1": alvo}},
                             point_keys={"AI_1"})

    assert model.edits_for(st, "abc", "R2", "D1") == {"AI_SCA1": "1.0"}
    assert o.touched == 1


def test_an_empty_selection_copies_nothing():
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", ""))
    alvo = set_dnp.parse(_setd("D1", "1.0", "OUTRO", ""))

    (o,) = model.copy_map_to(st, lock, "abc", "R1", "D1", src, "abc",
                             [("R2", "D1")], {"R2": {"D1": alvo}},
                             point_keys=set())

    assert model.edits_for(st, "abc", "R2", "D1") == {}
    assert o.touched == 0


def test_no_selection_still_means_the_whole_map():
    """`point_keys=None` e' o mapa inteiro -- e' assim que `copy_session` e o
    caminho antigo continuam funcionando."""
    st, lock = _fresh()
    src = set_dnp.parse(_setd("D1", "1.0", "PSV22", "TRIP"))
    alvo = set_dnp.parse(_setd("D1", "1.0", "OUTRO", "OUTRO2"))

    model.copy_map_to(st, lock, "abc", "R1", "D1", src, "abc",
                      [("R2", "D1")], {"R2": {"D1": alvo}})

    assert model.edits_for(st, "abc", "R2", "D1") == {
        "BI_1": "PSV22", "BI_2": "TRIP",
    }
