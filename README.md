# Resolve Audio Encoder

A small dark-mode GUI that fixes the biggest annoyance of **DaVinci Resolve on
Linux**: it cannot play/decode **AAC** audio (and some other formats). This tool
remuxes your videos into an **MKV** container, leaving the original **video
stream completely untouched** and transcoding the audio into a codec Resolve
handles happily.

- Drag & drop **multiple videos** into a queue, or use **Add Files…**
- Uses the **desktop's native file dialog** (KDE `kdialog`, GNOME/other `zenity`)
- Audio codec: **FLAC** (default, lossless), **OPUS**, or **MP3**
- Custom **bitrate** for OPUS/MP3 (not applicable to lossless FLAC)
- Copies video, subtitles and all **multiple audio tracks** into a new MKV
- Optional **MP4 remux**: after transcoding, copy the result into an MP4
  container and delete the intermediate MKV (subtitles/attachments are dropped)
- **OPUS requires the MP4 remux** — Resolve does not play OPUS from an MKV
  (the app warns you about this when OPUS is selected)
- Optional **auto-start** when files are added (off by default)
- Preferences persist to a config file
- UI palette inspired by DaVinci Resolve

Output is written next to each source file as:

```
My Video.mp4   ->   My Video [FLAC].mkv
```

If a file with that name already exists, ` (2)`, ` (3)` … is appended so the
tool never overwrites anything (including your source files).

With **“Also remux to MP4 (delete the MKV afterwards)”** enabled the result is
instead:

```
My Video.mp4   ->   My Video [FLAC].mp4
```

The MP4 is a stream copy of the MKV, so the video is still untouched. MP4 has
no place for subtitles or font attachments, so those are not carried over; if
the remux fails for any reason the MKV is kept and the job is marked failed.

> **OPUS users must enable the MP4 remux.** DaVinci Resolve does not play OPUS
> audio from an MKV container — the clip loads without sound. The same OPUS
> stream works once it is wrapped in an MP4. So with OPUS selected, always tick
> **“Also remux to MP4”**; the app shows a warning in the settings bar whenever
> OPUS is chosen. FLAC and MP3 need no special handling and work in either
> container.

---

## Requirements

- Linux with Python 3.9+ and Tk (`python3-tk` on Debian/Ubuntu; bundled with
  Python on Arch/Fedora)
- `ffmpeg` and `ffprobe` on your `PATH`
- Optional but recommended: `kdialog` (KDE) or `zenity` for the native file
  picker

`tkinterdnd2` (drag & drop) is installed automatically by the launcher.

## Run

```bash
./run.sh
```

On first run this creates a local `.venv`, installs `tkinterdnd2`, and starts
the app. Subsequent runs are instant.

If you prefer to manage it yourself:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python resolve_audio_encoder.py
```

> **Important:** drag & drop requires `tkinterdnd2`. If you start the app with
> your *system* Python and it isn't installed there, the drop hint will say
> "Drag & drop unavailable" and only the file picker will work. `./run.sh`
> makes sure it is present.

## AppImage

Build a self-contained AppImage (Python, Tk, drag & drop, the icon and static
`ffmpeg`/`ffprobe` are all bundled):

```bash
./packaging/build-appimage.sh
```

The result is written to:

```
dist/Resolve-Audio-Encoder-1.0.1-x86_64.AppImage
```

Build requirements: `python3` with Tk (`python3-tk` on Debian/Ubuntu), `curl`,
`tar`, `xz`, and `appimagetool` (used from `PATH` when available, otherwise
downloaded automatically). PyInstaller and the app's dependencies are installed
into a throwaway `.build-venv/`, so your normal `.venv/` is untouched.

To build a smaller AppImage that uses the host's `ffmpeg`/`ffprobe` instead of
bundling them:

```bash
BUNDLE_FFMPEG=0 ./packaging/build-appimage.sh
```

Show the app in your launcher after building:

```bash
chmod +x dist/Resolve-Audio-Encoder-*.AppImage
./dist/Resolve-Audio-Encoder-*.AppImage
```

For a proper desktop entry, use an AppImage manager such as AppImageLauncher, or
copy the `.desktop` file from `packaging/` into `~/.local/share/applications/`.

> AppImages built here target the machine they were built on: the PyInstaller
> runtime links against the build system's glibc and Tk. Build on the oldest
> distribution you intend to support if you plan to distribute the file
> broadly.

## Usage

1. Launch with `./run.sh`.
2. Drag videos into the window, or click **Add Files…**.
3. Pick the audio **codec** and, if not FLAC, a **bitrate**. With **OPUS**
   selected the app reminds you to enable the MP4 remux (Resolve cannot play
   OPUS from an MKV).
4. Optionally tick **Also remux to MP4 (delete the MKV afterwards)** — required
   for OPUS.
5. Click **Start** (or tick **Auto-start when files are added**).
6. Find the `... [CODEC].mkv` (or `.mp4`) files next to your originals.

**Stop** cancels the current job and leaves the rest queued. **Remove** deletes
the selected rows and **Clear** empties the whole queue (both are disabled for
pending jobs while running).

## Preferences

Settings are stored at:

```
${XDG_CONFIG_HOME:-~/.config}/resolve-audio-encoder/config.json
```

The chosen codec, per-codec bitrate, the auto-start toggle and the MP4-remux
toggle are remembered.

## Drag & drop on Wayland

Tk itself only has an X11 backend, so the app runs through **XWayland**. KDE
Plasma and GNOME bridge drag & drop between native Wayland apps (e.g. Dolphin)
and XWayland apps, but this bridge can be finicky depending on your desktop
version. If a drag from Dolphin does not register:

- Make sure you launched via `./run.sh` so `tkinterdnd2` is installed.
- Click **Add Files…** as a reliable fallback; it opens the real KDE file
  dialog and supports multi-select.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| "ffmpeg and ffprobe are required" | Install ffmpeg (`sudo pacman -S ffmpeg`, `sudo apt install ffmpeg`, …) |
| Drag & drop does nothing | Run via `./run.sh`; use **Add Files…** as a fallback |
| "Add Files…" opens an ugly dialog | Install `kdialog` or `zenity` for the native picker |
| Resolve still won't play audio | Make sure you opened the generated **`.mkv`**, not the original file |
| OPUS audio is silent in Resolve | OPUS only works in MP4: tick **Also remux to MP4** and use the generated `.mp4` |

## Files

| File | Purpose |
| --- | --- |
| `resolve_audio_encoder.py` | The whole application (engine + GUI) |
| `run.sh` | Creates the venv and launches the app |
| `requirements.txt` | The single optional dependency (`tkinterdnd2`) |
| `icon.png` | Application / launcher icon (also the window icon) |
| `packaging/build-appimage.sh` | Builds a self-contained AppImage |
| `packaging/AppRun` | AppImage entry point |
| `packaging/resolve-audio-encoder.desktop` | Desktop entry bundled in the AppImage |

## Changelog

### 1.0.1

- Documented that DaVinci Resolve cannot play **OPUS audio from MKV** — OPUS
  only works when the file is remuxed to MP4.
- The settings bar now shows a warning whenever OPUS is selected, pointing at
  the MP4 remux option.

### 1.0.0

- First release: MKV remux + FLAC/OPUS/MP3 audio transcode, optional MP4
  remux, queue with drag & drop, native file dialogs, AppImage packaging.

## License

MIT — see [LICENSE](LICENSE).
