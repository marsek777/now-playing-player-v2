"""Базовый интерфейс бэкенда."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from ..models import TrackInfo


class BackendError(RuntimeError):
    """Бэкенд недоступен или неправильно настроен."""


class MediaBackend(ABC):
    name: str = "base"

    @abstractmethod
    def get_sessions(self) -> List[TrackInfo]:
        """Все источники, которые сейчас сообщают о воспроизведении медиа."""

    def get_current(self, preferred_source: Optional[str] = None) -> Optional[TrackInfo]:
        """Лучший кандидат: выбранный пользователем источник, иначе играющий, иначе первый."""
        sessions = self.get_sessions()
        if not sessions:
            return None
        if preferred_source:
            for s in sessions:
                if s.source_id == preferred_source:
                    return s
        for s in sessions:
            if s.is_playing:
                return s
        return sessions[0]

    @abstractmethod
    def play_pause(self, source_id: str) -> None: ...

    @abstractmethod
    def next(self, source_id: str) -> None: ...

    @abstractmethod
    def previous(self, source_id: str) -> None: ...

    def seek(self, source_id: str, seconds: float) -> None:
        """Перемотка (если источник поддерживает)."""

    def close(self) -> None:
        pass
