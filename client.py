from __future__ import annotations

import argparse
import json
import math
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox
from typing import Any

import pygame
import webview
from mutagen import MutagenError
from mutagen.mp3 import MP3

from audio_stream import StreamingAudioFile
from download_meter import RETAIN_SECONDS, DownloadMeter
from receiver import AudioReceiver
from traffic_simulator import TrafficSimulator
from video_session import SIMULATED_LINK_BPS, AdaptiveVideoSession, validate_congestion
from video_stream import VideoHTTPServer

RESUME_END_MARGIN_SECONDS = 5.0
POSITION_SAVE_INTERVAL_SECONDS = 5.0
AUDIO_START_BUFFER_BYTES = 256 * 1024


def _format_time(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


class AudionatorAPI:
    def __init__(
        self,
        download_directory: Path,
        video_server: VideoHTTPServer,
        settings_path: Path | None = None,
    ) -> None:
        self.download_directory = download_directory
        self.download_meter = DownloadMeter()
        self.receiver = AudioReceiver(download_directory, self.download_meter)
        self.video_server = video_server
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.settings_path = settings_path or local_app_data / "Audionator" / "settings.json"
        self.saved_host = ""
        self.saved_port = 5005
        self.playback_positions: dict[str, float] = {}
        self.developer_mode = False
        self.traffic: TrafficSimulator | None = None
        self.congestion_percent = 0
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
                positions = settings.get("playback_positions", {})
                if not isinstance(positions, dict):
                    raise ValueError("Saved playback positions must be a JSON object.")
                self.playback_positions = {
                    name: float(seconds)
                    for name, seconds in positions.items()
                    if type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0
                }
                developer_mode = settings.get("developer_mode", False)
                if type(developer_mode) is not bool:
                    raise ValueError("Saved developer mode setting must be true or false.")
                self.developer_mode = developer_mode
        except (OSError, ValueError) as error:
            self._set_message(f"Could not load saved connection settings: {error}", is_error=True)
        self.video_session: AdaptiveVideoSession | None = None
        self.media_by_type: dict[str, list[dict[str, Any]]] = {"audio": [], "video": []}
        self.connected_server: tuple[str, int] | None = None
        self.connected = False
        self.connection_deadline: float | None = None
        self.message = "Connect to browse the shared library."
        self.error = False
        self.connection_warning: str | None = None
        self.session_code: str | None = None
        self.session_members = 0
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
        self.current_track_key: str | None = None
        self._last_position_save = 0.0
        self._lost_connection = False
        self.streaming_transfer_id: str | None = None
        self._stream_attempt_id: str | None = None
        self._stream_file: StreamingAudioFile | None = None
        self._stream_size = 0
        self._stream_probe: tuple[str, float] | None = None

    def connect(self, host: str, port: int) -> dict[str, Any]:
        try:
            host = host.strip()
            if not host:
                raise ValueError("Enter the server computer's IP address.")
            if not 1 <= port <= 65535:
                raise ValueError("Port must be between 1 and 65535.")
            server = (host, port)
            if self.connected and self.receiver.is_running and server == self.connected_server:
                self.receiver.request_library()
            else:
                self.close_video()
                if self.receiver.is_running:
                    self.receiver.close()
                self.receiver = AudioReceiver(self.download_directory, self.download_meter)
                self.receiver.connect(host, port)
                self.connected_server = server
                self.session_code = None
                self.session_members = 0
                self.connected = False
                self._lost_connection = False
                self.connection_deadline = time.monotonic() + 8
            self.saved_host = host
            self.saved_port = port
            self.connection_warning = self._save_settings()
            self.error = self.connection_warning is not None
            self.message = self.connection_warning or "Contacting the server…"
            return {"ok": True, "warning": self.connection_warning}
        except (OSError, ValueError, RuntimeError) as error:
            self._set_message(str(error), is_error=True)
            return {"ok": False, "message": str(error)}

    def get_saved_connection(self) -> dict[str, Any]:
        return {"host": self.saved_host, "port": self.saved_port}

    def create_session(self) -> dict[str, Any]:
        return self._session_request(self.receiver.create_session)

    def join_session(self, code: str) -> dict[str, Any]:
        code = code.strip().upper()
        if not code:
            return self._failure("Enter the session code.")
        return self._session_request(lambda: self.receiver.join_session(code))

    def leave_session(self) -> dict[str, Any]:
        return self._session_request(self.receiver.leave_session)

    def _session_request(self, request: Callable[[], None]) -> dict[str, Any]:
        if not self.connected or not self.receiver.is_running:
            return self._failure("Connect to a server before using sessions.")
        try:
            request()
        except (OSError, RuntimeError, ValueError) as error:
            return self._failure(str(error))
        return {"ok": True}

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
            self._remember_position()
        self.close_video()
        receiver = AudioReceiver(self.download_directory, self.download_meter)
        try:
            receiver.connect(*self.connected_server)
        except (OSError, RuntimeError, ValueError) as error:
            receiver.close()
            self._set_message(f"Could not open the video stream: {error}", is_error=True)
            return {"ok": False, "message": str(error)}
        session = AdaptiveVideoSession(receiver, name)
        session.set_congestion(self.congestion_percent)
        session.start()
        self.video_session = session
        self.video_server.set_video_session(session)
        self.video_status = ""
        self.message = f"Opening {name} in the built-in player…"
        self.error = False
        return {"ok": True}

    def set_video_quality(self, mode: str) -> dict[str, Any]:
        if not self.video_session:
            return self._failure("Open a video before choosing its quality.")
        try:
            self.video_session.set_quality(str(mode))
        except (RuntimeError, ValueError) as error:
            return self._failure(str(error))
        return {"ok": True}

    def close_video(self) -> dict[str, Any]:
        self.video_server.clear_video_session()
        if self.video_session:
            self.video_session.close()
            self.video_session = None
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
                self._remember_position()
            return {"ok": True}
        except pygame.error as error:
            self._set_message(f"Playback control failed: {error}", is_error=True)
            return {"ok": False, "message": str(error)}

    def seek(self, position: float) -> dict[str, Any]:
        if not self.current_path or self.track_duration <= 0:
            return {"ok": False}
        position = min(max(float(position), 0.0), self.track_duration)
        if self.streaming_transfer_id and not self._has_downloaded_through(
            bytes.fromhex(self.streaming_transfer_id), position, self.track_duration, self._stream_size
        ):
            return self._failure("That part of the track is still downloading.")
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
            self._remember_position()
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

    def _packet_loss(self) -> dict[str, Any]:
        receivers = [self.receiver] + ([self.video_session.receiver] if self.video_session else [])
        counts = [receiver.loss_counts() for receiver in receivers]
        missed = sum(count[0] for count in counts)
        packets = sum(count[1] for count in counts)
        return {
            "percent": round(100 * missed / packets, 1) if packets else None,
            "missed": missed,
            "packets": packets,
        }

    def get_download_history(self, window_seconds: Any) -> dict[str, Any]:
        if type(window_seconds) not in (int, float) or not 1 <= window_seconds <= RETAIN_SECONDS:
            return self._failure("Choose a window between 1 second and 1 hour.")
        return {"ok": True, "points": self.download_meter.history(window_seconds)}

    def get_state(self) -> dict[str, Any]:
        self.download_meter.sample()
        self._process_receiver_events(self.receiver, is_video=False)
        if self.video_session:
            self._process_receiver_events(self.video_session.receiver, is_video=True)
        if self.connection_deadline is not None and time.monotonic() >= self.connection_deadline:
            self.connection_deadline = None
            self.connected = False
            self.receiver.close()
            self._set_message(
                "No server response. Check the IP and port, then make sure the server is running.",
                is_error=True,
            )
        self._maybe_start_stream()
        self._update_audio_state()
        active = bool(self.active_transfer_id)
        if active and self.active_transfer_type == "audio" and not self.streaming_transfer_id:
            subtitle = f"Receiving {self.active_transfer_name}… {self.transfer_percent:.0f}%"
        elif self.current_path and not self.is_paused:
            subtitle = f"Streaming · {self.transfer_percent:.0f}% downloaded" if active else "Now playing"
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
            "latency": self.receiver.latency_summary(),
            "loss": self._packet_loss(),
            "audio": {
                "name": self.current_name or self.active_transfer_name,
                "subtitle": subtitle,
                "ready": self.current_path is not None,
                "playing": bool(self.current_path and not self.is_paused),
                "seekable": bool(self.current_path and self.track_duration > 0),
                "position": self._current_position() if self.current_path else 0,
                "duration": self.track_duration,
            },
            "video": {
                "active": self.video_session is not None,
                "status": self.video_status,
                "session": self.video_session.describe() if self.video_session else None,
            },
            "session": {"code": self.session_code if self.connected else None, "members": self.session_members},
            "developer_mode": self.developer_mode,
            "traffic": self.traffic.status() if self.traffic else None,
            "congestion": {"percent": self.congestion_percent, "link_bps": SIMULATED_LINK_BPS},
        }

    def set_developer_mode(self, enabled: bool) -> dict[str, Any]:
        self.developer_mode = bool(enabled)
        if not self.developer_mode:
            self._stop_traffic()
            self._set_congestion_percent(0)
        error = self._save_settings()
        if error:
            return self._failure(error)
        self._set_message("Developer mode is on." if self.developer_mode else "Developer mode is off.")
        return {"ok": True}

    def start_traffic(self, client_count: Any, file_name: str | None) -> dict[str, Any]:
        if not self.developer_mode:
            return self._failure("Turn on developer mode in Settings first.")
        if not self.connected or self.connected_server is None:
            return self._failure("Connect to a server before starting simulated traffic.")
        if type(client_count) is not int:
            return self._failure("Enter a whole number of simulated clients.")
        if file_name and file_name not in {item["name"] for item in self.media_by_type["audio"]}:
            return self._failure("Choose a track from the shared library.")
        self._stop_traffic()
        host, port = self.connected_server
        work_directory = Path(tempfile.mkdtemp(prefix="audionator-traffic-"))
        try:
            simulator = TrafficSimulator(host, port, client_count, work_directory, file_name or None)
        except ValueError as error:
            shutil.rmtree(work_directory, ignore_errors=True)
            return self._failure(str(error))
        simulator.start()
        self.traffic = simulator
        self._set_message(f"Started {client_count} simulated client{'s' if client_count != 1 else ''}.")
        return {"ok": True}

    def stop_traffic(self) -> dict[str, Any]:
        self._stop_traffic()
        self._set_message("Stopped simulated traffic.")
        return {"ok": True}

    def _stop_traffic(self) -> None:
        if self.traffic:
            self.traffic.stop()
            self.traffic = None

    def set_congestion(self, percent: Any) -> dict[str, Any]:
        if not self.developer_mode:
            return self._failure("Turn on developer mode in Settings first.")
        try:
            value = validate_congestion(percent)
        except ValueError as error:
            return self._failure(str(error))
        self._set_congestion_percent(value)
        return {"ok": True}

    def _set_congestion_percent(self, percent: int) -> None:
        self.congestion_percent = percent
        if self.video_session:
            self.video_session.set_congestion(percent)

    def shutdown(self) -> None:
        self._stop_traffic()
        self._remember_position()
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
        if event == "accepted":
            self.connection_deadline = None
            self.connected = True
            self.error = False
        elif event == "library":
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
        elif event == "session":
            self.session_code = str(payload["code"])
            self.session_members = int(payload["members"])
            self._set_message(f"In session {self.session_code}. Share this code so others can join.")
        elif event == "left":
            self.session_code = None
            self.session_members = 0
            self._set_message("Left the session.")
        elif event == "status":
            if not self.connected:
                self.message = payload["message"]
        elif event == "connection_lost":
            if self.connected:
                self.connected = False
                self.session_code = None
                self.session_members = 0
                self._lost_connection = True
                self._set_message(payload["message"], is_error=True)
        elif event == "connection_restored":
            if self._lost_connection:
                self._lost_connection = False
                self.connected = True
                self._set_message(payload["message"])
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
                streamed = self.streaming_transfer_id is not None
                self.active_transfer_id = None
                if streamed:
                    self._finish_stream(Path(payload["path"]))
                else:
                    self._play_file(Path(payload["path"]), payload["name"])
        elif event == "error":
            self.active_transfer_id = None
            self.streaming_transfer_id = None
            if self.connection_deadline is not None or not self.receiver.is_running:
                self.connection_deadline = None
                self.connected = False
            self._set_message(payload["message"], is_error=True)

    def _handle_video_event(self, event: str, payload: dict[str, Any]) -> None:
        # Segment progress is shown by the player itself, so only problems are reported here.
        if event in {"connection_lost", "error"}:
            self.video_status = str(payload["message"])
        elif event == "connection_restored":
            self.video_status = ""

    def _play_file(self, path: Path, track_name: str) -> None:
        self._remember_position()
        duration = self._probe_duration(path)
        resume_at = self._resume_position(track_name, duration)
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play(start=resume_at)
        except pygame.error as error:
            self.current_path = path
            self.current_name = path.name
            self.current_track_key = None
            self.track_duration = duration
            self.playback_offset = 0.0
            self.playback_started_at = None
            self.is_paused = True
            self._set_message(f"Received the MP3, but playback failed: {error}", is_error=True)
            return
        self.current_path = path
        self.current_name = path.name
        self.current_track_key = track_name
        self.track_duration = duration
        self.playback_offset = resume_at
        self.playback_started_at = time.monotonic()
        self._last_position_save = self.playback_started_at
        self.playback_finished = False
        self.is_paused = False
        if resume_at > 0:
            self._set_message(f"Now playing {path.name} from {_format_time(resume_at)}.")
        else:
            self._set_message(f"Now playing {path.name}.")

    @staticmethod
    def _probe_duration(path: Path) -> float:
        try:
            duration = float(MP3(path).info.length)
        except (OSError, MutagenError, TypeError, ValueError):
            return 0.0
        return duration if math.isfinite(duration) and duration > 0 else 0.0

    @staticmethod
    def _byte_offset(position: float, duration: float, size: int) -> int:
        if duration <= 0 or size <= 0:
            return 0
        return min(size, int(size * position / duration))

    def _has_downloaded_through(self, transfer_id: bytes, position: float, duration: float, size: int) -> bool:
        needed = min(size, self._byte_offset(position, duration, size) + AUDIO_START_BUFFER_BYTES)
        return self.receiver.wait_for_transfer_bytes(transfer_id, needed, timeout=0) is not None

    def _maybe_start_stream(self) -> None:
        transfer_id = self.active_transfer_id
        if not transfer_id or self.active_transfer_type != "audio" or transfer_id == self._stream_attempt_id:
            return
        tid = bytes.fromhex(transfer_id)
        snapshot = self.receiver.wait_for_transfer_bytes(tid, AUDIO_START_BUFFER_BYTES, timeout=0)
        if snapshot is None or snapshot.error or snapshot.path is None:
            return
        if self._stream_probe is None or self._stream_probe[0] != transfer_id:
            self._stream_probe = (transfer_id, self._probe_duration(snapshot.path))
        duration = self._stream_probe[1]
        name = self.active_transfer_name
        resume_at = self._resume_position(name, duration)
        if resume_at > 0 and not self._has_downloaded_through(tid, resume_at, duration, snapshot.size):
            return

        self._stream_attempt_id = transfer_id
        self._remember_position()
        stream = StreamingAudioFile(self.receiver, tid, snapshot.size)
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(stream, name)
            pygame.mixer.music.play(start=resume_at)
        except pygame.error as error:
            self._set_message(f"Could not start streaming {name}: {error}", is_error=True)
            return
        self.streaming_transfer_id = transfer_id
        self._stream_file = stream
        self._stream_size = snapshot.size
        self.current_path = snapshot.path
        self.current_name = name
        self.current_track_key = name
        self.track_duration = duration
        self.playback_offset = resume_at
        self.playback_started_at = time.monotonic()
        self._last_position_save = self.playback_started_at
        self.playback_finished = False
        self.is_paused = False
        self._set_message(f"Streaming {name} while it downloads.")

    def _finish_stream(self, path: Path) -> None:
        # The download is complete: point the player at the final file and use its exact duration.
        self.streaming_transfer_id = None
        self.current_path = path
        duration = self._probe_duration(path)
        if duration > 0:
            self.track_duration = duration

    def _update_audio_state(self) -> None:
        if not self.current_path or self.is_paused or self.playback_finished:
            return
        try:
            if not pygame.mixer.music.get_busy():
                self.playback_offset = self.track_duration
                self.playback_started_at = None
                self.playback_finished = True
                self.is_paused = True
                self._remember_position()
            elif time.monotonic() - self._last_position_save >= POSITION_SAVE_INTERVAL_SECONDS:
                self._remember_position()
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

    def _save_settings(self) -> str | None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(
                json.dumps(
                    {
                        "host": self.saved_host,
                        "port": self.saved_port,
                        "playback_positions": self.playback_positions,
                        "developer_mode": self.developer_mode,
                    }
                ),
                encoding="utf-8",
            )
            return None
        except OSError as error:
            return f"Couldn't save settings: {error}"

    def _remember_position(self) -> None:
        if not self.current_track_key or self.track_duration <= 0:
            return
        position = self._current_position()
        remaining = self.track_duration - position
        if self.playback_finished or position < 1 or remaining < RESUME_END_MARGIN_SECONDS:
            self.playback_positions.pop(self.current_track_key, None)
        else:
            self.playback_positions[self.current_track_key] = position
        self._last_position_save = time.monotonic()
        error = self._save_settings()
        if error:
            self._set_message(error, is_error=True)

    def _resume_position(self, track_name: str, duration: float) -> float:
        position = self.playback_positions.get(track_name, 0.0)
        if duration <= 0 or position < 1 or duration - position < RESUME_END_MARGIN_SECONDS:
            return 0.0
        return position

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
    video_server: VideoHTTPServer | None = None
    api: AudionatorAPI | None = None
    server_thread: threading.Thread | None = None
    try:
        video_server = VideoHTTPServer(("127.0.0.1", 0))
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
