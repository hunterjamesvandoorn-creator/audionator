from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import imageio_ffmpeg


class VideoPreparationService:
    def __init__(self, library: Path, cache_directory: Path | None = None) -> None:
        self.library = library.resolve()
        self.cache_directory = (cache_directory or default_video_cache_directory()).resolve()
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def prepare(self, name: str) -> Path:
        source = self._find_video(name)
        info = source.stat()
        key = hashlib.sha256(
            f"{source}\0{info.st_size}\0{info.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:24]
        destination = self.cache_directory / f"{key}.mp4"
        lock = self._get_lock(key)
        with lock:
            if destination.is_file():
                return destination
            temporary_directory = Path(
                tempfile.mkdtemp(prefix=f".{key}-", dir=self.cache_directory)
            )
            try:
                prepared_video = temporary_directory / "video.mp4"
                try:
                    subprocess.run(
                        [
                            self.ffmpeg,
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-y",
                            "-i",
                            str(source),
                            "-map",
                            "0:v:0",
                            "-map",
                            "0:a?",
                            "-c",
                            "copy",
                            "-movflags",
                            "+faststart",
                            str(prepared_video),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except subprocess.CalledProcessError as error:
                    detail = (error.stderr or error.stdout or str(error)).strip()
                    raise RuntimeError(f"Could not prepare this MP4 for streaming: {detail}") from error
                if not prepared_video.is_file():
                    raise RuntimeError("FFmpeg did not create a streamable MP4.")
                os.replace(prepared_video, destination)
            finally:
                shutil.rmtree(temporary_directory)
        return destination

    def _find_video(self, name: str) -> Path:
        if not isinstance(name, str) or not name or Path(name).name != name or "\x00" in name:
            raise ValueError("Invalid video file name.")
        if Path(name).suffix.lower() != ".mp4":
            raise ValueError("Only MP4 videos can be streamed.")
        source = (self.library / name).resolve()
        if source.parent != self.library or not source.is_file():
            raise ValueError("That MP4 is not in the server's videos folder.")
        return source

    def _get_lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())


def default_video_cache_directory() -> Path:
    if getattr(sys, "frozen", False):
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return local_app_data / "AudionatorServer" / "video_cache"
    return Path(__file__).resolve().parent / "video_cache"
