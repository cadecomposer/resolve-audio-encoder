#!/usr/bin/env python3
"""
Resolve Audio Encoder
=====================

A small GUI helper for DaVinci Resolve on Linux, which cannot decode AAC audio
(and a few other formats).  It remuxes your videos into an MKV container,
copying the original video stream untouched and transcoding every audio track
into a Resolve-friendly codec (FLAC by default, or OPUS / MP3).

Output files are written next to the source file as "<name> [CODEC].mkv".

Optionally the finished MKV can be remuxed (stream copy, no re-encode) into
"<name> [CODEC].mp4"; the intermediate MKV is then deleted.  MP4 cannot hold
subtitles or attachments, so those are dropped in that second pass.

Requires: ffmpeg / ffprobe on PATH.
Optional: tkinterdnd2 (drag-and-drop), kdialog or zenity (native file dialog).
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# Optional drag & drop support
# ---------------------------------------------------------------------------
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
    _BaseTk = TkinterDnD.Tk
except Exception:  # pragma: no cover - depends on environment
    DND_AVAILABLE = False
    _BaseTk = tk.Tk

APP_NAME = "Resolve Audio Encoder"
APP_VERSION = "1.0.0"

# Supported input extensions (used for the file picker and drag & drop filter).
VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".3gp", ".ogv", ".vob",
}

CODECS = ["FLAC", "OPUS", "MP3"]

BITRATE_OPTIONS = {
    "FLAC": [],
    "OPUS": ["64k", "96k", "128k", "160k", "192k", "256k", "320k", "510k"],
    "MP3": ["128k", "160k", "192k", "256k", "320k"],
}
DEFAULT_BITRATES = {"FLAC": "", "OPUS": "128k", "MP3": "192k"}

# ---------------------------------------------------------------------------
# DaVinci Resolve inspired dark palette
# ---------------------------------------------------------------------------
BG        = "#1b1b1b"   # window background
PANEL     = "#232323"   # panels / toolbars
PANEL_ALT = "#2b2b2b"   # alternate rows
FIELD     = "#2f2f2f"   # entry / combobox fields
BORDER    = "#3a3a3a"
TEXT      = "#d4d4d4"
TEXT_DIM  = "#8b8b8b"
ACCENT    = "#e8830c"   # Resolve orange
ACCENT_HI = "#ffa033"
ACCENT_LO = "#b8650a"
ON_ACCENT = "#1b1b1b"
OK_GREEN  = "#7ec46b"
ERR_RED   = "#e05a5a"

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "resolve-audio-encoder"
CONFIG_FILE = CONFIG_DIR / "config.json"


# ===========================================================================
# Engine (no Tk dependencies here, so it can be tested / reused headlessly)
# ===========================================================================
def tool_path(name: str) -> Optional[str]:
    return shutil.which(name)


def unique_output_path(source: Path, codec: str, out_dir: Optional[Path] = None) -> Path:
    """Return a non-existing path like ``<dir>/<stem> [FLAC].mkv``."""
    directory = out_dir if out_dir is not None else source.parent
    stem = source.stem
    tag = f" [{codec.upper()}]"
    candidate = directory / f"{stem}{tag}.mkv"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}{tag} ({counter}).mkv"
        counter += 1
    return candidate


def probe_media(path: Path, ffprobe: str = "ffprobe") -> tuple[float, int]:
    """Return (duration_seconds, audio_stream_count)."""
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries", "stream=codec_type",
        "-of", "json", str(path),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
        data = json.loads(out)
    except Exception:
        return 0.0, 0
    try:
        duration = float(data.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    audio = sum(1 for s in data.get("streams", []) if s.get("codec_type") == "audio")
    return duration, audio


def parse_time_to_seconds(value: str) -> float:
    """Parse an ffmpeg ``HH:MM:SS.micro`` timestamp."""
    try:
        h, m, s = value.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return 0.0


def normalize_bitrate(value: str) -> str:
    value = (value or "").strip().lower().replace(" ", "")
    if not value:
        return ""
    if value[-1] not in ("k", "m"):
        value += "k"
    return value


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def build_ffmpeg_cmd(ffmpeg: str, source: Path, codec: str, bitrate: str,
                     output: Path) -> list[str]:
    """Remux ``source`` to ``output``, copying video and re-encoding audio."""
    codec = codec.upper()
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-i", str(source),
        # Video, all audio tracks, subtitles and attachments are kept.
        "-map", "0:v?", "-map", "0:a?", "-map", "0:s?", "-map", "0:t?",
        "-c:v", "copy",
    ]
    if codec == "FLAC":
        cmd += ["-c:a", "flac", "-compression_level", "8"]
    elif codec == "OPUS":
        cmd += ["-c:a", "libopus", "-b:a", bitrate or "128k"]
    else:  # MP3
        cmd += ["-c:a", "libmp3lame", "-b:a", bitrate or "192k"]

    cmd += [
        "-c:s", "copy",
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-max_muxing_queue_size", "4096",
        "-progress", "pipe:1", "-nostats",
        "-loglevel", "error",
        str(output),
    ]
    return cmd


def unique_remux_path(mkv_path: Path) -> Path:
    """Return a non-existing ``.mp4`` path next to ``mkv_path``."""
    candidate = mkv_path.with_suffix(".mp4")
    counter = 2
    while candidate.exists():
        candidate = mkv_path.with_name(f"{mkv_path.stem} ({counter}).mp4")
        counter += 1
    return candidate


def build_remux_cmd(ffmpeg: str, source: Path, output: Path) -> list[str]:
    """Build a command that copies ``source`` into an MP4 container.

    Only video and audio are mapped: MP4 cannot carry the subtitle and
    attachment streams an MKV may hold, and ``-map`` entries with ``?`` keep
    the command valid for files that lack a video track.
    """
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-i", str(source),
        "-map", "0:v?", "-map", "0:a?",
        "-c", "copy",
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-max_muxing_queue_size", "4096",
        "-progress", "pipe:1", "-nostats",
        "-loglevel", "error",
        str(output),
    ]


def run_transcode(
    ffmpeg: str,
    source: Path,
    codec: str,
    bitrate: str,
    output: Path,
    duration: float = 0.0,
    on_progress: Optional[Callable[[float], None]] = None,
    stop_event: Optional[threading.Event] = None,
    on_proc: Optional[Callable[[Optional[subprocess.Popen]], None]] = None,
) -> tuple[bool, str]:
    """Run the transcoding subprocess. Returns ``(success, error_message)``."""
    cmd = build_ffmpeg_cmd(ffmpeg, source, codec, bitrate, output)
    return _run_ffmpeg(cmd, output, duration, on_progress, stop_event, on_proc)


def run_remux(
    ffmpeg: str,
    source: Path,
    output: Path,
    duration: float = 0.0,
    on_progress: Optional[Callable[[float], None]] = None,
    stop_event: Optional[threading.Event] = None,
    on_proc: Optional[Callable[[Optional[subprocess.Popen]], None]] = None,
) -> tuple[bool, str]:
    """Copy ``source`` into ``output`` (MP4) without re-encoding."""
    cmd = build_remux_cmd(ffmpeg, source, output)
    return _run_ffmpeg(cmd, output, duration, on_progress, stop_event, on_proc)


def _run_ffmpeg(
    cmd: list[str],
    output: Path,
    duration: float = 0.0,
    on_progress: Optional[Callable[[float], None]] = None,
    stop_event: Optional[threading.Event] = None,
    on_proc: Optional[Callable[[Optional[subprocess.Popen]], None]] = None,
) -> tuple[bool, str]:
    """Run an ``ffmpeg`` command, tracking progress. Returns ``(ok, error)``."""
    tail: list[str] = []

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
    except Exception as exc:
        return False, str(exc)

    if on_proc:
        on_proc(proc)

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if stop_event is not None and stop_event.is_set():
                try:
                    proc.terminate()
                except Exception:
                    pass
                break
            line = line.strip()
            if not line:
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                if key in ("out_time_us", "out_time_ms"):
                    try:
                        us = float(value)
                    except ValueError:
                        continue
                    # ffmpeg's "out_time_ms" is actually microseconds.
                    secs = us / 1_000_000.0
                    if duration > 0 and on_progress:
                        pct = max(0.0, min(99.0, secs / duration * 100.0))
                        on_progress(pct)
                    continue
                if key == "out_time":
                    # Fallback for builds that only emit HH:MM:SS.
                    secs = parse_time_to_seconds(value)
                    if duration > 0 and on_progress and secs > 0:
                        pct = max(0.0, min(99.0, secs / duration * 100.0))
                        on_progress(pct)
                    continue
                if key in ("frame", "fps", "stream_0_0_q",
                           "bitrate", "total_size", "dup_frames",
                           "drop_frames", "speed", "progress"):
                    continue
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
    finally:
        proc.wait()
        if on_proc:
            on_proc(None)

    cancelled = stop_event is not None and stop_event.is_set()
    if cancelled:
        _safe_unlink(output)
        return False, "Cancelled"
    if proc.returncode != 0:
        _safe_unlink(output)
        message = "\n".join(tail[-6:]) if tail else f"ffmpeg exited with code {proc.returncode}"
        return False, message
    if not output.exists():
        return False, "ffmpeg finished but no output file was produced"
    return True, ""


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


# ===========================================================================
# Native file dialog
# ===========================================================================
class NativeFileDialog:
    """Use the desktop's own file chooser when possible."""

    @staticmethod
    def _is_kde() -> bool:
        desktop = (os.environ.get("XDG_CURRENT_DESKTOP", "")
                   + os.environ.get("DESKTOP_SESSION", "")).upper()
        return "KDE" in desktop or "PLASMA" in desktop

    @staticmethod
    def _kdialog_filter() -> str:
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        return f"Video Files ({exts})|All Files (*)"

    @staticmethod
    def ask_open_files(parent: tk.Misc) -> list[str]:
        kdialog = tool_path("kdialog")
        zenity = tool_path("zenity")

        # Prefer the platform-native chooser.
        if kdialog and NativeFileDialog._is_kde():
            files = NativeFileDialog._run_kdialog(kdialog)
            if files is not None:
                return files
        if zenity:
            files = NativeFileDialog._run_zenity(zenity)
            if files is not None:
                return files
        if kdialog:
            files = NativeFileDialog._run_kdialog(kdialog)
            if files is not None:
                return files

        # Last resort: Tk's own dialog.
        selected = filedialog.askopenfilenames(
            parent=parent,
            title="Select video files",
            filetypes=[("Video files", " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))),
                       ("All files", "*.*")],
        )
        return list(selected)

    @staticmethod
    def _run_kdialog(kdialog: str) -> Optional[list[str]]:
        cmd = [kdialog, "--multiple", "--separate-output",
               "--title", "Select video files",
               "--getopenfilename", str(Path.home()),
               NativeFileDialog._kdialog_filter()]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except Exception:
            return None
        if result.returncode != 0:
            return []
        files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return [f for f in files if f]

    @staticmethod
    def _run_zenity(zenity: str) -> Optional[list[str]]:
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        cmd = [
            zenity, "--file-selection", "--multiple", "--separator=\n",
            "--title=Select video files",
            f"--file-filter=Video files | {exts}",
            "--file-filter=All files | *",
            f"--filename={Path.home()}/",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except Exception:
            return None
        if result.returncode != 0:
            return []
        files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return [f for f in files if f]


def uri_to_path(value: str) -> str:
    """kdialog/zenity may return ``file://`` URIs depending on version."""
    if value.startswith("file://"):
        from urllib.parse import unquote, urlparse
        return unquote(urlparse(value).path)
    return value


# ===========================================================================
# Job model
# ===========================================================================
@dataclass
class Job:
    id: int
    path: Path
    status: str = "Queued"          # Queued / Processing / Done / Error / Cancelled
    progress: float = 0.0
    duration: float = 0.0
    audio_tracks: int = 0
    output: Optional[Path] = None
    error: str = ""
    row: str = field(default="", repr=False)


# ===========================================================================
# Main application
# ===========================================================================
class ResolveAudioEncoder(_BaseTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("900x620")
        self.minsize(760, 520)
        self.configure(bg=BG)

        self.jobs: list[Job] = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._msg: "queue.Queue[tuple]" = queue.Queue()
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._current_proc: Optional[subprocess.Popen] = None
        self._running = False

        # Settings (loaded from disk below).
        self.var_codec = tk.StringVar(value="FLAC")
        self.var_bitrate = tk.StringVar(value=DEFAULT_BITRATES["FLAC"])
        self.var_auto_start = tk.BooleanVar(value=False)
        self.var_remux_mp4 = tk.BooleanVar(value=False)
        self.var_status = tk.StringVar(value="Ready")
        self.var_counts = tk.StringVar(value="")
        self.bitrates = dict(DEFAULT_BITRATES)

        self.ffmpeg = tool_path("ffmpeg")
        self.ffprobe = tool_path("ffprobe")

        self._build_style()
        self._build_ui()
        self._load_config()
        self._apply_codec_ui()
        self._setup_dnd()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_messages)

    # -- styling ------------------------------------------------------------
    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=BG, foreground=TEXT,
                        fieldbackground=FIELD, bordercolor=BORDER,
                        focuscolor=ACCENT, font=("Noto Sans", 10))

        for name, bg in (("TFrame", BG), ("Panel.TFrame", PANEL),
                         ("PanelAlt.TFrame", PANEL_ALT)):
            style.configure(name, background=bg)

        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Dim.TLabel", background=BG, foreground=TEXT_DIM)
        style.configure("PanelDim.TLabel", background=PANEL, foreground=TEXT_DIM)
        style.configure("Title.TLabel", background=BG, foreground=TEXT,
                        font=("Noto Sans", 13, "bold"))
        style.configure("Status.TLabel", background=PANEL, foreground=TEXT_DIM)

        # Buttons
        style.configure("TButton", background=FIELD, foreground=TEXT,
                        bordercolor=BORDER, focuscolor=FIELD,
                        relief="flat", padding=(14, 7), font=("Noto Sans", 10))
        style.map("TButton",
                  background=[("pressed", ACCENT_LO), ("active", PANEL_ALT),
                              ("disabled", PANEL)],
                  foreground=[("disabled", TEXT_DIM)])

        style.configure("Accent.TButton", background=ACCENT, foreground=ON_ACCENT,
                        bordercolor=ACCENT, focuscolor=ACCENT,
                        relief="flat", padding=(18, 7),
                        font=("Noto Sans", 10, "bold"))
        style.map("Accent.TButton",
                  background=[("pressed", ACCENT_LO), ("active", ACCENT_HI),
                              ("disabled", PANEL)],
                  foreground=[("disabled", TEXT_DIM)])

        # Checkbutton
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT,
                        focuscolor=PANEL, indicatorcolor=FIELD,
                        bordercolor=BORDER)
        style.map("TCheckbutton",
                  background=[("active", PANEL)],
                  indicatorcolor=[("selected", ACCENT), ("active", PANEL_ALT)],
                  foreground=[("disabled", TEXT_DIM)])

        # Entry / Combobox
        style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT,
                        bordercolor=BORDER, insertcolor=TEXT, padding=6)
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD,
                        foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER,
                        lightcolor=FIELD, darkcolor=FIELD, padding=5)
        style.map("TCombobox",
                  fieldbackground=[("readonly", FIELD), ("disabled", PANEL)],
                  foreground=[("disabled", TEXT_DIM)],
                  arrowcolor=[("disabled", TEXT_DIM)])
        # Drop-down list (a Tk listbox under the hood).
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", ON_ACCENT)
        self.option_add("*TCombobox*Listbox.font", ("Noto Sans", 10))

        # Treeview
        style.configure("Treeview",
                        background=PANEL, fieldbackground=PANEL,
                        foreground=TEXT, bordercolor=BORDER,
                        rowheight=28, font=("Noto Sans", 10))
        style.map("Treeview",
                  background=[("selected", ACCENT_LO)],
                  foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading",
                        background=PANEL_ALT, foreground=TEXT_DIM,
                        relief="flat", bordercolor=BORDER,
                        font=("Noto Sans", 9, "bold"), padding=6)
        style.map("Treeview.Heading", background=[("active", PANEL_ALT)])

        # Progressbar
        style.configure("Horizontal.TProgressbar",
                        troughcolor=PANEL_ALT, background=ACCENT,
                        bordercolor=PANEL_ALT, lightcolor=ACCENT,
                        darkcolor=ACCENT, thickness=14)

    # -- ui -----------------------------------------------------------------
    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # Header ------------------------------------------------------------
        header = ttk.Frame(self, style="TFrame", padding=(18, 14, 18, 6))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Resolve Audio Encoder", style="Title.TLabel").grid(
            row=0, column=0, sticky="w")
        subtitle = ("Remux video untouched, transcode audio for DaVinci Resolve "
                    "(Linux / AAC workaround)")
        ttk.Label(header, text=subtitle, style="Dim.TLabel").grid(
            row=1, column=0, sticky="w", pady=(2, 0))

        # Settings toolbar --------------------------------------------------
        bar = ttk.Frame(self, style="Panel.TFrame", padding=(18, 12))
        bar.grid(row=1, column=0, sticky="ew", padx=18, pady=(6, 0))
        bar.columnconfigure(9, weight=1)

        ttk.Label(bar, text="Audio codec", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8))
        self.cmb_codec = ttk.Combobox(
            bar, textvariable=self.var_codec, values=CODECS,
            state="readonly", width=8)
        self.cmb_codec.grid(row=0, column=1, sticky="w")
        self.cmb_codec.bind("<<ComboboxSelected>>", self._on_codec_change)

        ttk.Label(bar, text="Bitrate", style="Panel.TLabel").grid(
            row=0, column=2, sticky="w", padx=(20, 8))
        self.cmb_bitrate = ttk.Combobox(
            bar, textvariable=self.var_bitrate, width=10)
        self.cmb_bitrate.grid(row=0, column=3, sticky="w")
        self.cmb_bitrate.bind("<<ComboboxSelected>>", lambda e: self._on_setting_change())
        self.cmb_bitrate.bind("<FocusOut>", lambda e: self._on_setting_change())

        self.chk_auto = ttk.Checkbutton(
            bar, text="Auto-start when files are added",
            variable=self.var_auto_start, command=self._on_setting_change,
            style="TCheckbutton")
        self.chk_auto.grid(row=1, column=0, columnspan=2, sticky="w",
                          pady=(8, 0))

        self.chk_remux = ttk.Checkbutton(
            bar, text="Also remux to MP4 (delete the MKV afterwards)",
            variable=self.var_remux_mp4, command=self._on_setting_change,
            style="TCheckbutton")
        self.chk_remux.grid(row=1, column=2, columnspan=6, sticky="w",
                            padx=(20, 0), pady=(8, 0))

        self.btn_browse = ttk.Button(
            bar, text="Add Files…", command=self._choose_files)
        self.btn_browse.grid(row=0, column=10, sticky="e")

        # Queue -------------------------------------------------------------
        queue_frame = ttk.Frame(self, style="TFrame", padding=(18, 12, 18, 0))
        queue_frame.grid(row=2, column=0, sticky="nsew")
        queue_frame.columnconfigure(0, weight=1)
        queue_frame.rowconfigure(1, weight=1)

        listing = ttk.Frame(queue_frame, style="TFrame")
        listing.grid(row=0, column=0, sticky="ew")
        listing.columnconfigure(0, weight=1)
        ttk.Label(listing, text="Queue", style="Title.TLabel",
                  font=("Noto Sans", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.lbl_counts = ttk.Label(listing, textvariable=self.var_counts,
                                    style="Dim.TLabel")
        self.lbl_counts.grid(row=0, column=1, sticky="e")

        columns = ("name", "tracks", "duration", "status", "progress")
        self.tree = ttk.Treeview(queue_frame, columns=columns, show="headings",
                                 selectmode="extended")
        self.tree.heading("name", text="File")
        self.tree.heading("tracks", text="Audio")
        self.tree.heading("duration", text="Length")
        self.tree.heading("status", text="Status")
        self.tree.heading("progress", text="Progress")
        self.tree.column("name", width=420, anchor="w")
        self.tree.column("tracks", width=60, anchor="center", stretch=False)
        self.tree.column("duration", width=80, anchor="center", stretch=False)
        self.tree.column("status", width=130, anchor="w", stretch=False)
        self.tree.column("progress", width=90, anchor="center", stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.tree.tag_configure("done", foreground=OK_GREEN)
        self.tree.tag_configure("error", foreground=ERR_RED)
        self.tree.tag_configure("processing", foreground=ACCENT_HI)

        scroll = ttk.Scrollbar(queue_frame, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=1, sticky="ns", pady=(8, 0))

        # Drop zone / hint
        if DND_AVAILABLE:
            hint_text = "Drag & drop video files anywhere in this window"
        else:
            hint_text = ("Drag & drop unavailable (install tkinterdnd2) — "
                         "use “Add Files…”")
        self.drop_hint = ttk.Label(
            queue_frame, text=hint_text, style="Dim.TLabel",
            anchor="center", font=("Noto Sans", 11))
        self.drop_hint.grid(row=2, column=0, columnspan=2, pady=(10, 0))

        # Bottom controls ---------------------------------------------------
        bottom = ttk.Frame(self, style="Panel.TFrame", padding=(18, 12))
        bottom.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 18))
        bottom.columnconfigure(4, weight=1)

        self.btn_start = ttk.Button(bottom, text="Start", style="Accent.TButton",
                                    command=self._start_queue)
        self.btn_start.grid(row=0, column=0, padx=(0, 8))
        self.btn_stop = ttk.Button(bottom, text="Stop", command=self._stop_queue)
        self.btn_stop.grid(row=0, column=1, padx=(0, 8))
        self.btn_stop.state(["disabled"])
        ttk.Button(bottom, text="Remove", command=self._remove_selected).grid(
            row=0, column=2, padx=(0, 8))
        ttk.Button(bottom, text="Clear", command=self._clear_queue).grid(
            row=0, column=3, padx=(0, 8))

        self.progress = ttk.Progressbar(bottom, orient="horizontal",
                                        mode="determinate", maximum=100)
        self.progress.grid(row=0, column=4, sticky="ew", padx=(20, 12))

        ttk.Label(bottom, textvariable=self.var_status, style="Status.TLabel",
                  width=30, anchor="e").grid(row=0, column=7, sticky="e")

    # -- drag & drop --------------------------------------------------------
    def _setup_dnd(self) -> None:
        if not DND_AVAILABLE:
            return
        self._dnd_widgets: list[tk.Misc] = []
        # Register the toplevel plus the interactive widgets so a drop
        # anywhere in the window is accepted.
        targets = [self, self.tree, self.drop_hint]
        for widget in targets:
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
                widget.dnd_bind("<<DropEnter>>", self._on_drag_enter)
                widget.dnd_bind("<<DropLeave>>", self._on_drag_leave)
                self._dnd_widgets.append(widget)
            except Exception as exc:  # pragma: no cover
                print(f"[dnd] could not register {widget}: {exc}", file=sys.stderr)
        if not self._dnd_widgets:
            self.drop_hint.configure(
                text="Drag & drop could not start — use “Add Files…”")

    def _on_drag_enter(self, _event=None) -> None:
        self.drop_hint.configure(foreground=ACCENT_HI,
                                 text="Drop to add files")

    def _on_drag_leave(self, _event=None) -> None:
        self.drop_hint.configure(foreground=TEXT_DIM, text=self._hint_text())

    def _hint_text(self) -> str:
        return ("Drag & drop video files anywhere in this window"
                if DND_AVAILABLE else "Use “Add Files…”")

    def _parse_drop_data(self, raw) -> list[str]:
        """Parse the many shapes of XDND file-drop payloads.

        Depending on the desktop and source application this may be a list of
        brace-wrapped paths (``{/a b/c.mp4}``), a ``text/uri-list`` with one
        ``file://`` URI per line, or a bare path.  Handle all of them.
        """
        raw = str(raw or "").strip()
        if not raw:
            return []

        if "file://" in raw:
            return [line.strip() for line in raw.splitlines() if line.strip()]

        try:
            parts = list(self.tk.splitlist(raw))
        except Exception:
            parts = raw.split()

        # If nothing parsed as a real file, the path may contain unquoted
        # spaces; fall back to treating the whole payload as one path.
        if not any(Path(uri_to_path(p)).expanduser().is_file() for p in parts):
            if Path(uri_to_path(raw)).expanduser().is_file():
                return [raw]
        return parts

    def _on_drop(self, event) -> None:
        self._on_drag_leave()
        self.add_paths(self._parse_drop_data(getattr(event, "data", "")))

    # -- config -------------------------------------------------------------
    def _load_config(self) -> None:
        cfg = {}
        try:
            if CONFIG_FILE.exists():
                cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}

        codec = str(cfg.get("codec", "FLAC")).upper()
        if codec not in CODECS:
            codec = "FLAC"
        persisted = cfg.get("bitrates", {})
        for key, default in DEFAULT_BITRATES.items():
            value = persisted.get(key) if isinstance(persisted, dict) else None
            self.bitrates[key] = value if isinstance(value, str) and value else default

        self.var_codec.set(codec)
        self.var_auto_start.set(bool(cfg.get("auto_start", False)))
        self.var_remux_mp4.set(bool(cfg.get("remux_mp4", False)))

    def _save_config(self) -> None:
        codec = self.var_codec.get().upper()
        if codec in self.bitrates and codec != "FLAC":
            value = normalize_bitrate(self.var_bitrate.get())
            if value:
                self.bitrates[codec] = value
        data = {
            "codec": self.var_codec.get().upper(),
            "bitrates": self.bitrates,
            "auto_start": bool(self.var_auto_start.get()),
            "remux_mp4": bool(self.var_remux_mp4.get()),
        }
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    # -- settings behaviour -------------------------------------------------
    def _on_setting_change(self) -> None:
        codec = self.var_codec.get().upper()
        if codec != "FLAC":
            value = normalize_bitrate(self.var_bitrate.get())
            if value:
                self.bitrates[codec] = value
        self._save_config()

    def _on_codec_change(self, _event=None) -> None:
        self._save_config()
        self._apply_codec_ui()

    def _apply_codec_ui(self) -> None:
        codec = self.var_codec.get().upper()
        if codec == "FLAC":
            self.cmb_bitrate.configure(values=[], state="disabled")
            self.var_bitrate.set("")
        else:
            self.cmb_bitrate.configure(values=BITRATE_OPTIONS.get(codec, []),
                                       state="normal")
            self.var_bitrate.set(self.bitrates.get(codec, DEFAULT_BITRATES[codec]))

    # -- queue --------------------------------------------------------------
    def _choose_files(self) -> None:
        files = NativeFileDialog.ask_open_files(self)
        if files:
            self.add_paths(files)

    def add_paths(self, paths) -> None:
        added_jobs: list[Job] = []
        for raw in paths:
            path = uri_to_path(str(raw))
            p = Path(path).expanduser()
            try:
                if not p.is_file() or p.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
            except OSError:
                continue
            with self._lock:
                if any(j.path == p for j in self.jobs):
                    continue
                job = Job(id=self._next_id, path=p)
                self._next_id += 1
                self.jobs.append(job)
            self._insert_row(job)
            added_jobs.append(job)

        if added_jobs:
            self._refresh_counts()
            self._probe_async(added_jobs)
            self._set_status(f"Added {len(added_jobs)} "
                             f"file{'s' if len(added_jobs) != 1 else ''}")
            if self.var_auto_start.get() and not self._running:
                self._start_queue()
        else:
            self._set_status("No new video files added")

    def _insert_row(self, job: Job) -> None:
        job.row = self.tree.insert(
            "", "end",
            values=(job.path.name, "…", "", job.status, "0%"))
        self.tree.see(job.row)

    def _probe_async(self, jobs: list[Job]) -> None:
        def work(items: list[Job]) -> None:
            for job in items:
                duration, tracks = probe_media(job.path, self.ffprobe or "ffprobe")
                job.duration = duration
                job.audio_tracks = tracks
                self._msg.put(("probe", job.id, duration, tracks))
        threading.Thread(target=work, args=(list(jobs),), daemon=True).start()

    def _remove_selected(self) -> None:
        if self._running:
            removable = [i for i in self.tree.selection()
                         if (self._job_by_row(i) and
                             self._job_by_row(i).status in ("Done", "Error", "Cancelled"))]
            if not removable:
                messagebox.showinfo(APP_NAME,
                                    "Stop the queue before removing pending jobs.",
                                    parent=self)
                return
        else:
            removable = list(self.tree.selection())
        for row in removable:
            job = self._job_by_row(row)
            if job:
                with self._lock:
                    if job in self.jobs:
                        self.jobs.remove(job)
            self.tree.delete(row)
        self._refresh_counts()

    def _clear_queue(self) -> None:
        if self._running:
            messagebox.showinfo(APP_NAME,
                                "Stop the queue before clearing it.", parent=self)
            return
        with self._lock:
            self.jobs.clear()
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.progress["value"] = 0
        self._refresh_counts()
        self._set_status("Queue cleared")

    def _job_by_row(self, row: str) -> Optional[Job]:
        with self._lock:
            for job in self.jobs:
                if job.row == row:
                    return job
        return None

    def _refresh_counts(self) -> None:
        with self._lock:
            total = len(self.jobs)
            done = sum(1 for j in self.jobs if j.status == "Done")
            errors = sum(1 for j in self.jobs if j.status == "Error")
        parts = [f"{total} file{'s' if total != 1 else ''}"]
        if done:
            parts.append(f"{done} done")
        if errors:
            parts.append(f"{errors} failed")
        self.var_counts.set("   ·   ".join(parts))

    def _set_status(self, text: str) -> None:
        self.var_status.set(text)

    # -- processing ---------------------------------------------------------
    def _start_queue(self) -> None:
        if self._running:
            return
        if not self.ffmpeg or not self.ffprobe:
            messagebox.showerror(
                APP_NAME,
                "ffmpeg and ffprobe are required but were not found on PATH.\n\n"
                "Please install ffmpeg (e.g. `sudo pacman -S ffmpeg`).",
                parent=self)
            return
        with self._lock:
            pending = [j for j in self.jobs
                       if j.status in ("Queued", "Error", "Cancelled")]
        if not pending:
            self._set_status("Nothing to process")
            return

        self._save_config()
        self._stop_event.clear()
        self._running = True
        self.btn_start.state(["disabled"])
        self.btn_browse.state(["disabled"])
        self.btn_stop.state(["!disabled"])
        self.progress["value"] = 0
        for job in pending:
            job.status = "Queued"
            job.progress = 0.0
            job.error = ""
            self._update_row(job)
        self._refresh_counts()

        self._worker = threading.Thread(target=self._worker_loop,
                                        args=(pending, bool(self.var_remux_mp4.get())),
                                        daemon=True)
        self._worker.start()

    def _stop_queue(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        self._set_status("Stopping…")
        proc = self._current_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _worker_loop(self, pending: list[Job], remux_mp4: bool = False) -> None:
        codec = self.var_codec.get().upper()
        bitrate = normalize_bitrate(self.var_bitrate.get())
        total = len(pending)
        completed = 0

        for job in pending:
            if self._stop_event.is_set():
                job.status = "Cancelled"
                self._msg.put(("update", job.id, job.status, job.progress, job.error))
                continue
            self._msg.put(("processing", job.id))
            ok = self._transcode(job, codec, bitrate, remux_mp4)
            completed += 1
            if not ok and self._stop_event.is_set():
                job.status = "Cancelled"
            elif ok:
                job.status = "Done"
                job.progress = 100.0
            else:
                job.status = "Error"
            self._msg.put(("update", job.id, job.status, job.progress, job.error))
            self._msg.put(("overall", completed / total * 100.0))

        self._msg.put(("finished",))

    def _transcode(self, job: Job, codec: str, bitrate: str,
                   remux_mp4: bool = False) -> bool:
        output = unique_output_path(job.path, codec)
        job.output = output

        def on_progress(pct: float) -> None:
            job.progress = pct
            self._msg.put(("progress", job.id, pct))

        def on_proc(proc) -> None:
            self._current_proc = proc

        ok, error = run_transcode(
            self.ffmpeg, job.path, codec, bitrate, output,
            duration=job.duration, on_progress=on_progress,
            stop_event=self._stop_event, on_proc=on_proc)
        if not ok:
            if error:
                job.error = error
            return False
        if not remux_mp4:
            return True

        # Second pass: wrap the result in an MP4 container, then drop the
        # intermediate MKV.  The MKV is kept if the remux fails.
        mp4 = unique_remux_path(output)
        job.progress = 0.0
        self._msg.put(("status", job.id, "Remuxing to MP4"))
        ok, error = run_remux(
            self.ffmpeg, output, mp4,
            duration=job.duration, on_progress=on_progress,
            stop_event=self._stop_event, on_proc=on_proc)
        if not ok:
            detail = error if error else "unknown error"
            job.error = f"MP4 remux failed (MKV kept): {detail}"
            return False
        _safe_unlink(output)
        job.output = mp4
        return True

    # -- message pump -------------------------------------------------------
    def _poll_messages(self) -> None:
        try:
            while True:
                msg = self._msg.get_nowait()
                kind = msg[0]
                if kind == "probe":
                    _, job_id, duration, tracks = msg
                    job = self._job_by_id(job_id)
                    if job:
                        job.duration = duration
                        job.audio_tracks = tracks
                        self._update_row(job)
                elif kind == "processing":
                    job = self._job_by_id(msg[1])
                    if job:
                        job.status = "Processing"
                        job.progress = 0.0
                        self._update_row(job)
                        self._set_status(f"Processing {job.path.name}")
                elif kind == "status":
                    _, job_id, text = msg
                    job = self._job_by_id(job_id)
                    if job:
                        job.status = text
                        self._update_row(job)
                        self._set_status(f"{text} · {job.path.name}")
                elif kind == "progress":
                    _, job_id, pct = msg
                    job = self._job_by_id(job_id)
                    if job:
                        job.progress = pct
                        self._update_row(job, progress_only=True)
                elif kind == "update":
                    _, job_id, status, pct, error = msg
                    job = self._job_by_id(job_id)
                    if job:
                        job.status = status
                        job.progress = pct
                        job.error = error
                        self._update_row(job)
                        self._refresh_counts()
                elif kind == "overall":
                    self.progress["value"] = msg[1]
                elif kind == "finished":
                    self._on_worker_finished()
        except queue.Empty:
            pass
        self.after(100, self._poll_messages)

    def _job_by_id(self, job_id: int) -> Optional[Job]:
        with self._lock:
            for job in self.jobs:
                if job.id == job_id:
                    return job
        return None

    def _update_row(self, job: Job, progress_only: bool = False) -> None:
        if not job.row or not self.tree.exists(job.row):
            return
        if progress_only:
            self.tree.set(job.row, "progress", f"{job.progress:.0f}%")
            self.tree.set(job.row, "status", job.status)
            return
        tags = ()
        if job.status == "Done":
            tags = ("done",)
        elif job.status == "Error":
            tags = ("error",)
        elif job.status not in ("Queued", "Cancelled"):
            tags = ("processing",)
        self.tree.item(
            job.row, tags=tags,
            values=(
                job.path.name,
                str(job.audio_tracks) if job.audio_tracks else "…",
                format_duration(job.duration),
                job.status,
                f"{job.progress:.0f}%",
            ))

    def _on_worker_finished(self) -> None:
        self._running = False
        self.btn_start.state(["!disabled"])
        self.btn_browse.state(["!disabled"])
        self.btn_stop.state(["disabled"])
        with self._lock:
            jobs = list(self.jobs)
        done = sum(1 for j in jobs if j.status == "Done")
        errors = sum(1 for j in jobs if j.status == "Error")
        cancelled = sum(1 for j in jobs if j.status == "Cancelled")
        if errors:
            self._set_status(f"Finished · {done} done · {errors} failed")
        elif cancelled:
            self._set_status(f"Stopped · {done} done · {cancelled} cancelled")
        else:
            self.progress["value"] = 100
            self._set_status(f"Finished · {done} file{'s' if done != 1 else ''}")
        self._refresh_counts()

    # -- lifecycle ----------------------------------------------------------
    def _on_close(self) -> None:
        if self._running:
            if not messagebox.askyesno(
                    APP_NAME, "Transcoding is still running. Quit anyway?",
                    parent=self):
                return
            self._stop_queue()
        self._save_config()
        self.destroy()


def main() -> int:
    app = ResolveAudioEncoder()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
