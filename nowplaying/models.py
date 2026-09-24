"""Общие модели данных."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TrackInfo:
    """Информация о том, что сейчас играет в одном источнике (приложение/вкладка)."""

    source_id: str                      # внутренний идентификатор источника (для управления)
    source_name: str                    # человекочитаемое имя: "Spotify", "Chrome", ...
    title: str = ""
    artist: str = ""
    album: str = ""
    is_playing: bool = False
    position: Optional[float] = None    # секунды
    duration: Optional[float] = None    # секунды
    artwork: Optional[bytes] = field(default=None, repr=False)  # сырые байты картинки
    artwork_url: Optional[str] = None
    fetched_at: float = field(default_factory=time.monotonic)

    @property
    def key(self) -> tuple:
        """Ключ трека — меняется, когда меняется песня."""
        return (self.source_id, self.title, self.artist, self.album)

    def current_position(self) -> Optional[float]:
        """Позиция с учётом времени, прошедшего с момента получения данных."""
        if self.position is None:
            return None
        pos = self.position
        if self.is_playing:
            pos += time.monotonic() - self.fetched_at
        if self.duration:
            pos = min(pos, self.duration)
        return max(0.0, pos)

    def to_dict(self) -> dict:
        return {
            "source": self.source_name,
            "source_id": self.source_id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "is_playing": self.is_playing,
            "position": self.current_position(),
            "duration": self.duration,
            "artwork_url": self.artwork_url,
            "has_artwork": self.artwork is not None,
        }


def format_time(seconds: Optional[float]) -> str:
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def pretty_source_name(raw: str) -> str:
    """Превращает 'chrome.exe' / 'org.mpris.MediaPlayer2.firefox.instance123' / 'com.spotify.client' в 'Chrome' / 'Firefox' / 'Spotify'."""
    low = raw.lower()
    known = {
        "spotify": "Spotify",
        "chrome": "Google Chrome",
        "chromium": "Chromium",
        "msedge": "Microsoft Edge",
        "edge": "Microsoft Edge",
        "firefox": "Firefox",
        "opera": "Opera",
        "brave": "Brave",
        "yandex": "Яндекс Браузер",
        "vivaldi": "Vivaldi",
        "safari": "Safari",
        "vlc": "VLC",
        "yandexmusic": "Яндекс Музыка",
        "yandex.music": "Яндекс Музыка",
        "music": "Apple Music",
        "itunes": "iTunes",
        "zunemusic": "Windows Media Player",
        "mediaplayer": "Windows Media Player",
        "aimp": "AIMP",
        "foobar": "foobar2000",
        "rhythmbox": "Rhythmbox",
        "telegram": "Telegram",
        "discord": "Discord",
        "deezer": "Deezer",
        "tidal": "TIDAL",
        "soundcloud": "SoundCloud",
    }
    # сначала ищем более длинные ключи, чтобы "yandexmusic" победил "yandex"
    for k in sorted(known, key=len, reverse=True):
        if k in low:
            return known[k]
    name = raw.split("!")[0].split("\\")[-1].split("/")[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    if "." in name:
        name = name.split(".")[-1] or name
    return name[:1].upper() + name[1:] if name else raw
