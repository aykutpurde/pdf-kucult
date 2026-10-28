#!/bin/zsh
# PDF Küçült kurulumu: gerekli araçları kurar ve "PDF Küçült.app" uygulamasını ~/Applications'a yerleştirir.
# Tekrar çalıştırmak güvenlidir (güncelleme için de kullanılır).
set -e
HERE="${0:A:h}"
SUPPORT="$HOME/Library/Application Support/PDFKucult"
APP="$HOME/Applications/PDF Küçült.app"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "1/4 Homebrew araçları kontrol ediliyor…"
command -v brew >/dev/null || { echo "Homebrew gerekli: https://brew.sh"; exit 1; }
for pair in jbig2:jbig2enc jpegtran:jpeg-turbo tesseract:tesseract; do
  cmd=${pair%%:*}; pkg=${pair##*:}
  command -v $cmd >/dev/null || brew install $pkg
done

echo "2/4 Python ortamı hazırlanıyor…"
mkdir -p "$SUPPORT"
if ! "$SUPPORT/venv/bin/python" -c "import pikepdf, PIL, numpy, fontTools, pypdfium2, send2trash, mozjpeg_lossless_optimization" 2>/dev/null; then
  rm -rf "$SUPPORT/venv"
  python3 -m venv "$SUPPORT/venv"
  "$SUPPORT/venv/bin/pip" install -q --disable-pip-version-check pikepdf pillow numpy fonttools pypdfium2 send2trash mozjpeg-lossless-optimization
fi

echo "3/4 Uygulama derleniyor…"
mkdir -p "$HOME/Applications"
rm -rf "$APP"
osacompile -o "$APP" "$HERE/uygulama.applescript"
cp "$HERE/motor.py" "$APP/Contents/Resources/motor.py"
cp "$HERE/motor.py" "$SUPPORT/motor.py"   # Zotero eklentisi motoru buradan çalıştırır

# PDF ve klasörlerin simgeye bırakılabilmesi
PL="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Delete :CFBundleDocumentTypes" "$PL" 2>/dev/null || true
/usr/libexec/PlistBuddy \
  -c "Add :CFBundleDocumentTypes array" \
  -c "Add :CFBundleDocumentTypes:0 dict" \
  -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string 'PDF ve klasörler'" \
  -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Viewer" \
  -c "Add :CFBundleDocumentTypes:0:LSHandlerRank string Alternate" \
  -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array" \
  -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:0 string com.adobe.pdf" \
  -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:1 string public.folder" \
  "$PL"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.aykut.pdfkucult" "$PL" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.aykut.pdfkucult" "$PL"

echo "4/4 Simge oluşturuluyor…"
TMP=$(mktemp -d)
"$SUPPORT/venv/bin/python" -I "$HERE/simge.py" "$TMP/simge.iconset"
iconutil -c icns "$TMP/simge.iconset" -o "$APP/Contents/Resources/applet.icns"
rm -rf "$TMP"
touch "$APP"
codesign --force --deep -s - "$APP" 2>/dev/null || true

echo "Zotero eklentisi paketleniyor…"
XPI="$HERE/PDF Küçült Zotero.xpi"
rm -f "$XPI"
(cd "$HERE/zotero-eklenti" && zip -qr -X "$XPI" . -x ".*" -x "*/.*")

echo
echo "Kurulum tamam: $APP"
echo "Zotero eklentisi: $XPI  (Zotero › Tools › Plugins › ⚙ › Install Plugin From File…)"
echo "İpucu: uygulamayı Dock'a sürükleyin; PDF'leri veya klasörleri simgesinin üzerine bırakın."
