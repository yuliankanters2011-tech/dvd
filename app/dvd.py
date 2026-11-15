"""Read DVD-Video title metadata and locate the exact cells of a title.

This is a small, defensive IFO reader for ordinary authored DVD-Video. It does
not decrypt CSS, execute DVD menu commands, or read DVD-Audio (AOB) discs.
The sector layout follows the public DVD-Video IFO structures used by libdvdread.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

SECTOR = 2048
MAX_IFO = 8 * 1024 * 1024
VIDEO_SUFFIXES = {".ts", ".vob", ".mpg", ".mpeg"}


class DvdError(ValueError):
    """A Dutch, actionable error for an invalid or unsupported DVD layout."""


@dataclass(frozen=True)
class Segment:
    path: Path
    offset: int
    size: int


@dataclass(frozen=True)
class Title:
    number: int
    duration: float
    chapters: int
    segments: tuple[Segment, ...]
    supported: bool = True
    reason: str = ""

    @property
    def size_bytes(self) -> int:
        return sum(segment.size for segment in self.segments)

    def summary(self) -> dict:
        return {
            "number": self.number,
            "duration_seconds": round(self.duration, 3),
            "chapters": self.chapters,
            "size_bytes": self.size_bytes,
            "supported": self.supported,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Source:
    path: Path
    label: str
    kind: str
    titles: tuple[Title, ...] = ()
    note: str = ""

    @property
    def default_title(self) -> int:
        usable = [title for title in self.titles if title.supported]
        return max(usable, key=lambda title: title.duration).number if usable else 0

    def title(self, number: int = 0) -> Title:
        wanted = number or self.default_title
        for title in self.titles:
            if title.number == wanted:
                if not title.supported:
                    raise DvdError(title.reason or "Deze titel wordt niet ondersteund")
                return title
        raise DvdError("Kies een geldige titel uit de lijst")

    def summary(self) -> dict:
        return {
            "label": self.label,
            "kind": self.kind,
            "note": self.note,
            "default_title": self.default_title,
            "title_count": len(self.titles),
            "titles": [title.summary() for title in self.titles],
        }


def _u16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise DvdError("De DVD-index (IFO) is beschadigd: tabel buiten het bestand")
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise DvdError("De DVD-index (IFO) is beschadigd: tabel buiten het bestand")
    return struct.unpack_from(">I", data, offset)[0]


def _case_file(directory: Path, name: str) -> Path | None:
    wanted = name.casefold()
    try:
        for path in directory.iterdir():
            if path.name.casefold() == wanted and path.is_file() and not path.is_symlink():
                return path
    except OSError as exc:
        raise DvdError(f"Kan de DVD-map niet lezen: {exc.strerror or exc}") from exc
    return None


def _ifo(directory: Path, stem: str, magic: bytes) -> bytes:
    for extension in (".IFO", ".BUP"):
        path = _case_file(directory, stem + extension)
        if path is None:
            continue
        try:
            if path.stat().st_size > MAX_IFO:
                raise DvdError(f"{path.name} is te groot voor een DVD-index")
            data = path.read_bytes()
        except OSError as exc:
            raise DvdError(f"Kan {path.name} niet lezen: {exc.strerror or exc}") from exc
        if data.startswith(magic):
            return data
    raise DvdError(
        f"{stem}.IFO ontbreekt of is beschadigd. Kopieer de volledige VIDEO_TS-map, "
        "inclusief IFO- en BUP-bestanden."
    )


def _bcd_time(value: bytes) -> float:
    """Decode DVD dvd_time_t: BCD h/m/s, then 2 rate bits + BCD frames.

    libdvdread prints rate bits 01 as 25 fps and 11 as 29.97 fps. The frame
    count is only the low six bits (two BCD digits), never the whole byte.
    """
    if (
        len(value) != 4
        or any((byte >> 4) > 9 or (byte & 15) > 9 for byte in value[:3])
        or (value[3] & 15) > 9
    ):
        raise DvdError(f"De DVD-index bevat een ongeldige tijdsduur ({value.hex(' ')})")
    hours, minutes, seconds = ((byte >> 4) * 10 + (byte & 15) for byte in value[:3])
    frame_u = value[3]
    frames = ((frame_u >> 4) & 3) * 10 + (frame_u & 15)
    rate = frame_u >> 6
    if rate == 3:
        fps = 30000 / 1001
        max_frames = 30
    elif rate == 1:
        fps = 25.0
        max_frames = 25
    elif frames == 0:
        # Some authoring tools leave the rate bits zero when there are no
        # fractional frames. The whole-second duration is still unambiguous.
        fps = 25.0
        max_frames = 25
    else:
        raise DvdError(f"De DVD-index bevat een ongeldige tijdsduur ({value.hex(' ')})")
    if minutes > 59 or seconds > 59 or frames >= max_frames:
        raise DvdError(f"De DVD-index bevat een ongeldige tijdsduur ({value.hex(' ')})")
    return hours * 3600 + minutes * 60 + seconds + frames / fps


def _table(data: bytes, base: int, entry_size: int, maximum: int) -> tuple[int, int]:
    count = _u16(data, base)
    end = _u32(data, base + 4) + 1
    if (
        not 0 < count <= maximum
        or base + end > len(data)
        or base + 8 + count * entry_size > base + end
    ):
        raise DvdError("De DVD-index (IFO) bevat een ongeldige tabel")
    return count, base + end


def _vts_files(directory: Path, number: int) -> tuple[tuple[Path, ...], tuple[int, ...]]:
    pattern = re.compile(rf"vts_{number:02d}_([1-9])\.vob")
    found: dict[int, Path] = {}
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise DvdError(f"Kan de DVD-map niet lezen: {exc.strerror or exc}") from exc
    for path in entries:
        match = pattern.fullmatch(path.name.casefold())
        if match and path.is_file() and not path.is_symlink():
            found[int(match.group(1))] = path
    if not found or set(found) != set(range(1, max(found) + 1)):
        raise DvdError(f"De VOB-bestanden van titelset {number:02d} zijn niet compleet")
    files = tuple(found[index] for index in sorted(found))
    ends: list[int] = []
    total = 0
    for path in files:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise DvdError(f"Kan {path.name} niet lezen: {exc.strerror or exc}") from exc
        if size <= 0 or size % SECTOR:
            raise DvdError(f"{path.name} heeft geen geldige DVD-sectorgrootte")
        total += size
        ends.append(total)
    return files, tuple(ends)


def _range_segments(
    files: tuple[Path, ...], ends: tuple[int, ...], first: int, last: int
) -> tuple[Segment, ...]:
    if first > last or (last + 1) * SECTOR > ends[-1]:
        raise DvdError("De DVD-index verwijst naar ontbrekende VOB-sectoren")
    segments: list[Segment] = []
    file_start = 0
    for path, end in zip(files, ends, strict=True):
        start_byte = max(first * SECTOR, file_start)
        end_byte = min((last + 1) * SECTOR, end)
        if end_byte > start_byte:
            segments.append(Segment(path, start_byte - file_start, end_byte - start_byte))
        file_start = end
    return tuple(segments)


def _cell_addresses(data: bytes) -> dict[tuple[int, int], tuple[int, int]]:
    base = _u32(data, 0xE0) * SECTOR
    end = _u32(data, base + 4) + 1
    if end < 8 or base + end > len(data):
        raise DvdError("De DVD-index (IFO) bevat een ongeldige tabel")
    # C_ADT.nr_of_vobs is not the number of 12-byte cell addresses. One VOB
    # can contain many cells. libdvdread walks the whole declared table
    # (info_length / sizeof(cell_adr_t)) and only clamps nr_of_vobs when it is
    # larger than the available entries (e.g. Enemy of the State R2).
    entry_count = (end - 8) // 12
    result = {}
    for index in range(entry_count):
        offset = base + 8 + index * 12
        key = (_u16(data, offset), data[offset + 2])
        first, last = _u32(data, offset + 4), _u32(data, offset + 8)
        if first > last:
            continue
        # Keep the first address for a duplicated vob/cell id instead of
        # rejecting the whole DVD.
        result.setdefault(key, (first, last))
    return result


@dataclass(frozen=True)
class _Pgc:
    duration: float
    segments: tuple[Segment, ...]
    next_pgc: int
    playback_mode: int = 0


def _pgc(data: bytes, pgcn: int, files: tuple[Path, ...], ends: tuple[int, ...]) -> _Pgc:
    base = _u32(data, 0xCC) * SECTOR
    count, _end = _table(data, base, 8, 9999)
    if not 1 <= pgcn <= count:
        raise DvdError("De DVD-index verwijst naar een ontbrekende programmaketen")
    start = base + _u32(data, base + 8 + (pgcn - 1) * 8 + 4)
    # libdvdread only warns when a PGC extends past the declared PGCIT
    # last_byte; the authoritative bound is the IFO file itself.
    if start < base + 8 + count * 8 or start + 236 > len(data):
        raise DvdError("De DVD-index bevat een afgebroken programmaketen")
    next_pgc = _u16(data, start + 0x9C)
    # On-disk pgc_t is 236 bytes: still_time 0xA2, pg_playback_mode 0xA3.
    # The VMG title byte only says "multi or random" and also has a
    # cell-command bit, so it must not be used as the shuffle flag.
    playback_mode = data[start + 0xA3]
    if playback_mode != 0:
        return _Pgc(0.0, (), next_pgc, playback_mode)
    cells = data[start + 3]
    # Pointers are relative to the PGC start.
    playback_offset = _u16(data, start + 0xE8)
    position_offset = _u16(data, start + 0xEA)
    if cells == 0 or playback_offset == 0 or position_offset == 0:
        return _Pgc(0.0, (), next_pgc)
    if (
        playback_offset < 236
        or position_offset < 236
        or start + playback_offset + cells * 24 > len(data)
        or start + position_offset + cells * 4 > len(data)
    ):
        raise DvdError("De DVD-index bevat afgebroken celtabellen")
    addresses = _cell_addresses(data)
    segments: list[Segment] = []
    duration = _bcd_time(data[start + 4 : start + 8])
    for index in range(cells):
        playback = start + playback_offset + index * 24
        position = start + position_offset + index * 4
        flags = data[playback]
        # In an angle block, keep only the first angle. Never concatenate all
        # camera angles into one film with repeated scenes.
        if (flags >> 4) & 3 == 1 and flags >> 6 in {2, 3}:
            # The PGC total includes every angle; the MP4 contains only angle 1.
            duration = max(0.0, duration - _bcd_time(data[playback + 4 : playback + 8]))
            continue
        key = (_u16(data, position), data[position + 3])
        if key not in addresses:
            raise DvdError(f"De DVD-index mist het adres van videocel {key[0]}/{key[1]}")
        first, last = addresses[key]
        segments.extend(_range_segments(files, ends, first, last))
    if not segments:
        raise DvdError("De geselecteerde DVD-titel bevat geen leesbare cellen")
    return _Pgc(duration, tuple(segments), next_pgc)


def _vts_title(
    directory: Path, number: int, global_number: int, vts_ttn: int, chapters: int
) -> Title:
    data = _ifo(directory, f"VTS_{number:02d}_0", b"DVDVIDEO-VTS")
    ptt_base = _u32(data, 0xC8) * SECTOR
    count, ptt_end = _table(data, ptt_base, 4, 99)
    declared_count = count
    # Some authored discs (e.g. Transformers 3) have trailing TTU offsets past
    # the table. libdvdread truncates the usable title count there instead of
    # rejecting every earlier title in the same VTS.
    for index in range(count):
        relative = _u32(data, ptt_base + 8 + index * 4)
        if ptt_base + relative + 4 > ptt_end:
            count = index
            break
    if not 1 <= vts_ttn <= count:
        raise DvdError("De DVD-index verwijst naar een ontbrekende titel")
    unit = ptt_base + _u32(data, ptt_base + 8 + (vts_ttn - 1) * 4)
    # On disk the offset points straight at the first {pgcn, pgn} pair.
    if unit < ptt_base + 8 + declared_count * 4 or unit + 4 > ptt_end:
        raise DvdError("De DVD-index bevat een afgebroken titeltabel")
    pgcn = _u16(data, unit)
    files, ends = _vts_files(directory, number)
    segments: list[Segment] = []
    duration = 0.0
    visited: set[int] = set()
    while pgcn:
        if pgcn in visited or len(visited) >= 128:
            raise DvdError("De DVD-index bevat een lus in de programmaketen")
        visited.add(pgcn)
        chain = _pgc(data, pgcn, files, ends)
        if chain.playback_mode != 0:
            return Title(
                global_number,
                0,
                chapters,
                (),
                False,
                "Deze titel gebruikt willekeurige/shuffle-weergave en wordt niet ondersteund.",
            )
        duration += chain.duration
        segments.extend(chain.segments)
        pgcn = chain.next_pgc
    return Title(global_number, duration, chapters, tuple(segments))


def _dvd_source(directory: Path, label: str | None = None) -> Source:
    data = _ifo(directory, "VIDEO_TS", b"DVDVIDEO-VMG")
    base = _u32(data, 0xC4) * SECTOR
    count, _end = _table(data, base, 12, 99)
    titles: list[Title] = []
    for index in range(count):
        offset = base + 8 + index * 12
        vts = data[offset + 6]
        vts_ttn = data[offset + 7]
        if not 1 <= vts <= 99 or not 1 <= vts_ttn <= 99:
            raise DvdError("De DVD-index bevat een ongeldig titelnummer")
        chapters = _u16(data, offset + 2)
        try:
            title = _vts_title(directory, vts, index + 1, vts_ttn, chapters)
        except DvdError as exc:
            # Keep the problem attached to this title. One damaged extra or
            # missing VTS must not hide every other readable episode/film.
            title = Title(index + 1, 0, chapters, (), False, str(exc))
        titles.append(title)
    name = label or (
        directory.parent.name if directory.name.casefold() == "video_ts" else directory.name
    )
    return Source(
        directory,
        name or "DVD",
        "dvd",
        tuple(titles),
        "Alleen onversleutelde DVD-Video. Menu's en DVD-Audio worden niet omgezet.",
    )


def inspect_source(path: Path, label: str | None = None) -> Source:
    """Inspect a VIDEO_TS folder, a DVD root, an AUDIO_TS folder, or one TS/VOB file."""
    path = path.expanduser()
    if path.is_symlink():
        raise DvdError("Gebruik een echte map of bestand, geen symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise DvdError(f"De bron bestaat niet: {path}") from exc
    if resolved.is_file():
        if resolved.suffix.lower() not in VIDEO_SUFFIXES:
            raise DvdError("Kies een VIDEO_TS-map of een .ts-, .vob-, .mpg- of .mpeg-bestand")
        size = resolved.stat().st_size
        if size <= 0:
            raise DvdError("Het bronbestand is leeg")
        return Source(
            resolved,
            label or resolved.stem,
            "file",
            (Title(1, 0, 0, (Segment(resolved, 0, size),)),),
            "Losse TS/VOB-video. De duur wordt tijdens het omzetten gemeten.",
        )
    if not resolved.is_dir():
        raise DvdError("De bron is geen map of videobestand")
    if resolved.name.casefold() == "audio_ts":
        return Source(
            resolved,
            label or resolved.parent.name or "AUDIO_TS",
            "audio_ts",
            note=(
                "AUDIO_TS is leeg of bevat DVD-Audio (AOB). Bij een normale dvd staat het "
                "geluid in VIDEO_TS/*.VOB: kies die bron en 'Audio naar MP4'. "
                "Echte DVD-Audio-discs worden niet ondersteund."
            ),
        )
    video_ts = resolved if resolved.name.casefold() == "video_ts" else None
    if video_ts is None:
        try:
            video_ts = next(
                child
                for child in resolved.iterdir()
                if child.name.casefold() == "video_ts" and child.is_dir() and not child.is_symlink()
            )
        except StopIteration:
            raise DvdError(
                "Geen VIDEO_TS-map gevonden. Kies de dvd-map, een losse TS/VOB-video, "
                "of gebruik 'Audio naar MP4' voor het geluid uit VIDEO_TS."
            ) from None
        except OSError as exc:
            raise DvdError(f"Kan de bronmap niet lezen: {exc.strerror or exc}") from exc
    return _dvd_source(video_ts, label)
