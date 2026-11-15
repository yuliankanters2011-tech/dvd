import json
import logging
from pathlib import Path

import pytest
from dvd_fixture import write_dvd

from app import convert, drive, dvd, service
from app.config import Config


@pytest.fixture()
def svc(tmp_path):
    cfg = Config(
        share_dir=tmp_path / "share",
        state_dir=tmp_path / "state",
        films_dir=tmp_path / "films",
        music_dir=tmp_path / "music",
        puid=0,
        pgid=0,
    )
    cfg.films_dir.mkdir()
    cfg.music_dir.mkdir()
    cfg.library_dir.mkdir(parents=True)
    return service.Service(cfg)


def test_request_conversion_validates_and_queues(svc):
    svc.request_conversion("disc", 0, "video", "films", "  naam  ")
    job = svc.commands.get_nowait()
    assert job == service.ConvertJob("disc", 0, "video", "films", "  naam  ")
    with pytest.raises(ValueError):
        svc.request_conversion("disc", True, "video", "films")
    with pytest.raises(ValueError):
        svc.request_conversion("disc", 0, "video", "../films")
    with pytest.raises(ValueError):
        svc.request_conversion("disc", 0, "video", "films", "x" * 201)


def test_only_one_conversion_pending_and_cancel(svc):
    svc.request_conversion("disc", 0, "video", "films")
    with pytest.raises(RuntimeError, match="al omgezet"):
        svc.request_conversion("disc", 0, "audio", "music")
    with pytest.raises(RuntimeError, match="omzetten"):
        svc.request("eject")
    svc.request("cancel")
    assert svc._convert_stop.is_set()
    svc._command(svc.commands.get_nowait())
    assert svc._conversion_pending is False
    assert svc.snapshot()["convert_state"] == "error"
    svc.request_conversion("disc", 0, "video", "films")
    assert svc._convert_stop.is_set() is False


def test_convert_uses_fixed_destination_and_name(svc, monkeypatch):
    source = dvd.Source(Path("/tmp/film.ts"), "film", "file")
    monkeypatch.setattr(
        svc, "_collect_sources", lambda: [service.SourceEntry("x", "bibliotheek", source)]
    )
    seen = {}

    def fake_convert(src, title, mode, destination, state, settings, stop, progress, **kwargs):
        seen.update(
            src=src,
            title=title,
            mode=mode,
            destination=destination,
            state=state,
            settings=settings,
            stop=stop,
            kwargs=kwargs,
        )
        progress(convert.Progress(42, 10, 2.0, 5, 100, 200))
        return convert.Result(destination / "Naam.mp4", 123, 20.0, mode)

    monkeypatch.setattr(convert, "convert_title", fake_convert)
    svc.request_conversion("x", 1, "audio", "music", "Naam")
    svc._command(svc.commands.get_nowait())
    snapshot = svc.snapshot()
    assert snapshot["convert_state"] == "done"
    assert snapshot["convert_percent"] == 100
    assert snapshot["convert_file"].endswith("Naam.mp4")
    assert seen["destination"] == svc.config.music_dir
    assert seen["kwargs"]["name"] == "Naam"
    assert seen["kwargs"]["identity"] == ""


def test_convert_missing_destination_is_actionable(svc, monkeypatch):
    source = dvd.Source(Path("/tmp/film.ts"), "film", "file")
    monkeypatch.setattr(
        svc, "_collect_sources", lambda: [service.SourceEntry("x", "bibliotheek", source)]
    )
    svc.config.music_dir.rmdir()
    svc.request_conversion("x", 0, "audio", "music")
    svc._command(svc.commands.get_nowait())
    snapshot = svc.snapshot()
    assert snapshot["convert_state"] == "error"
    assert "niet gekoppeld" in snapshot["convert_error"]


def test_disc_conversion_auto_eject(svc, monkeypatch):
    source = dvd.Source(svc.config.mountpoint, "dvd", "dvd")
    monkeypatch.setattr(
        svc, "_collect_sources", lambda: [service.SourceEntry("disc", "schijf", source)]
    )
    svc._mounted = True
    svc._disc = drive.DiscInfo("identiteit", "dvd", "udf", 100)
    svc.config = Config(
        share_dir=svc.config.share_dir,
        state_dir=svc.config.state_dir,
        films_dir=svc.config.films_dir,
        music_dir=svc.config.music_dir,
        auto_eject=True,
    )
    monkeypatch.setattr(drive, "drive_status", lambda device: "disc")
    monkeypatch.setattr(drive, "media_changed", lambda device: False)
    ejected = []
    monkeypatch.setattr(drive, "eject", lambda device: ejected.append(device))
    monkeypatch.setattr(drive, "unmount", lambda target: None)
    monkeypatch.setattr(drive, "unlock_tray", lambda device: None)
    monkeypatch.setattr(
        convert,
        "convert_title",
        lambda *a, **k: convert.Result(svc.config.films_dir / "dvd.mp4", 1, 2, "video"),
    )
    svc.request_conversion("disc", 0, "video", "films")
    svc._command(svc.commands.get_nowait())
    assert ejected == [svc.config.device]
    assert svc.snapshot()["convert_state"] == "done"


def test_busy_blocks_drive_commands(svc):
    svc.update(convert_state="working")
    for command in ("eject", "close", "copy", "iso"):
        with pytest.raises(RuntimeError, match="omzetten"):
            svc.request(command)


def test_close_tray_command(svc, monkeypatch):
    calls = []
    monkeypatch.setattr(drive, "close_tray", lambda device: calls.append(device))
    svc._command("close")
    assert calls == [svc.config.device]
    assert svc.snapshot()["state"] == "not_ready"


def test_media_lists_library_sources(svc):
    write_dvd(svc.config.library_dir / "import")
    ts = svc.config.library_dir / "losse.ts"
    ts.write_bytes(b"x")
    hidden = svc.config.library_dir / "@eaDir"
    hidden.mkdir()
    (hidden / "verborgen.ts").write_bytes(b"x")
    media = svc.media()
    labels = {entry["label"] for entry in media["sources"]}
    assert "import" in labels and "losse" in labels
    assert "verborgen" not in labels
    assert media["destinations"]["films"]["ready"] is True
    assert media["destinations"]["music"]["path"] == str(svc.config.music_dir)


def test_mounted_episode_dvd_is_convertible_in_media(svc):
    write_dvd(svc.config.mountpoint, title_flags=[0x20])
    svc._mounted = True
    svc._disc = drive.DiscInfo("identiteit", "KNIGHT_RIDER", "udf", 100)
    media = svc.media()
    assert len(media["sources"]) == 1
    source = media["sources"][0]
    assert source["id"] == "disc"
    assert source["label"] == "KNIGHT_RIDER"
    assert source["default_title"] == 1
    assert source["titles"][0]["supported"] is True
    assert media["skipped_sources"] == []


def test_media_explains_skipped_import_and_logs_warning(svc, caplog):
    folder = svc.config.library_dir / "KNIGHT_RIDER-test"
    directory = write_dvd(folder)
    (folder / ".dvd-bridge-import.json").write_text(
        json.dumps({"label": "KNIGHT_RIDER"}), encoding="utf-8"
    )
    ifo = directory / "VIDEO_TS.IFO"
    good = ifo.read_bytes()
    ifo.write_bytes(b"broken")
    with caplog.at_level(logging.WARNING, logger="dvd.service"):
        media = svc.media()
    assert media["sources"] == []
    assert len(media["skipped_sources"]) == 1
    problem = media["skipped_sources"][0]
    assert problem["label"] == "KNIGHT_RIDER"
    assert problem["location"] == "bibliotheek"
    assert problem["path"] == str(directory)
    assert "ontbreekt of is beschadigd" in problem["reason"]
    assert any(
        record.levelno == logging.WARNING and "KNIGHT_RIDER" in record.getMessage()
        for record in caplog.records
    )
    ifo.write_bytes(good)
    recovered = svc.media()
    assert len(recovered["sources"]) == 1
    assert recovered["skipped_sources"] == []


def test_media_keeps_valid_sources_when_another_file_is_empty(svc):
    write_dvd(svc.config.library_dir / "import")
    empty = svc.config.library_dir / "empty.vob"
    empty.touch()
    media = svc.media()
    assert [source["label"] for source in media["sources"]] == ["import"]
    assert len(media["skipped_sources"]) == 1
    assert media["skipped_sources"][0]["path"] == str(empty)
    assert "leeg" in media["skipped_sources"][0]["reason"]


def test_shutdown_sets_conversion_stop(svc, monkeypatch):
    monkeypatch.setattr(svc._worker, "join", lambda timeout=None: None)
    svc.shutdown()
    assert svc.stop.is_set()
    assert svc._convert_stop.is_set()
