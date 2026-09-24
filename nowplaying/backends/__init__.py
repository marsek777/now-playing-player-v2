"""Выбор бэкенда под текущую ОС."""
from __future__ import annotations

import sys

from .base import BackendError, MediaBackend


def get_backend(demo: bool = False) -> MediaBackend:
    if demo:
        from .demo import DemoBackend
        return DemoBackend()
    if sys.platform == "win32":
        from .windows import WindowsBackend
        return WindowsBackend()
    if sys.platform == "darwin":
        from .macos import MacBackend
        return MacBackend()
    if sys.platform.startswith("linux") or "bsd" in sys.platform:
        from .linux import LinuxBackend
        return LinuxBackend()
    raise BackendError(f"Платформа {sys.platform} не поддерживается")


__all__ = ["get_backend", "MediaBackend", "BackendError"]
