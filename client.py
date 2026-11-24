from __future__ import annotations

import argparse
import math
import os
import queue
import subprocess
import sys
import tkinter as tk
import time
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import pygame
from mutagen import MutagenError
from mutagen.mp3 import MP3

from receiver import AudioReceiver
from video_stream import run_udp_video_player


BACKGROUND = "#10110f"
PANEL = "#191b17"
PANEL_LIGHT = "#23261f"
BORDER = "#30332d"
TEXT = "#f2f2e9"
MUTED = "#92968b"
ACCENT = "#d3f36b"


def default_download_directory() -> Path:
    if getattr(sys, "frozen", False):
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return local_app_data / "Audionator" / "received"
    return Path(__file__).resolve().parent / "received"


class AudionatorClient:
    def __init__(self, root: tk.Tk, download_directory: Path) -> None:
        self.root = root
        self.root.title("Audionator · Listen together")
        self.root.geometry("1080x740")
        self.root.minsize(860, 620)
        self.root.configure(bg=BACKGROUND)
        self.receiver = AudioReceiver(download_directory)
        self.media_by_type: dict[str, dict[str, dict[str, Any]]] = {"audio": {}, "video": {}}
        self.current_library_type = "audio"
        self.track_by_name = self.media_by_type[self.current_library_type]
        self.current_path: Path | None = None
        self.is_paused = False
        self.playback_finished = False
        self.playback_offset = 0.0
        self.playback_started_at: float | None = None
        self.track_duration = 0.0
        self.is_seeking = False
        self.connection_deadline: float | None = None
        self.connected_server: tuple[str, int] | None = None
        self._configure_theme()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._poll_events)
        self.root.after(500, self._poll_playback)

    def _configure_theme(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Audionator.Horizontal.TProgressbar",
            troughcolor="#30332d",
            background=ACCENT,
            bordercolor="#30332d",
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )
        style.configure("Library.TNotebook", background=PANEL, borderwidth=0)
        style.configure(
            "Library.TNotebook.Tab",
            background="#20221e",
            foreground=MUTED,
            padding=(16, 8),
            borderwidth=0,
        )
        style.map(
            "Library.TNotebook.Tab",
            background=[("selected", PANEL)],
            foreground=[("selected", ACCENT)],
        )

    def _build_ui(self) -> None:
        shell = tk.Frame(self.root, bg=BACKGROUND)
        shell.pack(fill="both", expand=True)
        self._build_sidebar(shell)

        content = tk.Frame(shell, bg=BACKGROUND)
        content.pack(side="left", fill="both", expand=True)
        self._build_topbar(content)

        body = tk.Frame(content, bg=BACKGROUND, padx=36, pady=18)
        body.pack(fill="both", expand=True)
        self._build_heading(body)
        self._build_connect_card(body)
        self._build_library(body)
        self._build_player(body)
        self.status_var = tk.StringVar(value="Connect to an Audionator server on your local network.")
        self.status_label = tk.Label(
            body,
            textvariable=self.status_var,
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.status_label.pack(fill="x", pady=(12, 0))

    def _build_sidebar(self, parent: tk.Frame) -> None:
        sidebar = tk.Frame(parent, bg="#141512", width=205, padx=17, pady=22)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg="#141512")
        brand.pack(fill="x", pady=(0, 37))
        mark = tk.Label(
            brand,
            text="≋",
            bg=ACCENT,
            fg="#171912",
            font=("Segoe UI", 20, "bold"),
            width=2,
            height=1,
        )
        mark.pack(side="left", padx=(0, 10))
        tk.Label(
            brand,
            text="audio",
            bg="#141512",
            fg=TEXT,
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            brand,
            text="nator",
            bg="#141512",
            fg=ACCENT,
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")

        tk.Label(
            sidebar,
            text="YOUR SPACE",
            bg="#141512",
            fg="#777b71",
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", padx=8, pady=(0, 9))
        nav_items = (
            ("♫   Listening room", self._focus_connection),
            ("▤   Your library", self._focus_library),
            ("⌁   Audio quality", self._show_audio_quality),
        )
        self.nav_buttons: list[tk.Button] = []
        for label, command in nav_items:
            item = tk.Button(
                sidebar,
                text=label,
                bg="#23261f" if label.startswith("♫") else "#141512",
                fg=ACCENT if label.startswith("♫") else "#a9aca2",
                activebackground="#23261f",
                activeforeground=TEXT,
                font=("Segoe UI", 10),
                anchor="w",
                padx=10, pady=10,
                relief="flat",
                cursor="hand2",
                command=command,
            )
            item.pack(fill="x", pady=2)
            self.nav_buttons.append(item)

        spacer = tk.Frame(sidebar, bg="#141512")
        spacer.pack(fill="both", expand=True)
        tk.Frame(sidebar, height=1, bg=BORDER).pack(fill="x", pady=(0, 13))
        tk.Label(
            sidebar,
            text="PRIVATE NETWORK",
            bg="#141512",
            fg="#777b71",
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", padx=8)
        tk.Label(
            sidebar,
            text="Audio is sent directly\nbetween your devices.",
            bg="#141512",
            fg=MUTED,
            font=("Segoe UI", 9),
            justify="left",
        ).pack(anchor="w", padx=8, pady=(7, 0))

    def _build_topbar(self, parent: tk.Frame) -> None:
        topbar = tk.Frame(parent, bg=BACKGROUND, padx=36, pady=18)
        topbar.pack(fill="x")
        tk.Label(
            topbar,
            text="AUDIONATOR  /  LISTENING ROOM",
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="left")
        self.connection_dot = tk.Label(
            topbar,
            text="●  OFFLINE",
            bg=BACKGROUND,
            fg="#c28f72",
            font=("Segoe UI", 8, "bold"),
        )
        self.connection_dot.pack(side="right")

    def _build_heading(self, parent: tk.Frame) -> None:
        tk.Label(
            parent,
            text="YOUR SHARED LISTENING SPACE",
            bg=BACKGROUND,
            fg=ACCENT,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        tk.Label(
            parent,
            text="Music sounds better together.",
            bg=BACKGROUND,
            fg=TEXT,
            font=("Segoe UI", 26, "bold"),
        ).pack(anchor="w", pady=(7, 2))
        tk.Label(
            parent,
            text="Connect to a server to listen to MP3s and watch MP4 video over UDP.",
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(0, 18))

    def _build_connect_card(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1, padx=16, pady=13)
        card.pack(fill="x", pady=(0, 9))
        fields = tk.Frame(card, bg=PANEL)
        fields.pack(side="left")
        self.host_var = tk.StringVar(value="127.0.0.1")
        self.port_var = tk.StringVar(value="5005")
        tk.Label(fields, text="SERVER IP ADDRESS", bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        tk.Label(fields, text="PORT", bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold")).grid(
            row=0, column=1, sticky="w", padx=(0, 10)
        )
        self.host_entry = tk.Entry(
            fields,
            textvariable=self.host_var,
            bg=PANEL_LIGHT,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            width=21,
        )
        self.host_entry.grid(row=1, column=0, sticky="ew", ipady=7, padx=(0, 10), pady=(4, 0))
        self.port_entry = tk.Entry(
            fields,
            textvariable=self.port_var,
            bg=PANEL_LIGHT,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            width=7,
        )
        self.port_entry.grid(row=1, column=1, sticky="w", ipady=7, padx=(0, 10), pady=(4, 0))
        self.connect_button = tk.Button(
            card,
            text="Connect",
            command=self._connect,
            bg=ACCENT,
            fg="#171912",
            activebackground="#c3e45d",
            activeforeground="#171912",
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padx=14,
            pady=7,
            cursor="hand2",
        )
        self.connect_button.pack(side="left", anchor="s", pady=(20, 0))
        self.connection_help = tk.Label(
            parent,
            text="On the server computer, run ipconfig and enter its IPv4 Address. Both computers must be on the same Wi-Fi or local network.",
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
            wraplength=770,
        )
        self.connection_help.pack(fill="x", pady=(0, 13))

    def _build_library(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True)
        header = tk.Frame(card, bg=PANEL, padx=17, pady=14)
        header.pack(fill="x")
        tk.Label(header, text="Server library", bg=PANEL, fg=TEXT, font=("Segoe UI", 12, "bold")).pack(
            side="left"
        )
        self.library_count = tk.Label(header, text="Connect to browse", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
        self.library_count.pack(side="right")

        self.library_notebook = ttk.Notebook(card, style="Library.TNotebook")
        self.library_notebook.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.audio_tab = tk.Frame(self.library_notebook, bg=PANEL)
        self.video_tab = tk.Frame(self.library_notebook, bg=PANEL)
        self.library_notebook.add(self.audio_tab, text="Audio")
        self.library_notebook.add(self.video_tab, text="Videos")
        self.audio_track_list = self._build_media_list(self.audio_tab)
        self.video_track_list = self._build_media_list(self.video_tab)
        self.track_lists = {
            "audio": self.audio_track_list,
            "video": self.video_track_list,
        }
        self.track_list = self.audio_track_list
        self.library_notebook.bind("<<NotebookTabChanged>>", self._on_library_tab_changed)

        footer = tk.Frame(parent, bg=BACKGROUND)
        footer.pack(fill="x", pady=(11, 0))
        self.receive_button = tk.Button(
            footer,
            text="↓   Receive & play",
            command=self._receive_selected,
            state="disabled",
            bg=ACCENT,
            fg="#171912",
            activebackground="#c3e45d",
            activeforeground="#171912",
            disabledforeground="#74786b",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=16,
            pady=10,
            cursor="hand2",
        )
        self.receive_button.pack(side="left")
        self.download_note = tk.Label(
            footer,
            text=f"Saved to  {self.receiver.download_directory}",
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 8),
        )
        self.download_note.pack(side="left", padx=12)
        self.open_downloads_button = tk.Button(
            footer,
            text="Open folder",
            command=self._open_download_directory,
            bg="#20221e",
            fg=TEXT,
            activebackground="#30332d",
            activeforeground=TEXT,
            relief="flat",
            font=("Segoe UI", 8),
            padx=10,
            pady=7,
            cursor="hand2",
        )
        self.open_downloads_button.pack(side="right")

    def _build_media_list(self, parent: tk.Frame) -> tk.Listbox:
        columns = tk.Frame(parent, bg="#20221e", padx=14, pady=8)
        columns.pack(fill="x", padx=1)
        tk.Label(columns, text="#", bg="#20221e", fg=MUTED, width=4, anchor="w", font=("Segoe UI", 8)).pack(
            side="left"
        )
        tk.Label(columns, text="TITLE", bg="#20221e", fg=MUTED, anchor="w", font=("Segoe UI", 8, "bold")).pack(
            side="left", fill="x", expand=True
        )
        tk.Label(columns, text="SIZE", bg="#20221e", fg=MUTED, width=12, anchor="e", font=("Segoe UI", 8)).pack(
            side="right"
        )

        list_container = tk.Frame(parent, bg=PANEL)
        list_container.pack(fill="both", expand=True, padx=8, pady=(3, 8))
        track_list = tk.Listbox(
            list_container,
            bg=PANEL,
            fg=TEXT,
            selectbackground="#30372a",
            selectforeground=ACCENT,
            activestyle="none",
            relief="flat",
            highlightthickness=0,
            font=("Segoe UI", 10),
            height=7,
        )
        track_list.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=track_list.yview)
        scrollbar.pack(side="right", fill="y")
        track_list.configure(yscrollcommand=scrollbar.set)
        track_list.bind("<Double-Button-1>", lambda _event: self._receive_selected())
        track_list.bind("<<ListboxSelect>>", lambda _event: self._update_action_for_selection())
        return track_list

    def _on_library_tab_changed(self, _event: Any = None) -> None:
        self.current_library_type = "video" if self.library_notebook.select() == str(self.video_tab) else "audio"
        self.track_list = self.track_lists[self.current_library_type]
        self.track_by_name = self.media_by_type[self.current_library_type]
        self._update_action_for_selection()

    def _build_player(self, parent: tk.Frame) -> None:
        player = tk.Frame(parent, bg=PANEL_LIGHT, highlightbackground=BORDER, highlightthickness=1, padx=14, pady=10)
        player.pack(fill="x", pady=(15, 0))
        self.now_playing = tk.Label(
            player,
            text="Nothing playing yet",
            bg=PANEL_LIGHT,
            fg=TEXT,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        )
        self.now_playing.pack(fill="x")
        self.transfer_progress = ttk.Progressbar(
            player,
            style="Audionator.Horizontal.TProgressbar",
            maximum=100,
            value=0,
        )
        self.transfer_progress.pack(fill="x", pady=(9, 8))
        self.transfer_progress.pack_forget()

        self.seek_row = tk.Frame(player, bg=PANEL_LIGHT)
        self.seek_row.pack(fill="x", pady=(6, 8))
        self.elapsed_label = tk.Label(
            self.seek_row,
            text="0:00",
            bg=PANEL_LIGHT,
            fg=MUTED,
            font=("Segoe UI", 8),
            width=5,
            anchor="w",
        )
        self.elapsed_label.pack(side="left")
        self.seek_var = tk.DoubleVar(value=0.0)
        self.seek_slider = tk.Scale(
            self.seek_row,
            from_=0.0,
            to=1.0,
            orient="horizontal",
            variable=self.seek_var,
            command=self._on_seek_changed,
            showvalue=False,
            resolution=0.1,
            bg=PANEL_LIGHT,
            fg=MUTED,
            troughcolor="#3a3d34",
            activebackground=ACCENT,
            highlightthickness=0,
            bd=0,
            sliderrelief="flat",
            sliderlength=14,
            state="disabled",
        )
        self.seek_slider.pack(side="left", fill="x", expand=True, padx=8)
        self.seek_slider.bind("<ButtonPress-1>", self._begin_seek)
        self.seek_slider.bind("<ButtonRelease-1>", self._end_seek)
        self.duration_label = tk.Label(
            self.seek_row,
            text="0:00",
            bg=PANEL_LIGHT,
            fg=MUTED,
            font=("Segoe UI", 8),
            width=5,
            anchor="e",
        )
        self.duration_label.pack(side="right")

        controls = tk.Frame(player, bg=PANEL_LIGHT)
        controls.pack(fill="x")
        self.play_button = tk.Button(
            controls,
            text="▶  Play",
            command=self._toggle_playback,
            state="disabled",
            bg="#30332d",
            fg=TEXT,
            activebackground="#41453b",
            activeforeground=TEXT,
            disabledforeground="#74786b",
            relief="flat",
            font=("Segoe UI", 9),
            padx=11,
            pady=6,
        )
        self.play_button.pack(side="left")
        self.time_label = tk.Label(
            controls,
            text="Drag the timeline to seek through a received track.",
            bg=PANEL_LIGHT,
            fg=MUTED,
            font=("Segoe UI", 8),
        )
        self.time_label.pack(side="left", padx=11)

    def _connect(self) -> None:
        try:
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError("Port must be between 1 and 65535.")
            host = self.host_var.get().strip()
            if not host:
                raise ValueError("Enter the server computer's IPv4 address.")
            server = (host, port)
            if self.receiver.is_running and server == self.connected_server:
                self.receiver.request_library()
                self._show_status("Refreshing the server library…")
            else:
                if self.receiver.is_running:
                    self.receiver.close()
                    self.receiver = AudioReceiver(self.receiver.download_directory)
                self.receiver.connect(host, port)
                self.connected_server = server
        except (OSError, ValueError, RuntimeError) as error:
            self._show_status(f"Could not connect: {error}", is_error=True)
            return
        self.connection_dot.configure(text="●  CONNECTING", fg="#e8c66a")
        self.connect_button.configure(state="disabled", text="Connecting…")
        self.connection_deadline = time.monotonic() + 6
        self._show_status("Waiting for the server…")

    def _receive_selected(self) -> None:
        selection = self.track_list.curselection()
        if not selection:
            return
        names = list(self.track_by_name)
        if selection[0] >= len(names):
            return
        name = names[selection[0]]
        if self.track_by_name[name].get("type") == "video":
            self._open_udp_video(name)
            return
        try:
            transfer_id = self.receiver.request_file(name)
        except (OSError, RuntimeError, ValueError) as error:
            self._show_status(f"Could not request the track: {error}", is_error=True)
            return
        self.active_transfer_id = transfer_id.hex()
        self.receive_button.configure(state="disabled", text="Receiving…")
        self.transfer_progress.configure(value=0)
        self.transfer_progress.pack(fill="x", pady=(9, 8), before=self.seek_row)
        self.now_playing.configure(text=f"Preparing  ·  {name}")
        self._show_status(f"Receiving {name} over UDP…")

    def _open_udp_video(self, name: str) -> None:
        if not self.connected_server:
            self._show_status("Connect to the server before opening a video.", is_error=True)
            return
        server_host, server_port = self.connected_server
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve()))
        command.extend(
            (
                "--udp-video-host",
                server_host,
                "--udp-video-port",
                str(server_port),
                "--udp-video-name",
                name,
                "--downloads",
                str(self.receiver.download_directory),
            )
        )
        try:
            subprocess.Popen(
                command,
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            self._show_status(f"Could not open the UDP video player: {error}", is_error=True)
            return
        self.now_playing.configure(text=f"Now watching  ·  {name}")
        self._show_status("Opening the video player. It will begin after a short UDP buffer.")

    def _update_action_for_selection(self) -> None:
        selection = self.track_list.curselection()
        names = list(self.track_by_name)
        if not selection or selection[0] >= len(names):
            self.receive_button.configure(state="disabled", text="↓   Receive & play")
            return
        name = names[selection[0]]
        is_video = self.track_by_name[name].get("type") == "video"
        self.receive_button.configure(
            state="normal",
            text="▶   Play video" if is_video else "↓   Receive & play",
        )

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.receiver.events.get_nowait()
                self._handle_event(event, payload)
        except queue.Empty:
            pass
        if self.connection_deadline is not None and time.monotonic() >= self.connection_deadline:
            self.connection_deadline = None
            self.receiver.close()
            self.connected_server = None
            self.connect_button.configure(state="normal", text="Try again")
            self.connection_dot.configure(text="●  NOT FOUND", fg="#f0a879")
            self._show_status(
                "No server response. Check the IP and port, then make sure the server is running.",
                is_error=True,
            )
        self.root.after(100, self._poll_events)

    def _handle_event(self, event: str, payload: dict[str, Any]) -> None:
        if event == "status":
            self._show_status(payload["message"])
        elif event == "library":
            self.connection_deadline = None
            self.connection_dot.configure(text="●  CONNECTED", fg=ACCENT)
            self.connect_button.configure(state="normal", text="Refresh library")
            files = payload["files"]
            self.media_by_type = {"audio": {}, "video": {}}
            for item in files:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                media_type = item.get("type")
                if media_type not in self.media_by_type:
                    media_type = "video" if Path(item["name"]).suffix.lower() == ".mp4" else "audio"
                self.media_by_type[media_type][item["name"]] = item
            audio_count = len(self.media_by_type["audio"])
            video_count = len(self.media_by_type["video"])
            for media_type, track_list in self.track_lists.items():
                track_list.delete(0, "end")
                entries = self.media_by_type[media_type]
                if not entries:
                    folder = "videos" if media_type == "video" else "music"
                    extension = "MP4" if media_type == "video" else "MP3"
                    track_list.insert("end", f"No {extension} files found in the server's {folder} folder.")
                    continue
                for name, item in entries.items():
                    symbol = "▶" if media_type == "video" else "♫"
                    track_list.insert(
                        "end",
                        f"{symbol}    {name}     ·     {self._format_size(item.get('size', 0))}",
                    )
            self.library_count.configure(text=f"{audio_count} audio · {video_count} videos")
            self.track_by_name = self.media_by_type[self.current_library_type]
            self.track_list = self.track_lists[self.current_library_type]
            if not self.track_by_name and video_count:
                self.library_notebook.select(self.video_tab)
                self._on_library_tab_changed()
            if self.track_by_name:
                self.track_list.selection_set(0)
                self._update_action_for_selection()
                self._show_status("Choose a song in Audio or a video in Videos, then select its action.")
            else:
                self.receive_button.configure(state="disabled", text="↓   Receive & play")
                self._show_status("The server is online, but its music and videos folders have no supported files.")
        elif event == "transfer_started":
            self.active_transfer_id = payload["id"]
            self.transfer_progress.configure(value=0)
            self.transfer_progress.pack(fill="x", pady=(9, 8), before=self.seek_row)
            self.now_playing.configure(text=f"Receiving  ·  {payload['name']}")
            self._show_status(f"Receiving {payload['name']} over UDP…")
        elif event == "transfer_progress":
            if payload["id"] == getattr(self, "active_transfer_id", None):
                percent = 100 if payload["total"] == 0 else 100 * payload["received"] / payload["total"]
                self.transfer_progress.configure(value=percent)
                self._show_status(f"Receiving {payload['name']}… {percent:.0f}%")
        elif event == "transfer_complete":
            self.transfer_progress.pack_forget()
            self._update_action_for_selection()
            self._play_file(Path(payload["path"]))
        elif event == "error":
            self.connection_deadline = None
            self.connection_dot.configure(text="●  CHECK CONNECTION", fg="#f0a879")
            self.connect_button.configure(state="normal", text="Try again")
            self.transfer_progress.pack_forget()
            self._update_action_for_selection()
            self._show_status(payload["message"], is_error=True)

    def _play_file(self, path: Path) -> None:
        duration_error: str | None = None
        try:
            duration = float(MP3(path).info.length)
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("The MP3 file does not contain a usable duration.")
        except (OSError, MutagenError, TypeError, ValueError) as error:
            duration = 0.0
            duration_error = str(error)

        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(str(path))
            pygame.mixer.music.play()
        except pygame.error as error:
            self.now_playing.configure(text=f"Received  ·  {path.name}")
            self.play_button.configure(state="disabled")
            self._show_status(f"Received the MP3, but playback failed: {error}", is_error=True)
            return
        self.current_path = path
        self.track_duration = duration
        self.playback_offset = 0.0
        self.playback_started_at = time.monotonic()
        self.playback_finished = False
        self.is_paused = False
        self.play_button.configure(state="normal", text="Ⅱ  Pause")
        self.now_playing.configure(text=f"Now playing  ·  {path.name}")
        self.seek_var.set(0.0)
        self.seek_slider.configure(from_=0.0, to=max(duration, 1.0))
        self.seek_slider.configure(state="normal" if duration else "disabled")
        self._update_time_labels(0.0)
        if duration_error:
            self._show_status(f"Playing {path.name}, but seeking is unavailable: {duration_error}", is_error=True)
        else:
            self._show_status(f"Received and playing {path.name}.")

    def _toggle_playback(self) -> None:
        if not self.current_path:
            return
        try:
            if self.playback_finished:
                pygame.mixer.music.play()
                self.playback_offset = 0.0
                self.playback_started_at = time.monotonic()
                self.playback_finished = False
                self.is_paused = False
                self.play_button.configure(text="Ⅱ  Pause")
                self.seek_var.set(0.0)
                self._update_time_labels(0.0)
            elif self.is_paused:
                pygame.mixer.music.unpause()
                self.playback_started_at = time.monotonic()
                self.is_paused = False
                self.play_button.configure(text="Ⅱ  Pause")
            else:
                self.playback_offset = self._current_position()
                pygame.mixer.music.pause()
                self.playback_started_at = None
                self.is_paused = True
                self.play_button.configure(text="▶  Play")
                self._update_time_labels(self.playback_offset)
        except pygame.error as error:
            self._show_status(f"Playback control failed: {error}", is_error=True)

    def _begin_seek(self, _event: tk.Event) -> None:
        if self.current_path and self.track_duration > 0:
            self.is_seeking = True

    def _end_seek(self, _event: tk.Event) -> None:
        if self.is_seeking:
            self.root.after_idle(self._commit_seek)

    def _on_seek_changed(self, value: str) -> None:
        if self.is_seeking:
            self._update_time_labels(float(value))

    def _commit_seek(self) -> None:
        if not self.is_seeking or not self.current_path:
            return
        self.is_seeking = False
        position = min(max(self.seek_var.get(), 0.0), self.track_duration)
        try:
            if self.playback_finished:
                pygame.mixer.music.play(start=position)
                self.is_paused = False
                self.playback_finished = False
                self.playback_started_at = time.monotonic()
                self.play_button.configure(text="Ⅱ  Pause")
            else:
                pygame.mixer.music.set_pos(position)
                if not self.is_paused:
                    self.playback_started_at = time.monotonic()
            self.playback_offset = position
            self._update_time_labels(position)
        except pygame.error as error:
            self._show_status(f"Could not seek to that position: {error}", is_error=True)

    def _focus_connection(self) -> None:
        self.host_entry.focus_set()

    def _focus_library(self) -> None:
        self.track_list.focus_set()
        if self.track_by_name:
            self.track_list.selection_clear(0, "end")
            self.track_list.selection_set(0)
            self.track_list.activate(0)

    def _show_audio_quality(self) -> None:
        messagebox.showinfo(
            "Audio quality",
            "Audionator transfers and plays the original MP3 without re-encoding it. "
            "That preserves the source file's audio quality; quality conversion settings are not available in this version.",
            parent=self.root,
        )

    def _open_download_directory(self) -> None:
        try:
            self.receiver.download_directory.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.receiver.download_directory))
        except OSError as error:
            self._show_status(f"Could not open the saved music folder: {error}", is_error=True)

    def _current_position(self) -> float:
        if self.is_paused or self.playback_started_at is None:
            return self.playback_offset
        position = self.playback_offset + time.monotonic() - self.playback_started_at
        return min(position, self.track_duration)

    def _update_time_labels(self, position: float) -> None:
        self.elapsed_label.configure(text=self._format_time(position))
        self.duration_label.configure(text=self._format_time(self.track_duration))

    def _poll_playback(self) -> None:
        if self.current_path and not self.is_paused and not self.playback_finished:
            try:
                if not pygame.mixer.music.get_busy():
                    self.playback_offset = self.track_duration
                    self.playback_started_at = None
                    self.playback_finished = True
                    self.is_paused = True
                    self.play_button.configure(text="▶  Play")
                    self._update_time_labels(self.track_duration)
                    self.seek_var.set(self.track_duration)
                elif not self.is_seeking:
                    position = self._current_position()
                    self.seek_var.set(position)
                    self._update_time_labels(position)
            except pygame.error as error:
                self._show_status(f"Audio output error: {error}", is_error=True)
        self.root.after(500, self._poll_playback)

    @staticmethod
    def _format_time(seconds: float) -> str:
        total_seconds = max(0, int(seconds))
        minutes, seconds_part = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds_part:02d}"
        return f"{minutes}:{seconds_part:02d}"

    def _show_status(self, message: str, is_error: bool = False) -> None:
        self.status_var.set(message)
        self.status_label.configure(fg="#f0a879" if is_error else MUTED)

    @staticmethod
    def _format_size(size: Any) -> str:
        if not isinstance(size, (int, float)) or size < 0:
            return "—"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    def close(self) -> None:
        self.receiver.close()
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        self.root.destroy()


def _show_video_error(message: str) -> None:
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Audionator video player", message)
    root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Open the Audionator audio and video client.")
    parser.add_argument(
        "--downloads",
        type=Path,
        default=default_download_directory(),
        help="Folder where received MP3 files are saved",
    )
    parser.add_argument("--udp-video-host", help=argparse.SUPPRESS)
    parser.add_argument("--udp-video-port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--udp-video-name", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.udp_video_name:
        if not args.udp_video_host or args.udp_video_port is None:
            _show_video_error("The video player is missing its server connection details.")
            return
        try:
            run_udp_video_player(
                args.udp_video_host,
                args.udp_video_port,
                args.udp_video_name,
                args.downloads,
            )
        except (OSError, RuntimeError, ValueError) as error:
            _show_video_error(f"The UDP video player could not start: {error}")
        return
    root = tk.Tk()
    try:
        app = AudionatorClient(root, args.downloads)
        app.host_entry.bind("<Return>", lambda _event: app._connect())
        app.port_entry.bind("<Return>", lambda _event: app._connect())
        root.mainloop()
    except tk.TclError as error:
        messagebox.showerror("Audionator", f"Could not start the client window: {error}")
        raise


if __name__ == "__main__":
    main()
