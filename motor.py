#!/usr/bin/env python3
"""PDF Küçült — okunabilirliği koruyarak PDF boyutunu küçülten motor.

Uygulamalar ve Zotero eklentisi tek modla çalışır: "Akıllı sıkıştırma" (iç adı "okuma", en sıkı
seviye). Diğer seviyeler
yalnızca komut satırında (-s) test ve karşılaştırma için durur.

Seviyeler:
  az        Kayıpsız: yapı yeniden paketlenir, JPEG'ler kayıpsız optimize edilir,
            siyah-beyaz taramalar kayıpsız JBIG2'ye çevrilir. Görüntü değişmez.
  onerilen  Dengeli: gereğinden yüksek çözünürlüklü görüntüler 200 DPI'ya indirilir,
            JPEG kalitesi 75; renksiz renkli taramalar griye çevrilir.
  asiri     En küçük: metin ağırlıklı gri/renkli taramalar 300 DPI siyah-beyaz
            JBIG2'ye çevrilir, fotoğraflar 150 DPI / kalite 60.
  okuma     Akıllı sıkıştırma: yalnızca okunabilirlik korunur. Bütün görüntüler gri
            (en çok 150 DPI), resimli tarama sayfaları da noktalı siyah-beyaz,
            çift sayfa taramalar ikiye bölünüp kenarları kırpılır.

Güvenlik: orijinal dosyaya asla yazılmaz; her lossy işlemden sonra sayfalar
düşük çözünürlükte render edilip orijinalle karşılaştırılır, sorun görülürse
kayıpsız sonuca geri dönülür. Sonuç orijinalden büyükse orijinal kopyalanır.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import copy
import hashlib
import io
import json
import logging
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
import zlib
from decimal import Decimal
from dataclasses import dataclass

import numpy as np
import pikepdf
from pikepdf import Name, PdfImage
from PIL import Image, ImageChops, ImageFilter

Image.MAX_IMAGE_PIXELS = None
logging.getLogger("fontTools").setLevel(logging.CRITICAL)

MOTOR_SURUM = "2.0"
ISARET = "/PDFKucult"  # küçültülmüş PDF'in bilgi sözlüğüne yazılan işaret: "sürüm;seviye;orijinal bayt"
OUT_SUFFIX = " (küçük)"
OUT_DIR_SUFFIX = " (küçültülmüş)"


@dataclass(frozen=True)
class Preset:
    name: str
    lossy: bool
    max_dpi: float = 0          # renkli/gri görüntüler bu DPI'nın üstündeyse indirilir
    jpeg_q: int = 0
    gray_chroma: int = 0        # renk sapması bu değerin altındaysa griye çevrilir
    to_bitonal: bool = False    # metin ağırlıklı taramaları siyah-beyaza çevir
    bitonal_dpi: float = 300
    bitonal_max_dpi: float = 0  # siyah-beyaz görüntüler bunun üstündeyse bitonal_dpi'ya indirilir
    graphic_max_dpi: float = 450  # kayıpsız grafikler (şema, çizelge) bunun üstündeyse 300 DPI'a indirilir
    gray: bool = False          # bütün görüntüler griye (grafikler dahil)
    halftone: bool = False      # resimli tarama sayfaları da siyah-beyaz (resimler noktalı)
    split_spreads: bool = False  # çift sayfa taramaları ikiye böl, siyah kenarları kırp


PRESETS = {
    "az": Preset("az", lossy=False),
    "onerilen": Preset("onerilen", lossy=True, max_dpi=200, jpeg_q=75, gray_chroma=10),
    "asiri": Preset("asiri", lossy=True, max_dpi=150, jpeg_q=60, gray_chroma=24,
                    to_bitonal=True, bitonal_max_dpi=320),
    "okuma": Preset("okuma", lossy=True, max_dpi=150, jpeg_q=50, gray_chroma=256, to_bitonal=True,
                    bitonal_max_dpi=320, graphic_max_dpi=360, gray=True, halftone=True, split_spreads=True),
}
BIG_CMYK_ICC = 200_000     # bundan büyük CMYK renk profilleri (baskı profilleri) DeviceCMYK olur

IS_WIN = sys.platform == "win32"


def _tool_dirs():
    """Yardımcı programların arandığı yerler: paketlenmiş uygulamanın 'bin' klasörü önce."""
    dirs = []
    if os.environ.get("PDFKUCULT_BIN"):
        dirs.append(os.environ["PDFKUCULT_BIN"])
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    dirs += [os.path.join(base, "bin"), os.path.join(getattr(sys, "_MEIPASS", base), "bin")]
    return dirs


def find_tool(name):
    exe = name + (".exe" if IS_WIN else "")
    for d in _tool_dirs():
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    found = shutil.which(exe)
    if found:
        return found
    for d in ("/opt/homebrew/bin", "/usr/local/bin"):
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    return None


TOOLS = {t: find_tool(t) for t in ("jbig2", "jpegtran", "tesseract")}
_TOOL_OK = {}

if IS_WIN:
    # Eksik DLL vb. durumlarda Windows'un ekrana açtığı (ve kapatılana kadar bekleyen) hata
    # pencerelerini kapat; alt süreçler de bu ayarı devralır -> program hata koduyla çıkar.
    try:
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    except Exception:
        pass


def tool_ok(name):
    """Yardımcı programı küçük bir deneme işiyle bir kez sınar (eksik kütüphane vb. -> kullanma)."""
    if name not in _TOOL_OK:
        ok = False
        if TOOLS.get(name):
            try:
                img = Image.new("1", (64, 64), 1)
                if name == "jbig2":
                    with tempfile.TemporaryDirectory() as td:
                        p = os.path.join(td, "t.png")
                        img.save(p)
                        r = _run([TOOLS[name], "-p", "-d", p], capture_output=True, timeout=30)
                else:
                    b = io.BytesIO()
                    img.convert("L").save(b, "JPEG")
                    r = _run([TOOLS[name], "-copy", "none", "-optimize"], input=b.getvalue(),
                             capture_output=True, timeout=30)
                ok = r.returncode == 0 and bool(r.stdout)
            except Exception:
                ok = False
        _TOOL_OK[name] = ok
    return _TOOL_OK[name]


def _run(cmd, **kw):
    # Windows'ta yardımcı programlar için siyah konsol penceresi açılmasın
    if IS_WIN:
        kw.setdefault("creationflags", 0x08000000)  # CREATE_NO_WINDOW
    return subprocess.run(cmd, **kw)


# ---------------------------------------------------------------- yardımcılar

def _mul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D,
            e * A + f * C + E, e * B + f * D + F)


def _inherited(page_obj, key):
    node, depth = page_obj, 0
    while node is not None and depth < 50:
        if key in node:
            return node[key]
        node = node.get("/Parent")
        depth += 1
    return None


def _num_components(cs):
    """Desteklenen renk uzayı için bileşen sayısı, aksi halde None."""
    if isinstance(cs, Name):
        return {"/DeviceGray": 1, "/DeviceRGB": 3, "/DeviceCMYK": 4,
                "/CalGray": 1, "/CalRGB": 3}.get(str(cs))
    if isinstance(cs, pikepdf.Array) and len(cs) >= 2:
        kind = str(cs[0])
        if kind == "/ICCBased":
            return int(cs[1].get("/N", 0)) or None
        if kind == "/CalGray":
            return 1
        if kind == "/CalRGB":
            return 3
    return None


def _key_of(v):
    if isinstance(v, pikepdf.Object) and v.is_indirect:
        return ("ref",) + tuple(v.objgen)
    if isinstance(v, pikepdf.Array):
        return tuple(_key_of(x) for x in v)
    if isinstance(v, pikepdf.Dictionary):
        return tuple((k, _key_of(v[k])) for k in sorted(v.keys()))
    return repr(v)


# ---------------------------------------------------------------- görüntü bulma

def dedupe_streams(pdf) -> int:
    """Birebir aynı akışları (her bölüme yeniden gömülmüş fontlar, her sayfadaki logo vb.)
    tek nesneye indirger; tüm referansları ilk kopyaya yönlendirir."""
    canon, remap = {}, {}
    for o in pdf.objects:
        if not isinstance(o, pikepdf.Stream):
            continue
        try:
            raw = o.read_raw_bytes()
        except Exception:
            continue
        if len(raw) < 512:
            continue
        meta = tuple((k, _key_of(o[k])) for k in sorted(o.keys()) if k != "/Length")
        first = canon.setdefault((hashlib.sha1(raw).digest(), meta), o)
        if first.objgen != o.objgen:
            remap[o.objgen] = first
    if not remap:
        return 0

    def fix(c, depth=0):
        if depth > 40:
            return
        if isinstance(c, pikepdf.Array):
            for i in range(len(c)):
                v = c[i]
                if not isinstance(v, pikepdf.Object):
                    continue
                if v.is_indirect:
                    if v.objgen in remap:
                        c[i] = remap[v.objgen]
                elif isinstance(v, (pikepdf.Array, pikepdf.Dictionary)):
                    fix(v, depth + 1)
        else:
            for k in list(c.keys()):
                v = c[k]
                if not isinstance(v, pikepdf.Object):
                    continue
                if v.is_indirect:
                    if v.objgen in remap:
                        c[k] = remap[v.objgen]
                elif isinstance(v, (pikepdf.Array, pikepdf.Dictionary)):
                    fix(v, depth + 1)

    for o in list(pdf.objects):
        if isinstance(o, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
            fix(o)
    fix(pdf.trailer)
    return len(remap)


def is_signed(pdf) -> bool:
    """Dijital imzalı mı? (değiştirmek imzayı geçersiz kılar)"""
    try:
        af = pdf.Root.get("/AcroForm")
        if af is not None and int(af.get("/SigFlags", 0)) & 1:
            return True
        for f in (af.get("/Fields") or []) if af is not None else []:
            if f.get("/FT") == Name.Sig and "/V" in f:
                return True
    except Exception:
        pass
    return False


def find_image_uses(pdf, page_cov_out=None):
    """Her görüntü nesnesi için sayfadaki en düşük etkin DPI'yı ve kullanıldığı sayfaları bulur."""
    info = {}  # objgen -> {"obj", "dpi", "pages", "scan"}
    page_cov = {}  # sayfa -> görüntülerin kapladığı alan oranı
    page_area = {}
    page_long = {}  # sayfa -> uzun kenar (inç)

    def walk(stream_or_page, res, ctm, pageno, depth, seen_forms):
        if res is None or "/XObject" not in res or depth > 12:
            return
        xobjs = res.XObject
        try:
            ops = pikepdf.parse_content_stream(stream_or_page, "q Q cm Do")
        except Exception:
            return
        stack = []
        for operands, op in ops:
            op = str(op)
            if op == "q":
                stack.append(ctm)
            elif op == "Q":
                if stack:
                    ctm = stack.pop()
            elif op == "cm" and len(operands) == 6:
                try:
                    ctm = _mul(tuple(float(v) for v in operands), ctm)
                except Exception:
                    pass
            elif op == "Do" and operands:
                name = str(operands[0])
                if name not in xobjs:
                    continue
                x = xobjs[name]
                if not isinstance(x, pikepdf.Stream):
                    continue
                st = x.get("/Subtype")
                if st == Name.Image and x.is_indirect:
                    a, b, c, d = ctm[:4]
                    wpt, hpt = math.hypot(a, b), math.hypot(c, d)
                    dpi = None
                    if wpt > 0.5 and hpt > 0.5:
                        dpi = min(int(x.Width) * 72 / wpt, int(x.Height) * 72 / hpt)
                    page_cov[pageno] = page_cov.get(pageno, 0) + min(1.0, abs(a * d - b * c) / page_area[pageno])
                    rec = info.get(x.objgen)
                    if rec is None:
                        info[x.objgen] = {"obj": x, "dpi": dpi, "pages": {pageno}}
                    else:
                        # bilinmeyen bir kullanım varsa DPI'ya güvenme (çözünürlük düşürülmez)
                        rec["pages"].add(pageno)
                        rec["dpi"] = None if dpi is None or rec["dpi"] is None else min(rec["dpi"], dpi)
                elif st == Name.Form:
                    key = (x.objgen, pageno)
                    if key in seen_forms:
                        continue
                    seen_forms.add(key)
                    fm = x.get("/Matrix")
                    m = tuple(float(v) for v in fm) if fm is not None and len(fm) == 6 else (1, 0, 0, 1, 0, 0)
                    walk(x, x.get("/Resources") or res, _mul(m, ctm), pageno, depth + 1, seen_forms)

    for i, page in enumerate(pdf.pages):
        res = _inherited(page.obj, "/Resources")
        if res is None or "/XObject" not in res:
            continue
        try:
            mb = [float(v) for v in _inherited(page.obj, "/MediaBox")]
            page_area[i] = max(1.0, abs((mb[2] - mb[0]) * (mb[3] - mb[1])))
            page_long[i] = max(abs(mb[2] - mb[0]), abs(mb[3] - mb[1])) / 72
        except Exception:
            page_area[i] = 612 * 792
        walk(page, res, (1, 0, 0, 1, 0, 0), i, 0, set())
    for rec in info.values():
        # tarama sayfası: sayfanın en az yarısı görüntülerle kaplı
        rec["scan"] = all(page_cov.get(p, 0) >= 0.5 for p in rec["pages"])
        # Bazı tarayıcı/servisler sayfayı "piksel/72" boyutunda (55-90 cm) kaydeder; DPI düşük
        # görünür ve görüntü küçültülmez. A3'ten büyük sayfada 160 DPI'nın altındaki tarama
        # A4 sayılır (gerçek sayfa daha küçükse gerçek DPI daha yüksektir: tahmin temkinli).
        if rec["scan"] and rec["dpi"] and rec["dpi"] < 160 and \
                min(page_long.get(p, 0) for p in rec["pages"]) > 17:
            o = rec["obj"]
            rec["dpi"] = max(rec["dpi"], max(int(o.Width), int(o.Height)) / 11.69)
    if page_cov_out is not None:
        page_cov_out.update(page_cov)
    return info


# ---------------------------------------------------------------- görüntü analizi

def _sample(img, maxside=700):
    w, h = img.size
    f = max(1, int(max(w, h) / maxside))
    return img.reduce(f) if f > 1 else img


def chroma_p99(rgb) -> float:
    a = np.asarray(_sample(rgb), dtype=np.int16)
    ch = np.maximum(np.maximum(abs(a[..., 0] - a[..., 1]), abs(a[..., 1] - a[..., 2])), abs(a[..., 0] - a[..., 2]))
    return float(np.percentile(ch, 99))


def normalize_background(gray):
    """Kağıt rengini/gölgeleri düzeltir: her bölgeyi yerel kağıt parlaklığına böler."""
    g = np.asarray(gray, dtype=np.float32)
    h, w = g.shape
    f = max(1, int(round(max(w, h) / 160)))
    small = gray.reduce(f) if f > 1 else gray
    bg = small.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.BoxBlur(3)).resize((w, h), Image.BILINEAR)
    b = np.maximum(np.asarray(bg, dtype=np.float32), 40.0)
    return np.clip(g * (255.0 / b), 0, 255)


def is_textlike(gray) -> bool:
    """Gri tarama metin ağırlıklı mı (fotoğraf/gri ton alanı az mı)?
    Fotoğrafla çekilmiş sayfalarda harfler de gridir (ara ton %10-13); bu yüzden toplam ara
    ton oranı yanında sayfa 8x8 bölgeye ayrılır: fotoğraf/resim olan bölgede oran %35'i aşar."""
    n = normalize_background(_sample(gray, 900))
    m = (n > 70) & (n < 190)
    dark = np.mean(n <= 70)
    if not (m.mean() < 0.15 and dark < 0.30 and np.mean(n < 190) > 0.02):  # sayfada mürekkep var
        return False
    h, w = m.shape
    th, tw = max(1, h // 8), max(1, w // 8)
    return max(m[y:y + th, x:x + tw].mean() for y in range(0, h - th + 1, th) for x in range(0, w - tw + 1, tw)) < 0.35


def similarity(a, b) -> float:
    """İki görüntünün küçültülmüş hallerinin korelasyonu (1 = aynı yapı)."""
    sz = (max(8, a.width // 8), max(8, a.height // 8))
    x = np.asarray(a.convert("L").resize(sz, Image.BOX), np.float32).ravel()
    y = np.asarray(b.convert("L").resize(sz, Image.BOX), np.float32).ravel()
    if x.std() < 1 or y.std() < 1:
        return 1.0 if abs(x.mean() - y.mean()) < 30 else 0.0
    return float(np.corrcoef(x, y)[0, 1])


_CMS = {}


def cmyk_profile_path():
    """İşletim sistemiyle gelen CMYK renk profili (macOS: Generic CMYK, Windows: RSWOP)."""
    cands = ["/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc"]
    windir = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if windir:
        cands.append(os.path.join(windir, "System32", "spool", "drivers", "color", "RSWOP.icm"))
    return next((c for c in cands if os.path.exists(c)), None)


def cmyk_to_rgb(img, icc=None):
    try:
        from PIL import ImageCms
        key = hashlib.sha1(icc).hexdigest() if icc else "generic"
        if key not in _CMS:
            if icc:
                src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            else:
                p = cmyk_profile_path()
                if not p:
                    return None
                src = ImageCms.getOpenProfile(p)
            _CMS[key] = ImageCms.buildTransform(src, ImageCms.createProfile("sRGB"), "CMYK", "RGB",
                                                renderingIntent=ImageCms.Intent.PERCEPTUAL)
        return ImageCms.applyTransform(img, _CMS[key])
    except Exception:
        return None


def is_photographic(img) -> bool:
    s = _sample(img, 256).convert("RGB" if img.mode not in ("L",) else "L")
    return len(s.getcolors(1 << 24) or ()) > 600 if s.mode == "RGB" else len(set(s.getdata())) > 96


def otsu(a) -> float:
    hist = np.bincount(a.astype(np.uint8).ravel(), minlength=256).astype(np.float64)
    p = hist / hist.sum()
    w = np.cumsum(p)
    mu = np.cumsum(p * np.arange(256))
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (mu[-1] * w - mu) ** 2 / (w * (1 - w))
    return float(np.nanargmax(s))


def to_bitonal(gray, scale):
    if abs(scale - 1) > 0.02:
        gray = gray.resize((max(1, round(gray.width * scale)), max(1, round(gray.height * scale))), Image.LANCZOS)
    n = normalize_background(gray)
    t = min(max(otsu(n), 100), 215)
    return Image.fromarray(np.where(n > t, 255, 0).astype(np.uint8)).convert("1", dither=Image.Dither.NONE)


def to_halftone(gray, scale, block=48):
    """Resimli tarama sayfası -> siyah-beyaz: yazı bölgeleri eşiklenir (keskin), ara tonlu
    (resim) bölgeler hata yayma ile noktalanır (gazete baskısı gibi). (görüntü, resim var mı) döner."""
    if abs(scale - 1) > 0.02:
        gray = gray.resize((max(1, round(gray.width * scale)), max(1, round(gray.height * scale))), Image.LANCZOS)
    n = normalize_background(gray)
    t = min(max(otsu(n), 100), 215)
    text = n > t
    h, w = n.shape
    # resim bölgesi: kağıt olmayan piksel oranı yüksek bloklar (yazıda en çok ~%30)
    bh, bw = -(-h // block), -(-w // block)
    pad = np.zeros((bh * block, bw * block), bool)
    pad[:h, :w] = n < 190
    pic = pad.reshape(bh, block, bw, block).mean(axis=(1, 3)) > 0.5
    if not pic.any():
        return Image.fromarray(text).convert("1"), False
    # resimler dikdörtgendir: her resim bölgesini kapsayan dikdörtgen bütünüyle resim sayılır
    # (açık tonlu yerleri "kağıt" sanılıp delik kalmasın)
    seen = np.zeros_like(pic)
    filled = np.zeros_like(pic)
    for y0, x0 in zip(*np.nonzero(pic)):
        if seen[y0, x0]:
            continue
        stack, ys, xs = [(y0, x0)], [], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            ys.append(y)
            xs.append(x)
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= yy < bh and 0 <= xx < bw and pic[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    stack.append((yy, xx))
        if len(ys) >= 4:  # tek tük blok (koyu başlık, leke) resim değildir
            # bir blok pay: resim kenarı blok ızgarasının ortasına denk gelebilir
            filled[max(0, min(ys) - 1):max(ys) + 2, max(0, min(xs) - 1):max(xs) + 2] = True
    if not filled.any():
        return Image.fromarray(text).convert("1"), False
    pic_full = np.kron(filled, np.ones((block, block), bool))[:h, :w]
    flat = np.where(n > 235, 255, n).astype(np.uint8)  # kağıt tam beyaz: resim kenarında benek olmasın
    dith = np.asarray(Image.fromarray(flat).convert("1"), dtype=bool)  # Floyd-Steinberg
    return Image.fromarray(np.where(pic_full, dith, text)).convert("1"), True


# ---------------------------------------------------------------- kodlayıcılar

def despeckle(img1):
    """Tarama lekelerini siler: en çok 2 siyah komşusu olan siyah pikseller (tek/çift benekler,
    harf kenarlarındaki pürüz). Noktalı (raster) resimlerde beneklerin çoğu bu türdendir;
    oran yüksekse görüntü olduğu gibi döner. Değişiklik yoksa None."""
    b = ~np.asarray(img1, dtype=bool)  # True = siyah
    black = int(b.sum())
    if not black:
        return None
    p = np.pad(b, 1)
    h, w = b.shape
    nb = sum(p[1 + dy:1 + dy + h, 1 + dx:1 + dx + w].astype(np.uint8)
             for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
    speck = b & (nb <= 2)
    ratio = speck.sum() / black
    if not 0.01 <= ratio <= 0.25:
        return None
    return Image.fromarray(~(b & ~speck)).convert("1")


def enc_bilevel(img1):
    """Siyah-beyaz görüntüyü kayıpsız kodlar: JBIG2 (varsa) ya da Flate. (veri, filtre) döner."""
    if tool_ok("jbig2"):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "i.png")
            img1.save(p)
            r = _run([TOOLS["jbig2"], "-p", "-d", p], capture_output=True, timeout=600)
            if r.returncode == 0 and r.stdout:
                return r.stdout, Name.JBIG2Decode
    # PIL '1' görüntü satır satır paketlenmiş bitler: 1 = beyaz, PDF DeviceGray 1 bit ile aynı
    return zlib.compress(img1.tobytes(), 9), Name.FlateDecode


try:  # mozjpeg'in kayıpsız paketleyicisi: aynı pikseller, ~%5 daha küçük JPEG
    import mozjpeg_lossless_optimization as _mozjpeg
except Exception:
    _mozjpeg = None


def enc_jpeg(img, q) -> bytes:
    b = io.BytesIO()
    img.save(b, "JPEG", quality=q, optimize=True)
    data = b.getvalue()
    return jpegtran_lossless(data) or data


def jpegtran_lossless(data) -> bytes | None:
    """JPEG'i kaliteye dokunmadan yeniden paketler (mozjpeg; yoksa jpegtran). Kazanç yoksa None."""
    if _mozjpeg is not None:
        try:
            out = _mozjpeg.optimize(data)
            return out if out and len(out) < len(data) else None
        except Exception:
            pass
    if not tool_ok("jpegtran"):
        return None
    try:
        r = _run([TOOLS["jpegtran"], "-copy", "none", "-optimize"], input=data,
                           capture_output=True, timeout=300)
        return r.stdout if r.returncode == 0 and r.stdout else None
    except Exception:
        return None


# ---------------------------------------------------------------- tek görüntü

@dataclass
class Job:
    objgen: tuple
    dpi: float | None
    width: int
    height: int
    bpc: int
    ncomp: int
    filt: str
    has_decode: bool
    scan: bool = False            # tam sayfa tarama görüntüsü mü
    icc: bytes | None = None      # ICCBased renk uzayının profili
    raw: bytes | None = None      # DCT ise ham JPEG
    pil: Image.Image | None = None
    indexed: bool = False         # paletli (/Indexed) görüntü
    jpx: bool = False             # JPEG 2000
    inverted: bool = False        # 1 bit ve /Decode [1 0]: ham değerler görünenin tersi


@dataclass
class Result:
    objgen: tuple
    data: bytes
    filt: Name
    width: int
    height: int
    bpc: int
    colorspace: object = None     # None = değiştirme
    drop_decode: bool = False
    kind: str = ""


def pil_raw(obj):
    """Görüntünün ham örnekleri: /Decode (ters çevirme) ve saydamlık maskesi UYGULANMADAN.
    Yeni pikepdf varsayılan olarak ikisini de uygular; /Decode yeni görüntüde de kalacağı için
    uygulanmış veriyle kodlamak görüntüyü iki kez ters çevirir. (görüntü, ham_mı) döner."""
    pim = PdfImage(obj)
    try:
        return pim.as_pil_image(apply_decode_array=False, apply_mask=False), True
    except TypeError:  # eski pikepdf: seçenek yok (davranış sürüme göre değişebilir)
        pass
    # 1 bit Flate/sıkıştırmasız görüntünün ham bitlerini kendimiz çözelim (qpdf filtreyi açar,
    # /Decode uygulanmaz). PIL '1': bit 1 = beyaz = PDF DeviceGray örneği 1.
    filt = obj.get("/Filter")
    if isinstance(filt, pikepdf.Array) and len(filt) == 1:
        filt = filt[0]
    if (int(obj.get("/BitsPerComponent", 0)) == 1 and _num_components(obj.get("/ColorSpace")) == 1
            and (filt is None or str(filt) == "/FlateDecode")):
        try:
            w, h = int(obj.Width), int(obj.Height)
            data = obj.read_bytes()
            if len(data) >= ((w + 7) // 8) * h:
                return Image.frombytes("1", (w, h), data), True
        except Exception:
            pass
    return pim.as_pil_image(), False


_GENERAL_FILTERS = {"/FlateDecode"}


def plan_job(obj, dpi, scan=False):
    """Ana iş parçacığında: görüntünün işlenebilir olup olmadığına karar verir ve veriyi hazırlar."""
    if obj.get("/ImageMask", False) or isinstance(obj.get("/Mask"), pikepdf.Array):
        return None
    filt = obj.get("/Filter")
    chained = False
    if isinstance(filt, pikepdf.Array):
        names = [str(f) for f in filt]
        if len(names) > 1:
            # ör. [/FlateDecode /DCTDecode] (iLovePDF, ABBYY): genel filtreler açılınca JPEG kalır
            if names[-1] != "/DCTDecode" or not set(names[:-1]) <= _GENERAL_FILTERS:
                return None
            chained = True
        filt = names[-1] if names else None
    filt = str(filt) if filt is not None else ""
    jpx = filt == "/JPXDecode"
    if jpx and int(obj.get("/SMaskInData", 0) or 0) != 0:
        return None  # saydamlık JPEG 2000 verisinin içinde
    sm = obj.get("/SMask")
    if sm is not None and "/Matte" in sm:
        return None
    cs = obj.get("/ColorSpace")
    indexed = isinstance(cs, pikepdf.Array) and len(cs) >= 4 and str(cs[0]) == "/Indexed"
    if indexed:
        ncomp = _num_components(cs[1])  # paletin temel renk uzayı
        if ncomp not in (1, 3):
            return None
    elif jpx and cs is None:
        ncomp = 0  # renk uzayı JPEG 2000 verisinde; çözünce belli olur
    else:
        ncomp = _num_components(cs)
    bpc = 8 if jpx else int(obj.get("/BitsPerComponent", 0))
    w, h = int(obj.Width), int(obj.Height)
    if ncomp is None or w * h < 40_000:
        return None
    if indexed:
        if bpc not in (1, 2, 4, 8):
            return None
        bpc = 8  # çözülmüş (paletten açılmış) görüntü üzerinde çalışılır
    elif bpc not in (1, 8) or (bpc == 1 and ncomp != 1):
        return None
    job = Job(obj.objgen, dpi, w, h, bpc, ncomp, filt, "/Decode" in obj, scan, indexed=indexed, jpx=jpx)
    if isinstance(cs, pikepdf.Array) and str(cs[0]) == "/ICCBased" and ncomp == 4:
        try:
            job.icc = cs[1].read_bytes()
        except Exception:
            pass
    if filt == "/DCTDecode":
        job.raw = obj.read_raw_bytes()
        if chained:  # qpdf zinciri yarıda açamıyor: Flate katmanlarını kendimiz açarız
            parms = obj.get("/DecodeParms")
            if isinstance(parms, pikepdf.Array):
                parms = list(parms)[:-1]  # son (DCT) parametreleri JPEG'i etkilemez (ör. iLovePDF /Quality)
            elif parms is not None:
                return None
            if any(p is not None and len(p) for p in parms or ()):
                return None
            for _ in range(len(obj.Filter) - 1):
                job.raw = zlib.decompress(job.raw)
    else:
        job.pil, raw_ok = pil_raw(obj)
        if bpc == 1 and job.has_decode and not raw_ok:
            return None  # ters çevirme (/Decode) uygulanmış mı bilinmiyor: dokunma
        if bpc == 1 and job.has_decode:
            try:
                dec = [float(v) for v in obj.Decode]
                job.inverted = dec == [1.0, 0.0]
                job.has_decode = dec != [0.0, 1.0]  # [0 1] varsayılanın aynısı (ör. ScanSnap)
            except Exception:
                pass
    return job


def run_job(job: Job, P: Preset, cur_len: int) -> Result | None:
    """İş parçacığında: görüntüyü yeniden kodlar; kazanç yoksa None döner."""
    best = None

    def offer(res, need):
        nonlocal best
        if len(res.data) < cur_len * need and (best is None or len(res.data) < len(best.data)):
            best = res

    # ---- siyah-beyaz (1 bit) görüntüler
    if job.bpc == 1:
        img = job.pil.convert("1") if job.pil.mode != "1" else job.pil
        kind = "jbig2"
        # /Decode [1 0] (ters çevrilmiş tarama): kayıplı işlemler görünen hal üzerinde yapılır,
        # sonra ham değerlere geri çevrilir; /Decode yerinde kalır
        if P.lossy and job.inverted:
            img = ImageChops.invert(img)
        if P.lossy and (not job.has_decode or job.inverted):
            clean = despeckle(img)
            if clean is not None:
                img, kind = clean, "leke-temizleme"
            w, h = img.size
            if P.bitonal_max_dpi and job.dpi and job.dpi > P.bitonal_max_dpi:
                s = P.bitonal_dpi / job.dpi
                g = img.convert("L").resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)
                img = g.point(lambda v: 255 if v > 140 else 0).convert("1", dither=Image.Dither.NONE)
        if P.lossy and job.inverted:
            img = ImageChops.invert(img)
        data, filt = enc_bilevel(img)
        offer(Result(job.objgen, data, filt, img.width, img.height, 1, kind=kind), 0.97)
        return best

    # ---- 8 bit görüntüler
    if job.filt == "/DCTDecode":
        opt = jpegtran_lossless(job.raw)
        if opt:
            offer(Result(job.objgen, opt, Name.DCTDecode, job.width, job.height, 8, kind="jpeg-kayipsiz"), 0.99)
    if not P.lossy or job.has_decode:
        return best

    if job.raw is not None:
        src = Image.open(io.BytesIO(job.raw))
        adobe = "adobe" in src.info
        src.load()
    else:
        src, adobe = job.pil, False
    cs = None
    photo = None
    if job.indexed or job.jpx:
        # paletli / JPEG 2000: düz RGB ya da gri JPEG'e dönüşür (renk uzayı da değişir)
        if src.mode in ("P", "PA"):
            # palet en çok 256 renk: 256 renge indirilmiş fotoğraf paletin çoğunu kullanır,
            # grafik/şema ise birkaç düzine renk
            used = src.getcolors(256)
            photo = used is not None and len(used) >= 96
            src = src.convert("RGB")
        if src.mode in ("L", "LA", "I", "I;16"):
            img, cs = src.convert("L"), Name.DeviceGray
        elif src.mode in ("RGB", "RGBA"):
            img, cs = src.convert("RGB"), Name.DeviceRGB
        elif src.mode == "CMYK":  # baskı için CMYK JPEG 2000 (InDesign/Acrobat çıktıları)
            img = cmyk_to_rgb(src, job.icc)
            if img is None:
                return best
            cs = Name.DeviceRGB
        else:
            return best
    elif job.ncomp == 4:
        # CMYK (baskı) -> sRGB, renk profiliyle. Pillow, Adobe işaretli JPEG'leri okurken
        # değerleri tersine çevirir; PDF okuyucular ise ham değerleri kullanır -> geri çevir.
        if src.mode != "CMYK":
            return best
        if adobe:
            src = ImageChops.invert(src)
        img = cmyk_to_rgb(src, job.icc)
        if img is None:
            return best
        cs = Name.DeviceRGB
    elif job.ncomp == 3:
        img = src.convert("RGB")
    else:
        img = src.convert("L")
    if job.raw is None and not job.jpx and not (photo if photo is not None else is_photographic(img)):
        # grafik/çizim: JPEG'e çevirmek yazıları bozar, kayıpsız kalsın. Ama aşırı çözünürlükte
        # (ör. 3000 DPI'lık şekil) kayıpsız olarak 300 DPI'a indirilir.
        kind = None
        if P.gray and img.mode == "RGB":
            img, cs, kind = img.convert("L"), Name.DeviceGray, "grafik-gri"
        if job.dpi and job.dpi > P.graphic_max_dpi:
            s = P.bitonal_dpi / job.dpi
            img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
            kind = "grafik-kucultme"
        if kind:
            offer(Result(job.objgen, zlib.compress(img.tobytes(), 9), Name.FlateDecode, img.width, img.height,
                         8, cs, kind=kind), 0.9)
        return best

    if img.mode == "RGB" and chroma_p99(img) < P.gray_chroma:
        img, cs = img.convert("L"), Name.DeviceGray

    # metin ağırlıklı tarama -> siyah-beyaz JBIG2
    if P.to_bitonal and job.scan and img.mode in ("L", "RGB") and job.dpi and job.dpi >= (100 if P.halftone else 140):
        gray = img if img.mode == "L" else img.convert("L")
        if is_textlike(gray):
            scale = min(P.bitonal_dpi / job.dpi, 2.0)
            bw = to_bitonal(gray, scale)
            # ışık eğimi/gölge (fotoğrafla çekilmiş sayfa) fark sayılmasın: kağıdı düzeltilmiş
            # gri ile karşılaştır (siyah-beyaz da bunun üzerinden üretiliyor)
            flat = Image.fromarray(normalize_background(gray).astype(np.uint8))
            if similarity(flat, bw) >= 0.95:
                data, filt = enc_bilevel(bw)
                offer(Result(job.objgen, data, filt, bw.width, bw.height, 1,
                             Name.DeviceGray, drop_decode=True, kind="tarama->jbig2"), 0.9)
            if best is not None and best.kind == "tarama->jbig2":
                return best
        if P.halftone:
            # okuma kopyası: resimli sayfa da siyah-beyaz; yazılar keskin, resimler noktalı
            bw, has_pic = to_halftone(gray, min(P.bitonal_dpi / job.dpi, 2.0))
            data, filt = enc_bilevel(bw)
            kind = "tarama->noktali" if has_pic else "tarama->jbig2"
            offer(Result(job.objgen, data, filt, bw.width, bw.height, 1,
                         Name.DeviceGray, drop_decode=True, kind=kind), 0.9)
            if best is not None and best.kind == kind:
                return best

    downsampled = False
    if job.dpi and job.dpi > P.max_dpi * 1.2:
        s = P.max_dpi / job.dpi
        img = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
        downsampled = True
    data = enc_jpeg(img, P.jpeg_q)
    kind = ("cmyk->rgb" if job.ncomp == 4 else "paletli->jpeg" if job.indexed else "jpeg2000->jpeg" if job.jpx
            else "jpeg-kucultme" if downsampled else "jpeg-yeniden")
    offer(Result(job.objgen, data, Name.DCTDecode, img.width, img.height, 8, cs, kind=kind),
          0.97 if downsampled or cs is not None else 0.88)
    return best


def apply_result(obj, r: Result):
    obj.write(r.data, filter=r.filt, decode_parms=None, type_check=False)
    obj.Width, obj.Height, obj.BitsPerComponent = r.width, r.height, r.bpc
    if r.colorspace is not None:
        obj.ColorSpace = r.colorspace
    if r.drop_decode and "/Decode" in obj:
        del obj["/Decode"]
    for k in ("/DecodeParms", "/Metadata", "/SMaskInData"):
        if k in obj:
            del obj[k]


# ---------------------------------------------------------------- font alt kümeleme
#
# Word gibi programlar fontların tamamını (binlerce harf) gömebiliyor. Burada her fontla
# gerçekten çizilen harfler bulunur ve font yalnızca onlara indirilir. Harf numaraları
# (GID) korunduğu için sayfa içeriği, metin katmanı, bağlantılar ve etiketler değişmez.

class _Unsafe(Exception):
    pass


def collect_font_usage(pdf):
    """{font objgen: (font dict, kullanılan kodlar kümesi, sayfalar kümesi)}; okunamayan
    içerik varsa None (o zaman fontlara hiç dokunulmaz)."""
    usage = {}

    def add(font, data, page):
        if font is None:
            return
        rec = usage.get(font.objgen)
        if rec is None:
            rec = usage[font.objgen] = (font, set(), set())
        rec[1].add(bytes(data))
        rec[2].add(page)

    def scan(stream, res, cur, page, depth, seen):
        if depth > 15:
            raise _Unsafe()
        if stream.is_indirect:
            key = (stream.objgen, res.objgen if isinstance(res, pikepdf.Object) and res.is_indirect else id(res))
            if key in seen:
                return
            seen.add(key)
        fontres = res.get("/Font") if res is not None else None
        xres = res.get("/XObject") if res is not None else None
        stack = []
        try:
            ops = pikepdf.parse_content_stream(stream, "q Q Tf Tj TJ ' \" Do")
        except Exception:
            raise _Unsafe()
        for operands, op in ops:
            op = str(op)
            if op == "q":
                stack.append(cur)
            elif op == "Q":
                cur = stack.pop() if stack else cur
            elif op == "Tf" and operands:
                f = fontres.get(str(operands[0])) if fontres is not None else None
                if f is not None and not f.is_indirect:
                    raise _Unsafe()
                cur = f
                if f is not None and f.get("/Subtype") == Name.Type3:
                    procs = f.get("/CharProcs") or {}
                    for k in list(procs.keys()):
                        scan(procs[k], f.get("/Resources") or res, None, page, depth + 1, seen)
            elif op in ("Tj", "'", '"') and operands:
                add(cur, operands[-1], page)
            elif op == "TJ" and operands:
                for x in operands[0]:
                    if isinstance(x, pikepdf.String):
                        add(cur, x, page)
            elif op == "Do" and operands and xres is not None:
                x = xres.get(str(operands[0]))
                if isinstance(x, pikepdf.Stream) and x.get("/Subtype") == Name.Form:
                    scan(x, x.get("/Resources") or res, cur, page, depth + 1, seen)
        pats = res.get("/Pattern") if res is not None else None
        if pats is not None:
            for k in list(pats.keys()):
                pt = pats[k]
                if isinstance(pt, pikepdf.Stream):
                    scan(pt, pt.get("/Resources") or res, None, page, depth + 1, seen)

    try:
        for i, page in enumerate(pdf.pages):
            seen = set()
            res = _inherited(page.obj, "/Resources")
            scan(page.obj, res, None, i, 0, seen)
            for annot in page.obj.get("/Annots") or []:
                ap = annot.get("/AP") if isinstance(annot, pikepdf.Dictionary) else None
                if ap is None:
                    continue
                for k in ("/N", "/R", "/D"):
                    a = ap.get(k)
                    streams = [a] if isinstance(a, pikepdf.Stream) else (
                        [a[x] for x in a.keys()] if isinstance(a, pikepdf.Dictionary) else [])
                    for st in streams:
                        if isinstance(st, pikepdf.Stream):
                            scan(st, st.get("/Resources"), None, i, 1, seen)
    except _Unsafe:
        return None
    return usage


def _ttf_program(font):
    """TrueType font programı: ('cid'|'simple', FontFile2 akışı, ek bilgi) ya da None."""
    st = font.get("/Subtype")
    if st == Name.Type0:
        if font.get("/Encoding") not in (Name("/Identity-H"), Name("/Identity-V")):
            return None
        desc = font.get("/DescendantFonts")
        if not desc or desc[0].get("/Subtype") != Name.CIDFontType2:
            return None
        ff = (desc[0].get("/FontDescriptor") or {}).get("/FontFile2")
        return ("cid", ff, desc[0].get("/CIDToGIDMap", Name.Identity)) if ff is not None else None
    if st == Name.TrueType:
        ff = (font.get("/FontDescriptor") or {}).get("/FontFile2")
        return ("simple", ff, font) if ff is not None else None
    return None


_BASE_ENC = {"/WinAnsiEncoding": "cp1252", "/MacRomanEncoding": "mac_roman"}


def _simple_gids(tt, font, codes):
    """Basit TrueType font: kodları olası tüm yollarla GID'e çevirir (fazlası zararsız)."""
    from fontTools import agl
    order = tt.getGlyphOrder()
    name2gid = {n: i for i, n in enumerate(order)}
    tables = tt["cmap"].tables if "cmap" in tt else []
    enc = font.get("/Encoding")
    base, diffs = None, {}
    if isinstance(enc, Name):
        base = _BASE_ENC.get(str(enc))
    elif isinstance(enc, pikepdf.Dictionary):
        base = _BASE_ENC.get(str(enc.get("/BaseEncoding", "")))
        code = 0
        for x in enc.get("/Differences") or []:
            if isinstance(x, Name):
                diffs[code] = str(x)[1:]
                code += 1
            else:
                code = int(x)
    gids = set()
    for c in codes:
        for t in tables:
            for k in (c, 0xF000 + c, 0xF100 + c, 0xF200 + c):
                g = t.cmap.get(k)
                if g in name2gid:
                    gids.add(name2gid[g])
        names = [diffs[c]] if c in diffs else []
        ch = bytes([c]).decode(base or "cp1252", errors="ignore")
        uni = [ord(ch)] if ch else []
        for n in names:
            if n in name2gid:
                gids.add(name2gid[n])
            u = agl.toUnicode(n)
            if u:
                uni.append(ord(u[0]))
        for u in uni:
            for t in tables:
                g = t.cmap.get(u) if t.isUnicode() else None
                if g in name2gid:
                    gids.add(name2gid[g])
            n = agl.UV2AGL.get(u)
            if n in name2gid:
                gids.add(name2gid[n])
    return gids


def subset_fonts(pdf, keep_hinting, only_full, exclude=frozenset()):
    """TrueType fontları kullanılan harflere indirir. (adet, {sayfa: [(objgen, 'font')]}) döner."""
    from fontTools import subset
    from fontTools.ttLib import TTFont

    usage = collect_font_usage(pdf)
    if usage is None:
        return 0, {}
    # formlarda (doldurulabilir alanlar) kullanılan fontlara dokunma: yeni yazı yazılabilir
    protected = set()
    af = pdf.Root.get("/AcroForm")
    if af is not None:
        dr = (af.get("/DR") or {}).get("/Font") or {}
        for k in list(dr.keys()):
            if dr[k].is_indirect:
                protected.add(dr[k].objgen)

    # aynı font programını paylaşan font sözlüklerini grupla
    programs = {}  # FontFile2 objgen -> [ff, kind, [(font, codes, pages, extra)]]
    for og, (font, codes, pages) in usage.items():
        if og in protected:
            continue
        prog = _ttf_program(font)
        if prog is None or not prog[1].is_indirect:
            continue
        kind, ff, extra = prog
        g = programs.setdefault(ff.objgen, [ff, set(), []])
        g[1].add(kind)  # Word aynı programı hem CID hem basit fontla paylaşabilir
        g[2].append((font, codes, pages, kind, extra))

    # programı kullanan ama içerikte hiç görülmeyen font sözlükleri varsa güvenli değil
    referenced = {}
    for o in pdf.objects:
        if isinstance(o, pikepdf.Dictionary) and o.get("/Type") == Name.Font:
            prog = _ttf_program(o)
            if prog is not None and prog[1].is_indirect:
                referenced.setdefault(prog[1].objgen, set()).add(o.objgen)

    count, changes = 0, {}
    for ffog, (ff, kinds, users) in programs.items():
        if ffog in exclude:
            continue
        if referenced.get(ffog, set()) - {u[0].objgen for u in users}:
            continue
        name = str((users[0][0].get("/BaseFont") or ""))
        is_subset = len(name) > 8 and name[7] == "+" and name[1:7].isupper()
        if only_full and is_subset:
            continue
        try:
            data = ff.read_bytes()
            tt = TTFont(io.BytesIO(data), recalcBBoxes=False, recalcTimestamp=False, lazy=False)
            n = tt["maxp"].numGlyphs
            gids = {0}
            for font, codes, pages, kind, extra in users:
                if kind == "cid":
                    cmap = None if extra == Name.Identity else bytes(extra.read_bytes())
                    for b in codes:
                        for i in range(0, len(b) - 1, 2):
                            cid = (b[i] << 8) | b[i + 1]
                            if cmap is None:
                                gids.add(cid)
                            elif 2 * cid + 1 < len(cmap):
                                gids.add((cmap[2 * cid] << 8) | cmap[2 * cid + 1])
                else:
                    allc = set()
                    for b in codes:
                        allc.update(b)
                    gids |= _simple_gids(tt, font, allc)
            gids = {g for g in gids if g < n}
            opts = subset.Options()
            opts.retain_gids = True
            opts.notdef_outline = True
            opts.hinting = keep_hinting
            opts.layout_features = []
            opts.name_IDs = ["*"]
            opts.name_languages = ["*"]
            opts.name_legacy = True
            opts.legacy_cmap = True
            opts.symbol_cmap = True
            opts.glyph_names = "simple" in kinds
            opts.recalc_bounds = False
            opts.recalc_timestamp = False
            opts.prune_unicode_ranges = False
            # PDF okuyucular dizgi (GSUB/GPOS…) ve gömülü bitmap tablolarını kullanmaz
            opts.drop_tables += ["DSIG", "GSUB", "GPOS", "GDEF", "BASE", "JSTF", "MATH", "kern", "morx",
                                 "feat", "meta", "EBDT", "EBLC", "EBSC", "CBDT", "CBLC", "sbix"]
            orig_cmap = cmap_gids = None
            if "simple" in kinds and "cmap" in tt:
                rev = {g: i for i, g in enumerate(tt.getGlyphOrder())}
                orig_cmap = copy.deepcopy(tt["cmap"])
                cmap_gids = [{c: rev[g] for c, g in t.cmap.items() if g in rev} for t in orig_cmap.tables]
            sub = subset.Subsetter(opts)
            sub.populate(gids=sorted(gids))
            sub.subset(tt)
            if orig_cmap is not None:
                # basit fontlarda okuyucu kodu cmap ile bulur: orijinal eşlemeler aynen kalsın.
                # GID'ler korunduğu için eşlemeyi GID üzerinden yeni harf adlarına kur.
                order = tt.getGlyphOrder()
                for t, m in zip(orig_cmap.tables, cmap_gids):
                    t.cmap = {c: order[g] for c, g in m.items() if g < len(order)}
                tt["cmap"] = orig_cmap
            buf = io.BytesIO()
            tt.save(buf)
            new = buf.getvalue()
        except Exception:
            continue
        if len(zlib.compress(new, 6)) >= len(ff.read_raw_bytes()) * 0.95:
            continue
        ff.write(new)
        ff.Length1 = len(new)
        for k in ("/Length2", "/Length3"):
            if k in ff:
                del ff[k]
        count += 1
        for font, codes, pages, kind, extra in users:
            for pg in pages:
                changes.setdefault(pg, []).append((ffog, "font"))
    return count, changes


# ---------------------------------------------------------------- vektör çizimler
#
# "Yazdır > PDF" gibi araçlar metni harf harf eğri olarak (ve 6 ondalıkla) yazabiliyor:
# sayfa başına yüzlerce KB. İki çözüm:
#  - yuvarlama (Önerilen/Aşırı): yalnızca çizim komutlarının (m l c v y re) sayıları 0,01'e
#    yuvarlanır; dönüşüm matrisleri ve metin konumları değişmez. Sayfa birebir kontrol edilir.
#  - taramaya çevirme (Aşırı): metni olmayan, çizim ağırlıklı, siyah-beyaz sayfalar 300 DPI
#    siyah-beyaz görüntüye çevrilir; ardından OCR aranabilir metin ekler.

PATH_OPS = frozenset({"m", "l", "c", "v", "y", "re"})
_Q = Decimal("0.01")


def _round_num(v):
    if not isinstance(v, Decimal):
        return v
    x = v.quantize(_Q)
    return int(x) if x == x.to_integral_value() else Decimal(format(x, "f").rstrip("0"))


def round_paths(pdf, exclude=frozenset()):
    """Büyük çizim akışlarındaki koordinatları yuvarlar. {sayfa: [(objgen, 'vektör')]} döner."""
    changes = {}
    seen_forms = set()

    def rewrite(target):
        """target: Page ya da Form XObject. Kazanç varsa yeni içerik baytları, yoksa None."""
        try:
            raw_len = (sum(len(c.read_raw_bytes()) for c in target.obj.Contents)
                       if isinstance(target, pikepdf.Page) and isinstance(target.obj.get("/Contents"), pikepdf.Array)
                       else len((target.obj.Contents if isinstance(target, pikepdf.Page) else target).read_raw_bytes()))
        except Exception:
            return None
        if raw_len < 20_000:
            return None
        ins = pikepdf.parse_content_stream(target)
        n = sum(1 for i in ins if not isinstance(i, pikepdf.ContentStreamInlineImage) and str(i.operator) in PATH_OPS)
        if n < 2000:
            return None
        new = [pikepdf.ContentStreamInstruction([_round_num(v) for v in i.operands], i.operator)
               if not isinstance(i, pikepdf.ContentStreamInlineImage) and str(i.operator) in PATH_OPS else i
               for i in ins]
        data = pikepdf.unparse_content_stream(new)
        return data if len(zlib.compress(data, 6)) < raw_len * 0.9 else None

    for i, page in enumerate(pdf.pages):
        if page.obj.objgen in exclude:
            continue
        try:
            data = rewrite(page)
        except Exception:
            data = None
        if data is not None:
            page.obj.Contents = pdf.make_stream(data)
            changes.setdefault(i, []).append((page.obj.objgen, "vektör"))
        res = _inherited(page.obj, "/Resources")
        for name, x in ((res.get("/XObject") or {}).items() if res is not None else []):
            if not (isinstance(x, pikepdf.Stream) and x.get("/Subtype") == Name.Form and x.is_indirect):
                continue
            if x.objgen in exclude:
                continue
            if x.objgen in seen_forms:
                if any(og == x.objgen for items in changes.values() for og, _ in items):
                    changes.setdefault(i, []).append((x.objgen, "vektör"))
                continue
            seen_forms.add(x.objgen)
            try:
                data = rewrite(x)
            except Exception:
                data = None
            if data is not None:
                x.write(data)
                changes.setdefault(i, []).append((x.objgen, "vektör"))
    return changes


# ---------------------------------------------------------------- çift sayfa taramaları
#
# Fotokopi/kitap tarayıcısında iki sayfa tek görüntüde: ortada kitap gölgesi, kenarlarda siyah
# bant. Bu hali sayfa "fotoğraf" sanılır ve siyah-beyaza çevrilemez; sayfa sayısı da yanlıştır.
# Sayfa ikiye bölünür: sol yarı orijinal sayfa nesnesinde kalır (yer imleri bozulmaz), sağ yarı
# arkasına eklenir. Sayfa içeriği ve OCR katmanı olduğu gibi kalır; her yarıda görüntünün
# adı, kırpılmış görüntüyü doğru yere çizen bir form nesnesine bağlanır.

def find_gutter(gray):
    """Çift sayfa görüntüsünde iki sayfa arasının x konumu (0-1) ya da None.
    Yazı her sütunun kendi ortancasına göre ölçülür: kitap sırtının gölgesi ve siyah kat
    çizgisi boydan boya olduğu için "yazı" sayılmaz, sırt bölgesi boşluk olarak görünür.
    Ortadaki en boş bant iki yandaki yazı yoğunluğunun yarısından azsa sayfa çift sayfadır."""
    raw = np.asarray(_sample(gray, 1000), np.float32)
    h, w = raw.shape
    band = raw[int(h * 0.1):int(h * 0.9)]
    ink = (band < np.median(band, axis=0) - 50).mean(axis=0)
    k = max(5, w // 30)
    s = np.convolve(ink, np.ones(k) / k, mode="same")
    lo, hi = int(w * 0.3), int(w * 0.7)
    x = lo + int(np.argmin(s[lo:hi]))
    text = min(s[int(w * 0.1):lo].mean(), s[hi:int(w * 0.9)].mean())
    if text < 0.01 or s[x] >= 0.5 * text:
        return None
    # boş bant (iki sayfanın kenar boşlukları + sırt): içinde gölgenin en koyu yeri, yoksa ortası
    lim = s[x] + 0.25 * (text - s[x])
    a = b = x
    while a > lo and s[a - 1] <= lim:
        a -= 1
    while b < hi - 1 and s[b + 1] <= lim:
        b += 1
    med = np.convolve(np.median(band, axis=0), np.ones(k) / k, mode="same")
    d = a + int(np.argmin(med[a:b + 1]))
    paper = np.percentile(med[int(w * 0.1):int(w * 0.9)], 75)
    return (d if med[d] < 0.9 * paper else (a + b) // 2) / w


def paper_box(gray):
    """Sayfa kağıdının (siyah kenar bantları hariç) kutusu: (x0, y0, x1, y1), 0-1 arası."""
    g = np.asarray(_sample(gray, 800), np.float32)
    paper = g > max(otsu(g), 90)
    h, w = g.shape

    def span(frac, n):
        ok = np.nonzero(frac > 0.5)[0]
        if not len(ok):
            return 0, n
        return int(ok[0]), int(ok[-1]) + 1

    x0, x1 = span(paper.mean(axis=0), w)
    y0, y1 = span(paper.mean(axis=1), h)
    if (x1 - x0) < w * 0.5 or (y1 - y0) < h * 0.5:
        return 0.0, 0.0, 1.0, 1.0
    return x0 / w, y0 / h, x1 / w, y1 / h


def _single_scan_image(page):
    """Sayfa tek bir tam sayfa tarama görüntüsünden mi oluşuyor? (ad, görüntü, (x, y, gen, yük)) ya da None."""
    res = _inherited(page.obj, "/Resources")
    if res is None or "/XObject" not in res or int(page.obj.get("/Rotate", 0) or 0) % 360:
        return None
    try:
        ops = pikepdf.parse_content_stream(page, "q Q cm Do")
    except Exception:
        return None
    ctm, stack, found = (1, 0, 0, 1, 0, 0), [], None
    for operands, op in ops:
        op = str(op)
        if op == "q":
            stack.append(ctm)
        elif op == "Q":
            ctm = stack.pop() if stack else ctm
        elif op == "cm" and len(operands) == 6:
            ctm = _mul(tuple(float(v) for v in operands), ctm)
        elif op == "Do" and operands:
            name = str(operands[0])
            x = res.XObject.get(name)
            if isinstance(x, pikepdf.Stream) and x.get("/Subtype") == Name.Image:
                if found is not None:
                    return None  # birden çok görüntü
                found = (name, x, ctm)
    if found is None:
        return None
    name, img, (a, b, c, d, e, f) = found
    if abs(b) > 1e-3 or abs(c) > 1e-3 or a <= 0 or d <= 0 or img.get("/ImageMask", False) or "/SMask" in img:
        return None
    mb = [float(v) for v in _inherited(page.obj, "/MediaBox")]
    pw, ph = mb[2] - mb[0], mb[3] - mb[1]
    if a * d < 0.85 * pw * ph or pw < ph * 1.02:  # tam sayfa ve yatay değilse çift sayfa değildir
        return None
    return name, img, (e, f, a, d)


def _crop_image_stream(pdf, pil, box):
    """PIL görüntüsünün box (piksel) bölümünden yeni görüntü nesnesi (1 bit -> Flate, gri/renkli -> JPEG)."""
    part = pil.crop(box)
    if part.mode == "1":
        st = pikepdf.Stream(pdf, zlib.compress(part.tobytes(), 9))
        cs, bpc, filt = Name.DeviceGray, 1, Name.FlateDecode
    else:
        part = part.convert("L" if part.mode in ("L", "LA", "I", "I;16", "1") else "RGB")
        st = pikepdf.Stream(pdf, enc_jpeg(part, 90))
        cs, bpc, filt = (Name.DeviceGray if part.mode == "L" else Name.DeviceRGB), 8, Name.DCTDecode
    st.Type, st.Subtype, st.Width, st.Height = Name.XObject, Name.Image, part.width, part.height
    st.ColorSpace, st.BitsPerComponent, st.Filter = cs, bpc, filt
    return pdf.make_indirect(st)


def split_spreads(src, dst) -> int:
    """Çift sayfa taramalarını ikiye böler, siyah kenarları kırpar. Bölünen sayfa sayısını döner
    (0 ise dst yazılmaz). Açıklama (not/vurgu) içeren sayfalara dokunulmaz."""
    count = 0
    with pikepdf.open(src) as pdf:
        if is_signed(pdf):
            return 0
        i = 0
        while i < len(pdf.pages):
            page = pdf.pages[i]
            info = None if page.obj.get("/Annots") else _single_scan_image(page)
            if info is None:
                i += 1
                continue
            name, img, (ix, iy, iw, ih) = info
            try:
                pil = PdfImage(img).as_pil_image()
                if pil.mode not in ("1", "L", "RGB"):
                    pil = pil.convert("RGB")
                gray = pil.convert("L")
                gx = find_gutter(gray)
            except Exception:
                gx = None
            if gx is None:
                i += 1
                continue
            W, H = pil.size
            sx = round(gx * W)
            mb = [float(v) for v in _inherited(page.obj, "/MediaBox")]
            xs = ix + iw * sx / W
            halves = []
            for (px0, px1), (mx0, mx1) in (((0, sx), (mb[0], xs)), ((sx, W), (xs, mb[2]))):
                bx0, by0, bx1, by1 = paper_box(gray.crop((px0, 0, px1, H)))
                cw = px1 - px0
                box = (px0 + round(bx0 * cw), round(by0 * H), px0 + round(bx1 * cw), round(by1 * H))
                part = _crop_image_stream(pdf, pil, box)
                # form, görüntünün birim karesinde kırpılan bölümü yerine çizer
                u0, u1 = box[0] / W, box[2] / W
                v0, v1 = 1 - box[3] / H, 1 - box[1] / H
                form = pikepdf.Stream(pdf, f"q {u1 - u0:.6f} 0 0 {v1 - v0:.6f} {u0:.6f} {v0:.6f} cm /I Do Q".encode())
                form.Type, form.Subtype = Name.XObject, Name.Form
                form.BBox = pikepdf.Array([0, 0, 1, 1])
                form.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(I=part))
                halves.append(((mx0, mx1), pdf.make_indirect(form)))
            res = _inherited(page.obj, "/Resources")
            new_pages = []
            for k, ((mx0, mx1), form) in enumerate(halves):
                if k == 0:
                    pobj = page.obj
                else:
                    pobj = pikepdf.Dictionary({key: page.obj[key] for key in page.obj.keys() if key != "/Parent"})
                r = pikepdf.Dictionary({key: res[key] for key in res.keys()})
                r.XObject = pikepdf.Dictionary({key: res.XObject[key] for key in res.XObject.keys()})
                r.XObject[name] = form
                pobj.Resources = r
                pobj.MediaBox = pikepdf.Array([mx0, mb[1], mx1, mb[3]])
                for key in ("/CropBox", "/TrimBox", "/BleedBox", "/ArtBox", "/Thumb"):
                    if key in pobj:
                        del pobj[key]
                new_pages.append(pobj)
            pdf.pages.insert(i + 1, pikepdf.Page(pdf.make_indirect(new_pages[1])))
            count += 1
            i += 2
        if not count:
            return 0
        if "/PageLabels" in pdf.Root:  # sayfa numaraları kaydı artık tutmaz
            del pdf.Root["/PageLabels"]
        pdf.save(dst)
    return count


def rasterize_vector_pages(src, dst, threads):
    """Metni olmayan, eğri ağırlıklı, siyah-beyaz sayfaları 300 DPI siyah-beyaz görüntüye çevirir.
    Değiştirilen sayfa sayısını döner (0 ise dst yazılmaz)."""
    import pypdfium2 as pdfium
    with pikepdf.open(src) as pdf:
        if is_signed(pdf) or pdf.is_encrypted:
            return 0
        cov = {}
        find_image_uses(pdf, cov)
        cands = []
        for i, page in enumerate(pdf.pages):
            if cov.get(i, 0) >= 0.3:
                continue  # tarama sayfası: OCR ilgilenir
            try:
                contents = page.obj.get("/Contents")
                streams = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
                raw_len = sum(len(c.read_raw_bytes()) for c in streams if c is not None)
            except Exception:
                continue
            if raw_len < 120_000:
                continue
            n = sum(1 for _ in pikepdf.parse_content_stream(page, "m l c v y re"))
            if n >= 5000:
                cands.append((i, raw_len, n))
    if not cands:
        return 0
    out = {}
    doc = pdfium.PdfDocument(src)
    try:
        for i, raw_len, n in cands:
            page = doc[i]
            tp = page.get_textpage()
            chars = len("".join(tp.get_text_bounded().split()))
            tp.close()
            # Yazıcı sürücüleri harflerin çoğunu çizgiye çevirip birkaç bozuk parça bırakabiliyor:
            # çizgi sayısına göre çok az metin varsa sayfanın metni çizimdir
            if chars >= 20 and chars >= n * 0.05:
                continue  # gerçek metni olan sayfaya dokunma
            rot = (360 - page.get_rotation()) % 360
            small = page.render(scale=40 / 72, rotation=rot).to_pil().convert("RGB")
            if chroma_p99(small) >= 40:
                continue  # renkli içerik: vektör olarak kalsın
            gray = page.render(scale=OCR_DPI / 72, grayscale=True, rotation=rot).to_pil().convert("L")
            bw = to_bitonal(gray, 1.0)
            if similarity(gray, bw) < 0.95:
                continue
            data, filt = enc_bilevel(bw)
            if len(data) < raw_len * 0.5:
                out[i] = (data, filt, bw.width, bw.height)
    finally:
        doc.close()
    if not out:
        return 0
    with pikepdf.open(src) as pdf:
        for i, (data, filt, w, h) in out.items():
            page = pdf.pages[i]
            x0, y0, x1, y1 = [float(v) for v in page.cropbox]
            img = pikepdf.Stream(pdf, data)
            img[Name.Type], img[Name.Subtype] = Name.XObject, Name.Image
            img[Name.Width], img[Name.Height] = w, h
            img[Name.ColorSpace], img[Name.BitsPerComponent], img[Name.Filter] = Name.DeviceGray, 1, filt
            page.obj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(PkSayfa=pdf.make_indirect(img)))
            page.obj.Contents = pdf.make_stream(
                f"q {x1 - x0:.4f} 0 0 {y1 - y0:.4f} {x0:.4f} {y0:.4f} cm /PkSayfa Do Q".encode())
        pdf.save(dst)
    return len(out)


# ---------------------------------------------------------------- metin tanıma (OCR)
#
# Metni olmayan tarama sayfaları Tesseract ile okunur ve sayfanın üzerine görünmez bir metin
# katmanı eklenir (arama, kopyalama, Zotero tam metin dizini). Sayfanın görünümü değişmez.

OCR_LANGS = ("tur", "eng", "deu", "fra")
OCR_DPI = 300


def tessdata_dir():
    cands = [os.environ.get("TESSDATA_PREFIX", "")]
    cands += [os.path.join(os.path.dirname(d), "tessdata") for d in _tool_dirs()]
    cands += ["/opt/homebrew/share/tessdata", "/usr/local/share/tessdata"]
    for c in cands:
        if c and any(os.path.isfile(os.path.join(c, f"{lang}.traineddata")) for lang in OCR_LANGS):
            return c
    return None


def ocr_langs(tessdir):
    return "+".join(lang for lang in OCR_LANGS if os.path.isfile(os.path.join(tessdir, f"{lang}.traineddata")))


def add_ocr(src, dst, threads):
    """Metinsiz tarama sayfalarına görünmez metin katmanı ekleyip dst'ye yazar; eklenen sayfa sayısını döner."""
    tessdir = tessdata_dir()
    if not TOOLS["tesseract"] or not tessdir:
        return 0
    import pypdfium2 as pdfium
    with pikepdf.open(src) as pdf:
        if is_signed(pdf) or pdf.is_encrypted:
            return 0
        cov = {}
        find_image_uses(pdf, cov)
    cands = sorted(p for p, c in cov.items() if c >= 0.3)  # sayfanın en az %30'u görüntü
    if not cands:
        return 0
    td = tempfile.mkdtemp(prefix="pdfkucult-ocr-")
    try:
        todo = []
        doc = pdfium.PdfDocument(src)
        try:
            for i in cands:
                page = doc[i]
                tp = page.get_textpage()
                has_text = len(tp.get_text_bounded().strip()) >= 20
                tp.close()
                if has_text:
                    continue  # zaten metni var (ör. daha önce OCR yapılmış)
                # PDFium sayfanın /Rotate değerini uygular; ters çevirerek döndürülmemiş halini al
                rot = (360 - page.get_rotation()) % 360
                img = page.render(scale=OCR_DPI / 72, grayscale=True, rotation=rot).to_pil().convert("L")
                img.save(os.path.join(td, f"{i}.png"))
                todo.append(i)
        finally:
            doc.close()
        if not todo:
            return 0
        langs = ocr_langs(tessdir)
        env = dict(os.environ, OMP_THREAD_LIMIT="1", TESSDATA_PREFIX=tessdir)

        def run(i):
            base = os.path.join(td, f"o{i}")
            r = _run([TOOLS["tesseract"], os.path.join(td, f"{i}.png"), base, "-l", langs,
                      "--dpi", str(OCR_DPI), "--tessdata-dir", tessdir, "-c", "textonly_pdf=1",
                      # "pdf"/"txt" ayar dosyaları yerine doğrudan seçenek (paketteki tessdata'da configs yok)
                      "-c", "tessedit_create_pdf=1", "-c", "tessedit_create_txt=1"],
                     capture_output=True, timeout=900, env=env)
            try:
                text = open(base + ".txt", encoding="utf-8", errors="ignore").read().strip()
            except OSError:
                text = ""
            ok = r.returncode == 0 and os.path.exists(base + ".pdf") and len(text) >= 3
            return i, (base + ".pdf" if ok else None)

        with cf.ThreadPoolExecutor(max(1, threads)) as ex:
            results = [(i, p) for i, p in ex.map(run, todo) if p]
        if not results:
            return 0
        with pikepdf.open(src) as pdf:
            opened = []
            try:
                for i, p in results:
                    o = pikepdf.open(p)
                    opened.append(o)  # yabancı nesneler kaydedilene kadar açık kalmalı
                    page = pdf.pages[i]
                    page.add_overlay(o.pages[0], pikepdf.Rectangle(page.cropbox))
                pdf.save(dst)
            finally:
                for o in opened:
                    o.close()
        return len(results)
    finally:
        shutil.rmtree(td, ignore_errors=True)


# ---------------------------------------------------------------- doğrulama

def _render(doc, page, dpi, oversample=1):
    """Sayfayı gri tonlamalı render eder (PDFium; tüm platformlarda aynı sonuç).
    oversample > 1: o kat büyük render edip blok ortalamasıyla küçültür. PDFium siyah-beyaz
    görüntüleri düşük çözünürlükte ortalamadan (pürüzlü) çizdiği için karşılaştırma bununla yapılır."""
    bm = doc[page].render(scale=dpi * oversample / 72, grayscale=True)
    a = np.asarray(bm.to_pil().convert("L"), dtype=np.float32)
    if oversample > 1:
        f = oversample
        h, w = a.shape[0] // f * f, a.shape[1] // f * f
        a = a[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3))
    return a


def pick_pages(changes, limit=12):
    """Kontrol edilecek sayfalar: her işlem türünden (fontlarda her fonttan) en az bir sayfa
    + eşit aralıklı örnekler."""
    groups = {}
    for page, items in changes.items():
        for og, kind in items:
            groups.setdefault((kind, og) if kind == "font" else (kind, None), []).append(page)
    chosen = set()
    for (kind, og), pages in groups.items():
        pages = sorted(pages)
        chosen.add(pages[0])
        chosen.add(pages[len(pages) // 2])
    rest = sorted(set(changes) - chosen)
    room = max(0, limit - len(chosen))
    if rest and room:
        step = max(1, len(rest) / room)
        chosen |= {rest[int(i * step)] for i in range(min(room, len(rest)))}
    return sorted(chosen)


def verify(orig, new, changes):
    """Değişen sayfaları orijinalle karşılaştırır; sorunlu sayfaları [(sayfa, neden)] döner.
    Yalnızca fontu değişen sayfalar 72 DPI'da neredeyse piksel piksel aynı olmalıdır."""
    import pypdfium2 as pdfium
    bad = []
    pages = pick_pages(changes)
    if not pages:
        return bad
    da, db = pdfium.PdfDocument(orig), pdfium.PdfDocument(new)
    try:
        for p in pages:
            font_only = all(kind in ("font", "vektör") for _, kind in changes.get(p, []))
            dpi, over = (72, 1) if font_only else (30, 4)
            try:
                a = _render(da, p, dpi, over)
                b = _render(db, p, dpi, over)
            except Exception:
                bad.append((p, f"sayfa {p + 1} render edilemedi"))
                continue
            if a.shape == b.shape and any(kind.startswith("tarama->") for _, kind in changes.get(p, [])):
                # siyah-beyaza çevirme kağıt rengini/gölgeleri bilerek siler: orijinali de aynı
                # düzeltmeden geçirip karşılaştır. Neredeyse boş sayfada (kapak, boş sayfa)
                # benzerlik anlamsızdır; yalnızca parlaklığa bakılır.
                a = normalize_background(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)))
                if np.mean(a < 160) < 0.01:
                    if abs(a.mean() - b.mean()) > 40:
                        bad.append((p, f"sayfa {p + 1} parlaklığı değişti"))
                    continue
            if a.shape != b.shape:
                bad.append((p, f"sayfa {p + 1} boyutu değişti"))
            elif font_only:
                # aynı okuyucu, aynı harf çizimleri -> sonuç birebir aynı olmalı
                if int(np.count_nonzero(np.abs(a - b) > 48)) > 8:
                    bad.append((p, f"sayfa {p + 1} yazıları farklı görünüyor"))
            elif a.std() < 3 or b.std() < 3:
                if abs(a.mean() - b.mean()) > 40:
                    bad.append((p, f"sayfa {p + 1} parlaklığı değişti"))
            else:
                c = float(np.corrcoef(a.ravel(), b.ravel())[0, 1])
                if not c > 0.85:
                    bad.append((p, f"sayfa {p + 1} farklı görünüyor (benzerlik {c:.2f})"))
    finally:
        da.close()
        db.close()
    return bad


def text_of(path):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(path)
    try:
        parts = []
        for page in doc:
            tp = page.get_textpage()
            parts.append(tp.get_text_bounded())
            tp.close()
            page.close()
        return " ".join(" ".join(parts).split())
    finally:
        doc.close()


def font_text_problem(orig, new, changes):
    """Font değiştiyse belgenin metni (arama/kopyalama) birebir aynı kalmalı."""
    fonts = {og for items in changes.values() for og, kind in items if kind == "font"}
    if fonts and text_of(orig) != text_of(new):
        return fonts
    return set()


# ---------------------------------------------------------------- dosya

def _threads():
    return max(2, min(8, (os.cpu_count() or 4)))


class SignedPDF(Exception):
    pass


def drop_big_cmyk_profiles(pdf) -> int:
    """Baskı için gömülmüş dev CMYK profillerini (ör. 1,3 MB'lık ISO Coated) DeviceCMYK ile
    değiştirir: birkaç küçük resim için dosyanın çoğunu kaplayabiliyorlar. Ekranda renk
    farkı çok az olur. Yalnızca baskı hedefi (PDF/X OutputIntent) olarak gömülmüş dev
    profiller de atılır: ekrandaki görünümü etkilemezler. Değişiklik sayısını döner."""
    big = set()
    for o in pdf.objects:
        if isinstance(o, pikepdf.Stream) and o.get("/N") == 4 and "/Subtype" not in o \
                and len(o.read_raw_bytes()) > BIG_CMYK_ICC:
            big.add(o.objgen)
    if not big:
        return 0
    count = 0

    def fix(x, depth=0):
        """x içindeki doğrudan (dolaylı olmayan) [/ICCBased büyük] dizilerini değiştirir."""
        nonlocal count
        if depth > 20:
            return
        if isinstance(x, pikepdf.Array):
            items = list(enumerate(x))
        elif isinstance(x, (pikepdf.Dictionary, pikepdf.Stream)):
            items = [(k, x[k]) for k in x.keys()]
        else:
            return
        for k, v in items:
            if is_big_icc(v):
                x[k] = Name.DeviceCMYK
                count += 1
            elif isinstance(v, (pikepdf.Array, pikepdf.Dictionary)) and not v.is_indirect:
                fix(v, depth + 1)

    def is_big_icc(v):
        return (isinstance(v, pikepdf.Array) and len(v) == 2 and v[0] == Name.ICCBased
                and v[1].is_indirect and v[1].objgen in big)

    for o in pdf.objects:
        if isinstance(o, pikepdf.Array) and is_big_icc(o):
            continue  # dolaylı dizi: ona başvuranlar aşağıda düzeltilir
        fix(o)
    # dolaylı [/ICCBased büyük] dizilerine yapılan başvurular
    ind = {o.objgen for o in pdf.objects if isinstance(o, pikepdf.Array) and is_big_icc(o)}
    if ind:
        def fix_ref(x, depth=0):
            nonlocal count
            if depth > 20:
                return
            items = list(enumerate(x)) if isinstance(x, pikepdf.Array) else [(k, x[k]) for k in x.keys()]
            for k, v in items:
                if isinstance(v, pikepdf.Object) and v.is_indirect and v.objgen in ind:
                    x[k] = Name.DeviceCMYK
                    count += 1
                elif isinstance(v, (pikepdf.Array, pikepdf.Dictionary)) and not v.is_indirect:
                    fix_ref(v, depth + 1)
        for o in pdf.objects:
            if isinstance(o, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)) and o.objgen not in ind:
                fix_ref(o)
    ois = pdf.Root.get("/OutputIntents")
    if isinstance(ois, pikepdf.Array):
        keep = [oi for oi in ois if not (isinstance(oi, pikepdf.Dictionary) and oi.get("/DestOutputProfile") is not None
                                         and oi.DestOutputProfile.is_indirect and oi.DestOutputProfile.objgen in big)]
        if len(keep) < len(ois):
            count += len(ois) - len(keep)
            if keep:
                pdf.Root.OutputIntents = pikepdf.Array(keep)
            else:
                del pdf.Root["/OutputIntents"]
    return count


def compress_once(src, dst, P: Preset, threads, exclude=frozenset(), images=True, fonts=True,
                  keep_hinting=None, vectors=True, orig_size=None):
    """Bir geçiş: görüntüleri işler, kaydeder. (istatistik, {sayfa: [(objgen, tür)]}) döner."""
    stats, changes = {}, {}
    with pikepdf.open(src) as pdf:
        if is_signed(pdf):
            raise SignedPDF()
        stats["tekrar-eden"] = dedupe_streams(pdf)
        uses = find_image_uses(pdf)
        items = [rec for og, rec in uses.items() if og not in exclude] if images else []
        with cf.ThreadPoolExecutor(threads) as ex:
            chunk = threads * 2
            for i in range(0, len(items), chunk):
                batch = []
                for rec in items[i:i + chunk]:
                    try:
                        job = plan_job(rec["obj"], rec["dpi"], rec["scan"])
                    except Exception:
                        job = None
                    if job is not None:
                        batch.append((rec, ex.submit(run_job, job, P, len(rec["obj"].read_raw_bytes()))))
                for rec, fut in batch:
                    try:
                        r = fut.result()
                    except Exception:
                        r = None
                    if r is None:
                        continue
                    apply_result(rec["obj"], r)
                    stats[r.kind] = stats.get(r.kind, 0) + 1
                    for pg in rec["pages"]:
                        changes.setdefault(pg, []).append((rec["obj"].objgen, r.kind))
                del batch
        try:
            if not fonts:
                raise _Unsafe()
            kh = (not P.lossy) if keep_hinting is None else keep_hinting
            n, fchanges = subset_fonts(pdf, keep_hinting=kh, only_full=not P.lossy, exclude=exclude)
        except Exception:
            n, fchanges = 0, {}
        if n:
            stats["font-küçültme"] = n
            for pg, items in fchanges.items():
                changes.setdefault(pg, []).extend(items)
        if vectors and P.lossy:
            try:
                vchanges = round_paths(pdf, exclude)
            except Exception:
                vchanges = {}
            if vchanges:
                stats["vektör-yuvarlama"] = sum(len(v) for v in vchanges.values())
                for pg, items in vchanges.items():
                    changes.setdefault(pg, []).extend(items)
        if P.lossy:
            try:
                n = drop_big_cmyk_profiles(pdf)
            except Exception:
                n = 0
            if n:
                stats["renk-profili"] = n
        for page in pdf.pages:
            for k in ("/Thumb", "/PieceInfo"):
                if k in page.obj:
                    del page.obj[k]
        if "/PieceInfo" in pdf.Root:
            del pdf.Root["/PieceInfo"]
        try:
            pdf.remove_unreferenced_resources()
        except Exception:
            pass
        try:  # başka bilgisayarda/uygulamada da "küçültülmüş" olduğu anlaşılsın
            pdf.docinfo[Name(ISARET)] = pikepdf.String(f"{MOTOR_SURUM};{P.name};{orig_size or ''}")
        except Exception:
            pass
        pdf.save(dst, compress_streams=True, recompress_flate=True,
                 object_stream_mode=pikepdf.ObjectStreamMode.generate,
                 encryption=True if pdf.is_encrypted else None)
    if not stats["tekrar-eden"]:
        del stats["tekrar-eden"]
    return stats, changes


def safe_font_exclusions(src, tmpf, P, threads):
    """Fontları önce yalnız başlarına (görüntülere dokunmadan) dener: font değişen sayfalar
    birebir aynı, metin katmanı aynı olmalı. Güvenle küçültülemeyen font programlarının
    kümesini döner; None = fontlara hiç dokunma."""
    excl = set()
    try:
        for _ in range(3):
            # Harf eşlemesi ipucu (hinting) verisi korunarak denetlenir: ekran okuyucuları (PDFium)
            # ipucunu uyguladığı için ancak böyle birebir karşılaştırılabilir. Son dosyada ipucu
            # silinse de aynı harfler (aynı GID'ler) çizilir; yalnızca piksel hizalaması değişir.
            _, ch = compress_once(src, tmpf, P, threads, frozenset(excl), images=False, keep_hinting=True,
                                  vectors=False)
            if not ch:
                return excl
            bad = verify(src, tmpf, ch)
            badf = {og for p, _ in bad for og, k in ch.get(p, []) if k == "font"}
            badf |= font_text_problem(src, tmpf, ch)
            if not badf:
                return excl
            excl |= badf
        return None
    finally:
        if os.path.exists(tmpf):
            os.remove(tmpf)


def _image_changes(changes):
    out = {}
    for p, items in changes.items():
        items = [x for x in items if x[1] != "font"]
        if items:
            out[p] = items
    return out


def compress_file(src, dst, level="onerilen", threads=None, ocr=True, split=True):
    t0 = time.time()
    P = PRESETS[level]
    threads = threads or _threads()
    before = os.path.getsize(src)
    out = {"dosya": src, "cikti": dst, "seviye": level, "once": before, "sonra": before, "ocr": 0, "tarama": 0,
           "bolunen": 0}
    try:
        with pikepdf.open(src) as pdf:
            out["sayfa"] = len(pdf.pages)  # raporda sayfa başına KB için
    except Exception:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(dst)) or ".", exist_ok=True)
    tmp = dst + ".tmp.pdf"
    ocr_tmp = dst + ".ocr.pdf"
    ras_tmp = dst + ".ras.pdf"
    split_tmp = dst + ".split.pdf"
    note = None
    work = src  # sıkıştırılacak kaynak (OCR eklendiyse OCR'lı ara dosya)

    def finish_with(path, durum, mesaj=None, stats=None):
        """Sonucu dst'ye koy. OCR eklendiyse orijinal yerine OCR'lı dosya esas alınır."""
        if os.path.abspath(path) != os.path.abspath(dst):
            shutil.copy2(path, dst) if path in (src, work) else os.replace(path, dst)
        st = os.stat(src)
        os.utime(dst, (st.st_atime, st.st_mtime))
        msgs = [m for m in (
            mesaj,
            f"{out['bolunen']} çift sayfa tarama ikiye bölündü." if out["bolunen"] else None,
            f"Çizim olarak kaydedilmiş {out['tarama']} metin sayfası taramaya çevrildi." if out["tarama"] else None,
            f"{out['ocr']} sayfaya metin katmanı (OCR) eklendi." if out["ocr"] else None) if m]
        out.update(durum=durum, sonra=os.path.getsize(dst))
        if stats:
            out["islemler"] = stats
        if msgs:
            out["mesaj"] = " ".join(msgs)
        return out

    try:
        pikepdf.settings.set_flate_compression_level(6)
        if P.split_spreads and split:  # okuma kopyası: çift sayfa taramaları ikiye böl
            try:
                out["bolunen"] = split_spreads(work, split_tmp)
            except Exception:
                out["bolunen"] = 0
            if out["bolunen"]:
                work = split_tmp
        if P.to_bitonal:  # Aşırı: çizim olarak kaydedilmiş metin sayfaları -> siyah-beyaz tarama
            try:
                out["tarama"] = rasterize_vector_pages(work, ras_tmp, threads)
            except Exception:
                out["tarama"] = 0
            if out["tarama"]:
                work = ras_tmp
        if ocr:
            try:
                out["ocr"] = add_ocr(work, ocr_tmp, threads)
            except Exception as e:  # OCR olmazsa sıkıştırma yine yapılır
                note = f"OCR yapılamadı ({type(e).__name__})."
            if out["ocr"]:
                work = ocr_tmp
        changed = out["ocr"] or out["tarama"] or out["bolunen"]
        base_durum = "tamam" if changed else "zaten-optimum"
        try:
            fexcl = safe_font_exclusions(work, dst + ".font.pdf", P, threads)
        except pikepdf.PasswordError:
            return finish_with(src, "atlandi", "Şifreli PDF (açılış parolası gerekiyor); orijinal kopyalandı.")
        except SignedPDF:
            return finish_with(src, "atlandi", "E-imzalı PDF; imzayı bozmamak için dokunulmadı, orijinal kopyalandı.")
        fonts_on = fexcl is not None
        excl = set(fexcl or ())
        stats, changes = compress_once(work, tmp, P, threads, frozenset(excl), fonts=fonts_on, orig_size=before)
        bad = verify(work, tmp, _image_changes(changes))
        if bad:
            # yalnızca sorunlu sayfalardaki görüntüleri olduğu gibi bırakıp tekrar dene
            excl |= {og for p, _ in bad for og, k in changes.get(p, []) if k != "font"}
            note = f"Kalite kontrolü: {bad[0][1]}; o sayfadaki görüntüler değiştirilmedi."
            stats, changes = compress_once(work, tmp, P, threads, frozenset(excl), fonts=fonts_on, orig_size=before)
            bad = verify(work, tmp, _image_changes(changes))
        if bad and P.lossy:
            note = f"Kalite kontrolü: {bad[0][1]}; yalnızca kayıpsız sıkıştırma uygulandı."
            stats, changes = compress_once(work, tmp, PRESETS["az"], threads, frozenset(excl), fonts=False,
                                           orig_size=before)
            bad = verify(work, tmp, _image_changes(changes))
        if bad:
            return finish_with(work, "tamam" if changed else "atlandi",
                               f"Kalite kontrolü başarısız ({bad[0][1]}); sıkıştırma uygulanmadı.")
        if os.path.getsize(tmp) >= os.path.getsize(work):
            return finish_with(work, base_durum,
                               None if changed else "Daha fazla küçültülemedi; orijinal kopyalandı.")
        return finish_with(tmp, "tamam", note, stats)
    except Exception as e:
        out.update(durum="hata", mesaj=f"{type(e).__name__}: {e}")
    finally:
        for t in (tmp, ocr_tmp, ras_tmp, split_tmp, dst + ".font.pdf"):
            if os.path.exists(t):
                os.remove(t)
        out["sure_sn"] = round(time.time() - t0, 1)
    return out


# ---------------------------------------------------------------- toplu iş

def _nfc(x):
    # macOS/Drive Türkçe harfleri ayrışık (NFD) saklayabilir: "ü" = "u" + "¨"
    return unicodedata.normalize("NFC", x)


def collect(inputs):
    """(kaynak, hedef) çiftleri: dosya -> yanına '(küçük)', klasör -> kardeş '(küçültülmüş)' klasörü.
    Elle bırakılan dosyalar her zaman işlenir; klasör içindeki '(küçük)' çıktılar yalnızca
    orijinalleri yanında duruyorsa atlanır (aynı belge iki kez işlenmesin)."""
    pairs = []
    for inp in inputs:
        inp = os.path.abspath(inp.rstrip("/"))
        if os.path.isdir(inp):
            if _nfc(inp).endswith(OUT_DIR_SUFFIX):
                continue
            outroot = inp + OUT_DIR_SUFFIX
            for root, dirs, files in os.walk(inp):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not _nfc(d).endswith(OUT_DIR_SUFFIX))
                pdfs = [f for f in sorted(files) if f.lower().endswith(".pdf") and not f.startswith(".")]
                names = {_nfc(f) for f in pdfs}
                for f in pdfs:
                    stem = _nfc(os.path.splitext(f)[0])
                    if stem.endswith(OUT_SUFFIX) and stem[: -len(OUT_SUFFIX)] + ".pdf" in names:
                        continue
                    s = os.path.join(root, f)
                    pairs.append((s, os.path.join(outroot, os.path.relpath(s, inp))))
        elif inp.lower().endswith(".pdf"):
            stem = os.path.splitext(inp)[0]
            pairs.append((inp, stem + OUT_SUFFIX + ".pdf"))
    return pairs


def _worker(args):
    src, dst, level, threads, ocr, split = args
    try:
        return compress_file(src, dst, level, threads, ocr, split)
    except Exception as e:  # ör. dosya işlem sırasında taşındı/silindi: toplu iş devam etsin
        return {"dosya": src, "cikti": dst, "seviye": level, "once": 0, "sonra": 0,
                "durum": "hata", "mesaj": f"{type(e).__name__}: {e}", "sure_sn": 0}


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u in ("B", "KB") else f"{n:.1f} {u}"
        n /= 1024


def main(argv=None):
    ap = argparse.ArgumentParser(description="PDF'leri kaliteyi koruyarak küçültür.")
    ap.add_argument("girdiler", nargs="*", default=[], help="PDF dosyaları ve/veya klasörler")
    ap.add_argument("-s", "--seviye", choices=list(PRESETS), default="okuma")
    ap.add_argument("-o", "--cikti", help="tek dosya için çıktı yolu")
    ap.add_argument("-j", "--paralel", type=int, default=0, help="aynı anda işlenecek dosya sayısı")
    ap.add_argument("--json", action="store_true", help="her dosya için bir JSON satırı yaz")
    ap.add_argument("--durum", help="(uygulama için) ilerleme durum dosyası")
    ap.add_argument("--sessiz", action="store_true", help="rapor dosyası yazma (Zotero eklentisi için)")
    ap.add_argument("--sonuc", help="sonuçları (JSON satırları) bu dosyaya da yaz")
    ap.add_argument("--cop-kutusuna", action="store_true", help="girdileri Çöp Kutusu'na taşı ve çık")
    ap.add_argument("--ocr-yok", action="store_true", help="metinsiz taramalara OCR uygulama")
    ap.add_argument("--bolme-yok", action="store_true",
                    help="çift sayfa taramaları bölme (sayfa numaraları değişmesin)")
    ap.add_argument("--isaret-oku", action="store_true", help="girdilerdeki PDF Küçült işaretlerini oku ve çık")
    ap.add_argument("--liste", help="girdileri bu dosyadan (satır satır) oku")
    ap.add_argument("--parmak-izi", action="store_true", help="tekrar tespiti için girdilerin parmak izlerini yaz ve çık")
    a = ap.parse_args(argv)
    if a.liste:
        with open(a.liste, encoding="utf-8") as f:
            a.girdiler = [ln for ln in f.read().splitlines() if ln] + [g for g in a.girdiler if g != "-"]
    _on_term()
    if a.cop_kutusuna:
        return to_trash(a.girdiler)
    if a.parmak_izi:
        # PDFium iş parçacıkları arasında güvenli değil: ayrı süreçler
        out = open(a.sonuc, "w", encoding="utf-8") if a.sonuc else None
        with cf.ProcessPoolExecutor(max(1, min(8, os.cpu_count() or 4))) as ex:
            for r in ex.map(fingerprint, a.girdiler, chunksize=16):
                line = json.dumps(r, ensure_ascii=False)
                print(line)
                if out:
                    out.write(line + "\n")
        if out:
            out.close()
        return 0
    if a.isaret_oku:
        out = open(a.sonuc, "w", encoding="utf-8") if a.sonuc else None
        for g in a.girdiler:
            line = json.dumps({"dosya": g, "isaret": read_mark(g)}, ensure_ascii=False)
            print(line)
            if out:
                out.write(line + "\n")
        if out:
            out.close()
        return 0

    def status(line):
        if a.durum:
            with open(a.durum, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    pairs = collect(a.girdiler)
    if a.cikti and len(pairs) == 1:
        pairs = [(pairs[0][0], os.path.abspath(a.cikti))]
    total = len(pairs)
    status(f"T\t{total}")
    if not total:
        msg = "İşlenecek PDF bulunamadı. (Daha önce küçültülmüş '(küçük)' dosyaları atlanır.)"
        print(msg, file=sys.stderr)
        if a.durum:
            with open(a.durum + ".ozet", "w", encoding="utf-8") as f:
                f.write(msg)
        status("S\t")
        return 1

    ncpu = os.cpu_count() or 4
    jobs = a.paralel or max(1, min(total, ncpu // 2, 6))
    threads = max(2, ncpu // jobs)
    results = []

    def run_all():
        if total == 1:  # tek dosya (ör. Zotero): alt süreç açmadan, doğrudan
            yield _worker((pairs[0][0], pairs[0][1], a.seviye, max(2, min(8, ncpu)), not a.ocr_yok, not a.bolme_yok))
            return
        with cf.ProcessPoolExecutor(jobs, initializer=_on_term) as ex:
            futs = [ex.submit(_worker, (s, d, a.seviye, threads, not a.ocr_yok, not a.bolme_yok)) for s, d in pairs]
            for fut in cf.as_completed(futs):
                yield fut.result()

    for k, r in enumerate(run_all(), 1):
        results.append(r)
        if a.sonuc:
            with open(a.sonuc, "a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        if a.json:
            print(json.dumps(r, ensure_ascii=False), flush=True)
        else:
            print(f"[{k}/{total}] {line_of(r)}", flush=True)
        status(f"D\t{k}\t{total}\t{os.path.basename(r['dosya'])}")

    if a.sessiz:  # rapor dosyası yok, ama bitiş satırı yine yazılır
        status("S\t")
        return 0
    summary, report = write_report(results, a.seviye)
    if not a.json:
        print("\n" + summary + f"\nRapor: {report}")
    if a.durum:
        with open(a.durum + ".ozet", "w", encoding="utf-8") as f:
            f.write(summary)
    status(f"S\t{report}")
    return 0


LEVEL_NAMES = {"az": "Az (kayıpsız)", "onerilen": "Önerilen (dengeli)", "asiri": "Aşırı (en küçük)",
               "okuma": "Akıllı sıkıştırma"}


def line_of(r):
    pct = 100 * (1 - r["sonra"] / r["once"]) if r["once"] else 0
    msg = f"  — {r['mesaj']}" if r.get("mesaj") else ""
    if r["durum"] == "hata":
        return f"{os.path.basename(r['dosya'])}: HATA{msg}"
    n = r.get("sayfa")
    per_page = f", sayfa başına {r['once'] / n / 1024:.0f} → {r['sonra'] / n / 1024:.0f} KB" if n else ""
    return (f"{os.path.basename(r['dosya'])}: {human(r['once'])} → {human(r['sonra'])} "
            f"(%{pct:.0f} küçüldü{per_page}, {r.get('sure_sn', 0)} sn){msg}")


def write_report(results, level):
    ok = [r for r in results if r["durum"] != "hata"]
    errs = [r for r in results if r["durum"] == "hata"]
    warns = [r for r in results if r.get("mesaj") and r["durum"] != "hata"]
    b = sum(r["once"] for r in ok)
    af = sum(r["sonra"] for r in ok)
    pct = 100 * (1 - af / b) if b else 0
    lines = [f"{len(results)} PDF işlendi" + ("" if level == "okuma" else f" — seviye: {LEVEL_NAMES[level]}"),
             f"Toplam: {human(b)} → {human(af)}  (%{pct:.0f} küçüldü, {human(b - af)} kazanıldı)"]
    if warns:
        lines.append(f"{len(warns)} dosyada not var (ayrıntılar raporda).")
    if errs:
        lines.append(f"{len(errs)} dosya işlenemedi (ayrıntılar raporda).")
    summary = "\n".join(lines)

    logdir = log_dir()
    os.makedirs(logdir, exist_ok=True)
    path = os.path.join(logdir, time.strftime("rapor-%Y%m%d-%H%M%S.txt"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("PDF Küçült raporu — " + time.strftime("%d.%m.%Y %H:%M") + "\n\n" + summary + "\n\n")
        for r in sorted(results, key=lambda r: r["dosya"]):
            f.write(line_of(r) + "\n")
            f.write(f"    kaynak: {r['dosya']}\n")
            if r["durum"] != "hata":
                f.write(f"    çıktı:  {r['cikti']}\n")
    return summary, path


def read_mark(path):
    """PDF'teki PDF Küçült işaretini okur: {"surum", "seviye"} ya da None."""
    try:
        with pikepdf.open(path) as pdf:
            v = pdf.trailer.get("/Info", {}).get(ISARET)
            if v is None:
                return None
            parts = str(v).split(";")
            orig = parts[2] if len(parts) > 2 and parts[2].isdigit() else None
            return {"surum": parts[0], "seviye": parts[1] if len(parts) > 1 else "",
                    "orijinal": int(orig) if orig else None}
    except Exception:
        return None


def fingerprint(path):
    """Tekrar eden PDF tespiti için: dosya özeti + ilk 3 sayfanın metninden içerik izi + sayfa sayısı.
    İçerik izi, biri küçültülmüş öteki küçültülmemiş aynı belgeyi de eşleştirir."""
    import re as _re
    out = {"dosya": path, "sha1": None, "metin": None, "sayfa": 0}
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        out["sha1"] = h.hexdigest()
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(path)
        try:
            out["sayfa"] = len(doc)
            txt = ""
            for i in range(min(3, len(doc))):
                tp = doc[i].get_textpage()
                txt += tp.get_text_bounded()
                tp.close()
        finally:
            doc.close()
        norm = _re.sub(r"\W+", "", unicodedata.normalize("NFKC", txt).lower())
        if len(norm) >= 200:  # yeterli metin yoksa (tarama) yalnızca dosya özeti kullanılır
            out["metin"] = hashlib.sha1(f"{out['sayfa']}|{norm[:4000]}".encode()).hexdigest()
    except Exception:
        pass
    return out


def log_dir():
    if IS_WIN:
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "PDFKucult", "Raporlar")
    return os.path.expanduser("~/Library/Logs/PDFKucult")


def to_trash(paths):
    """Dosyaları Çöp Kutusu'na / Geri Dönüşüm Kutusu'na taşır (Zotero eklentisi kullanır)."""
    from send2trash import send2trash
    ok = True
    for p in paths:
        try:
            send2trash(p)
        except Exception as e:
            print(f"{p}: {e}", file=sys.stderr)
            ok = False
    return 0 if ok else 1


def _on_term():
    # iptal edildiğinde (SIGTERM) 'finally' blokları çalışsın, yarım dosya kalmasın
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  # paketlenmiş (PyInstaller) uygulamada alt süreçler için
    for _name in ("stdout", "stderr"):  # penceresiz Windows uygulamasında konsol yok
        if getattr(sys, _name) is None:
            setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
    sys.exit(main())
