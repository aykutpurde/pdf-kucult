# Windows için kendi kendine yeten uygulamayı paketler: dist\PDF Kucult\  (GitHub Actions'ta çalışır)
# Gerekenler: Python 3 (PATH'te), MSYS2 (C:\msys64) içinde mingw-w64-x86_64-jbig2enc ve libjpeg-turbo.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $PSScriptRoot
$out = if ($env:OUT) { $env:OUT } else { Join-Path $here "derleme-win" }
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force $out | Out-Null
Set-Location $out

python -m venv venv
.\venv\Scripts\python -m pip install -q --disable-pip-version-check -r (Join-Path $here "paketleme\gereksinimler.txt") pyinstaller tkinterdnd2
.\venv\Scripts\python (Join-Path $here "simge.py") --ico (Join-Path $out "simge.ico")

.\venv\Scripts\pyinstaller --noconfirm --clean --onedir --windowed --disable-windowed-traceback --name "PDF Kucult" `
    --icon (Join-Path $out "simge.ico") --add-data "$(Join-Path $out 'simge.ico');." `
    --paths $here --hidden-import motor --hidden-import gui_win `
    --collect-submodules fontTools --collect-all pypdfium2 --collect-all pypdfium2_raw --collect-all tkinterdnd2 --collect-all mozjpeg_lossless_optimization `
    --distpath dist --workpath work --specpath work (Join-Path $here "pdfkucult.py")
if ($LASTEXITCODE -ne 0) { throw "pyinstaller başarısız" }

# jbig2 ve jpegtran + bağımlı DLL'ler (MSYS2 mingw64)
$bin = Join-Path $out "dist\PDF Kucult\bin"
New-Item -ItemType Directory -Force $bin | Out-Null
$mingw = "C:\msys64\mingw64\bin"
foreach ($exe in @("jbig2.exe", "tesseract.exe")) {
    Copy-Item (Join-Path $mingw $exe) $bin
    $deps = & C:\msys64\usr\bin\bash.exe -lc "ldd /mingw64/bin/$exe | grep -i mingw64 | awk '{print `$3}'"
    foreach ($d in $deps) {
        $w = & C:\msys64\usr\bin\cygpath.exe -w $d.Trim()
        Copy-Item $w $bin -Force
    }
}

# OCR dil modelleri
.\venv\Scripts\python (Join-Path $here "paketleme\tessdata_indir.py") (Join-Path $out "dist\PDF Kucult\tessdata")
if ($LASTEXITCODE -ne 0) { throw "tessdata indirilemedi" }

# öz test (penceresiz uygulama: çıkış kodu kontrol edilir)
$p = Start-Process -FilePath (Join-Path $out "dist\PDF Kucult\PDF Kucult.exe") -ArgumentList "--oz-test" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "öz test başarısız: $($p.ExitCode)" }
Write-Host "Windows uygulaması hazır: $out\dist\PDF Kucult"
