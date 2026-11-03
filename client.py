from __future__ import annotations

import argparse
import queue
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import pygame

from receiver import AudioReceiver


BACKGROUND = "#10110f"
PANEL = "#191b17"
PANEL_LIGHT = "#23261f"
BORDER = "#30332d"
TEXT = "#f2f2e9"
MUTED = "#92968b"
ACCENT = "#d3f36b"


class AudionatorClient:
    def __init__(self, root: tk.Tk, download_directory: Path) -> None:
        self.root = root
        self.root.title("Audionator · Listen together")
        self.root.geometry("1020x700")
        self.root.minsize(760, 560)
        self.root.configure(bg=BACKGROUND)
        self.receiver = AudioReceiver(download_directory)
        self.track_by_name: dict[str, dict[str, Any]] = {}
        self.current_path: Path | None = None
        self.is_paused = False
        self._progress_after: str | None = None
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
        self.nav_labels: list[tk.Label] = []
        for label in ("♫   Listening room", "▤   Your library", "⌁   Audio quality"):
            item = tk.Label(
                sidebar,
                text=label,
                bg="#23261f" if label.startswith("♫") else "#141512",
                fg=ACCENT if label.startswith("♫") else "#a9aca2",
                font=("Segoe UI", 10),
                anchor="w",
                padx=10,
                pady=11,
            )
            item.pack(fill="x", pady=2)
            self.nav_labels.append(item)

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
            text="Connect to a server and bring its MP3 library into your room.",
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(0, 18))

    def _build_connect_card(self, parent: tk.Frame) -> None:
        card = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1, padx=16, pady=12)
        card.pack(fill="x", pady=(0, 15))
        tk.Label(card, text="SERVER ADDRESS", bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(
            side="left", padx=(0, 12)
        )
        self.host_var = tk.StringVar(value="127.0.0.1")
        self.port_var = tk.StringVar(value="5005")
        self.host_entry = tk.Entry(
            card,
            textvariable=self.host_var,
            bg=PANEL_LIGHT,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            width=22,
        )
        self.host_entry.pack(side="left", ipady=7, padx=(0, 8))
        self.port_entry = tk.Entry(
            card,
            textvariable=self.port_var,
            bg=PANEL_LIGHT,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            width=7,
        )
        self.port_entry.pack(side="left", ipady=7, padx=(0, 10))
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
        self.connect_button.pack(side="left")

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

        columns = tk.Frame(card, bg="#20221e", padx=14, pady=8)
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

        list_container = tk.Frame(card, bg=PANEL)
        list_container.pack(fill="both", expand=True, padx=8, pady=(3, 8))
        self.track_list = tk.Listbox(
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
        self.track_list.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=self.track_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.track_list.configure(yscrollcommand=scrollbar.set)
        self.track_list.bind("<Double-Button-1>", lambda _event: self._receive_selected())

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
            text=f"Received files save to  {self.receiver.download_directory}",
            bg=BACKGROUND,
            fg=MUTED,
            font=("Segoe UI", 8),
        )
        self.download_note.pack(side="left", padx=12)

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
        self.progress = ttk.Progressbar(
            player,
            style="Audionator.Horizontal.TProgressbar",
            maximum=100,
            value=0,
        )
        self.progress.pack(fill="x", pady=(9, 8))
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
            text="MP3 playback is available after a track is received.",
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
            if self.receiver.is_running:
                self.receiver.request_library()
                self._show_status("Refreshing the server library…")
            else:
                self.receiver.connect(self.host_var.get().strip(), port)
        except (OSError, ValueError, RuntimeError) as error:
            self._show_status(f"Could not connect: {error}", is_error=True)
            return
        self.connect_button.configure(state="normal", text="Refresh")
        if not self.receiver.is_running:
            self._show_status("Connecting and requesting the server's MP3 library…")

    def _receive_selected(self) -> None:
        selection = self.track_list.curselection()
        if not selection:
            return
        name = list(self.track_by_name)[selection[0]]
        try:
            transfer_id = self.receiver.request_file(name)
        except (OSError, RuntimeError, ValueError) as error:
            self._show_status(f"Could not request the track: {error}", is_error=True)
            return
        self.active_transfer_id = transfer_id.hex()
        self.receive_button.configure(state="disabled", text="Receiving…")
        self.progress.configure(value=0)
        self.now_playing.configure(text=f"Preparing  ·  {name}")
        self._show_status(f"Receiving {name} over UDP…")

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.receiver.events.get_nowait()
                self._handle_event(event, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, event: str, payload: dict[str, Any]) -> None:
        if event == "status":
            self.connection_dot.configure(text="●  CONNECTED", fg=ACCENT)
            self.connect_button.configure(state="normal", text="Refresh")
            self._show_status(payload["message"])
        elif event == "library":
            files = payload["files"]
            self.track_by_name = {
                item["name"]: item for item in files if isinstance(item, dict) and "name" in item
            }
            self.track_list.delete(0, "end")
            for name, item in self.track_by_name.items():
                self.track_list.insert("end", f"♫    {name}     ·     {self._format_size(item.get('size', 0))}")
            self.library_count.configure(text=f"{len(self.track_by_name)} MP3 tracks")
            self.receive_button.configure(state="normal" if self.track_by_name else "disabled")
            self._show_status("Library updated. Select a track to receive it.")
        elif event == "transfer_started":
            self.active_transfer_id = payload["id"]
            self.now_playing.configure(text=f"Receiving  ·  {payload['name']}")
            self._show_status(f"Receiving {payload['name']} over UDP…")
        elif event == "transfer_progress":
            if payload["id"] == getattr(self, "active_transfer_id", None):
                percent = 100 if payload["total"] == 0 else 100 * payload["received"] / payload["total"]
                self.progress.configure(value=percent)
                self._show_status(f"Receiving {payload['name']}… {percent:.0f}%")
        elif event == "transfer_complete":
            self.receive_button.configure(state="normal", text="↓   Receive & play")
            self._play_file(Path(payload["path"]))
        elif event == "error":
            self.receive_button.configure(state="normal", text="↓   Receive & play")
            self._show_status(payload["message"], is_error=True)

    def _play_file(self, path: Path) -> None:
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
        self.is_paused = False
        self.play_button.configure(state="normal", text="Ⅱ  Pause")
        self.now_playing.configure(text=f"Now playing  ·  {path.name}")
        self.time_label.configure(text="Playing from your received library")
        self._show_status(f"Received and playing {path.name}.")

    def _toggle_playback(self) -> None:
        if not self.current_path:
            return
        try:
            if self.is_paused:
                pygame.mixer.music.unpause()
                self.play_button.configure(text="Ⅱ  Pause")
            else:
                pygame.mixer.music.pause()
                self.play_button.configure(text="▶  Play")
            self.is_paused = not self.is_paused
        except pygame.error as error:
            self._show_status(f"Playback control failed: {error}", is_error=True)

    def _poll_playback(self) -> None:
        if self.current_path and not self.is_paused:
            try:
                if not pygame.mixer.music.get_busy():
                    self.play_button.configure(text="▶  Play")
                    self.is_paused = True
            except pygame.error as error:
                self._show_status(f"Audio output error: {error}", is_error=True)
        self.root.after(500, self._poll_playback)

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Open the Audionator MP3 receiving client.")
    parser.add_argument(
        "--downloads",
        type=Path,
        default=Path(__file__).resolve().parent / "received",
        help="Folder where received MP3 files are saved",
    )
    args = parser.parse_args()
    root = tk.Tk()
    try:
        AudionatorClient(root, args.downloads)
        root.mainloop()
    except tk.TclError as error:
        messagebox.showerror("Audionator", f"Could not start the client window: {error}")
        raise


if __name__ == "__main__":
    main()
