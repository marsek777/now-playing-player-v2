"""Демо-бэкенд: имитирует два источника. Нужен для проверки интерфейса без реальной музыки."""
from __future__ import annotations

import io
import time
from typing import List

from ..models import TrackInfo
from .base import MediaBackend

PLAYLIST = [
    ("Blinding Lights", "The Weeknd", "After Hours", 200),
    ("Кукла колдуна", "Король и Шут", "Акустический альбом", 205),
    ("Bohemian Rhapsody", "Queen", "A Night at the Opera", 354),
]


def _make_cover(seed: int) -> bytes | None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    palettes = [((255, 94, 98), (255, 195, 113)), ((67, 206, 162), (24, 90, 157)), ((142, 45, 226), (74, 0, 224))]
    a, b = palettes[seed % len(palettes)]
    img = Image.new("RGB", (300, 300))
    d = ImageDraw.Draw(img)
    for y in range(300):
        t = y / 299
        d.line([(0, y), (300, y)], fill=tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    d.ellipse((90, 90, 210, 210), outline=(255, 255, 255), width=6)
    d.ellipse((140, 140, 160, 160), fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class DemoBackend(MediaBackend):
    name = "demo"

    def __init__(self) -> None:
        self._index = 0
        self._playing = True
        self._started = time.monotonic() - 42
        self._paused_pos = 0.0
        self._covers = [_make_cover(i) for i in range(len(PLAYLIST))]

    def _pos(self) -> float:
        dur = PLAYLIST[self._index][3]
        if not self._playing:
            return self._paused_pos
        pos = time.monotonic() - self._started
        if pos >= dur:
            self.next("demo:player")
            return 0.0
        return pos

    def get_sessions(self) -> List[TrackInfo]:
        title, artist, album, dur = PLAYLIST[self._index]
        main = TrackInfo(
            source_id="demo:player", source_name="Демо-плеер",
            title=title, artist=artist, album=album,
            is_playing=self._playing, position=self._pos(), duration=dur,
            artwork=self._covers[self._index],
        )
        browser = TrackInfo(
            source_id="demo:browser", source_name="Google Chrome",
            title="Lo-fi hip hop radio", artist="YouTube", is_playing=False,
            artwork=self._covers[2],
        )
        return [main, browser]

    def play_pause(self, source_id: str) -> None:
        if self._playing:
            self._paused_pos = self._pos()
        else:
            self._started = time.monotonic() - self._paused_pos
        self._playing = not self._playing

    def next(self, source_id: str) -> None:
        self._index = (self._index + 1) % len(PLAYLIST)
        self._started = time.monotonic()
        self._paused_pos = 0.0

    def previous(self, source_id: str) -> None:
        self._index = (self._index - 1) % len(PLAYLIST)
        self._started = time.monotonic()
        self._paused_pos = 0.0

    def seek(self, source_id: str, seconds: float) -> None:
        if self._playing:
            self._started = time.monotonic() - seconds
        else:
            self._paused_pos = seconds
