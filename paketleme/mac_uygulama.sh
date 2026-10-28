#!/bin/bash
# "PDF Küçült.app" + .dmg oluşturur. Motor klasörleri argüman olarak verilir (her mimari için bir tane):
#   mac_uygulama.sh <arm64 motor klasörü> [<x86_64 motor klasörü>]
# Her klasör, mac_motor.sh'nin ürettiği dist/pdfkucult klasörüdür; mimari, içindeki ikili dosyadan okunur.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-$HOME/Library/Caches/PDFKucult-derleme}"
PY="${PY:-python3}"
STAGE="$OUT/dmg"
APP="$STAGE/PDF Küçült.app"
rm -rf "$STAGE"
mkdir -p "$STAGE"

osacompile -o "$APP" "$HERE/uygulama.applescript"
cp "$HERE/motor.py" "$APP/Contents/Resources/motor.py"
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
  -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:1 string public.folder" "$PL"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.pdfkucult.app" "$PL" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.pdfkucult.app" "$PL"
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 13.0" "$PL" 2>/dev/null || true

# simge
TMP="$(mktemp -d)"
"$PY" "$HERE/simge.py" "$TMP/simge.iconset"
iconutil -c icns "$TMP/simge.iconset" -o "$APP/Contents/Resources/applet.icns"
rm -rf "$TMP"

# motorlar
for dir in "$@"; do
  arch="$(lipo -archs "$dir/pdfkucult" | awk '{print $1}')"
  mkdir -p "$APP/Contents/Resources/motor"
  cp -R "$dir" "$APP/Contents/Resources/motor/$arch"
  echo "motor eklendi: $arch"
done
xattr -cr "$APP"
codesign --force --deep -s - "$APP"

# Zotero eklentisi + rehber
(cd "$HERE/zotero-eklenti" && rm -f "$STAGE/PDF Küçült Zotero.xpi" && zip -qr -X "$STAGE/PDF Küçült Zotero.xpi" . -x ".*" -x "*/.*")
cp "$HERE/Kurulum Rehberi.html" "$STAGE/"
ln -s /Applications "$STAGE/Uygulamalar"

DMG="$OUT/PDF-Kucult-Mac.dmg"
rm -f "$DMG"
hdiutil create -quiet -volname "PDF Küçült" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
echo "Hazır: $DMG ($(du -h "$DMG" | cut -f1))"
