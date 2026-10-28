#!/bin/bash
# Bu Mac'in mimarisi (arm64 / x86_64) için kendi kendine yeten motoru paketler:
#   $OUT/<mimari>/dist/pdfkucult/   (Python + kütüphaneler + jbig2 + jpegtran, hiçbir şey kurulu olmadan çalışır)
# Gerekenler: Homebrew'da jbig2enc, jpeg-turbo ve tesseract; PY ile bir Python 3.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PY:-python3}"
OUT="${OUT:-$HOME/Library/Caches/PDFKucult-derleme}"
ARCH="$(uname -m)"
W="$OUT/$ARCH"
rm -rf "$W"
mkdir -p "$W"

# En eski desteklenen macOS için derlenmiş paketleri indir (yoksa çalışan sunucunun sürümü seçilir)
MINOS="$([ "$ARCH" = arm64 ] && echo 14_0 || echo 13_0)"
PYV="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
"$PY" -m venv "$W/venv"
"$W/venv/bin/pip" download -q --disable-pip-version-check --only-binary=:all: \
    --platform "macosx_${MINOS}_${ARCH}" --python-version "$PYV" -d "$W/wheels" -r "$HERE/paketleme/gereksinimler.txt"
"$W/venv/bin/pip" install -q --disable-pip-version-check --no-index --find-links "$W/wheels" -r "$HERE/paketleme/gereksinimler.txt"
"$W/venv/bin/pip" install -q --disable-pip-version-check pyinstaller

cd "$W"
"$W/venv/bin/pyinstaller" --noconfirm --clean --onedir --console --name pdfkucult \
    --paths "$HERE" --hidden-import motor \
    --collect-submodules fontTools --collect-all pypdfium2 --collect-all pypdfium2_raw \
    --collect-all mozjpeg_lossless_optimization \
    --distpath dist --workpath work --specpath work "$HERE/pdfkucult.py"

BIN="$W/dist/pdfkucult/bin"
mkdir -p "$BIN"
BREW="$(brew --prefix)"
cp -L "$BREW/bin/jbig2" "$BREW/bin/tesseract" "$BIN/"
chmod u+w "$BIN"/*
"$W/venv/bin/python" "$HERE/paketleme/dylib_topla.py" "$BIN" jbig2 tesseract
"$W/venv/bin/python" "$HERE/paketleme/tessdata_indir.py" "$W/dist/pdfkucult/tessdata"

"$W/dist/pdfkucult/pdfkucult" --oz-test
echo "Motor hazır: $W/dist/pdfkucult ($(du -sh "$W/dist/pdfkucult" | cut -f1))"
