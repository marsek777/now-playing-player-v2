"""Окно плеера на tkinter."""
from __future__ import annotations

import io
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import List, Optional

from .backends import MediaBackend
from .models import TrackInfo, format_time

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
except ImportError:  # Pillow необязателен, но без него обложки не показываются
    Image = None  # type: ignore

BG = "#121212"
CARD = "#1c1c1e"
FG = "#f5f5f7"
MUTED = "#9a9aa0"
ACCENT = "#1ed760"
TRACK_BG = "#3a3a3c"
COVER = 300
AUTO = "Авто (что играет)"


class Poller(threading.Thread):
    """Фоновый поток: опрашивает бэкенд и выполняет команды управления, чтобы не тормозить UI."""

    def __init__(self, backend: MediaBackend, interval: float = 1.0) -> None:
        super().__init__(daemon=True)
        self.backend = backend
        self.interval = interval
        self.results: "queue.Queue[tuple]" = queue.Queue()
        self.commands: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                cmd = self.commands.get(timeout=0.01)
                action, source, *args = cmd
                try:
                    if action != "refresh":
                        getattr(self.backend, action)(source, *args)
                except Exception as exc:
                    self.results.put(("error", f"Не удалось выполнить команду: {exc}"))
                time.sleep(0.25)  # даём приложению обновить состояние
            except queue.Empty:
                pass
            try:
                self.results.put(("sessions", self.backend.get_sessions()))
            except Exception as exc:
                self.results.put(("error", str(exc)))
            # ждём интервал, но просыпаемся сразу, если пришла команда
            deadline = time.monotonic() + self.interval
            while time.monotonic() < deadline and self.commands.empty() and not self._stop.is_set():
                time.sleep(0.05)

    def stop(self) -> None:
        self._stop.set()


class IconButton(tk.Canvas):
    """Кнопка, нарисованная фигурами — не зависит от шрифтов с символами."""

    def __init__(self, master, kind: str, command, size: int = 44, circle: bool = False) -> None:
        super().__init__(master, width=size, height=size, bg=BG, highlightthickness=0, cursor="hand2")
        self.kind, self.size, self.circle = kind, size, circle
        self._command = command
        self._hover = False
        self.bind("<Button-1>", lambda e: self._command())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.draw()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self.draw()

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.draw()

    def draw(self) -> None:
        self.delete("all")
        s = self.size
        c = s / 2
        color = ACCENT if self._hover else FG
        if self.circle:
            self.create_oval(2, 2, s - 2, s - 2, fill=color, width=0)
            color = BG
        u = s * (0.16 if self.circle else 0.2)  # масштаб иконки
        if self.kind == "play":
            self.create_polygon(c - u * 0.8, c - u * 1.1, c - u * 0.8, c + u * 1.1, c + u * 1.2, c,
                                fill=color, width=0)
        elif self.kind == "pause":
            self.create_rectangle(c - u, c - u * 1.1, c - u * 0.3, c + u * 1.1, fill=color, width=0)
            self.create_rectangle(c + u * 0.3, c - u * 1.1, c + u, c + u * 1.1, fill=color, width=0)
        elif self.kind == "next":
            self.create_polygon(c - u, c - u, c - u, c + u, c + u * 0.6, c, fill=color, width=0)
            self.create_rectangle(c + u * 0.6, c - u, c + u, c + u, fill=color, width=0)
        elif self.kind == "prev":
            self.create_polygon(c + u, c - u, c + u, c + u, c - u * 0.6, c, fill=color, width=0)
            self.create_rectangle(c - u, c - u, c - u * 0.6, c + u, fill=color, width=0)


class PlayerApp:
    def __init__(self, backend: MediaBackend, interval: float = 1.0,
                 local=None, store=None, spotify=None) -> None:
        self.backend = backend
        self.local = local
        self.root = tk.Tk()
        self.root.title("Now Playing")
        self.root.configure(bg=BG)
        with_panel = local is not None and store is not None
        self.root.geometry("920x640" if with_panel else "380x610")
        self.root.minsize(760 if with_panel else 340, 580)
        self.ui_queue: "queue.Queue" = queue.Queue()
        self.left = tk.Frame(self.root, bg=BG, width=380)
        self.left.pack(side="left", fill="y")
        self.left.pack_propagate(False)

        self.sessions: List[TrackInfo] = []
        self.current: Optional[TrackInfo] = None
        self.preferred: Optional[str] = None
        self._cover_key = None
        self._cover_img = None
        self._placeholder = self._make_placeholder()

        self._build()
        self.panel = None
        if with_panel:
            from .playlist_panel import PlaylistPanel
            tk.Frame(self.root, bg=TRACK_BG, width=1).pack(side="left", fill="y")
            self.panel = PlaylistPanel(self.root, self, store, local, spotify)
            self.panel.pack(side="left", fill="both", expand=True)
        self.poller = Poller(backend, interval)
        self.poller.start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._drain)
        self.root.after(250, self._tick)

    # --- построение интерфейса -----------------------------------------
    def _build(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Dark.TCombobox", fieldbackground=CARD, background=CARD,
                        foreground=FG, arrowcolor=FG, bordercolor=TRACK_BG,
                        lightcolor=CARD, darkcolor=CARD, padding=6)
        style.map("Dark.TCombobox", fieldbackground=[("readonly", CARD)],
                  foreground=[("readonly", FG)], selectbackground=[("readonly", CARD)],
                  selectforeground=[("readonly", FG)])

        top = tk.Frame(self.left, bg=BG)
        top.pack(fill="x", padx=20, pady=(16, 8))
        tk.Label(top, text="ИСТОЧНИК", bg=BG, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.source_var = tk.StringVar(value=AUTO)
        self.source_box = ttk.Combobox(top, textvariable=self.source_var, state="readonly",
                                       style="Dark.TCombobox", values=[AUTO])
        self.source_box.pack(fill="x", pady=(4, 0))
        self.source_box.bind("<<ComboboxSelected>>", self._on_source)

        self.cover = tk.Label(self.left, bg=BG, image=self._placeholder, bd=0)
        self.cover.pack(pady=(10, 14))

        self.title_lbl = tk.Label(self.left, text="Ничего не играет", bg=BG, fg=FG,
                                  font=("Segoe UI", 15, "bold"), wraplength=330, justify="center")
        self.title_lbl.pack(padx=20)
        self.artist_lbl = tk.Label(self.left, text="Включите музыку в браузере или приложении",
                                   bg=BG, fg=MUTED, font=("Segoe UI", 11), wraplength=330, justify="center")
        self.artist_lbl.pack(padx=20, pady=(2, 0))
        self.album_lbl = tk.Label(self.left, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9),
                                  wraplength=330, justify="center")
        self.album_lbl.pack(padx=20)

        prog = tk.Frame(self.left, bg=BG)
        prog.pack(fill="x", padx=24, pady=(16, 0))
        self.bar = tk.Canvas(prog, height=6, bg=BG, highlightthickness=0)
        self.bar.pack(fill="x")
        self.bar.config(cursor="hand2")
        self.bar.bind("<Button-1>", self._on_seek)
        times = tk.Frame(prog, bg=BG)
        times.pack(fill="x", pady=(4, 0))
        self.pos_lbl = tk.Label(times, text="--:--", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.pos_lbl.pack(side="left")
        self.dur_lbl = tk.Label(times, text="--:--", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.dur_lbl.pack(side="right")

        controls = tk.Frame(self.left, bg=BG)
        controls.pack(pady=(6, 4))
        IconButton(controls, "prev", lambda: self._send("previous")).pack(side="left", padx=10)
        self.play_btn = IconButton(controls, "play", lambda: self._send("play_pause"), size=58, circle=True)
        self.play_btn.pack(side="left", padx=10)
        IconButton(controls, "next", lambda: self._send("next")).pack(side="left", padx=10)

        bottom = tk.Frame(self.left, bg=BG)
        bottom.pack(fill="x", side="bottom", padx=20, pady=(0, 10))
        self.status_lbl = tk.Label(bottom, text=f"Бэкенд: {self.backend.name}", bg=BG, fg=MUTED,
                                   font=("Segoe UI", 8), anchor="w")
        self.status_lbl.pack(side="left")
        self.ontop_var = tk.BooleanVar(value=False)
        tk.Checkbutton(bottom, text="Поверх окон", variable=self.ontop_var, command=self._toggle_top,
                       bg=BG, fg=MUTED, selectcolor=CARD, activebackground=BG, activeforeground=FG,
                       font=("Segoe UI", 8), bd=0, highlightthickness=0).pack(side="right")

        def hotkey(action):
            def handler(event):
                if isinstance(event.widget, (tk.Entry, ttk.Entry, ttk.Combobox)):
                    return None
                self._send(action)
                return "break"
            return handler

        self.root.bind("<space>", hotkey("play_pause"))
        self.root.bind("<Right>", hotkey("next"))
        self.root.bind("<Left>", hotkey("previous"))

    def _make_placeholder(self):
        if Image is None:
            return tk.PhotoImage(width=COVER, height=COVER)
        img = Image.new("RGB", (COVER, COVER), CARD)
        d = ImageDraw.Draw(img)
        d.ellipse((95, 95, 205, 205), outline=TRACK_BG, width=8)
        d.ellipse((140, 140, 160, 160), fill=TRACK_BG)
        return ImageTk.PhotoImage(self._rounded(img))

    @staticmethod
    def _rounded(img):
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, *img.size), radius=18, fill=255)
        out = Image.new("RGB", img.size, BG)
        out.paste(img, (0, 0), mask)
        return out

    # --- события ---------------------------------------------------------
    def _on_source(self, _event=None) -> None:
        value = self.source_var.get()
        self.preferred = None
        for s in self.sessions:
            if self._label(s) == value:
                self.preferred = s.source_id
        self._apply()

    def _toggle_top(self) -> None:
        self.root.attributes("-topmost", self.ontop_var.get())

    def _send(self, action: str, *args) -> None:
        if self.current:
            self.poller.commands.put((action, self.current.source_id, *args))

    def _on_seek(self, event) -> None:
        t = self.current
        if not t or not t.duration:
            return
        frac = min(max(event.x / max(self.bar.winfo_width(), 1), 0.0), 1.0)
        target = frac * t.duration
        t.position, t.fetched_at = target, __import__("time").monotonic()  # мгновенный отклик
        self._send("seek", target)

    def run_bg(self, func, on_done=None, on_error=None) -> None:
        """Выполнить func() в фоне, результат вернуть в UI-поток."""
        def worker():
            try:
                res = func()
                if on_done:
                    self.ui_queue.put(lambda: on_done(res))
            except Exception as exc:
                err = on_error or (lambda e: self.set_status(f"Ошибка: {e}"))
                self.ui_queue.put(lambda: err(exc))
        threading.Thread(target=worker, daemon=True).start()

    def set_status(self, text: str, seconds: float = 6.0) -> None:
        import time as _t
        self._status_until = _t.monotonic() + seconds
        self.status_lbl.config(text=text[:60])

    def refresh_now(self) -> None:
        self.poller.commands.put(("refresh", ""))

    @staticmethod
    def _label(s: TrackInfo) -> str:
        state = "▶" if s.is_playing else "❚❚"
        return f"{state}  {s.source_name} — {s.title or 'без названия'}"

    # --- обновление ------------------------------------------------------
    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.poller.results.get_nowait()
                if kind == "sessions":
                    self.sessions = payload
                    if time.monotonic() > getattr(self, "_status_until", 0):
                        self.status_lbl.config(text=f"Источников: {len(payload)} · {self.backend.name}"[:60])
                    self._apply()
                    if self.panel:
                        self.panel.on_now_playing(self.current)
                else:
                    self.status_lbl.config(text=f"Ошибка: {payload}"[:70])
        except queue.Empty:
            pass
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _pick(self) -> Optional[TrackInfo]:
        if not self.sessions:
            return None
        if self.preferred:
            for s in self.sessions:
                if s.source_id == self.preferred:
                    return s
        for s in self.sessions:
            if s.is_playing:
                return s
        return self.sessions[0]

    def _apply(self) -> None:
        labels = [AUTO] + [self._label(s) for s in self.sessions]
        self.source_box.config(values=labels)
        if self.preferred:
            match = next((self._label(s) for s in self.sessions if s.source_id == self.preferred), None)
            self.source_var.set(match or AUTO)
            if match is None:
                self.preferred = None

        track = self._pick()
        self.current = track
        if track is None:
            self.title_lbl.config(text="Ничего не играет")
            self.artist_lbl.config(text="Включите музыку в браузере или приложении")
            err = getattr(self.backend, "system_error", None)
            self.album_lbl.config(text=err or "")
            self.play_btn.set_kind("play")
            self._set_cover(None, None)
            self.root.title("Now Playing")
            return

        self.title_lbl.config(text=track.title or "Без названия")
        self.artist_lbl.config(text=track.artist or "Неизвестный исполнитель")
        album = f"{track.album} · {track.source_name}" if track.album else track.source_name
        self.album_lbl.config(text=album)
        self.play_btn.set_kind("pause" if track.is_playing else "play")
        self._set_cover(track.key, track.artwork)
        prefix = "▶ " if track.is_playing else "❚❚ "
        self.root.title(prefix + (f"{track.artist} — {track.title}" if track.artist else track.title))

    def _set_cover(self, key, data: Optional[bytes]) -> None:
        if key == self._cover_key and key is not None:
            return
        self._cover_key = key
        if not data or Image is None:
            self.cover.config(image=self._placeholder)
            return
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            # обложки YouTube 16:9 — обрезаем по центру до квадрата
            w, h = img.size
            side = min(w, h)
            img = img.crop(((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
            img = img.resize((COVER, COVER), Image.LANCZOS)
            self._cover_img = ImageTk.PhotoImage(self._rounded(img))
            self.cover.config(image=self._cover_img)
        except Exception:
            self.cover.config(image=self._placeholder)

    def _tick(self) -> None:
        """Плавно двигаем прогресс-бар между опросами."""
        self.bar.delete("all")
        width = max(self.bar.winfo_width(), 1)
        self.bar.create_rectangle(0, 1, width, 5, fill=TRACK_BG, width=0)
        t = self.current
        pos = t.current_position() if t else None
        dur = t.duration if t else None
        if pos is not None and dur:
            x = int(width * min(pos / dur, 1.0))
            self.bar.create_rectangle(0, 1, x, 5, fill=ACCENT, width=0)
            self.bar.create_oval(x - 5, -2, x + 5, 8, fill=FG, width=0)
        self.pos_lbl.config(text=format_time(pos))
        self.dur_lbl.config(text=format_time(dur))
        self.root.after(250, self._tick)

    def close(self) -> None:
        self.poller.stop()
        try:
            self.backend.close()
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_gui(backend: MediaBackend, interval: float = 1.0, local=None, store=None, spotify=None) -> None:
    PlayerApp(backend, interval, local=local, store=store, spotify=spotify).run()
