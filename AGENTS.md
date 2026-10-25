# AGENTS.md

Guidance for AI coding agents working on **Resolve Audio Encoder**.

## What this project is

A single-file Python/Tkinter GUI for Linux that works around DaVinci Resolve's
inability to decode AAC (and some other codecs). It remuxes videos into MKV,
**copying the video stream untouched** and transcoding every audio track into
FLAC (default), OPUS, or MP3.

There is exactly one runtime dependency: `tkinterdnd2` (drag & drop). Everything
else is the Python standard library plus the external `ffmpeg` / `ffprobe`
binaries.

## Repository layout

```
resolve_audio_encoder.py   # THE ENTIRE APP (engine + GUI, ~1000 lines)
run.sh                     # creates .venv, installs deps, launches the app
requirements.txt           # tkinterdnd2
README.md                  # user-facing docs
.gitignore                 # ignores .venv/, __pycache__/
*.mp4                      # test media (do not commit large changes / delete)
```

The `.venv/` directory is created by `run.sh` and is gitignored.

## Commands

```bash
# Run the app (creates/reuses .venv). This is the ONLY supported launch path.
./run.sh

# Compile check
.venv/bin/python -m py_compile resolve_audio_encoder.py

# Lint (install first: .venv/bin/pip install pyflakes)
.venv/bin/python -m pyflakes resolve_audio_encoder.py

# Probe a file
ffprobe -v error -show_entries stream=index,codec_type,codec_name,bitrate -of default=nw=1 file.mp4
```

## Architecture

The file is deliberately split into a **headless engine** (module-level
functions and classes) and a **GUI** class on top. Keep it that way.

### Engine — no Tk imports, fully testable headlessly

- `tool_path(name)` — `shutil.which` wrapper.
- `unique_output_path(source, codec, out_dir=None)` — returns
  `<stem> [CODEC].mkv`, appending ` (2)`, ` (3)`… to avoid overwriting.
- `unique_remux_path(mkv_path)` — returns `<stem> [CODEC].mp4` next to the MKV,
  appending ` (2)`, ` (3)`… to avoid overwriting.
- `probe_media(path, ffprobe="ffprobe")` — returns `(duration_seconds, audio_stream_count)` via ffprobe JSON.
- `normalize_bitrate(value)` — `"192"` → `"192k"`, `"128k"` stays.
- `parse_time_to_seconds(value)` — parses ffmpeg `HH:MM:SS.micro`.
- `format_duration(seconds)` — UI display helper.
- `build_ffmpeg_cmd(ffmpeg, source, codec, bitrate, output)` — builds the
  `ffmpeg` argument list. **This is the single source of truth for the
  transcode.**
- `build_remux_cmd(ffmpeg, source, output)` — builds the MP4 remux command
  (stream copy, video + audio only).
- `run_transcode(...)` / `run_remux(...)` — thin wrappers that build the
  command and hand it to `_run_ffmpeg(...)`, which runs the subprocess, parses
  `-progress pipe:1`, supports cancellation via a `threading.Event`, and
  returns `(ok, error_message)`. Calls `on_progress(pct)` and
  `on_proc(proc|None)` callbacks. **All subprocess plumbing lives in
  `_run_ffmpeg` — change it there, not in the wrappers.**
- `_safe_unlink(path)` — best-effort cleanup of partial output.
- `NativeFileDialog` — dispatches to the desktop-native picker.
- `uri_to_path(value)` — converts `file://` URIs to plain paths.

### GUI

- `Job` dataclass — one queued file and its state.
- `ResolveAudioEncoder(_BaseTk)` — the only GUI class. All widget building,
  queue management, config persistence, and the message pump live here.
- `_BaseTk` is `TkinterDnD.Tk` when `tkinterdnd2` imports, otherwise `tk.Tk`.
  `DND_AVAILABLE` reflects this.

### Threading model (important)

- The UI runs on the Tk main thread.
- `_start_queue()` snapshots pending jobs and spawns **one** `_worker_loop`
  daemon thread that processes jobs **sequentially**.
- The worker never touches widgets. It communicates by putting tuples on
  `self._msg` (`queue.Queue`).
- `_poll_messages()` runs on the UI thread via `self.after(100, ...)` and is the
  only place widgets are updated from worker results.
- `probe_media` also runs on short-lived daemon threads (`_probe_async`).
- Cancellation: `self._stop_event` (a `threading.Event`). `run_transcode`
  checks it per output line and terminates the subprocess; `_stop_queue()` also
  calls `terminate()` on `self._current_proc` directly for responsiveness.
- Shared `self.jobs` is guarded by `self._lock` for reads/writes from worker and
  UI. Don't mutate `self.jobs` from the worker without it.

## Conventions

- Python 3.9+ syntax; `from __future__ import annotations` is used, so modern
  type hints are fine.
- Match the existing style: 4-space indent, ~90 col soft limit, type hints on
  engine functions, short docstrings.
- Colors are module-level constants (`BG`, `PANEL`, `ACCENT`, …). There are
  **no hardcoded hex values in widget code** — always use the constants.
- ttk styles are defined once in `_build_style()` (theme = `clam`). Add new
  styles there, not inline.
- Output naming is always `<stem> [<CODEC>].mkv`; use `unique_output_path()`.
- Config lives at
  `${XDG_CONFIG_HOME:-~/.config}/resolve-audio-encoder/config.json`
  (resolved at import time into `CONFIG_FILE`). Never store absolute paths to
  media in the config.

## Gotchas / traps

These have bitten before — read them before changing related code.

1. **ffprobe duration / audio count.** `probe_media` must NOT assume a single
   audio track. Multi-track files are a first-class use case.

2. **ffmpeg's `out_time_ms` is actually microseconds.** This is an ffmpeg
   quirk. `_run_ffmpeg` deliberately treats both `out_time_us` and
   `out_time_ms` as microseconds. Do not "fix" this to divide by 1000.

3. **Progress uses stdout, errors use stderr.** Merge them with
   `stderr=subprocess.STDOUT` and filter known progress keys; anything else is
   collected as error tail. Don't add a second pipe read (deadlock risk).

4. **Map streams explicitly.** The transcode command maps `0:v?`, `0:a?`,
   `0:s?`, `0:t?` and uses `-c:v copy` + `-c:s copy`. Use `?` so missing stream
   types don't fail. Removing the maps can drop audio tracks or attachments.
   The **MP4 remux deliberately maps only `0:v?` and `0:a?`**: MP4 cannot carry
   MKV subtitles or attachments, and keeping them makes the mux fail.

5. **Never overwrite.** Always route output through `unique_output_path()` (and
   `unique_remux_path()` for the optional MP4 pass). Partial outputs are deleted
   on failure/cancel via `_safe_unlink`.

6. **MP4 remux deletes the MKV only on success.** `_transcode()` keeps the MKV
   when the remux fails (the error message says so) so the user never loses a
   finished transcode. Cancelling the remux likewise keeps the MKV. The setting
   is a single option (`var_remux_mp4`) — remuxing and deleting are not
   independent toggles.

7. **Drag & drop is XDND/XWayland.** Tk has no Wayland backend; the app runs
   through XWayland. `tkinterdnd2` must be importable or DnD is disabled.
   `run.sh` guarantees this. The event names are `<<DropEnter>>` /
   `<<DropLeave>>` (**not** `<<DragEnter>>`/`<<DragLeave>>`).

8. **Drop payloads vary.** `_parse_drop_data()` handles brace-wrapped paths
   (`{/a b/c.mp4}`), `text/uri-list` (`file://…` per line), and unbraced single
   paths with spaces. Extend it rather than adding format-specific branches
   elsewhere. `tk.splitlist` alone is not enough.

9. **Native dialogs are external processes.** `NativeFileDialog` shells out to
   `kdialog` (preferred on KDE) then `zenity`, falling back to Tk's dialog.
   A return code of `1` means the user cancelled → return `[]`, not an error.

10. **Auto-start can re-enter `_start_queue`.** `add_paths` calls
   `_start_queue` when `var_auto_start` is set; guard on `self._running`.

11. **Do not put blocking work on the UI thread.** Transcoding and probing
    belong on worker threads.

## Testing

`ffmpeg`/`ffprobe` and the `.mp4` test media in the repo root are available.
There is no formal test suite; validate changes with the following.

### 1. Headless engine test (fast, no display)

Generate tiny multi-track clips and exercise `run_transcode` directly:

```bash
mkdir -p /tmp/raetest && cd /tmp/raetest
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc=size=320x240:rate=15:duration=3" \
  -f lavfi -i "sine=frequency=440:duration=3" \
  -f lavfi -i "sine=frequency=880:duration=3" \
  -map 0:v -map 1:a -map 2:a -c:v libx264 -pix_fmt yuv420p \
  -c:a aac -shortest "test clip aac spaces.mp4"

cd /media/SSD-2/ai_slop/code_slop/resolve-audio-encoder
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, ".")
import resolve_audio_encoder as rae
from pathlib import Path
src = Path("/tmp/raetest/test clip aac spaces.mp4")
for codec, br in [("FLAC", ""), ("OPUS", "96k"), ("MP3", "160k")]:
    d, n = rae.probe_media(src)
    out = rae.unique_output_path(src, codec)
    ok, err = rae.run_transcode("ffmpeg", src, codec, br, out, duration=d)
    print(codec, "ok=", ok, "err=", err, "tracks=", n, "->", out.name)
PY
```

Then confirm with ffprobe that the **video codec is unchanged**, the audio
count is preserved, and the audio codec matches. For a real multi-track AV1
sample use `2025-01-14 01-08-15.mp4` (2 audio tracks, spaces in name).

For the optional MP4 pass, remux the produced MKV and check the streams:

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, ".")
import resolve_audio_encoder as rae
from pathlib import Path
mkv = Path("/tmp/raetest/test clip aac spaces [FLAC].mkv")
mp4 = rae.unique_remux_path(mkv)
print(rae.run_remux("ffmpeg", mkv, mp4))
print(rae._safe_unlink(mkv), mkv.exists(), mp4.exists())
PY
ffprobe -v error -show_entries stream=codec_type,codec_name -of csv=p=0 \
  "/tmp/raetest/test clip aac spaces [FLAC].mp4"
```

The MP4 must contain the same video codec and the same number of audio tracks;
subtitles and attachments are expected to be absent. Also test an MKV that
*does* carry subtitles/attachments (the remux must still succeed), and a failing
remux target (e.g. a non-existent directory) — the MKV must be kept.

### 2. GUI smoke test (needs a display)

```bash
DISPLAY=:0 .venv/bin/python - <<'PY'
import sys; sys.path.insert(0, ".")
import resolve_audio_encoder as rae
app = rae.ResolveAudioEncoder()
print("DND_AVAILABLE:", rae.DND_AVAILABLE)
print("dnd widgets:", len(getattr(app, "_dnd_widgets", [])))
app.add_paths(["/tmp/raetest/test clip aac spaces.mp4"])
print("jobs:", [j.path.name for j in app.jobs])
app.after(1000, app.destroy); app.mainloop()
PY
```

### 3. Full UI pipeline

Drive `add_paths()` then `_start_queue()` with `app.mainloop()`, scheduling
checks via `app.after(...)`. Always include a timeout watchdog. Clean up any
generated `* [CODEC].mkv` / `* [CODEC].mp4` files afterwards. Run it once with
`app.var_remux_mp4.set(True)` and confirm the job ends on a `.mp4` with no MKV
left next to it.

### Checklist for a change

- [ ] `.venv/bin/python -m py_compile resolve_audio_encoder.py`
- [ ] `.venv/bin/python -m pyflakes resolve_audio_encoder.py` (clean)
- [ ] Engine test passes for all three codecs, multi-track + spaces
- [ ] MP4 remux test passes (same video/audio, MKV deleted on success)
- [ ] GUI still launches and DnD registers (`DND_AVAILABLE` true via `run.sh`)
- [ ] Preferences still round-trip (codec / bitrate / auto-start / remux)
- [ ] No generated `.mkv` or `.mp4` left in the repo

## Style / PR notes

Keep the app to a single file unless there is a strong reason to split it. Prefer
small, focused functions over new classes. Avoid adding dependencies — the only
justified one is `tkinterdnd2`, and the app must remain usable without it.
