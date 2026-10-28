"""Paketlenmiş (ya da geliştirme) motorunu örnek PDF'lerle sınar. Başarısızlıkta çıkış kodu 1.

  python calistir.py <motor komutu...> -- <örnek klasörü> <çıktı klasörü>
  ör.  python calistir.py dist/pdfkucult/pdfkucult --motor -- ornekler cikti
"""
import json
import os
import subprocess
import sys

import pypdfium2 as pdfium

BEKLENEN = {  # (dosya, seviye): bu işlem türlerinden biri yapılmış olmalı
    ("tarama.pdf", "asiri"): {"tarama->jbig2"},
    ("cmyk.pdf", "onerilen"): {"cmyk->rgb"},
    ("siyahbeyaz.pdf", "az"): {"jbig2"},
    ("font.pdf", "onerilen"): {"font-küçültme"},
    ("font.pdf", "az"): {"font-küçültme"},
    ("ters.pdf", "az"): {"jbig2"},
    ("ters.pdf", "asiri"): {"jbig2", "leke-temizleme"},
    ("paletli.pdf", "onerilen"): {"paletli->jpeg"},
    ("vektor.pdf", "onerilen"): {"vektör-yuvarlama"},
    ("zincir.pdf", "onerilen"): {"jpeg-kucultme"},
    ("fotograf.pdf", "asiri"): {"tarama->jbig2"},
    ("grafik.pdf", "onerilen"): {"grafik-kucultme"},
    ("profil.pdf", "onerilen"): {"renk-profili"},
    ("baskihedef.pdf", "onerilen"): {"renk-profili"},
    ("jp2cmyk.pdf", "onerilen"): {"cmyk->rgb"},
    ("cift.pdf", "okuma"): {"tarama->jbig2"},
    ("cift.pdf", "asiri"): {"jpeg-kucultme", "jpeg-yeniden", "tarama->jbig2", "jpeg-kayipsiz"},
    ("resimli.pdf", "okuma"): {"tarama->noktali"},
    ("grafik.pdf", "okuma"): {"grafik-kucultme"},
    ("zincir.pdf", "okuma"): {"jpeg-kucultme"},
}
OCR_KELIMELER = ("hukuku", "sürdürülebilir", "kalkınma", "madencilik", "ormanlık", "değerlendirilmiştir")


def mean_brightness(path):
    import numpy as np
    doc = pdfium.PdfDocument(path)
    try:
        return float(np.asarray(doc[0].render(scale=0.5, grayscale=True).to_pil().convert("L")).mean())
    finally:
        doc.close()


def text_of(path):
    doc = pdfium.PdfDocument(path)
    try:
        return " ".join(" ".join(p.get_textpage().get_text_bounded() for p in doc).split())
    finally:
        doc.close()


def main():
    i = sys.argv.index("--")
    cmd, (src, out) = sys.argv[1:i], sys.argv[i + 1:i + 3]
    os.makedirs(out, exist_ok=True)
    fails = []
    for (name, level), expect in BEKLENEN.items():
        s = os.path.join(src, name)
        d = os.path.join(out, f"{level}-{name}")
        res = d + ".json"
        if os.path.exists(res):
            os.remove(res)
        try:
            p = subprocess.run(cmd + ["--sessiz", "--ocr-yok", "--sonuc", res, "-s", level, "-o", d, s],
                               capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired as e:
            fails.append(f"{name}/{level}: 300 sn'de bitmedi (takıldı) {str(e.stderr or '')[-300:]}")
            print(f"{name:15s} {level:9s} ZAMAN AŞIMI")
            continue
        try:
            r = json.loads(open(res, encoding="utf-8").read().strip().splitlines()[-1])
        except Exception:
            fails.append(f"{name}/{level}: sonuç yok (çıkış {p.returncode}) {p.stderr[-400:]}")
            continue
        ops = set((r.get("islemler") or {}).keys())
        line = f"{name:15s} {level:9s} {r['once'] / 1024:7.0f} KB -> {r['sonra'] / 1024:7.0f} KB  {r['durum']}  {sorted(ops)}  {r.get('mesaj', '')}"
        print(line)
        if r["durum"] != "tamam" or not r["sonra"] < r["once"]:
            fails.append(f"{name}/{level}: küçülmedi ({r['durum']} {r.get('mesaj', '')})")
        elif not (ops & expect):
            fails.append(f"{name}/{level}: beklenen işlem yok {expect}, olan {ops}")
        elif text_of(s) != text_of(d):
            fails.append(f"{name}/{level}: metin katmanı değişti")
        elif mean_brightness(d) < mean_brightness(s) - 25:
            # kağıt rengi beyaza çekilince sayfa aydınlanabilir; ters dönme ise belirgin karartır
            fails.append(f"{name}/{level}: sayfa karardı (görüntü ters dönmüş olabilir)")

    # Okuma kopyası: çift sayfa taramalar ikiye bölünür; Aşırı'da bölünmez ve resimli sayfa korunur
    for name, level, pages in (("cift.pdf", "okuma", 4), ("cift.pdf", "asiri", 2)):
        d = os.path.join(out, f"{level}-{name}")
        n = len(pdfium.PdfDocument(d)) if os.path.exists(d) else 0
        print(f"bölme           {level:9s} {name}: {n} sayfa (beklenen {pages})")
        if n != pages:
            fails.append(f"{name}/{level}: {n} sayfa, beklenen {pages}")
    for name, level in (("resimli.pdf", "asiri"),):
        d = os.path.join(out, f"{level}-{name}")
        res = d + ".json"
        subprocess.run(cmd + ["--sessiz", "--ocr-yok", "--sonuc", res, "-s", level, "-o", d, os.path.join(src, name)],
                       capture_output=True, timeout=300)
        try:
            ops = set((json.loads(open(res, encoding="utf-8").read().strip().splitlines()[-1]).get("islemler") or {}))
        except Exception:
            ops = set()
        print(f"resim koruması  {level:9s} {name}: {sorted(ops)}")
        if ops & {"tarama->jbig2", "tarama->noktali"}:
            fails.append(f"{name}/{level}: fotoğraflı sayfa siyah-beyaza çevrildi")

    # OCR: metinsiz taramaya görünmez metin katmanı
    s, d = os.path.join(src, "tarama.pdf"), os.path.join(out, "ocr-tarama.pdf")
    res = d + ".json"
    if os.path.exists(res):
        os.remove(res)
    try:
        subprocess.run(cmd + ["--sessiz", "--sonuc", res, "-s", "onerilen", "-o", d, s],
                       capture_output=True, text=True, timeout=600)
        r = json.loads(open(res, encoding="utf-8").read().strip().splitlines()[-1])
        txt = text_of(d).lower()
        found = [w for w in OCR_KELIMELER if w in txt]
        print(f"OCR             onerilen  sayfa={r.get('ocr')}  bulunan kelimeler: {found}")
        if not r.get("ocr") or len(found) < 3:
            fails.append(f"OCR: metin katmanı yok ya da okunamadı ({r.get('ocr')}, {found})")
        elif abs(mean_brightness(s) - mean_brightness(d)) > 8:
            fails.append("OCR: sayfa görünümü değişti")
    except Exception as e:
        fails.append(f"OCR: {type(e).__name__}: {e}")

    # Aşırı: çizim olarak kaydedilmiş metin -> siyah-beyaz tarama + OCR (aranabilir)
    s, d = os.path.join(src, "vektor.pdf"), os.path.join(out, "asiri-vektor.pdf")
    res = d + ".json"
    if os.path.exists(res):
        os.remove(res)
    try:
        subprocess.run(cmd + ["--sessiz", "--sonuc", res, "-s", "asiri", "-o", d, s],
                       capture_output=True, text=True, timeout=600)
        r = json.loads(open(res, encoding="utf-8").read().strip().splitlines()[-1])
        found = [w for w in OCR_KELIMELER if w in text_of(d).lower()]
        print(f"vektör->tarama  asiri     {r['once'] / 1024:7.0f} KB -> {r['sonra'] / 1024:7.0f} KB  "
              f"tarama={r.get('tarama')} ocr={r.get('ocr')} kelimeler={len(found)}")
        if not r.get("tarama") or not r["sonra"] < r["once"] * 0.5 or len(found) < 3:
            fails.append(f"vektör->tarama: beklenen küçülme/OCR yok ({r.get('tarama')}, {r.get('ocr')}, {found})")
    except Exception as e:
        fails.append(f"vektör->tarama: {type(e).__name__}: {e}")

    # Klasör kipi: birden çok dosya paralel alt süreçlerle işlenir (uygulamanın normal kullanımı)
    durum = os.path.join(out, "durum.txt")
    try:
        subprocess.run(cmd + ["--sessiz", "-s", "onerilen", "--durum", durum, src],
                       capture_output=True, text=True, timeout=600)
        lines = open(durum, encoding="utf-8").read().splitlines() if os.path.exists(durum) else []
        ok = any(ln.startswith("S\t") for ln in lines) and sum(ln.startswith("D\t") for ln in lines) == len(
            [f for f in os.listdir(src) if f.endswith(".pdf")])
        print("klasör kipi (paralel):", "tamam" if ok else f"BAŞARISIZ {lines}")
        if not ok:
            fails.append("klasör kipi tamamlanmadı")
    except subprocess.TimeoutExpired:
        print("klasör kipi (paralel): ZAMAN AŞIMI")
        fails.append("klasör kipi takıldı (paralel alt süreçler)")

    # "küçültüldü" işareti: çıktıda var, orijinalde yok; --isaret-oku ile okunur
    liste, sonuc = os.path.join(out, "isaret-liste.txt"), os.path.join(out, "isaret-sonuc.jsonl")
    a, b = os.path.join(out, "onerilen-paletli.pdf"), os.path.join(src, "paletli.pdf")
    open(liste, "w", encoding="utf-8").write(a + "\n" + b)
    subprocess.run(cmd + ["--isaret-oku", "--liste", liste, "--sonuc", sonuc], capture_output=True, timeout=120)
    try:
        marks = {json.loads(ln)["dosya"]: json.loads(ln)["isaret"] for ln in open(sonuc, encoding="utf-8") if ln.strip()}
        ok = bool(marks.get(a)) and marks.get(a, {}).get("seviye") == "onerilen" and marks.get(b) is None
    except Exception:
        ok, marks = False, {}
    print("işaret:", "tamam" if ok else f"BAŞARISIZ {marks}")
    if not ok:
        fails.append("küçültüldü işareti yazılmadı/okunamadı")

    # Çöp Kutusu / Geri Dönüşüm Kutusu
    t = os.path.join(out, "cop-deneme.txt")
    open(t, "w").write("deneme")
    subprocess.run(cmd + ["--cop-kutusuna", t], capture_output=True, timeout=120)
    print("çöp kutusu:", "tamam" if not os.path.exists(t) else "BAŞARISIZ")
    if os.path.exists(t):
        fails.append("çöp kutusuna taşıma başarısız")

    if fails:
        print("\nBAŞARISIZ:\n  " + "\n  ".join(fails))
        return 1
    print("\nTüm testler geçti.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
