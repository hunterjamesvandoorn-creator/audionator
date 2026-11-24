from __future__ import annotations

import argparse
import json
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Any

import pygame
import webview
from mutagen import MutagenError
from mutagen.mp3 import MP3

from receiver import AudioReceiver
from video_stream import ProgressiveVideoHTTPServer


class AudionatorAPI:
    def __init__(
        self,
        download_directory: Path,
        video_server: ProgressiveVideoHTTPServer,
        settings_path: Path | None = None,
    ) -> None:
        self.download_directory = download_directory
        self.receiver = AudioReceiver(download_directory)
        self.video_server = video_server
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.settings_path = settings_path or local_app_data / "Audionator" / "settings.json"
        self.saved_host = ""
        self.saved_port = 5005
        try:
            if self.settings_path.exists():
                settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
                if not isinstance(settings, dict):
                    raise ValueError("Saved settings must be a JSON object.")
                host = settings.get("host", "")
                port = settings.get("port", 5005)
                if not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535:
                    raise ValueError("Saved server address or port is invalid.")
                self.saved_host = host
                self.saved_port = port
        except (OSError, ValueError) as error:
            self._set_message(f"Could not load saved connection settings: {error}", is_error=True)
        self.video_receiver: AudioReceiver | None = None
        self.media_by_type: dict[str, list[dict[str, Any]]] = {"audio": [], "video": []}
        self.connected_server: tuple[str, int] | None = None
        self.connected = False
        self.connection_deadline: float | None = None
        self.message = "Connect to browse the shared library."
        self.error = False
        self.connection_warning: str | None = None
        self.active_transfer_id: str | None = None
        self.active_transfer_type: str | None = None
        self.active_transfer_name = ""
        self.transfer_percent = 0.0
        self.video_status = ""
        self.current_path: Path | None = None
        self.current_name = ""
        self.track_duration = 0.0
        self.playback_offset = 0.0
        self.playback_started_at: float | None = None
        self.is_paused = True
        self.playback_finished = False

    def connect(self, host: str, port: int) -> dict[str, Any]:
        try:
            host = host.strip()
            if not host:
                raise ValueError("Enter the server computer's IP address.")
            if not 1 <= port <= 65535:
                raise ValueError("Port must be between 1 and 65535.")
            server = (host, port)
            if self.receiver.is_running and server == self.connected_server:
                self.receiver.request_library()
            else:
                self.close_video()
                if self.receiver.is_running:
                    self.receiver.close()
                self.receiver = AudioReceiver(self.download_directory)
                self.receiver.connect(host, port)
                self.connected_server = server
            self.saved_host = host
            self.saved_port = port
            self.connected = False
            self.connection_deadline = time.monotonic() + 8
            self.connection_warning = self._save_connection()
            self.error = self.connection_warning is not None
            self.message = self.connection_warning or "Contacting the server…"
            return {"ok": True, "warning": self.connection_warning}
        except (OSError, ValueError, RuntimeError) as error:
            self._set_message(str(error), is_error=True)
            return {"ok": False, "message": str(error)}

    def get_saved_connection(self) -> dict[str, Any]:
        return {"host": self.saved_host, "port": self.saved_port}

    def receive_audio(self, name: str) -> dict[str, Any]:
        if not self.connected or not self.receiver.is_running:
            return self._failure("Connect to a server before playing audio.")
        if self.active_transfer_id:
            return self._failure("Wait for the current track to finish downloading.")
        try:
            transfer_id = self.receiver.request_file(name)
        except (OSError, RuntimeError, ValueError) as error:
            self._set_message(f"Could not request the track: {error}", is_error=True)
            return {"ok": False, "message": str(error)}
        self.active_transfer_id = transfer_id.hex()
        self.active_transfer_type = "audio"
        self.active_transfer_name = name
        self.transfer_percent = 0
        self.message = f"Receiving {name}…"
        self.error = False
        return {"ok": True}

    def play_video(self, name: str) -> dict[str, Any]:
        if not self.connected_server or not self.connected:
            return self._failure("Connect to the server before opening a video.")
        if self.current_path and not self.is_paused:
            self.playback_offset = self._current_position()
            pygame.mixer.music.pause()
            self.playback_started_at = None
            self.is_paused = True
        self.close_video()
        receiver = AudioReceiver(self.download_directory)
        try:
            receiver.connect(*self.connected_server)
            transfer_id = receiver.request_file(name)
            self.video_server.set_video_source(receiver, transfer_id)
        except (OSError, RuntimeError, ValueError) as error:
            receiver.close()
            self._set_message(f"Could not open the video stream: {error}", is_error=True)
            return {"ok": False, "message": str(error)}
        self.video_receiver = receiver
        self.video_status = "Buffering video over reliable UDP…"
        self.message = f"Opening {name} in the built-in player…"
        self.error = False
        return {"ok": True}

    def close_video(self) -> dict[str, Any]:
        self.video_server.clear_video_source()
        if self.video_receiver:
            self.video_receiver.close()
            self.video_receiver = None
        self.video_status = ""
        return {"ok": True}

    def toggle_audio(self) -> dict[str, Any]:
        if not self.current_path:
            return {"ok": False}
        try:
            if self.playback_finished:
                pygame.mixer.music.play()
                self.playback_offset = 0.0
                self.playback_started_at = time.monotonic()
                self.playback_finished = False
                self.is_paused = False
            elif self.is_paused:
                pygame.mixer.music.unpause()
                self.playback_started_at = time.monotonic()
                self.is_paused = False
            else:
                self.playback_offset = self._current_position()
                pygame.mixer.music.pause()
                self.playback_started_at = None
                self.is_paused = True
            return {"ok": True}
        except pygame.error as error:
            self._set_message(f"Playback control failed: {error}", is_error=True)
            return {"ok": False, "message": str(error)}

    def seek(self, position: float) -> dict[str, Any]:
        if not self.current_path or self.track_duration <= 0:
            return {"ok": False}
        position = min(max(float(position), 0.0), self.track_duration)
        try:
            if self.playback_finished:
                pygame.mixer.music.play(start=position)
                self.playback_finished = False
                self.is_paused = False
                self.playback_started_at = time.monotonic()
            else:
                pygame.mixer.music.set_pos(position)
                if not self.is_paused:
                    self.playback_started_at = time.monotonic()
            self.playback_offset = position
            return {"ok": True}
        except pygame.error as error:
            self._set_message(f"Could not seek to that position: {error}", is_error=True)
            return {"ok": False, "message": str(error)}

    def open_download_folder(self) -> dict[str, Any]:
        try:
            self.download_directory.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.download_directory))
            return {"ok": True}
        except OSError as error:
            message = f"Could not open the received music folder: {error}"
            self._set_message(message, is_error=True)
            return {"ok": False, "message": message}

    def get_state(self) -> dict[str, Any]:
        self._process_receiver_events(self.receiver, is_video=False)
        if self.video_receiver:
            self._process_receiver_events(self.video_receiver, is_video=True)
        if self.connection_deadline is not None and time.monotonic() >= self.connection_deadline:
            self.connection_deadline = None
            self.connected = False
            self.receiver.close()
            self._set_message(
                "No server response. Check the IP and port, then make sure the server is running.",
                is_error=True,
            )
        self._update_audio_state()
        active = bool(self.active_transfer_id)
        if active and self.active_transfer_type == "audio":
            subtitle = f"Receiving {self.active_transfer_name}… {self.transfer_percent:.0f}%"
        elif self.current_path and not self.is_paused:
            subtitle = "Now playing"
        elif self.current_path and self.is_paused:
            subtitle = "Paused"
        else:
            subtitle = "Choose an MP3 to receive and play it"
        return {
            "connected": self.connected,
            "connecting": self.connection_deadline is not None,
            "error": self.error,
            "message": self.message,
            "files": self.media_by_type,
            "server": {
                "host": self.connected_server[0] if self.connected_server else "",
                "port": self.connected_server[1] if self.connected_server else 5005,
            },
            "download_directory": str(self.download_directory),
            "audio": {
                "name": self.current_name or self.active_transfer_name,
                "subtitle": subtitle,
                "ready": self.current_path is not None,
                "playing": bool(self.current_path and not self.is_paused),
                "seekable": bool(self.current_path and self.track_duration > 0),
                "position": self._current_position() if self.current_path else 0,
                "duration": self.track_duration,
            },
            "video": {"status": self.video_status, "active": self.video_receiver is not None},
        }

    def shutdown(self) -> None:
        self.close_video()
        self.receiver.close()
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()

    def _process_receiver_events(self, receiver: AudioReceiver, is_video: bool) -> None:
        try:
            while True:
                event, payload = receiver.events.get_nowait()
                if is_video:
                    self._handle_video_event(event, payload)
                else:
                    self._handle_event(event, payload)
        except queue.Empty:
            return

    def _handle_event(self, event: str, payload: dict[str, Any]) -> None:
        if event == "library":
            self.connection_deadline = None
            self.connected = True
            self.error = False
            files: dict[str, list[dict[str, Any]]] = {"audio": [], "video": []}
            for item in payload.get("files", []):
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                media_type = item.get("type")
                if media_type not in files:
                    media_type = "video" if Path(item["name"]).suffix.lower() == ".mp4" else "audio"
                files[media_type].append(item)
            self.media_by_type = files
            total = len(files["audio"]) + len(files["video"])
            message = (
                f"Connected. {total} item{'s' if total != 1 else ''} in the shared library."
                if total
                else "Connected, but the server's music and videos folders are empty."
            )
            if self.connection_warning:
                message = f"{message} {self.connection_warning}"
            self._set_message(message, is_error=self.connection_warning is not None)
        elif event == "status":
            if not self.connected:
                self.message = payload["message"]
        elif event == "transfer_started":
            self.active_transfer_id = payload["id"]
            self.active_transfer_name = payload["name"]
            self.transfer_percent = 0
        elif event == "transfer_progress":
            if payload["id"] == self.active_transfer_id:
                total = payload["total"]
                self.transfer_percent = 100 if total == 0 else 100 * payload["received"] / total
        elif event == "transfer_complete":
            if self.active_transfer_type == "audio":
                self.active_transfer_id = None
                self._play_file(Path(payload["path"]))
        elif event == "error":
            self.active_transfer_id = None
            if self.connection_deadline is not None or not self.receiver.is_running:
                self.connection_deadline = None
                self.connected = False
            self._set_message(payload["message"], is_error=True)

    def _handle_video_event(self, event: str, payload: dict[str, Any]) -> None:
        if event == "transfer_started":
            self.video_status = f"Receiving video… 0%"
        elif event == "transfer_progress":
            total = payload["total"]
            percent = 100 if total == 0 else 100 * payload["received"] / total
            self.video_status = f"Receiving video… {percent:.0f}%"
        elif event == "transfer_complete":
            self.video_status = "Video received. You can keep watching or close the player."
        elif event == "error":
            self.video_status = payload["message"]
            self._set_message(payload["message"], is_error=True)

    def _play_file(self, path: Path) -> None:
        try:
            duration = float(MP3(path).info.length)
            if not math.isfinite(duration) or duration <= 0:
                duration = 0
        except (OSError, MutagenError, TypeError, ValueError):
            duration = 0
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play()
        except pygame.error as error:
            self.current_path = path
            self.current_name = path.name
            self.track_duration = duration
            self.playback_offset = 0.0
            self.playback_started_at = None
            self.is_paused = True
            self._set_message(f"Received the MP3, but playback failed: {error}", is_error=True)
            return
        self.current_path = path
        self.current_name = path.name
        self.track_duration = duration
        self.playback_offset = 0.0
        self.playback_started_at = time.monotonic()
        self.playback_finished = False
        self.is_paused = False
        self._set_message(f"Now playing {path.name}.")

    def _update_audio_state(self) -> None:
        if not self.current_path or self.is_paused or self.playback_finished:
            return
        try:
            if not pygame.mixer.music.get_busy():
                self.playback_offset = self.track_duration
                self.playback_started_at = None
                self.playback_finished = True
                self.is_paused = True
        except pygame.error as error:
            self._set_message(f"Audio output error: {error}", is_error=True)

    def _current_position(self) -> float:
        if self.is_paused or self.playback_started_at is None:
            return self.playback_offset
        position = self.playback_offset + time.monotonic() - self.playback_started_at
        return min(position, self.track_duration) if self.track_duration else position

    def _set_message(self, message: str, is_error: bool = False) -> None:
        self.message = message
        self.error = is_error

    def _save_connection(self) -> str | None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(
                json.dumps({"host": self.saved_host, "port": self.saved_port}),
                encoding="utf-8",
            )
            return None
        except OSError as error:
            return f"Couldn't save the server address: {error}"

    def _failure(self, message: str) -> dict[str, Any]:
        self._set_message(message, is_error=True)
        return {"ok": False, "message": message}


def _show_startup_error(message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Audionator", message)
    root.destroy()


def default_download_directory() -> Path:
    if getattr(sys, "frozen", False):
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return local_app_data / "Audionator" / "received"
    return Path(__file__).resolve().parent / "received"


def main() -> None:
    parser = argparse.ArgumentParser(description="Open the Audionator audio and video client.")
    parser.add_argument(
        "--downloads",
        type=Path,
        default=default_download_directory(),
        help="Folder where received MP3 files are saved",
    )
    args = parser.parse_args()
    video_server: ProgressiveVideoHTTPServer | None = None
    api: AudionatorAPI | None = None
    server_thread: threading.Thread | None = None
    try:
        video_server = ProgressiveVideoHTTPServer(("127.0.0.1", 0))
        server_thread = threading.Thread(target=video_server.serve_forever, daemon=True)
        server_thread.start()
        api = AudionatorAPI(args.downloads, video_server)
        address = f"http://127.0.0.1:{video_server.server_address[1]}/"
        webview.create_window(
            "Audionator · Shared listening",
            url=address,
            js_api=api,
            width=1240,
            height=860,
            min_size=(1000, 700),
        )
        webview.start()
    except (OSError, RuntimeError, webview.errors.WebViewException) as error:
        _show_startup_error(f"Audionator could not start: {error}")
    finally:
        if api:
            api.shutdown()
        if video_server:
            video_server.shutdown()
            video_server.server_close()
        if server_thread:
            server_thread.join(timeout=2)


if __name__ == "__main__":
    main()
