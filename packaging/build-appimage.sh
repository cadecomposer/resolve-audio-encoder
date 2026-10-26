#!/usr/bin/env bash
#
# Build a self-contained AppImage for Resolve Audio Encoder.
#
#   ./packaging/build-appimage.sh
#
# Environment:
#   BUNDLE_FFMPEG=0   Do not download/bundle static ffmpeg + ffprobe
#                     (the AppImage will then need ffmpeg on the host PATH).
#   ARCH=x86_64       Override the target architecture (default: uname -m).
#   VERSION=1.2.3     Override the version embedded in the AppImage name.
#
# Output: dist/Resolve-Audio-Encoder-<version>-<arch>.AppImage
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build"
DIST="$ROOT/dist"
APPDIR="$BUILD/AppDir"
TOOLS="$BUILD/tools"
CACHE="$BUILD/cache"

ARCH="${ARCH:-$(uname -m)}"
VERSION="${VERSION:-$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' "$ROOT/resolve_audio_encoder.py" | head -n1)}"
BUNDLE_FFMPEG="${BUNDLE_FFMPEG:-1}"

if [ -z "$VERSION" ]; then
    echo "error: could not determine version (set VERSION=...)" >&2
    exit 1
fi

case "$ARCH" in
    x86_64|aarch64) ;;
    *) echo "error: unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 is required" >&2
    exit 1
fi
if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "error: Python Tk support is missing (install python3-tk)" >&2
    exit 1
fi

mkdir -p "$BUILD" "$DIST" "$TOOLS" "$CACHE"

# ---------------------------------------------------------------------------
# 1. Build tooling (PyInstaller) in an isolated venv
# ---------------------------------------------------------------------------
BUILD_VENV="$ROOT/.build-venv"
if [ ! -x "$BUILD_VENV/bin/python" ]; then
    echo "==> Creating build virtual environment"
    python3 -m venv "$BUILD_VENV"
fi

if ! "$BUILD_VENV/bin/python" -c "import PyInstaller" >/dev/null 2>&1; then
    echo "==> Installing PyInstaller"
    "$BUILD_VENV/bin/pip" install --upgrade pip >/dev/null
    "$BUILD_VENV/bin/pip" install "pyinstaller>=6.10" >/dev/null
fi

# The app must be importable from the build venv, otherwise PyInstaller's
# --collect-all tkinterdnd2 silently produces a build without drag & drop.
echo "==> Ensuring app dependencies are installed"
"$BUILD_VENV/bin/pip" install -r "$ROOT/requirements.txt" >/dev/null

# ---------------------------------------------------------------------------
# 2. Bundle the Python app with PyInstaller (one directory, embedded runtime)
# ---------------------------------------------------------------------------
echo "==> Building application with PyInstaller"
rm -rf "$BUILD/pyinstaller-work" "$BUILD/pyinstaller-dist" "$APPDIR"

"$BUILD_VENV/bin/pyinstaller" \
    --noconfirm \
    --clean \
    --onedir \
    --noupx \
    --name resolve-audio-encoder \
    --distpath "$BUILD/pyinstaller-dist" \
    --workpath "$BUILD/pyinstaller-work" \
    --specpath "$BUILD" \
    --add-data "$ROOT/icon.png:." \
    --collect-all tkinterdnd2 \
    "$ROOT/resolve_audio_encoder.py"

mkdir -p "$APPDIR/usr/bin" \
         "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/1024x1024/apps"
cp -a "$BUILD/pyinstaller-dist/resolve-audio-encoder/." "$APPDIR/usr/bin/"

# ---------------------------------------------------------------------------
# 3. Optional: static ffmpeg/ffprobe so the AppImage is fully self-contained
# ---------------------------------------------------------------------------
if [ "$BUNDLE_FFMPEG" = "1" ]; then
    echo "==> Fetching static ffmpeg + ffprobe"
    case "$ARCH" in
        x86_64)  FF_ASSET="ffmpeg-release-amd64-static" ;;
        aarch64) FF_ASSET="ffmpeg-release-arm64-static" ;;
    esac
    FF_URL="https://johnvansickle.com/ffmpeg/releases/$FF_ASSET.tar.xz"
    FF_TARBALL="$CACHE/$FF_ASSET.tar.xz"

    if [ ! -f "$FF_TARBALL" ]; then
        curl -fL --retry 3 --retry-delay 2 -o "$FF_TARBALL.part" "$FF_URL"
        mv "$FF_TARBALL.part" "$FF_TARBALL"
    fi

    rm -rf "$CACHE/ffmpeg-extract"
    mkdir -p "$CACHE/ffmpeg-extract"
    tar -xJf "$FF_TARBALL" -C "$CACHE/ffmpeg-extract"
    FF_DIR="$(find "$CACHE/ffmpeg-extract" -mindepth 1 -maxdepth 1 -type d | head -n1)"
    install -m 0755 "$FF_DIR/ffmpeg"  "$APPDIR/usr/bin/ffmpeg"
    install -m 0755 "$FF_DIR/ffprobe" "$APPDIR/usr/bin/ffprobe"
else
    echo "==> BUNDLE_FFMPEG=0: ffmpeg/ffprobe must be on the host PATH"
fi

# ---------------------------------------------------------------------------
# 4. AppDir metadata
# ---------------------------------------------------------------------------
install -m 0755 "$ROOT/packaging/AppRun" "$APPDIR/AppRun"
install -m 0644 "$ROOT/packaging/resolve-audio-encoder.desktop" \
    "$APPDIR/resolve-audio-encoder.desktop"
install -m 0644 "$ROOT/packaging/resolve-audio-encoder.desktop" \
    "$APPDIR/usr/share/applications/resolve-audio-encoder.desktop"
install -m 0644 "$ROOT/icon.png" "$APPDIR/resolve-audio-encoder.png"
install -m 0644 "$ROOT/icon.png" \
    "$APPDIR/usr/share/icons/hicolor/1024x1024/apps/resolve-audio-encoder.png"

# ---------------------------------------------------------------------------
# 5. appimagetool (use the system one when available, else download it)
# ---------------------------------------------------------------------------
APPIMAGETOOL="${APPIMAGETOOL:-$(command -v appimagetool || true)}"
if [ -z "$APPIMAGETOOL" ]; then
    APPIMAGETOOL="$TOOLS/appimagetool-$ARCH.AppImage"
    if [ ! -x "$APPIMAGETOOL" ]; then
        echo "==> Downloading appimagetool"
        curl -fL --retry 3 --retry-delay 2 -o "$APPIMAGETOOL.part" \
            "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
        mv "$APPIMAGETOOL.part" "$APPIMAGETOOL"
        chmod +x "$APPIMAGETOOL"
    fi
else
    echo "==> Using system appimagetool: $APPIMAGETOOL"
fi

OUT="$DIST/Resolve-Audio-Encoder-$VERSION-$ARCH.AppImage"
echo "==> Packaging $OUT"
rm -f "$OUT"
# APPIMAGE_EXTRACT_AND_RUN avoids requiring FUSE just to build.
ARCH="$ARCH" VERSION="$VERSION" APPIMAGE_EXTRACT_AND_RUN=1 \
    "$APPIMAGETOOL" --no-appstream "$APPDIR" "$OUT"

echo
echo "Done: $OUT"
ls -lh "$OUT"
