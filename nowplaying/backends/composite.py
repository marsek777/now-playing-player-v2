"""Объединяет системный бэкенд (что играет в браузере/приложениях) и встроенный плеер."""
from __future__ import annotations

from typing import List, Optional

from ..local_player import SOURCE_ID, LocalPlayer
from ..models import TrackInfo
from .base import MediaBackend


class CompositeBackend(MediaBackend):
    def __init__(self, system: Optional[MediaBackend], local: LocalPlayer,
                 system_error: Optional[str] = None) -> None:
        self.system = system
        self.local = local
        self.system_error = system_error
        self.name = (system.name if system else "без системного бэкенда") + " + свой плеер"

    def get_sessions(self) -> List[TrackInfo]:
        result: List[TrackInfo] = []
        mine = self.local.session()
        if mine:
            result.append(mine)
        if self.system:
            result.extend(self.system.get_sessions())
        return result

    def _target(self, source_id: str):
        return self.local if source_id == SOURCE_ID else self.system

    def play_pause(self, source_id: str) -> None:
        t = self._target(source_id)
        if t:
            t.play_pause(source_id)

    def next(self, source_id: str) -> None:
        t = self._target(source_id)
        if t:
            t.next(source_id)

    def previous(self, source_id: str) -> None:
        t = self._target(source_id)
        if t:
            t.previous(source_id)

    def seek(self, source_id: str, seconds: float) -> None:
        t = self._target(source_id)
        if t:
            t.seek(source_id, seconds)

    def close(self) -> None:
        self.local.stop()
        if self.system:
            self.system.close()
