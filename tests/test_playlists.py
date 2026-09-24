import os
import time

import pytest

from nowplaying.library import PlaylistStore, read_metadata, scan_folder
from nowplaying.spotify import SpotifyClient, LIKED


def test_store_crud(tmp_path):
    store = PlaylistStore(tmp_path / "pl.json")
    assert store.names()[0].name == "Мой плейлист"
    pid = store.create("Рок")
    f1, f2 = tmp_path / "a.mp3", tmp_path / "b.ogg"
    f1.write_bytes(b""); f2.write_bytes(b"")
    assert store.add_files(pid, [str(f1), str(f2), str(f1), str(tmp_path / "x.txt")]) == 2
    assert store.move(pid, 0, 1) == 1
    assert store.get(pid)["tracks"][1] == str(f1)
    store.remove(pid, [0])
    assert store.get(pid)["tracks"] == [str(f1)]
    store.rename(pid, "Rock")
    again = PlaylistStore(tmp_path / "pl.json")
    assert [p.name for p in again.names()] == ["Мой плейлист", "Rock"]
    again.delete(pid)
    assert len(again.names()) == 1


def test_metadata_from_filename(tmp_path):
    f = tmp_path / "Кино - Группа крови.mp3"
    f.write_bytes(b"")
    t = read_metadata(str(f))
    assert t.artist == "Кино" and t.title == "Группа крови"


def test_scan_folder(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.flac").write_bytes(b"")
    (tmp_path / "b.txt").write_bytes(b"")
    assert [os.path.basename(p) for p in scan_folder(str(tmp_path))] == ["a.flac"]


def test_spotify_track_parsing():
    t = SpotifyClient._track({"uri": "spotify:track:1", "name": "Song", "duration_ms": 120000,
                              "artists": [{"name": "A"}, {"name": "B"}], "album": {"name": "X"}})
    assert t.artist == "A, B" and t.duration == 120 and t.playable
    assert SpotifyClient._track({"uri": "spotify:local:1", "name": "L", "is_local": True}).playable is False
    assert SpotifyClient._track(None) is None


def test_spotify_play_body(tmp_path, monkeypatch):
    monkeypatch.setenv("NOWPLAYING_HOME", str(tmp_path))
    c = SpotifyClient()
    calls = []
    c._api = lambda method, path, params=None, body=None: calls.append((method, path, body))
    tracks = [SpotifyClient._track({"uri": f"spotify:track:{i}", "name": str(i)}) for i in range(3)]
    c.play("pl123", tracks, 1)
    assert calls[-1] == ("PUT", "/me/player/play",
                         {"context_uri": "spotify:playlist:pl123", "offset": {"uri": "spotify:track:1"}})
    c.play(LIKED, tracks, 1)
    assert calls[-1][2] == {"uris": ["spotify:track:1", "spotify:track:2"]}


@pytest.mark.skipif(not os.path.exists("/tmp/music/01.mp3"), reason="нет тестовых файлов")
def test_local_player(monkeypatch):
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    from nowplaying.local_player import LocalPlayer
    files = sorted(scan_folder("/tmp/music"))
    tracks = [read_metadata(f) for f in files]
    p = LocalPlayer()
    p.load(tracks, "0", 0)
    s = p.session()
    assert s.is_playing and s.title == tracks[0].title and s.duration
    p.next(); assert p.index == 1
    p.previous(); assert p.index == 0
    p.play_pause(); assert not p.session().is_playing
    p.play_pause(); assert p.session().is_playing
    p.seek("local:player", 2.0); assert p.position() >= 2.0
    p.stop()
