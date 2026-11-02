"""PDF Küçült — yeni sürüm denetimi (Mac ve Windows uygulaması, Zotero eklentisi ortak kullanır).

Günde en çok bir kez GitHub'a en son sürümün etiketini sorar; hiçbir dosya ya da kişisel bilgi gönderilmez.
Yeni sürüm varsa bildirim de günde en çok bir kez gösterilir. Son denetim, son hatırlatma ve "bir daha sorma"
tercihi kullanıcı klasöründeki guncelleme.json'da tutulur (Mac: ~/Library/Application Support/PDFKucult,
Windows: %LOCALAPPDATA%\\PDFKucult).

  pdfkucult --surum [--dosya YOL]       kurulu sürüm (penceresiz Windows uygulamasında çıktı kaybolabilir:
                                        --dosya ile ayrıca bu dosyaya yazılır; Zotero eklentisi böyle okur)
  pdfkucult --guncelleme [--metin]      {"surum", "yeni", "goster", "adres"} (JSON) ya da "yeni<TAB>surum<TAB>adres"
                                        (yalnızca gösterilecek bildirim varsa; yoksa boş)
  pdfkucult --guncelleme-kapat          denetimi kapatır ("Bir daha sorma")
"""
import json
import os
import re
import subprocess
import sys
import time

SURUM = "1.8.3"   # zotero-eklenti/manifest.json ile aynı olmalı (derleme denetler)
DEPO = "aykutpurde/pdf-kucult"
API = f"https://api.github.com/repos/{DEPO}/releases/latest"
ADRES = f"https://github.com/{DEPO}/releases/latest"
ARALIK = 24 * 3600


def durum_dosyasi():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "PDFKucult", "guncelleme.json")
    return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "PDFKucult", "guncelleme.json")


def oku(yol=None):
    try:
        with open(yol or durum_dosyasi(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def yaz(d, yol=None):
    yol = yol or durum_dosyasi()
    try:
        os.makedirs(os.path.dirname(yol), exist_ok=True)
        gecici = yol + ".tmp"
        with open(gecici, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(gecici, yol)
    except OSError:
        pass   # yazılamazsa denetim yine çalışır, yalnızca ertesi açılışta tekrarlanır


def anahtar(s):
    """"v1.8.10" → (1, 8, 10); sürüm gibi görünmüyorsa ()."""
    m = re.match(r"^[vV]?(\d+(?:\.\d+)*)$", (s or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def en_son_etiket(zaman_asimi=5):
    """GitHub'daki en son sürümün etiketi ("v1.8.3") ya da None. Ağ yoksa sessizce None."""
    basliklar = {"User-Agent": f"PDF-Kucult/{SURUM}", "Accept": "application/vnd.github+json"}
    try:
        import urllib.request
        istek = urllib.request.Request(API, headers=basliklar)
        with urllib.request.urlopen(istek, timeout=zaman_asimi) as yanit:
            return json.load(yanit).get("tag_name")
    except Exception:
        pass
    if sys.platform != "win32":   # paketlenmiş Python'da sertifika deposu olmayabilir: sistemin curl'ü
        try:
            cikti = subprocess.run(["/usr/bin/curl", "-fsSL", "--max-time", str(zaman_asimi)]
                                   + sum((["-H", f"{k}: {v}"] for k, v in basliklar.items()), []) + [API],
                                   capture_output=True, timeout=zaman_asimi + 2)
            if cikti.returncode == 0:
                return json.loads(cikti.stdout).get("tag_name")
        except Exception:
            pass
    return None


def denetle(simdi=None, yol=None, etiket_al=en_son_etiket):
    """{"surum", "yeni", "goster", "adres"}. goster: yeni sürüm var ve son 24 saatte hatırlatılmadı;
    True dönerse hatırlatma zamanı kaydedilir (çağıran bildirimi gösterir)."""
    simdi = time.time() if simdi is None else simdi
    d = oku(yol)
    sonuc = {"surum": SURUM, "yeni": None, "goster": False, "adres": ADRES}
    if d.get("kapali"):
        sonuc["kapali"] = True
        return sonuc
    if "son_denetim" not in d or simdi - d["son_denetim"] >= ARALIK:
        etiket = etiket_al()
        if etiket:
            d["son_etiket"] = etiket
        d["son_denetim"] = simdi   # ağ yoksa da: her açılışta beklememek için ertesi güne kadar sorma
    son = d.get("son_etiket")
    if son and anahtar(son) > anahtar(SURUM):
        sonuc["yeni"] = son.lstrip("vV")
        if "son_hatirlatma" not in d or simdi - d["son_hatirlatma"] >= ARALIK:
            sonuc["goster"] = True
            d["son_hatirlatma"] = simdi
    yaz(d, yol)
    return sonuc


def kapat(yol=None):
    d = oku(yol)
    d["kapali"] = True
    yaz(d, yol)


def main(args):
    if args[:1] == ["--surum"]:
        print(SURUM)
        if "--dosya" in args[1:-1]:
            with open(args[args.index("--dosya") + 1], "w", encoding="utf-8") as f:
                f.write(SURUM)
    elif args[:1] == ["--guncelleme-kapat"]:
        kapat()
    else:
        s = denetle()
        if "--metin" in args:
            if s["goster"]:
                print(f"{s['yeni']}\t{s['surum']}\t{s['adres']}")
        else:
            print(json.dumps(s, ensure_ascii=False))
    return 0
