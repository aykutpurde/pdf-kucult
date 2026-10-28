"""PDF Küçült — paketlenmiş uygulamanın giriş noktası.

  pdfkucult --motor [motor.py argümanları]   sıkıştırma motoru (Mac uygulaması ve Zotero eklentisi)
  pdfkucult [dosyalar/klasörler]             Windows: pencereli arayüz (simgeye bırakılanlar listede)
  pdfkucult --oz-test                        paketin çalıştığını doğrular (CI)
"""
import multiprocessing
import os
import sys


def main():
    multiprocessing.freeze_support()  # alt süreçler (ProcessPool) paketlenmiş uygulamada da çalışsın
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:  # penceresiz Windows uygulamasında konsol yok
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
        else:  # Windows'un eski kod sayfası (cp1252) "→", "ş" gibi karakterlerde çöker
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    args = sys.argv[1:]
    if args[:1] == ["--motor"]:
        import motor
        try:
            return motor.main(args[1:])
        except Exception:  # pencereli uygulamada hata penceresi açılıp beklemesin
            import traceback
            traceback.print_exc()
            return 1
    if args[:1] == ["--oz-test"]:
        return self_test()
    if sys.platform == "win32":
        import gui_win
        return gui_win.run(args)
    import motor
    return motor.main(args)


def self_test():
    import motor
    import pypdfium2  # noqa: F401
    import send2trash  # noqa: F401
    from fontTools import subset  # noqa: F401
    missing = [t for t, p in motor.TOOLS.items() if not p and t != "jpegtran"]  # jpegtran yerine mozjpeg
    if motor._mozjpeg is None:
        missing.append("mozjpeg")
    tess = motor.tessdata_dir()
    print("dil modelleri:", tess, motor.ocr_langs(tess) if tess else "")
    if not tess or len(motor.ocr_langs(tess).split("+")) < len(motor.OCR_LANGS):
        missing.append("tessdata")
    print("araçlar:", motor.TOOLS)
    if sys.platform == "win32":
        import tkinter
        root = tkinter.Tk()
        root.destroy()
    if missing:
        print("EKSİK:", missing)
        return 1
    print("tamam")
    return 0


if __name__ == "__main__":
    sys.exit(main())
