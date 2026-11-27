from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import imageio_ffmpeg


SEGMENT_SECONDS = 6
AUDIO_BITRATE_KBPS = 96
# H.264 High 4.0 and AAC-LC for every level, so one MSE SourceBuffer can switch levels.
CODECS = "avc1.640028,mp4a.40.2"
MANIFEST_NAME = "manifest.json"
INIT_SEGMENT_INDEX = -1


@dataclass(frozen=True)
class VideoLevel:
    id: str
    height: int
    maxrate_kbps: int

    @property
    def bandwidth(self) -> int:
        """Upper-bound bits per second for this level, including audio."""
        return (self.maxrate_kbps + AUDIO_BITRATE_KBPS) * 1000


VIDEO_LEVELS = (
    VideoLevel("720p", 720, 3000),
    VideoLevel("480p", 480, 1200),
    VideoLevel("360p", 360, 700),
)


@dataclass(frozen=True)
class PreparedVideo:
    directory: Path
    manifest: dict[str, Any]


class VideoPreparationService:
    def __init__(self, library: Path, cache_directory: Path | None = None) -> None:
        self.library = library.resolve()
        self.cache_directory = (cache_directory or default_video_cache_directory()).resolve()
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def video_names(self) -> list[str]:
        return sorted(
            (path.name for path in self.library.iterdir() if path.is_file() and path.suffix.lower() == ".mp4"),
            key=str.lower,
        )

    def prepare(self, name: str) -> PreparedVideo:
        """Return the segmented ladder for a video, encoding it first if needed."""
        source = self._find_video(name)
        info = source.stat()
        key = hashlib.sha256(
            f"{source}\0{info.st_size}\0{info.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:24]
        directory = self.cache_directory / key
        lock = self._get_lock(key)
        with lock:
            if not (directory / MANIFEST_NAME).is_file():
                self._build(source, key, directory)
            manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
        return PreparedVideo(directory, manifest)

    def segment_path(self, name: str, level_id: str, index: int) -> Path:
        """Return the cached file for a level's init segment (index -1) or media segment."""
        prepared = self.prepare(name)
        if not any(level.id == level_id for level in VIDEO_LEVELS):
            raise ValueError("Unknown video quality level.")
        if index == INIT_SEGMENT_INDEX:
            return prepared.directory / level_id / "init.mp4"
        if not isinstance(index, int) or not 0 <= index < len(prepared.manifest["segments"]):
            raise ValueError("That video segment does not exist.")
        return prepared.directory / level_id / f"seg{index:05d}.m4s"

    def _build(self, source: Path, key: str, directory: Path) -> None:
        self.cache_directory.mkdir(parents=True, exist_ok=True)
        temporary_directory = Path(
            tempfile.mkdtemp(prefix=f".{key}-", dir=self.cache_directory)
        )
        try:
            for level in VIDEO_LEVELS:
                level_directory = temporary_directory / level.id
                level_directory.mkdir()
                self._encode_level(source, level, level_directory)
            durations = _read_segment_durations(temporary_directory / VIDEO_LEVELS[0].id / "index.m3u8")
            if not durations:
                raise RuntimeError("FFmpeg did not create any video segments.")
            manifest = {
                "name": source.name,
                "codecs": CODECS,
                "segment_seconds": SEGMENT_SECONDS,
                "segments": durations,
                "levels": [
                    {"id": level.id, "height": level.height, "bandwidth": level.bandwidth}
                    for level in VIDEO_LEVELS
                ],
            }
            # The manifest is written last, so a directory without one is always incomplete.
            (temporary_directory / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
            if directory.exists():
                shutil.rmtree(directory)
            os.replace(temporary_directory, directory)
        finally:
            shutil.rmtree(temporary_directory, ignore_errors=True)

    def _encode_level(self, source: Path, level: VideoLevel, directory: Path) -> None:
        command = [
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
            "-vf",
            f"scale=-2:min({level.height}\\,ih)",
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-level:v",
            "4.0",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-maxrate",
            f"{level.maxrate_kbps}k",
            "-bufsize",
            f"{level.maxrate_kbps * 2}k",
            "-pix_fmt",
            "yuv420p",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{SEGMENT_SECONDS})",
            "-c:a",
            "aac",
            "-b:a",
            f"{AUDIO_BITRATE_KBPS}k",
            "-f",
            "hls",
            "-hls_time",
            str(SEGMENT_SECONDS),
            "-hls_playlist_type",
            "vod",
            "-hls_segment_type",
            "fmp4",
            "-hls_fmp4_init_filename",
            str(directory / "init.mp4"),
            "-hls_segment_filename",
            str(directory / "seg%05d.m4s"),
            str(directory / "index.m3u8"),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or str(error)).strip()
            raise RuntimeError(f"Could not prepare the {level.id} version of this video: {detail}") from error
        if not (directory / "index.m3u8").is_file() or not (directory / "init.mp4").is_file():
            raise RuntimeError(f"FFmpeg did not create the {level.id} video segments.")

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


def manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    """Compact form sent over UDP. Every segment except the last lasts segment_seconds."""
    segments = manifest["segments"]
    return {
        "codecs": manifest["codecs"],
        "segment_seconds": manifest["segment_seconds"],
        "segment_count": len(segments),
        "last_segment_seconds": segments[-1],
        "duration": round(sum(segments), 3),
        "levels": manifest["levels"],
    }


def _read_segment_durations(playlist: Path) -> list[float]:
    durations: list[float] = []
    for line in playlist.read_text(encoding="utf-8").splitlines():
        if line.startswith("#EXTINF:"):
            durations.append(round(float(line.split(":", 1)[1].split(",")[0]), 3))
    return durations
