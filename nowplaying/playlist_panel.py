"""Панель «Плейлист»: свои файлы и плейлисты Spotify, выбор и запуск трека."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import List, Optional

from .library import AUDIO_EXTS, PlaylistInfo, PlaylistStore, PlaylistTrack, read_m3u, scan_folder
from .local_player import SOURCE_ID, LocalPlayer
from .models import TrackInfo, format_time

BG = "#121212"
CARD = "#1c1c1e"
FG = "#f5f5f7"
MUTED = "#9a9aa0"
ACCENT = "#1ed760"
LINE = "#3a3a3c"

MINE = "Мои файлы"
SPOTIFY = "Spotify"


def _btn(master, text: str, command, accent: bool = False) -> tk.Button:
    return tk.Button(master, text=text, command=command, bg=ACCENT if accent else CARD,
                     fg=BG if accent else FG, activebackground=LINE, activeforeground=FG,
                     relief="flat", bd=0, padx=10, pady=4, cursor="hand2", font=("Segoe UI", 9),
                     highlightthickness=0)


class PlaylistPanel(tk.Frame):
    def __init__(self, master, app, store: PlaylistStore, local: LocalPlayer, spotify=None) -> None:
        super().__init__(master, bg=BG)
        self.app = app
        self.store = store
        self.local = local
        self.spotify = spotify
        self.mode = MINE
        self.playlists: List[PlaylistInfo] = []
        self.tracks: List[PlaylistTrack] = []
        self.visible: List[int] = []           # индексы self.tracks, показанные в таблице
        self.playlist_id: Optional[str] = None
        self.playing_id: Optional[str] = None   # id трека, который подсвечиваем
        self._now: Optional[TrackInfo] = None
        self._build()
        self.set_mode(MINE)

    # ------------------------------------------------------------ интерфейс
    def _build(self) -> None:
        style = ttk.Style(self)
        style.configure("PL.Treeview", background=BG, fieldbackground=BG, foreground=FG,
                        rowheight=28, borderwidth=0, font=("Segoe UI", 10))
        style.configure("PL.Treeview.Heading", background=CARD, foreground=MUTED, relief="flat",
                        font=("Segoe UI", 8, "bold"))
        style.map("PL.Treeview", background=[("selected", LINE)], foreground=[("selected", FG)])
        style.map("PL.Treeview.Heading", background=[("active", LINE)])
        style.configure("Vertical.TScrollbar", background=CARD, troughcolor=BG, bordercolor=BG,
                        arrowcolor=MUTED, lightcolor=CARD, darkcolor=CARD)
        style.map("Vertical.TScrollbar", background=[("active", LINE)])
        style.layout("PL.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(head, text="ПЛЕЙЛИСТ", bg=BG, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(side="left")
        self.mode_var = tk.StringVar(value=MINE)
        for name in (SPOTIFY, MINE):
            tk.Radiobutton(head, text=name, value=name, variable=self.mode_var, indicatoron=False,
                           command=lambda: self.set_mode(self.mode_var.get()), bg=CARD, fg=FG,
                           selectcolor=LINE, activebackground=LINE, activeforeground=FG, relief="flat",
                           bd=0, padx=12, pady=3, font=("Segoe UI", 9), highlightthickness=0,
                           cursor="hand2").pack(side="right", padx=(6, 0))

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=18, pady=(4, 6))
        self.pl_var = tk.StringVar()
        self.pl_box = ttk.Combobox(row, textvariable=self.pl_var, state="readonly", style="Dark.TCombobox")
        self.pl_box.pack(side="left", fill="x", expand=True)
        self.pl_box.bind("<<ComboboxSelected>>", lambda e: self._on_playlist())
        self.pl_tools = tk.Frame(row, bg=BG)
        self.pl_tools.pack(side="left")

        search_row = tk.Frame(self, bg=BG)
        search_row.pack(fill="x", padx=18, pady=(0, 8))
        tk.Label(search_row, text="Поиск:", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render())
        tk.Entry(search_row, textvariable=self.search_var, bg=CARD, fg=FG, insertbackground=FG,
                 relief="flat", font=("Segoe UI", 10), highlightthickness=1, highlightbackground=LINE,
                 highlightcolor=ACCENT).pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=3)

        table = tk.Frame(self, bg=BG)
        table.pack(fill="both", expand=True, padx=18)
        cols = ("n", "title", "artist", "dur")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", style="PL.Treeview",
                                 selectmode="extended")
        for c, text, w, anchor, stretch in (("n", "#", 40, "e", False), ("title", "Название", 220, "w", True),
                                            ("artist", "Исполнитель", 160, "w", True),
                                            ("dur", "Время", 60, "e", False)):
            self.tree.heading(c, text=text, anchor=anchor)
            self.tree.column(c, width=w, anchor=anchor, stretch=stretch)
        self.tree.tag_configure("playing", foreground=ACCENT)
        self.tree.tag_configure("disabled", foreground="#5a5a5e")
        sb = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.play_selected())
        self.tree.bind("<Return>", lambda e: self.play_selected())
        self.tree.bind("<Delete>", lambda e: self._remove_selected())

        self.empty_lbl = tk.Label(self.tree, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10),
                                  justify="center", wraplength=380)

        self.bottom = tk.Frame(self, bg=BG)
        self.bottom.pack(fill="x", padx=18, pady=(8, 4))
        self.hint = tk.Label(self, text="Двойной клик по треку — включить", bg=BG, fg=MUTED,
                             font=("Segoe UI", 8), anchor="w")
        self.hint.pack(fill="x", padx=18, pady=(0, 10))

    def _clear(self, frame: tk.Frame) -> None:
        for w in frame.winfo_children():
            w.destroy()

    def _build_tools(self) -> None:
        self._clear(self.pl_tools)
        self._clear(self.bottom)
        if self.mode == MINE:
            _btn(self.pl_tools, "Новый", self._new_playlist).pack(side="left", padx=(6, 0))
            _btn(self.pl_tools, "Имя", self._rename_playlist).pack(side="left", padx=(4, 0))
            _btn(self.pl_tools, "Удалить", self._delete_playlist).pack(side="left", padx=(4, 0))

            _btn(self.bottom, "+ Файлы", self._add_files, accent=True).pack(side="left")
            _btn(self.bottom, "+ Папка", self._add_folder).pack(side="left", padx=(6, 0))
            _btn(self.bottom, "Убрать", self._remove_selected).pack(side="left", padx=(6, 0))
            _btn(self.bottom, "Вверх", lambda: self._move(-1)).pack(side="left", padx=(6, 0))
            _btn(self.bottom, "Вниз", lambda: self._move(1)).pack(side="left", padx=(6, 0))

            opts = tk.Frame(self.bottom, bg=BG)
            opts.pack(side="bottom", fill="x", pady=(8, 0), before=self.bottom.winfo_children()[0])
            self.shuffle_var = tk.BooleanVar(value=self.local.shuffle)
            self.repeat_var = tk.BooleanVar(value=self.local.repeat)
            for text, var, attr in (("Перемешать", self.shuffle_var, "shuffle"), ("Повтор", self.repeat_var, "repeat")):
                tk.Checkbutton(opts, text=text, variable=var, bg=BG, fg=MUTED, selectcolor=CARD,
                               activebackground=BG, activeforeground=FG, font=("Segoe UI", 8), bd=0,
                               highlightthickness=0,
                               command=lambda v=var, a=attr: setattr(self.local, a, v.get())).pack(side="left")
            tk.Label(opts, text="Громкость", bg=BG, fg=MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(16, 0))
            self.vol = tk.Scale(opts, from_=0, to=100, orient="horizontal", showvalue=False, length=140,
                                bg=MUTED, fg=FG, troughcolor=LINE, highlightthickness=0, bd=0, width=8, sliderlength=16,
                                sliderrelief="flat", activebackground=ACCENT,
                                command=lambda v: self.local.set_volume(int(v) / 100))
            self.vol.set(int(self.local.volume * 100))
            self.vol.pack(side="left", padx=(6, 0))
        else:
            if self.spotify is None:
                return
            if self.spotify.logged_in:
                _btn(self.pl_tools, "Обновить", self._reload).pack(side="left", padx=(6, 0))
                _btn(self.bottom, "Выйти из Spotify", self._spotify_logout).pack(side="right")
            else:
                _btn(self.bottom, "Войти в Spotify", self._spotify_login, accent=True).pack(side="left")
                _btn(self.bottom, "Сменить Client ID", self._ask_client_id).pack(side="left", padx=(6, 0))

    # ------------------------------------------------------------ режимы
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.mode_var.set(mode)
        self.tracks, self.visible = [], []
        self._build_tools()
        if mode == MINE:
            self.hint.config(text="Двойной клик — включить · Delete — убрать · mp3, flac, ogg, opus, wav")
            self._load_playlists()
        else:
            self.hint.config(text="Двойной клик — включить в Spotify (нужен Premium и открытый Spotify)")
            if self.spotify is None or not self.spotify.logged_in:
                self.pl_box.config(values=[])
                self.pl_var.set("")
                self._render("Войдите в Spotify, чтобы видеть свои плейлисты,\n«Любимые треки» и очередь.\n\n"
                             "Понадобится бесплатный Client ID —\nинструкция в README.")
            else:
                self._load_playlists()

    def _load_playlists(self, select: Optional[str] = None) -> None:
        if self.mode == MINE:
            self._set_playlists(self.store.names(), select)
        else:
            self._render("Загружаю плейлисты…")
            self.app.run_bg(self.spotify.playlists, lambda pls: self._set_playlists(pls, select),
                            self._spotify_error)

    def _set_playlists(self, playlists: List[PlaylistInfo], select: Optional[str] = None) -> None:
        self.playlists = playlists
        labels = [self._pl_label(p) for p in playlists]
        self.pl_box.config(values=labels)
        ids = [p.id for p in playlists]
        target = select if select in ids else (self.playlist_id if self.playlist_id in ids else None)
        if target is None and playlists:
            # в Spotify по умолчанию первый настоящий плейлист, а не очередь
            target = playlists[2].id if self.mode == SPOTIFY and len(playlists) > 2 else playlists[0].id
        if target is not None:
            i = ids.index(target)
            self.pl_box.current(i)
            self._on_playlist()
        else:
            self._render("Плейлистов нет")

    @staticmethod
    def _pl_label(p: PlaylistInfo) -> str:
        return f"{p.name}  ({p.count})" if p.count is not None else p.name

    def _on_playlist(self) -> None:
        i = self.pl_box.current()
        if i < 0 or i >= len(self.playlists):
            return
        self.playlist_id = self.playlists[i].id
        if self.mode == MINE:
            self.tracks = self.store.tracks(self.playlist_id)
            self._render()
        else:
            pid = self.playlist_id
            self._render("Загружаю треки…")

            def done(tracks):
                if pid == self.playlist_id and self.mode == SPOTIFY:
                    self.tracks = tracks
                    self._render()
            self.app.run_bg(lambda: self.spotify.tracks(pid), done, self._spotify_error)

    def _reload(self) -> None:
        self._load_playlists(self.playlist_id)

    # ------------------------------------------------------------ таблица
    def _render(self, message: Optional[str] = None) -> None:
        self.tree.delete(*self.tree.get_children())
        if message is not None:
            self.tracks, self.visible = [], []
        q = self.search_var.get().strip().lower()
        self.visible = [i for i, t in enumerate(self.tracks)
                        if not q or q in t.title.lower() or q in t.artist.lower() or q in t.album.lower()]
        for i in self.visible:
            t = self.tracks[i]
            tags = []
            if t.id == self.playing_id:
                tags.append("playing")
            if not t.playable:
                tags.append("disabled")
            title = ("▶ " if t.id == self.playing_id else "") + t.title
            self.tree.insert("", "end", iid=str(i), values=(i + 1, title, t.artist, format_time(t.duration)),
                             tags=tags)
        if message is None and not self.tracks:
            message = ("Плейлист пуст.\nНажмите «+ Файлы» или «+ Папка», чтобы добавить музыку."
                       if self.mode == MINE else "В этом плейлисте нет треков\n(или Spotify не отдаёт его содержимое)")
        elif message is None and not self.visible:
            message = "Ничего не найдено"
        if message:
            self.empty_lbl.config(text=message)
            self.empty_lbl.place(relx=0.5, rely=0.4, anchor="center")
        else:
            self.empty_lbl.place_forget()

    def _highlight(self, track_id: Optional[str]) -> None:
        if track_id == self.playing_id:
            return
        self.playing_id = track_id
        self._render()
        if track_id:
            for i in self.visible:
                if self.tracks[i].id == track_id:
                    self.tree.see(str(i))
                    break

    def on_now_playing(self, now: Optional[TrackInfo]) -> None:
        """Вызывается окном при каждом обновлении: подсвечиваем играющий трек."""
        self._now = now
        if self.mode == MINE:
            cur = self.local.current
            same = cur is not None and self.local.playlist_id == self.playlist_id
            self._highlight(cur.id if same and now and now.source_id == SOURCE_ID else None)
        elif now is not None and "spotify" in now.source_name.lower():
            match = next((t.id for t in self.tracks if t.title == now.title), None)
            if match:
                self._highlight(match)

    # ------------------------------------------------------------ запуск трека
    def _selected(self) -> List[int]:
        return [int(iid) for iid in self.tree.selection()]

    def play_selected(self) -> None:
        sel = self._selected()
        if not sel:
            return
        index = sel[0]
        track = self.tracks[index]
        if not track.playable:
            self.app.set_status("Этот трек недоступен")
            return
        if self.mode == MINE:
            try:
                self.local.load(self.tracks, self.playlist_id, index)
            except Exception as exc:
                messagebox.showerror("Now Playing", str(exc))
                return
            if self.local.error:
                self.app.set_status(self.local.error)
            self.app.preferred = SOURCE_ID
            self._highlight(self.local.current.id if self.local.current else None)
            self.app.refresh_now()
        else:
            pid, tracks = self.playlist_id, list(self.tracks)
            self.app.set_status(f"Включаю в Spotify: {track.title}")

            def done(_):
                self._highlight(track.id)
                self.app.preferred = None
                self.app.refresh_now()
            self.app.run_bg(lambda: self.spotify.play(pid, tracks, index), done, self._spotify_error)

    # ------------------------------------------------------------ свои плейлисты
    def _sync_queue(self) -> None:
        """Если редактируем плейлист, который сейчас играет, обновляем очередь плеера."""
        if self.local.playlist_id == self.playlist_id and self.local.current is not None:
            cur = self.local.current.id
            self.local.queue = list(self.tracks)
            ids = [t.id for t in self.tracks]
            self.local.index = ids.index(cur) if cur in ids else -1

    def _after_edit(self) -> None:
        self.tracks = self.store.tracks(self.playlist_id)
        self._sync_queue()
        labels = [self._pl_label(p) for p in self.store.names()]
        self.playlists = self.store.names()
        self.pl_box.config(values=labels)
        self.pl_box.current([p.id for p in self.playlists].index(self.playlist_id))
        self._render()

    def _add_files(self) -> None:
        pattern = " ".join("*" + e for e in sorted(AUDIO_EXTS))
        files = filedialog.askopenfilenames(
            title="Добавить музыку", filetypes=[("Аудио", pattern), ("Плейлист M3U", "*.m3u *.m3u8"),
                                                ("Все файлы", "*.*")])
        if not files:
            return
        paths: List[str] = []
        for f in files:
            paths.extend(read_m3u(f) if f.lower().endswith((".m3u", ".m3u8")) else [f])
        self._add_paths(paths)

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Добавить папку с музыкой")
        if folder:
            self.app.set_status("Сканирую папку…")
            self.app.run_bg(lambda: scan_folder(folder), self._add_paths)

    def _add_paths(self, paths: List[str]) -> None:
        if self.playlist_id is None:
            return
        added = self.store.add_files(self.playlist_id, paths)
        self.app.set_status(f"Добавлено треков: {added}")
        self._after_edit()

    def add_paths(self, paths: List[str]) -> None:
        """Публичный вызов (например, из аргументов командной строки)."""
        if self.mode != MINE:
            self.set_mode(MINE)
        self._add_paths(paths)

    def _remove_selected(self) -> None:
        if self.mode != MINE or not self._selected():
            return
        self.store.remove(self.playlist_id, self._selected())
        self._after_edit()

    def _move(self, delta: int) -> None:
        sel = self._selected()
        if self.mode != MINE or len(sel) != 1 or self.search_var.get():
            return
        new = self.store.move(self.playlist_id, sel[0], delta)
        self._after_edit()
        self.tree.selection_set(str(new))
        self.tree.see(str(new))

    def _new_playlist(self) -> None:
        name = simpledialog.askstring("Новый плейлист", "Название:", parent=self)
        if name and name.strip():
            pid = self.store.create(name.strip())
            self._load_playlists(pid)

    def _rename_playlist(self) -> None:
        if self.playlist_id is None:
            return
        old = self.store.get(self.playlist_id)["name"]
        name = simpledialog.askstring("Переименовать", "Новое название:", initialvalue=old, parent=self)
        if name and name.strip():
            self.store.rename(self.playlist_id, name.strip())
            self._load_playlists(self.playlist_id)

    def _delete_playlist(self) -> None:
        if self.playlist_id is None:
            return
        name = self.store.get(self.playlist_id)["name"]
        if messagebox.askyesno("Удалить плейлист", f"Удалить «{name}»? Файлы на диске не удаляются."):
            if self.local.playlist_id == self.playlist_id:
                self.local.stop()
                self.local.playlist_id = None
            self.store.delete(self.playlist_id)
            self.playlist_id = None
            self._load_playlists()

    # ------------------------------------------------------------ Spotify
    def _ask_client_id(self) -> bool:
        cid = simpledialog.askstring(
            "Spotify Client ID",
            "Вставьте Client ID вашего приложения Spotify.\n\n"
            "1) developer.spotify.com/dashboard → Create app\n"
            "2) Redirect URI: http://127.0.0.1:8765/callback\n"
            "3) API: Web API → Save → скопируйте Client ID",
            initialvalue=self.spotify.client_id, parent=self)
        if cid and cid.strip():
            self.spotify.client_id = cid.strip()
            return True
        return False

    def _spotify_login(self) -> None:
        if not self.spotify.client_id and not self._ask_client_id():
            return
        self._render("Откроется браузер — подтвердите доступ в Spotify…")

        def done(_):
            self.app.set_status("Вход в Spotify выполнен")
            self.set_mode(SPOTIFY)
        self.app.run_bg(self.spotify.login, done, self._spotify_error)

    def _spotify_logout(self) -> None:
        self.spotify.logout()
        self.set_mode(SPOTIFY)

    def _spotify_error(self, exc: Exception) -> None:
        self.app.set_status(str(exc))
        if self.mode == SPOTIFY:
            if not self.spotify.logged_in:
                self.set_mode(SPOTIFY)
            elif not self.tracks:
                self._render(str(exc))
            messagebox.showwarning("Spotify", str(exc))
