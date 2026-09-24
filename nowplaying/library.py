"""Локальная медиатека: плейлисты из своих файлов, метаданные и обложки."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

AUDIO_EXTS = {".mp3", ".ogg", ".oga", ".opus", ".flac", ".wav"}
COVER_NAMES = ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "album.jpg")


def config_dir() -> Path:
    base = os.environ.get("NOWPLAYING_HOME")
    if base:
        path = Path(base)
    elif os.name == "nt":
        path = Path(os.environ.get("APPDATA", Path.home())) / "NowPlaying"
    else:
        path = Path.home() / ".config" / "nowplaying"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class PlaylistTrack:
    """Строка плейлиста (локальный файл или трек Spotify)."""

    id: str                      # путь к файлу или spotify:track:...
    title: str
    artist: str = ""
    album: str = ""
    duration: Optional[float] = None
    playable: bool = True


@dataclass
class PlaylistInfo:
    id: str
    name: str
    count: Optional[int] = None


# ---------------------------------------------------------------- метаданные
_meta_cache: Dict[str, PlaylistTrack] = {}
_meta_lock = threading.Lock()


def read_metadata(path: str) -> PlaylistTrack:
    with _meta_lock:
        if path in _meta_cache:
            return _meta_cache[path]
    title = Path(path).stem
    artist = album = ""
    duration = None
    try:
        import mutagen

        audio = mutagen.File(path, easy=True)
        if audio is not None:
            if audio.info is not None and getattr(audio.info, "length", None):
                duration = float(audio.info.length)
            tags = audio.tags or {}

            def first(key: str) -> str:
                try:
                    v = tags.get(key)
                except Exception:
                    return ""
                if isinstance(v, list):
                    return str(v[0]) if v else ""
                return str(v) if v else ""

            title = first("title") or title
            artist = first("artist") or first("albumartist")
            album = first("album")
    except Exception:
        pass
    if not artist and " - " in title:  # "Исполнитель - Название.mp3"
        artist, title = [p.strip() for p in title.split(" - ", 1)]
    track = PlaylistTrack(id=path, title=title, artist=artist, album=album, duration=duration,
                          playable=os.path.exists(path))
    with _meta_lock:
        _meta_cache[path] = track
    return track


def read_cover(path: str) -> Optional[bytes]:
    """Обложка из тегов файла, иначе cover.jpg/folder.jpg рядом с файлом."""
    try:
        import mutagen

        audio = mutagen.File(path)
        if audio is not None:
            pictures = getattr(audio, "pictures", None)  # FLAC
            if pictures:
                return pictures[0].data
            tags = audio.tags
            if tags is not None:
                if hasattr(tags, "getall"):  # ID3
                    apic = tags.getall("APIC")
                    if apic:
                        return apic[0].data
                try:  # OGG/Opus: METADATA_BLOCK_PICTURE
                    blocks = tags.get("metadata_block_picture")
                    if blocks:
                        import base64
                        from mutagen.flac import Picture

                        return Picture(base64.b64decode(blocks[0])).data
                except Exception:
                    pass
    except Exception:
        pass
    folder = Path(path).parent
    for name in COVER_NAMES:
        p = folder / name
        if p.exists():
            try:
                return p.read_bytes()
            except OSError:
                pass
    return None


def scan_folder(folder: str) -> List[str]:
    files = []
    for root, _dirs, names in os.walk(folder):
        for n in sorted(names):
            if Path(n).suffix.lower() in AUDIO_EXTS:
                files.append(str(Path(root) / n))
    return files


def read_m3u(path: str) -> List[str]:
    base = Path(path).parent
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = Path(line)
            if not p.is_absolute():
                p = base / p
            if p.suffix.lower() in AUDIO_EXTS:
                out.append(str(p))
    return out


# ---------------------------------------------------------------- хранилище
class PlaylistStore:
    """Плейлисты хранятся в JSON: {"playlists": [{"name": ..., "tracks": [пути]}]}"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (config_dir() / "playlists.json")
        self._lock = threading.Lock()
        self.playlists: List[dict] = []
        self.load()
        if not self.playlists:
            self.playlists.append({"name": "Мой плейлист", "tracks": []})
            self.save()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.playlists = [p for p in data.get("playlists", []) if "name" in p]
            for p in self.playlists:
                p.setdefault("tracks", [])
        except (OSError, ValueError):
            self.playlists = []

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"playlists": self.playlists}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.path)

    def names(self) -> List[PlaylistInfo]:
        return [PlaylistInfo(id=str(i), name=p["name"], count=len(p["tracks"]))
                for i, p in enumerate(self.playlists)]

    def get(self, pid: str) -> dict:
        return self.playlists[int(pid)]

    def create(self, name: str) -> str:
        self.playlists.append({"name": name, "tracks": []})
        self.save()
        return str(len(self.playlists) - 1)

    def rename(self, pid: str, name: str) -> None:
        self.get(pid)["name"] = name
        self.save()

    def delete(self, pid: str) -> None:
        del self.playlists[int(pid)]
        if not self.playlists:
            self.playlists.append({"name": "Мой плейлист", "tracks": []})
        self.save()

    def add_files(self, pid: str, files: List[str]) -> int:
        tracks = self.get(pid)["tracks"]
        existing = set(tracks)
        added = 0
        for f in files:
            if Path(f).suffix.lower() in AUDIO_EXTS and f not in existing:
                tracks.append(f)
                existing.add(f)
                added += 1
        self.save()
        return added

    def remove(self, pid: str, indexes: List[int]) -> None:
        tracks = self.get(pid)["tracks"]
        for i in sorted(set(indexes), reverse=True):
            if 0 <= i < len(tracks):
                del tracks[i]
        self.save()

    def move(self, pid: str, index: int, delta: int) -> int:
        tracks = self.get(pid)["tracks"]
        j = index + delta
        if 0 <= index < len(tracks) and 0 <= j < len(tracks):
            tracks[index], tracks[j] = tracks[j], tracks[index]
            self.save()
            return j
        return index

    def tracks(self, pid: str) -> List[PlaylistTrack]:
        return [read_metadata(p) for p in self.get(pid)["tracks"]]
