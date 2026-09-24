"""Windows: System Media Transport Controls (SMTC).

Это тот же механизм, который показывает плашку громкости/медиа в Windows 10/11.
В него пишут Chrome, Edge, Firefox, Opera, Яндекс Браузер, Spotify, Яндекс Музыка,
Windows Media Player, AIMP (с плагином) и многие другие приложения.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import threading
from typing import List, Optional

from ..models import TrackInfo, pretty_source_name
from .base import BackendError, MediaBackend

try:  # современный pywinrt
    from winrt.windows.media.control import (  # type: ignore
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )
    from winrt.windows.storage.streams import (  # type: ignore
        Buffer,
        DataReader,
        InputStreamOptions,
    )
except ImportError:  # pragma: no cover - старый пакет winsdk
    try:
        from winsdk.windows.media.control import (  # type: ignore
            GlobalSystemMediaTransportControlsSessionManager as MediaManager,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
        )
        from winsdk.windows.storage.streams import (  # type: ignore
            Buffer,
            DataReader,
            InputStreamOptions,
        )
    except ImportError as exc:
        raise BackendError(
            "Не найдены пакеты WinRT. Установите: pip install -r requirements.txt"
        ) from exc


MAX_THUMB = 8 * 1024 * 1024


def _td_seconds(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    # на всякий случай: TimeSpan в тиках по 100 нс
    duration = getattr(value, "duration", None)
    if duration is not None:
        return duration / 10_000_000
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class WindowsBackend(MediaBackend):
    name = "windows-smtc"

    def __init__(self) -> None:
        # Один постоянный event loop в отдельном потоке — WinRT async-вызовы идут через него.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._manager = self._run(MediaManager.request_async())
        self._thumb_cache: dict = {}

    def _run(self, coro, timeout: float = 5.0):
        async def wrapper():
            return await coro

        fut = asyncio.run_coroutine_threadsafe(wrapper(), self._loop)
        return fut.result(timeout)

    # --- чтение ---------------------------------------------------------
    async def _read_thumbnail(self, thumb_ref) -> Optional[bytes]:
        stream = await thumb_ref.open_read_async()
        size = min(int(stream.size), MAX_THUMB)
        if size <= 0:
            return None
        buf = Buffer(size)
        await stream.read_async(buf, buf.capacity, InputStreamOptions.READ_AHEAD)
        length = buf.length
        try:
            return bytes(memoryview(buf))[:length]  # pywinrt: Buffer поддерживает buffer protocol
        except TypeError:
            reader = DataReader.from_buffer(buf)
            data = bytearray(length)
            reader.read_bytes(data)
            return bytes(data)

    async def _session_info(self, session) -> Optional[TrackInfo]:
        app_id = session.source_app_user_model_id or "unknown"
        try:
            props = await session.try_get_media_properties_async()
        except Exception:
            return None
        if props is None:
            return None

        playback = session.get_playback_info()
        is_playing = bool(playback and playback.playback_status == PlaybackStatus.PLAYING)

        position = duration = None
        try:
            tl = session.get_timeline_properties()
            end = _td_seconds(tl.end_time) or 0.0
            start = _td_seconds(tl.start_time) or 0.0
            if end > start:
                duration = end - start
                position = (_td_seconds(tl.position) or 0.0) - start
                # SMTC обновляет позицию не постоянно — досчитываем от last_updated_time
                last = getattr(tl, "last_updated_time", None)
                if is_playing and isinstance(last, dt.datetime) and last.year > 1970:
                    now = dt.datetime.now(last.tzinfo or dt.timezone.utc)
                    delta = (now - last).total_seconds()
                    if 0 < delta < 24 * 3600:
                        position += delta
                position = max(0.0, min(position, duration))
        except Exception:
            pass

        title = props.title or ""
        artist = props.artist or props.album_artist or ""
        album = props.album_title or ""

        artwork = None
        cache_key = (app_id, title, artist, album)
        if cache_key in self._thumb_cache:
            artwork = self._thumb_cache[cache_key]
        elif props.thumbnail is not None:
            try:
                artwork = await self._read_thumbnail(props.thumbnail)
            except Exception:
                artwork = None
            if len(self._thumb_cache) > 32:
                self._thumb_cache.clear()
            self._thumb_cache[cache_key] = artwork

        return TrackInfo(
            source_id=app_id,
            source_name=pretty_source_name(app_id),
            title=title,
            artist=artist,
            album=album,
            is_playing=is_playing,
            position=position,
            duration=duration,
            artwork=artwork,
        )

    async def _all(self) -> List[TrackInfo]:
        result: List[TrackInfo] = []
        current = self._manager.get_current_session()
        current_id = current.source_app_user_model_id if current else None
        for session in self._manager.get_sessions():
            info = await self._session_info(session)
            if info and (info.title or info.artist):
                result.append(info)
        # текущая (по мнению Windows) сессия — первой
        result.sort(key=lambda t: (t.source_id != current_id,))
        return result

    def get_sessions(self) -> List[TrackInfo]:
        return self._run(self._all())

    # --- управление -----------------------------------------------------
    def _find(self, source_id: str):
        for s in self._manager.get_sessions():
            if s.source_app_user_model_id == source_id:
                return s
        return self._manager.get_current_session()

    def _control(self, source_id: str, method: str) -> None:
        session = self._find(source_id)
        if session is not None:
            self._run(getattr(session, method)())

    def play_pause(self, source_id: str) -> None:
        self._control(source_id, "try_toggle_play_pause_async")

    def next(self, source_id: str) -> None:
        self._control(source_id, "try_skip_next_async")

    def previous(self, source_id: str) -> None:
        self._control(source_id, "try_skip_previous_async")

    def seek(self, source_id: str, seconds: float) -> None:
        session = self._find(source_id)
        if session is not None:
            self._run(session.try_change_playback_position_async(int(seconds * 10_000_000)))

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
