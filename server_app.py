from __future__ import annotations

import argparse
import sys
from pathlib import Path

from server import run_udp_server


def default_music_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "music"
    return Path(__file__).resolve().parent / "music"


def default_video_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "videos"
    return Path(__file__).resolve().parent / "videos"


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the Audionator media server.")
    parser.add_argument("--host", default="0.0.0.0", help="Network interface to serve on (default: all interfaces)")
    parser.add_argument("--port", type=int, default=5005, help="UDP port (default: 5005)")
    parser.add_argument(
        "--library",
        type=Path,
        default=default_music_directory(),
        help="Folder containing MP3 files (default: music folder beside this program)",
    )
    parser.add_argument(
        "--video-library",
        type=Path,
        default=default_video_directory(),
        help="Folder containing MP4 files (default: videos folder beside this program)",
    )
    args = parser.parse_args()
    run_udp_server(args.host, args.port, args.library, args.video_library)


if __name__ == "__main__":
    main()
