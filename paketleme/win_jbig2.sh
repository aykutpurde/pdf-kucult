#!/bin/bash
# MSYS2 (MINGW64) içinde jbig2enc'i kaynaktan derler (MSYS2'de hazır paketi yok) ve jpegtran'ı kurar.
set -euo pipefail
pacman -Sy --noconfirm --needed git autoconf automake libtool make \
    mingw-w64-x86_64-gcc mingw-w64-x86_64-pkgconf mingw-w64-x86_64-leptonica mingw-w64-x86_64-libjpeg-turbo \
    mingw-w64-x86_64-tesseract-ocr
cd /tmp
rm -rf jbig2enc
git clone --depth 1 https://github.com/agl/jbig2enc.git
cd jbig2enc
./autogen.sh
./configure --prefix=/mingw64
make -j4
make install
/mingw64/bin/jbig2.exe -V
