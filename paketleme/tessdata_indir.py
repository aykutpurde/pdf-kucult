"""Tesseract dil modellerini (resmî tessdata_fast 4.1.0, Apache-2.0) indirir.

  python tessdata_indir.py <hedef klasör>
"""
import os
import sys
import urllib.request

LANGS = ("tur", "eng", "deu", "fra")
URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/{}.traineddata"


def main(out):
    os.makedirs(out, exist_ok=True)
    for lang in LANGS:
        dst = os.path.join(out, f"{lang}.traineddata")
        with urllib.request.urlopen(URL.format(lang), timeout=120) as r, open(dst, "wb") as f:
            f.write(r.read())
        size = os.path.getsize(dst)
        if size < 500_000:
            raise SystemExit(f"{lang}: indirme eksik ({size} bayt)")
        print(f"{lang}.traineddata {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(sys.argv[1])
