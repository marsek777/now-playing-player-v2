"""macOS.

1) Если установлен `media-control` (brew install media-control) — читаем системный
   Now Playing (работает и для браузеров: Safari, Chrome, Яндекс и т.д.).
2) Иначе — AppleScript для Spotify и Apple Music.
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import urllib.request
from typing import List, Optional

from ..models import TrackInfo, pretty_source_name
from .base import MediaBackend

SEP = "\x1f"

APPLESCRIPT_TEMPLATE = '''
if application "{app}" is running then
    tell application "{app}"
        set st to player state as string
        if st is "stopped" then return ""
        set t to current track
        set out to (name of t) & (ASCII character 31) & (artist of t) & (ASCII character 31) & (album of t)
        set out to out & (ASCII character 31) & st & (ASCII character 31) & (player position as string)
        set out to out & (ASCII character 31) & ((duration of t) as string)
        {art}
        return out
    end tell
end if
return ""
'''

SPOTIFY_ART = 'set out to out & (ASCII character 31) & (artwork url of t)'
MUSIC_ART = 'set out to out & (ASCII character 31) & ""'


def _osascript(script: str) -> str:
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _num(v) -> Optional[float]:
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


class MacBackend(MediaBackend):
    name = "macos"

    def __init__(self) -> None:
        self._media_control = shutil.which("media-control")
        self._art_cache: dict = {}

    # --- media-control ----------------------------------------------------
    def _from_media_control(self) -> Optional[TrackInfo]:
        try:
            r = subprocess.run(
                [self._media_control, "get"], capture_output=True, text=True, timeout=3
            )
            data = json.loads(r.stdout or "null")
        except Exception:
            return None
        if not data or not (data.get("title") or data.get("artist")):
            return None
        art = None
        if data.get("artworkData"):
            try:
                art = base64.b64decode(data["artworkData"])
            except Exception:
                art = None
        bundle = data.get("bundleIdentifier") or data.get("parentApplicationBundleIdentifier") or "system"
        return TrackInfo(
            source_id="media-control:" + bundle,
            source_name=pretty_source_name(bundle),
            title=data.get("title") or "",
            artist=data.get("artist") or "",
            album=data.get("album") or "",
            is_playing=bool(data.get("playing")),
            position=_num(data.get("elapsedTimeNow", data.get("elapsedTime"))),
            duration=_num(data.get("duration")),
            artwork=art,
        )

    # --- AppleScript ------------------------------------------------------
    def _from_applescript(self, app: str) -> Optional[TrackInfo]:
        art_line = SPOTIFY_ART if app == "Spotify" else MUSIC_ART
        out = _osascript(APPLESCRIPT_TEMPLATE.format(app=app, art=art_line))
        if not out:
            return None
        parts = out.split(SEP)
        if len(parts) < 6:
            return None
        title, artist, album, state, pos, dur = parts[:6]
        art_url = parts[6] if len(parts) > 6 and parts[6] else None
        duration = _num(dur)
        if duration and app == "Spotify":
            duration /= 1000  # Spotify отдаёт миллисекунды
        return TrackInfo(
            source_id="applescript:" + app,
            source_name=pretty_source_name(app),
            title=title,
            artist=artist,
            album=album,
            is_playing=state == "playing",
            position=_num(pos),
            duration=duration,
            artwork_url=art_url,
            artwork=self._load_art(art_url),
        )

    def _load_art(self, url: Optional[str]) -> Optional[bytes]:
        if not url:
            return None
        if url not in self._art_cache:
            try:
                with urllib.request.urlopen(url, timeout=4) as resp:
                    self._art_cache[url] = resp.read(8 * 1024 * 1024)
            except Exception:
                self._art_cache[url] = None
        return self._art_cache[url]

    def get_sessions(self) -> List[TrackInfo]:
        result: List[TrackInfo] = []
        if self._media_control:
            info = self._from_media_control()
            if info:
                result.append(info)
        for app in ("Spotify", "Music"):
            info = self._from_applescript(app)
            if info and not any(r.title == info.title and r.artist == info.artist for r in result):
                result.append(info)
        return result

    # --- управление -------------------------------------------------------
    def _control(self, source_id: str, mc_cmd: str, as_cmd: str) -> None:
        if source_id.startswith("applescript:"):
            app = source_id.split(":", 1)[1]
            _osascript(f'tell application "{app}" to {as_cmd}')
        elif self._media_control:
            subprocess.run([self._media_control, mc_cmd], capture_output=True, timeout=3)

    def play_pause(self, source_id: str) -> None:
        self._control(source_id, "toggle-play-pause", "playpause")

    def next(self, source_id: str) -> None:
        self._control(source_id, "next-track", "next track")

    def previous(self, source_id: str) -> None:
        self._control(source_id, "previous-track", "previous track")

    def seek(self, source_id: str, seconds: float) -> None:
        if source_id.startswith("applescript:"):
            app = source_id.split(":", 1)[1]
            _osascript(f'tell application "{app}" to set player position to {seconds:.1f}')
        elif self._media_control:
            subprocess.run([self._media_control, "seek", f"{seconds:.1f}"], capture_output=True, timeout=3)
