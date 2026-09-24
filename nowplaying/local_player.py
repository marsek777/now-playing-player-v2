"""Встроенный плеер для своих плейлистов (pygame-ce / SDL_mixer)."""
from __future__ import annotations

import os
import random
import threading
import time
from typing import List, Optional

from .library import PlaylistTrack, read_cover
from .models import TrackInfo

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

SOURCE_ID = "local:player"


class LocalPlayer:
    """Играет список файлов. Потокобезопасен: все вызовы pygame под одной блокировкой."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mixer = None
        self.error: Optional[str] = None
        self.queue: List[PlaylistTrack] = []
        self.playlist_id: Optional[str] = None
        self.index = -1
        self.playing = False
        self.paused = False
        self.offset = 0.0          # позиция, с которой запущен текущий play()
        self.volume = 0.8
        self.shuffle = False
        self.repeat = True
        self._cover_cache: dict = {}
        self._started_at = 0.0

    # --- инициализация ------------------------------------------------
    def _ensure_mixer(self):
        if self._mixer is None:
            try:
                import pygame

                pygame.mixer.init()
                pygame.mixer.music.set_volume(self.volume)
                self._mixer = pygame.mixer
            except Exception as exc:  # нет pygame или аудиоустройства
                self.error = f"Встроенный плеер недоступен: {exc}"
                raise RuntimeError(self.error) from exc
        return self._mixer

    @property
    def available(self) -> bool:
        try:
            import pygame  # noqa: F401
            return True
        except ImportError:
            return False

    # --- воспроизведение ------------------------------------------------
    def load(self, tracks: List[PlaylistTrack], playlist_id: Optional[str], index: int = 0) -> None:
        with self._lock:
            self.queue = list(tracks)
            self.playlist_id = playlist_id
            self.play_index(index)

    def play_index(self, index: int, start: float = 0.0) -> None:
        with self._lock:
            if not self.queue:
                return
            mixer = self._ensure_mixer()
            # пропускаем отсутствующие/битые файлы
            for _ in range(len(self.queue)):
                index %= len(self.queue)
                track = self.queue[index]
                try:
                    mixer.music.load(track.id)
                    mixer.music.play(start=start) if start else mixer.music.play()
                    self.index = index
                    self.offset = start
                    self.playing, self.paused = True, False
                    self._started_at = time.monotonic()
                    self.error = None
                    return
                except Exception as exc:
                    self.error = f"Не удалось открыть {os.path.basename(track.id)}: {exc}"
                    index += 1
                    start = 0.0
            self.stop()

    def stop(self) -> None:
        with self._lock:
            if self._mixer:
                self._mixer.music.stop()
            self.playing = self.paused = False

    def play_pause(self, _source_id: str = SOURCE_ID) -> None:
        with self._lock:
            if self.index < 0:
                if self.queue:
                    self.play_index(0)
                return
            mixer = self._ensure_mixer()
            if self.playing and not self.paused:
                mixer.music.pause()
                self.paused = True
            elif self.paused:
                mixer.music.unpause()
                self.paused = False
            else:
                self.play_index(self.index)

    def next(self, _source_id: str = SOURCE_ID) -> None:
        with self._lock:
            if not self.queue:
                return
            if self.shuffle and len(self.queue) > 1:
                choices = [i for i in range(len(self.queue)) if i != self.index]
                self.play_index(random.choice(choices))
            else:
                self.play_index(self.index + 1)

    def previous(self, _source_id: str = SOURCE_ID) -> None:
        with self._lock:
            if not self.queue:
                return
            # как в обычных плеерах: если прошло > 3 сек — в начало трека
            if (self.position() or 0) > 3:
                self.play_index(self.index)
            else:
                self.play_index(self.index - 1)

    def seek(self, _source_id: str, seconds: float) -> None:
        with self._lock:
            if self.index < 0:
                return
            was_paused = self.paused
            self.play_index(self.index, start=max(0.0, seconds))
            if was_paused and self._mixer:
                self._mixer.music.pause()
                self.paused = True

    def set_volume(self, value: float) -> None:
        with self._lock:
            self.volume = max(0.0, min(1.0, value))
            if self._mixer:
                self._mixer.music.set_volume(self.volume)

    # --- состояние ------------------------------------------------------
    def position(self) -> Optional[float]:
        with self._lock:
            if self.index < 0 or not self._mixer:
                return None
            ms = self._mixer.music.get_pos()
            return self.offset + (ms / 1000 if ms >= 0 else 0)

    def poll(self) -> None:
        """Вызывается регулярно: переключает на следующий трек, когда текущий закончился."""
        with self._lock:
            if not (self.playing and not self.paused and self._mixer):
                return
            if time.monotonic() - self._started_at < 1.0:
                return
            if not self._mixer.music.get_busy():
                last = self.index == len(self.queue) - 1
                if last and not self.repeat and not self.shuffle:
                    self.playing = False
                    self.index = -1 if not self.queue else self.index
                    self.stop()
                else:
                    self.next()

    @property
    def current(self) -> Optional[PlaylistTrack]:
        with self._lock:
            if 0 <= self.index < len(self.queue):
                return self.queue[self.index]
            return None

    def session(self) -> Optional[TrackInfo]:
        self.poll()
        with self._lock:
            track = self.current
            if track is None or not (self.playing or self.paused):
                return None
            if track.id not in self._cover_cache:
                if len(self._cover_cache) > 32:
                    self._cover_cache.clear()
                self._cover_cache[track.id] = read_cover(track.id)
            return TrackInfo(
                source_id=SOURCE_ID,
                source_name="Мой плейлист",
                title=track.title,
                artist=track.artist,
                album=track.album,
                is_playing=self.playing and not self.paused,
                position=self.position(),
                duration=track.duration,
                artwork=self._cover_cache[track.id],
            )
