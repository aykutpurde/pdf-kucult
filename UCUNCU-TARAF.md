# Üçüncü taraf bileşenler

PDF Küçült'ün Mac ve Windows paketleri aşağıdaki açık kaynak bileşenleri içerir. Her biri kendi
lisansıyla dağıtılır; lisans metinleri bağlantılı projelerde bulunur.

| Bileşen | Kullanım | Lisans |
|---|---|---|
| [Python](https://www.python.org/) | Motorun çalıştığı dil | PSF-2.0 |
| [PyInstaller](https://pyinstaller.org/) | Paketleme (önyükleyici) | GPL-2.0, paketlenen uygulamalar için istisnalı |
| [pikepdf](https://github.com/pikepdf/pikepdf) / [qpdf](https://github.com/qpdf/qpdf) | PDF okuma/yazma | MPL-2.0 / Apache-2.0 |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) / [PDFium](https://pdfium.googlesource.com/pdfium/) | Sayfa görüntüleme, kalite kontrolü | Apache-2.0 veya BSD-3-Clause / BSD-3-Clause |
| [Pillow](https://python-pillow.org/) | Görüntü işleme | MIT-CMU (HPND) |
| [NumPy](https://numpy.org/) | Görüntü analizi | BSD-3-Clause |
| [fontTools](https://github.com/fonttools/fonttools) | Font küçültme | MIT |
| [Send2Trash](https://github.com/arsenetar/send2trash) | Çöp Kutusu'na taşıma | BSD-3-Clause |
| [mozjpeg-lossless-optimization](https://github.com/wanadev/mozjpeg-lossless-optimization) / [mozjpeg](https://github.com/mozilla/mozjpeg) | Kayıpsız JPEG optimizasyonu | BSD-3-Clause / IJG, BSD-3-Clause, Zlib |
| [jbig2enc](https://github.com/agl/jbig2enc) | Siyah-beyaz sıkıştırma (JBIG2) | Apache-2.0 |
| [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) ve [tessdata](https://github.com/tesseract-ocr/tessdata_fast) | Metin tanıma | Apache-2.0 |
| [Leptonica](http://www.leptonica.org/) | jbig2enc/Tesseract görüntü kütüphanesi | BSD-2-Clause |
| [libarchive](https://www.libarchive.org/) | Tesseract bağımlılığı | BSD-2-Clause |
| libjpeg-turbo, libpng, libtiff, libwebp, giflib, OpenJPEG, zlib, zstd, lz4, xz (liblzma), libb2 | Leptonica/libarchive bağımlılıkları | IJG/BSD-3-Clause/Zlib, libpng, libtiff, BSD-3-Clause, MIT, BSD-2-Clause, Zlib, BSD-3-Clause, BSD-2-Clause, 0BSD, CC0-1.0 |

Paketlerde GPL/LGPL lisanslı bir kütüphane bulunmaz (PyInstaller önyükleyicisi, lisansındaki istisna
gereği paketlenen uygulamanın lisansını etkilemez).
