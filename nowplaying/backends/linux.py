"""Linux: MPRIS через утилиту playerctl.

Chrome/Chromium, Firefox, Spotify, VLC, Rhythmbox, Telegram и почти все плееры
регистрируют себя в D-Bus по стандарту MPRIS — playerctl умеет их читать и управлять ими.
"""
from __future__ import annotations

import shutil
import subprocess
import urllib.parse
import urllib.request
from typing import List, Optional

from ..models import TrackInfo, pretty_source_name
from .base import BackendError, MediaBackend

SEP = "\x1f"
FIELDS = [
    "playerName",
    "playerInstance",
    "status",
    "xesam:title",
    "xesam:artist",
    "xesam:album",
    "mpris:artUrl",
    "mpris:length",
    "position",
]
FORMAT = SEP.join("{{" + f + "}}" for f in FIELDS)


def parse_playerctl_line(line: str) -> Optional[TrackInfo]:
    parts = line.rstrip("\n").split(SEP)
    if len(parts) != len(FIELDS):
        return None
    data = dict(zip(FIELDS, parts))
    instance = data["playerInstance"] or data["playerName"]
    if not instance:
        return None

    def micro(v: str) -> Optional[float]:
        try:
            return int(v) / 1_000_000 if v else None
        except ValueError:
            return None

    duration = micro(data["mpris:length"])
    return TrackInfo(
        source_id=instance,
        source_name=pretty_source_name(data["playerName"] or instance),
        title=data["xesam:title"],
        artist=data["xesam:artist"],
        album=data["xesam:album"],
        is_playing=data["status"].lower() == "playing",
        position=micro(data["position"]) if duration else None,
        duration=duration if duration and duration > 0 else None,
        artwork_url=data["mpris:artUrl"] or None,
    )


class LinuxBackend(MediaBackend):
    name = "linux-mpris"

    def __init__(self) -> None:
        self._bin = shutil.which("playerctl")
        if not self._bin:
            raise BackendError(
                "Не найден playerctl. Установите его: sudo apt install playerctl "
                "(Fedora: sudo dnf install playerctl, Arch: sudo pacman -S playerctl)"
            )
        self._art_cache: dict = {}

    def _call(self, *args: str) -> str:
        try:
            out = subprocess.run(
                [self._bin, *args], capture_output=True, text=True, timeout=3
            )
        except subprocess.TimeoutExpired:
            return ""
        return out.stdout if out.returncode == 0 else ""

    def _load_art(self, url: Optional[str]) -> Optional[bytes]:
        if not url:
            return None
        if url in self._art_cache:
            return self._art_cache[url]
        data = None
        try:
            if url.startswith("file://"):
                path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
                with open(path, "rb") as fh:
                    data = fh.read()
            elif url.startswith(("http://", "https://")):
                req = urllib.request.Request(url, headers={"User-Agent": "NowPlayingPlayer"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = resp.read(8 * 1024 * 1024)
        except Exception:
            data = None
        if len(self._art_cache) > 32:
            self._art_cache.clear()
        self._art_cache[url] = data
        return data

    def get_sessions(self) -> List[TrackInfo]:
        raw = self._call("--all-players", "metadata", "--format", FORMAT)
        result: List[TrackInfo] = []
        for line in raw.splitlines():
            info = parse_playerctl_line(line)
            if info and (info.title or info.artist):
                info.artwork = self._load_art(info.artwork_url)
                result.append(info)
        return result

    def play_pause(self, source_id: str) -> None:
        self._call("-p", source_id, "play-pause")

    def next(self, source_id: str) -> None:
        self._call("-p", source_id, "next")

    def previous(self, source_id: str) -> None:
        self._call("-p", source_id, "previous")

    def seek(self, source_id: str, seconds: float) -> None:
        self._call("-p", source_id, "position", f"{max(0.0, seconds):.1f}")
