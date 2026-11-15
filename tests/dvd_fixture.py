"""Tiny, hand-authored DVD-Video indexes for parser and converter tests."""

from __future__ import annotations

import struct
from pathlib import Path

SECTOR = 2048


def bcd(seconds: int, frames: int = 0, *, ntsc: bool = False) -> bytes:
    """Encode dvd_time_t with explicit rate bits (01=25 fps, 11=29.97 fps)."""
    limit = 30 if ntsc else 25
    if not 0 <= frames < limit:
        raise ValueError(f"fixture frames must be 0..{limit - 1}")
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    frame_u = (0xC0 if ntsc else 0x40) | ((frames // 10) << 4) | (frames % 10)
    return bytes(
        (
            (hours // 10) << 4 | hours % 10,
            (minutes // 10) << 4 | minutes % 10,
            (secs // 10) << 4 | secs % 10,
            frame_u,
        )
    )


def _table(entries: bytes, count: int) -> bytes:
    payload = struct.pack(">HHI", count, 0, 8 + len(entries) - 1) + entries
    size = ((len(payload) + SECTOR - 1) // SECTOR) * SECTOR
    return payload.ljust(size, b"\0")


def write_dvd(
    root: Path,
    *,
    cells: list[tuple[int, int, int, int, int, int]] | None = None,
    pgcs: list[tuple[list[int], int]] | None = None,
    titles: list[tuple[int, int, int]] | None = None,
    title_flags: list[int] | None = None,
    playback_modes: dict[int, int] | None = None,
    vob_sectors: dict[int, int] | None = None,
    vob_bytes: dict[int, bytes] | None = None,
    css: bool = False,
) -> Path:
    """Create VIDEO_TS.

    cells: (vob_id, cell_nr, first_sector, last_sector, flags, seconds)
    pgcs: (cell indexes, next_pgc); titles: (vts_ttn, pgcn, chapters)
    title_flags: VMG playback-type bytes, in title order (default sequential)
    playback_modes: PGC number -> pg_playback_mode byte (default sequential)
    css: mark the first title VOB sector with a clear MPEG-2 PES scrambling flag
    """
    directory = root / "VIDEO_TS"
    directory.mkdir(parents=True, exist_ok=True)
    cells = cells or [(1, 1, 0, 1, 0, 2)]
    pgcs = pgcs or [(list(range(len(cells))), 0)]
    titles = titles or [(1, 1, 1)]
    title_flags = title_flags if title_flags is not None else [0] * len(titles)
    playback_modes = playback_modes or {}
    if len(title_flags) != len(titles):
        raise ValueError("fixture needs one VMG playback flag per title")
    if not set(playback_modes) <= set(range(1, len(pgcs) + 1)):
        raise ValueError("fixture playback mode refers to a missing PGC")
    vob_sectors = vob_sectors or {1: 2}

    vmg = bytearray(SECTOR * 2)
    vmg[0:12] = b"DVDVIDEO-VMG"
    struct.pack_into(">I", vmg, 0xC4, 1)
    tt_entries = b"".join(
        struct.pack(">BBHHBBI", flag, 1, chapters, 0, 1, ttn, 0)
        for flag, (ttn, _pgcn, chapters) in zip(title_flags, titles, strict=True)
    )
    vmg[SECTOR : SECTOR * 2] = _table(tt_entries, len(titles))
    (directory / "VIDEO_TS.IFO").write_bytes(vmg)

    max_ttn = max(ttn for ttn, _pgcn, _chapters in titles)
    offsets = b"".join(
        struct.pack(">I", 8 + max_ttn * 4 + 4 * (index - 1)) for index in range(1, max_ttn + 1)
    )
    units = b"".join(struct.pack(">HH", pgcn, 1) for _ttn, pgcn, _chapters in titles)
    ptt = _table(offsets + units, max_ttn)

    pgc_payloads = []
    for number, (cell_indexes, next_pgc) in enumerate(pgcs, start=1):
        header = bytearray(236)
        header[2] = len(cell_indexes)
        header[3] = len(cell_indexes)
        total = sum(cells[index][5] for index in cell_indexes)
        header[4:8] = bcd(total)
        struct.pack_into(">H", header, 0x9C, next_pgc)
        # On-disk pgc_t: goup_pgc_nr 0xA0 (u16), still_time 0xA2,
        # pg_playback_mode 0xA3. Keep these independent of the parser.
        header[0xA3] = playback_modes.get(number, 0)
        struct.pack_into(">H", header, 0xE8, 236)
        struct.pack_into(">H", header, 0xEA, 236 + len(cell_indexes) * 24)
        playback = b"".join(
            bytes([cells[index][4], 0, 0, 0])
            + bcd(cells[index][5])
            + struct.pack(">IIII", cells[index][2], 0, cells[index][3], cells[index][3])
            for index in cell_indexes
        )
        position = b"".join(
            struct.pack(">HBB", cells[index][0], 0, cells[index][1]) for index in cell_indexes
        )
        pgc_payloads.append(bytes(header) + playback + position)
    search = b"".join(
        struct.pack(">BBHI", 0, 0, 0, 8 + len(pgcs) * 8 + sum(map(len, pgc_payloads[:index])))
        for index in range(len(pgcs))
    )
    pgcit = _table(search + b"".join(pgc_payloads), len(pgcs))
    used = {index for cell_indexes, _ in pgcs for index in cell_indexes}
    c_entries = b"".join(
        struct.pack(">HBBII", cells[index][0], cells[index][1], 0, cells[index][2], cells[index][3])
        for index in sorted(used)
    )
    # C_ADT's first field is nr_of_vobs, not the number of cell addresses.
    # A single VOB can contain many cells; the table length carries the count.
    vob_count = len({cells[index][0] for index in used})
    c_adt = _table(c_entries, vob_count)

    vts = bytearray(SECTOR)
    vts[0:12] = b"DVDVIDEO-VTS"
    struct.pack_into(">I", vts, 0xC8, 1)  # VTS_PTT_SRPT
    struct.pack_into(">I", vts, 0xCC, 2)  # VTS_PGCIT
    struct.pack_into(">I", vts, 0xE0, 2 + len(pgcit) // SECTOR)  # VTS_C_ADT
    vts.extend(ptt)
    vts.extend(pgcit)
    vts.extend(c_adt)
    (directory / "VTS_01_0.IFO").write_bytes(vts)

    for part, sectors in vob_sectors.items():
        data = (vob_bytes or {}).get(part, bytes(range(256)) * (sectors * SECTOR // 256))
        if css and part == 1 and part not in (vob_bytes or {}):
            marked = bytearray(data)
            marked[0:4] = b"\x00\x00\x01\xba"
            marked[4] = 0x44  # MPEG-2 pack header
            marked[13] = 0  # no pack stuffing
            marked[14:18] = b"\x00\x00\x01\xe0"
            marked[18:20] = b"\x07\xea"
            marked[20] = 0x30  # PES_scrambling_control
            data = bytes(marked)
        if len(data) % SECTOR:
            raise ValueError("fixture VOB must be sector-aligned")
        (directory / f"VTS_01_{part}.VOB").write_bytes(data)
    return directory
