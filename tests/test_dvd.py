import struct

import pytest
from dvd_fixture import SECTOR, write_dvd

from app import dvd


def test_single_file_source(tmp_path):
    path = tmp_path / "film.ts"
    path.write_bytes(b"transport stream")
    source = dvd.inspect_source(path)
    assert source.kind == "file"
    assert source.default_title == 1
    assert source.title().size_bytes == path.stat().st_size
    assert source.summary()["titles"][0]["duration_seconds"] == 0


def test_empty_and_unsupported_files(tmp_path):
    empty = tmp_path / "empty.vob"
    empty.touch()
    with pytest.raises(dvd.DvdError, match="leeg"):
        dvd.inspect_source(empty)
    other = tmp_path / "film.mkv"
    other.write_bytes(b"x")
    with pytest.raises(dvd.DvdError, match="Kies een VIDEO_TS"):
        dvd.inspect_source(other)


def test_symlink_source_rejected(tmp_path):
    target = tmp_path / "film.ts"
    target.write_bytes(b"x")
    link = tmp_path / "link.ts"
    link.symlink_to(target)
    with pytest.raises(dvd.DvdError, match="symlink"):
        dvd.inspect_source(link)


def test_audio_ts_is_explained(tmp_path):
    folder = tmp_path / "AUDIO_TS"
    folder.mkdir()
    source = dvd.inspect_source(folder)
    assert source.titles == ()
    assert source.default_title == 0
    assert "VIDEO_TS" in source.note


def test_video_ts_summary_and_case_insensitive_lookup(tmp_path):
    directory = write_dvd(tmp_path)
    (directory / "VIDEO_TS.IFO").rename(directory / "video_ts.ifo")
    (directory / "VTS_01_0.IFO").rename(directory / "vts_01_0.ifo")
    (directory / "VTS_01_1.VOB").rename(directory / "vts_01_1.vob")
    source = dvd.inspect_source(tmp_path)
    assert source.label == tmp_path.name
    summary = source.summary()
    assert summary["kind"] == "dvd"
    assert summary["title_count"] == 1
    assert summary["default_title"] == 1
    assert summary["titles"][0]["duration_seconds"] == 2.0
    assert source.title().segments[0].size == 2 * SECTOR


def test_longest_title_and_ptt_pgcn(tmp_path):
    write_dvd(
        tmp_path,
        cells=[(1, 1, 0, 1, 0, 2), (1, 2, 2, 5, 0, 9)],
        pgcs=[([0], 0), ([1], 0)],
        titles=[(1, 1, 1), (2, 2, 4)],
        vob_sectors={1: 6},
    )
    source = dvd.inspect_source(tmp_path / "VIDEO_TS")
    assert source.default_title == 2
    assert source.title(2).duration == 9
    assert source.title(2).chapters == 4
    with pytest.raises(dvd.DvdError, match="geldige titel"):
        source.title(3)


def test_segments_span_vob_files(tmp_path):
    directory = write_dvd(
        tmp_path,
        cells=[(1, 1, 1, 2, 0, 2)],
        vob_sectors={1: 2, 2: 2},
    )
    title = dvd.inspect_source(directory).title()
    assert title.segments == (
        dvd.Segment(directory / "VTS_01_1.VOB", SECTOR, SECTOR),
        dvd.Segment(directory / "VTS_01_2.VOB", 0, SECTOR),
    )


def test_angle_block_keeps_only_first_angle(tmp_path):
    first, middle, last = 0x50, 0x90, 0xD0
    directory = write_dvd(
        tmp_path,
        cells=[
            (1, 1, 0, 1, first, 3),
            (1, 2, 2, 3, middle, 3),
            (1, 3, 4, 5, last, 3),
            (1, 4, 6, 7, 0, 2),
        ],
        vob_sectors={1: 8},
    )
    title = dvd.inspect_source(directory).title()
    assert title.duration == 5
    assert title.size_bytes == 4 * SECTOR
    assert title.segments[0].offset == 0
    assert title.segments[1].offset == 6 * SECTOR


def test_corrupt_ifo_falls_back_to_bup(tmp_path):
    directory = write_dvd(tmp_path)
    ifo = directory / "VTS_01_0.IFO"
    good = ifo.read_bytes()
    ifo.write_bytes(b"broken")
    (directory / "VTS_01_0.BUP").write_bytes(good)
    assert dvd.inspect_source(directory).title().duration == 2


def test_missing_vob_marks_only_that_title_unsupported(tmp_path):
    directory = write_dvd(tmp_path)
    (directory / "VTS_01_1.VOB").unlink()
    source = dvd.inspect_source(directory)
    assert source.default_title == 0
    assert source.titles[0].supported is False
    assert "niet compleet" in source.titles[0].reason
    with pytest.raises(dvd.DvdError, match="niet compleet"):
        source.title(1)


def test_truncated_table_rejected(tmp_path):
    directory = write_dvd(tmp_path)
    ifo = directory / "VIDEO_TS.IFO"
    data = bytearray(ifo.read_bytes())
    struct.pack_into(">I", data, SECTOR + 4, 0xFFFFFFFF)
    ifo.write_bytes(data)
    with pytest.raises(dvd.DvdError, match="ongeldige tabel"):
        dvd.inspect_source(directory)


def test_pgc_loop_marks_title_unsupported(tmp_path):
    write_dvd(tmp_path, pgcs=[([0], 1)])
    source = dvd.inspect_source(tmp_path / "VIDEO_TS")
    assert source.default_title == 0
    assert "lus" in source.titles[0].reason
    with pytest.raises(dvd.DvdError, match="lus"):
        source.title(1)


@pytest.mark.parametrize("flag", [0x20, 0x40, 0x60])
def test_vmg_playback_flags_do_not_disable_sequential_title(tmp_path, flag):
    """Cell commands / multi-PGC hints are not proof of random/shuffle playback."""
    directory = write_dvd(tmp_path, title_flags=[flag])
    source = dvd.inspect_source(directory)
    assert source.default_title == 1
    assert source.title(1).supported is True


def test_episode_titles_with_cell_commands_are_selectable(tmp_path):
    """Regression: nine ordinary episodes must not become zero usable titles."""
    directory = write_dvd(
        tmp_path,
        cells=[(1, number, (number - 1) * 2, number * 2 - 1, 0, number) for number in range(1, 10)],
        pgcs=[([number - 1], 0) for number in range(1, 10)],
        titles=[(number, number, 1) for number in range(1, 10)],
        title_flags=[0x20] * 9,
        vob_sectors={1: 18},
    )
    source = dvd.inspect_source(directory, label="KNIGHT_RIDER")
    assert source.summary()["title_count"] == 9
    assert all(title.supported for title in source.titles)
    assert source.default_title == 9
    assert source.title(1).duration == 1
    assert source.title(9).size_bytes == 2 * SECTOR


def test_multi_pgc_sequential_title_with_vmg_flag_supported(tmp_path):
    directory = write_dvd(
        tmp_path,
        cells=[(1, 1, 0, 1, 0, 2), (1, 2, 2, 3, 0, 3)],
        pgcs=[([0], 2), ([1], 0)],
        title_flags=[0x40],
        vob_sectors={1: 4},
    )
    source = dvd.inspect_source(directory)
    assert source.default_title == 1
    title = source.title(1)
    assert title.supported is True
    assert title.duration == 5
    assert title.size_bytes == 4 * SECTOR


@pytest.mark.parametrize("mode", [1, 2, 0x80])
def test_pgc_playback_mode_marks_title_unsupported(tmp_path, mode):
    directory = write_dvd(tmp_path, playback_modes={1: mode})
    source = dvd.inspect_source(directory)
    assert source.default_title == 0
    assert "shuffle" in source.titles[0].reason
    with pytest.raises(dvd.DvdError, match="shuffle"):
        source.title(1)


def test_pgc_playback_mode_in_later_chain_marks_title_unsupported(tmp_path):
    directory = write_dvd(
        tmp_path,
        cells=[(1, 1, 0, 1, 0, 2), (1, 2, 2, 3, 0, 3)],
        pgcs=[([0], 2), ([1], 0)],
        playback_modes={2: 1},
        vob_sectors={1: 4},
    )
    source = dvd.inspect_source(directory)
    assert source.default_title == 0
    with pytest.raises(dvd.DvdError, match="shuffle"):
        source.title(1)


def test_default_title_skips_non_sequential_pgc(tmp_path):
    directory = write_dvd(
        tmp_path,
        cells=[(1, 1, 0, 1, 0, 20), (1, 2, 2, 3, 0, 3)],
        pgcs=[([0], 0), ([1], 0)],
        titles=[(1, 1, 1), (2, 2, 1)],
        playback_modes={1: 2},
        vob_sectors={1: 4},
    )
    source = dvd.inspect_source(directory)
    assert source.default_title == 2
    assert source.title(2).duration == 3


def _first_pgc_offset(data: bytes) -> int:
    pgcit = struct.unpack_from(">I", data, 0xCC)[0] * SECTOR
    return pgcit + struct.unpack_from(">I", data, pgcit + 12)[0]


def test_pgc_layout_uses_disk_offsets_not_struct_offsets(tmp_path):
    """Regression for 1.2.2: mode is 0xA3, not the high byte of goup at 0xA1."""
    directory = write_dvd(tmp_path)
    ifo = directory / "VTS_01_0.IFO"
    data = bytearray(ifo.read_bytes())
    start = _first_pgc_offset(data)
    struct.pack_into(">H", data, start + 0x9E, 0x0304)  # previous PGC
    struct.pack_into(">H", data, start + 0xA0, 0x0102)  # go-up PGC
    data[start + 0xA2] = 255  # still_time
    data[start + 0xA3] = 0  # sequential playback
    ifo.write_bytes(data)
    source = dvd.inspect_source(directory)
    assert source.default_title == 1
    assert source.title(1).supported is True


@pytest.mark.parametrize("mode", [1, 2, 0x80])
def test_pgc_mode_byte_a3_is_authoritative(tmp_path, mode):
    directory = write_dvd(tmp_path)
    ifo = directory / "VTS_01_0.IFO"
    data = bytearray(ifo.read_bytes())
    start = _first_pgc_offset(data)
    data[start + 0xA1] = 0
    data[start + 0xA3] = mode
    ifo.write_bytes(data)
    source = dvd.inspect_source(directory)
    assert source.default_title == 0
    assert "shuffle" in source.titles[0].reason


def test_duplicate_cell_address_keeps_first_mapping(tmp_path):
    directory = write_dvd(tmp_path)
    ifo = directory / "VTS_01_0.IFO"
    data = bytearray(ifo.read_bytes())
    base = struct.unpack_from(">I", data, 0xE0)[0] * SECTOR
    count = struct.unpack_from(">H", data, base)[0]
    last = struct.unpack_from(">I", data, base + 4)[0]
    entry = bytes(data[base + 8 : base + 20])
    data[base + 8 + count * 12 : base + 8 + (count + 1) * 12] = entry
    struct.pack_into(">H", data, base, count + 1)
    struct.pack_into(">I", data, base + 4, last + 12)
    ifo.write_bytes(data)
    title = dvd.inspect_source(directory).title()
    assert title.supported is True
    assert title.segments == (dvd.Segment(directory / "VTS_01_1.VOB", 0, 2 * SECTOR),)


def test_oversized_cell_address_count_is_clamped(tmp_path):
    directory = write_dvd(tmp_path)
    ifo = directory / "VTS_01_0.IFO"
    data = bytearray(ifo.read_bytes())
    base = struct.unpack_from(">I", data, 0xE0)[0] * SECTOR
    struct.pack_into(">H", data, base, 500)
    ifo.write_bytes(data)
    assert dvd.inspect_source(directory).title().size_bytes == 2 * SECTOR


def test_reversed_cell_address_is_skipped(tmp_path):
    directory = write_dvd(tmp_path)
    ifo = directory / "VTS_01_0.IFO"
    data = bytearray(ifo.read_bytes())
    base = struct.unpack_from(">I", data, 0xE0)[0] * SECTOR
    struct.pack_into(">II", data, base + 12, 3, 1)
    ifo.write_bytes(data)
    source = dvd.inspect_source(directory)
    assert source.default_title == 0
    assert "mist het adres" in source.titles[0].reason


def test_duplicate_pgc_start_is_shared(tmp_path):
    directory = write_dvd(
        tmp_path,
        cells=[(1, 1, 0, 1, 0, 2), (1, 2, 2, 3, 0, 3)],
        pgcs=[([0], 0), ([1], 0)],
        titles=[(1, 1, 1), (2, 2, 1)],
        vob_sectors={1: 4},
    )
    ifo = directory / "VTS_01_0.IFO"
    data = bytearray(ifo.read_bytes())
    base = struct.unpack_from(">I", data, 0xCC)[0] * SECTOR
    first = struct.unpack_from(">I", data, base + 12)[0]
    struct.pack_into(">I", data, base + 20, first)
    ifo.write_bytes(data)
    source = dvd.inspect_source(directory)
    assert source.default_title in {1, 2}
    assert source.title(1).segments == source.title(2).segments
    assert source.title(1).duration == source.title(2).duration == 2


def test_trailing_ptt_offset_past_table_does_not_hide_earlier_title(tmp_path):
    directory = write_dvd(
        tmp_path,
        cells=[(1, 1, 0, 1, 0, 2), (1, 2, 2, 3, 0, 3)],
        pgcs=[([0], 0), ([1], 0)],
        titles=[(1, 1, 1), (2, 2, 1)],
        vob_sectors={1: 4},
    )
    ifo = directory / "VTS_01_0.IFO"
    data = bytearray(ifo.read_bytes())
    base = struct.unpack_from(">I", data, 0xC8)[0] * SECTOR
    struct.pack_into(">I", data, base + 12, SECTOR)
    ifo.write_bytes(data)
    source = dvd.inspect_source(directory)
    assert source.default_title == 1
    assert source.title(1).duration == 2
    assert source.titles[1].supported is False
    assert "ontbrekende titel" in source.titles[1].reason


def test_pgc_past_declared_pgcit_end_but_inside_ifo_is_readable(tmp_path):
    directory = write_dvd(tmp_path)
    ifo = directory / "VTS_01_0.IFO"
    data = bytearray(ifo.read_bytes())
    base = struct.unpack_from(">I", data, 0xCC)[0] * SECTOR
    struct.pack_into(">I", data, base + 4, 15)  # header + one 8-byte entry - 1
    ifo.write_bytes(data)
    assert dvd.inspect_source(directory).title().duration == 2


def test_pgc_truncated_inside_ifo_is_rejected(tmp_path):
    directory = write_dvd(tmp_path)
    ifo = directory / "VTS_01_0.IFO"
    data = bytearray(ifo.read_bytes())
    base = struct.unpack_from(">I", data, 0xCC)[0] * SECTOR
    struct.pack_into(">I", data, base + 4, 15)
    start = _first_pgc_offset(data)
    ifo.write_bytes(data[: start + 200])
    source = dvd.inspect_source(directory)
    assert source.default_title == 0
    assert "afgebroken programmaketen" in source.titles[0].reason
