"""Spotify Web API: ваши плейлисты, «Любимые треки», очередь и запуск выбранного трека.

Авторизация — OAuth 2.0 PKCE (без client secret). Нужен свой Client ID:
https://developer.spotify.com/dashboard → Create app → Redirect URI: http://127.0.0.1:8765/callback
Управление воспроизведением через Web API работает только с Spotify Premium.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import List, Optional

from .library import PlaylistInfo, PlaylistTrack, config_dir

REDIRECT_URI = "http://127.0.0.1:8765/callback"
SCOPES = " ".join([
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
])
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

LIKED = "__liked__"
QUEUE = "__queue__"


class SpotifyError(RuntimeError):
    pass


class SpotifyClient:
    def __init__(self) -> None:
        self.path = config_dir() / "spotify.json"
        self.data: dict = {}
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self._lock = threading.Lock()
        self._playlist_uris: dict = {}

    # --- хранение ---------------------------------------------------------
    def _save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    @property
    def client_id(self) -> str:
        return self.data.get("client_id", "")

    @client_id.setter
    def client_id(self, value: str) -> None:
        self.data["client_id"] = value.strip()
        self._save()

    @property
    def logged_in(self) -> bool:
        return bool(self.data.get("refresh_token"))

    def logout(self) -> None:
        for k in ("access_token", "refresh_token", "expires_at"):
            self.data.pop(k, None)
        self._save()

    # --- авторизация PKCE ---------------------------------------------------
    def login(self, timeout: float = 180) -> None:
        if not self.client_id:
            raise SpotifyError("Не указан Client ID")
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        result: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if q.get("state", [""])[0] == state:
                    result["code"] = q.get("code", [""])[0]
                    result["error"] = q.get("error", [""])[0]
                ok = bool(result.get("code"))
                body = ("<h2>Готово! Можно вернуться в Now Playing.</h2>" if ok
                        else "<h2>Авторизация не удалась. Закройте вкладку и попробуйте снова.</h2>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><body style='font-family:sans-serif'>{body}</body></html>".encode())

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 8765), Handler)
        server.timeout = 1
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "scope": SCOPES,
            "state": state,
        }
        webbrowser.open(AUTH_URL + "?" + urllib.parse.urlencode(params))
        deadline = time.monotonic() + timeout
        try:
            while "code" not in result and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if not result.get("code"):
            raise SpotifyError(result.get("error") or "Время ожидания авторизации истекло")
        self._token_request({
            "grant_type": "authorization_code",
            "code": result["code"],
            "redirect_uri": REDIRECT_URI,
            "client_id": self.client_id,
            "code_verifier": verifier,
        })

    def _token_request(self, form: dict) -> None:
        req = urllib.request.Request(
            TOKEN_URL, data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                tok = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            if form.get("grant_type") == "refresh_token":
                self.logout()
            raise SpotifyError(f"Ошибка авторизации Spotify: {detail}") from exc
        self.data["access_token"] = tok["access_token"]
        self.data["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        if tok.get("refresh_token"):
            self.data["refresh_token"] = tok["refresh_token"]
        self._save()

    def _token(self) -> str:
        with self._lock:
            if not self.logged_in:
                raise SpotifyError("Сначала войдите в Spotify")
            if time.time() >= self.data.get("expires_at", 0):
                self._token_request({
                    "grant_type": "refresh_token",
                    "refresh_token": self.data["refresh_token"],
                    "client_id": self.client_id,
                })
            return self.data["access_token"]

    # --- HTTP -------------------------------------------------------------
    def _api(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None):
        url = path if path.startswith("http") else API + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            try:
                msg = json.loads(detail)["error"]["message"]
            except Exception:
                msg = detail[:200]
            if exc.code == 404 and "/me/player" in url:
                msg = "Нет активного устройства: откройте Spotify и запустите любой трек"
            elif exc.code == 403 and "/me/player" in url:
                msg = "Spotify разрешает управление только с Premium-аккаунтом"
            raise SpotifyError(f"Spotify {exc.code}: {msg}") from exc

    def _paged(self, path: str, limit: int = 50, max_items: int = 2000) -> List[dict]:
        out: List[dict] = []
        page = self._api("GET", path, {"limit": limit})
        while page:
            out.extend(page.get("items") or [])
            nxt = page.get("next")
            if not nxt or len(out) >= max_items:
                break
            page = self._api("GET", nxt)
        return out

    # --- данные -----------------------------------------------------------
    def playlists(self) -> List[PlaylistInfo]:
        result = [PlaylistInfo(id=QUEUE, name="Сейчас в очереди"), PlaylistInfo(id=LIKED, name="Любимые треки")]
        for p in self._paged("/me/playlists"):
            if not p:
                continue
            # с февраля 2026 поле называется items (раньше tracks)
            meta = p.get("items") or p.get("tracks") or {}
            count = meta.get("total") if isinstance(meta, dict) else None
            self._playlist_uris[p["id"]] = p.get("uri")
            result.append(PlaylistInfo(id=p["id"], name=p.get("name") or "Без названия", count=count))
        return result

    @staticmethod
    def _track(obj: Optional[dict]) -> Optional[PlaylistTrack]:
        if not obj or not obj.get("uri"):
            return None
        artists = ", ".join(a.get("name", "") for a in obj.get("artists") or [])
        if not artists and obj.get("show"):
            artists = obj["show"].get("name", "")
        dur = obj.get("duration_ms")
        return PlaylistTrack(
            id=obj["uri"],
            title=obj.get("name") or "Без названия",
            artist=artists,
            album=(obj.get("album") or {}).get("name", ""),
            duration=dur / 1000 if dur else None,
            playable=obj.get("is_playable", True) is not False and not obj.get("is_local"),
        )

    def tracks(self, playlist_id: str) -> List[PlaylistTrack]:
        if playlist_id == QUEUE:
            q = self._api("GET", "/me/player/queue") or {}
            items = ([q.get("currently_playing")] if q.get("currently_playing") else []) + (q.get("queue") or [])
            return [t for t in (self._track(i) for i in items) if t]
        if playlist_id == LIKED:
            rows = self._paged("/me/tracks")
            return [t for t in (self._track(r.get("track") or r.get("item")) for r in rows) if t]
        rows = self._paged(f"/playlists/{playlist_id}/items", limit=100)
        return [t for t in (self._track(r.get("item") or r.get("track")) for r in rows) if t]

    def play(self, playlist_id: str, tracks: List[PlaylistTrack], index: int) -> None:
        track = tracks[index]
        if playlist_id in (QUEUE, LIKED):
            # у «Любимых» нет context_uri — передаём список треков, начиная с выбранного
            uris = [t.id for t in tracks[index:index + 100] if t.playable]
            body = {"uris": uris}
        else:
            uri = self._playlist_uris.get(playlist_id) or f"spotify:playlist:{playlist_id}"
            body = {"context_uri": uri, "offset": {"uri": track.id}}
        self._api("PUT", "/me/player/play", body=body)

    def current_uri(self) -> Optional[str]:
        try:
            cur = self._api("GET", "/me/player/currently-playing")
        except SpotifyError:
            return None
        item = (cur or {}).get("item") or {}
        return item.get("uri")
