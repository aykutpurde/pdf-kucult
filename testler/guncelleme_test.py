"""Yeni sürüm denetimi (guncelleme.py) testleri: ağsız, geçici durum dosyasıyla. Bağımlılık yok.

  python testler/guncelleme_test.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import guncelleme as g  # noqa: E402

hatalar = []


def esit(ad, alinan, beklenen):
    if alinan != beklenen:
        hatalar.append(f"{ad}: beklenen {beklenen!r}, alınan {alinan!r}")


def surum_etiketi(fark):
    p = list(g.anahtar(g.SURUM))
    p[-1] += fark
    return "v" + ".".join(map(str, p))


with tempfile.TemporaryDirectory() as d:
    yol = os.path.join(d, "alt", "guncelleme.json")
    sayac = {"n": 0}

    def etiket(e):
        def al():
            sayac["n"] += 1
            return e
        return al

    yeni = surum_etiketi(1)
    s = g.denetle(simdi=1000, yol=yol, etiket_al=etiket(yeni))
    esit("yeni sürüm bildirilir", (s["yeni"], s["goster"]), (yeni[1:], True))
    s = g.denetle(simdi=2000, yol=yol, etiket_al=etiket(yeni))
    esit("aynı gün: ağa sorulmaz, bildirim tekrarlanmaz", (sayac["n"], s["yeni"], s["goster"]), (1, yeni[1:], False))
    s = g.denetle(simdi=1000 + g.ARALIK, yol=yol, etiket_al=etiket(yeni))
    esit("ertesi gün yeniden hatırlatılır", (sayac["n"], s["goster"]), (2, True))

    os.remove(yol)
    s = g.denetle(simdi=1000, yol=yol, etiket_al=etiket("v" + g.SURUM))
    esit("aynı sürüm: bildirim yok", (s["yeni"], s["goster"]), (None, False))
    os.remove(yol)
    s = g.denetle(simdi=1000, yol=yol, etiket_al=etiket(surum_etiketi(-1)))
    esit("eski etiket: bildirim yok", s["yeni"], None)
    os.remove(yol)
    s = g.denetle(simdi=1000, yol=yol, etiket_al=etiket(None))
    esit("ağ yok: sessiz, ertesi güne dek sorulmaz", (s["yeni"], json.load(open(yol))["son_denetim"]), (None, 1000))

    os.remove(yol)
    g.kapat(yol)
    s = g.denetle(simdi=1000, yol=yol, etiket_al=etiket(yeni))
    esit("bir daha sorma", (s.get("kapali"), s["goster"]), (True, False))

    with open(yol, "w") as f:
        f.write("bozuk{")
    s = g.denetle(simdi=1000, yol=yol, etiket_al=etiket(yeni))
    esit("bozuk durum dosyası", s["goster"], True)

esit("sürüm karşılaştırma", [g.anahtar("v1.8.10") > g.anahtar("1.8.9"), g.anahtar("v2.0") > g.anahtar("1.9.9"), g.anahtar("beta")],
     [True, True, ()])

manifest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "zotero-eklenti", "manifest.json")
esit("sürüm Zotero eklentisiyle aynı", g.SURUM, json.load(open(manifest, encoding="utf-8"))["version"])

for h in hatalar:
    print("✗", h)
print("guncelleme:", "tamam" if not hatalar else f"{len(hatalar)} hata")
sys.exit(1 if hatalar else 0)
