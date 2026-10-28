"""Test için örnek PDF'ler üretir (gerçek kullanıcı belgesi gerekmez; her platformda aynı).

  tarama.pdf      300 DPI gri tarama gibi metin sayfası (JPEG)      -> Aşırı: siyah-beyaz JBIG2
  cmyk.pdf        baskı için CMYK fotoğraf (Adobe JPEG)             -> Önerilen: CMYK->RGB
  siyahbeyaz.pdf  600 DPI 1 bit tarama (Flate)                      -> her seviye: kayıpsız JBIG2
  font.pdf        tam gömülü TrueType (basit + CID/Identity-H)      -> font küçültme, metin aynı
  zincir.pdf      300 DPI fotoğraf, [/FlateDecode /DCTDecode]       -> Önerilen: çözünürlük düşürme
  fotograf.pdf    fotoğrafla çekilmiş gri sayfa, 60x88 cm sayfa     -> Aşırı: siyah-beyaz JBIG2
  grafik.pdf      ~1500 DPI kayıpsız çizelge                        -> Önerilen: kayıpsız 300 DPI
  profil.pdf      küçük resme bağlı 300 KB CMYK renk profili        -> Önerilen: DeviceCMYK
  cift.pdf        fotokopi çift sayfa (gölge, siyah kenar), 2 sayfa  -> Okuma: 4 sayfa, siyah-beyaz
  resimli.pdf     metin + fotoğraflı tarama sayfası                 -> Okuma: noktalı siyah-beyaz
  baskihedef.pdf  yalnızca baskı hedefi olarak 300 KB CMYK profil  -> Önerilen: profil atılır
  jp2cmyk.pdf     CMYK JPEG 2000 fotoğraf                          -> Önerilen: CMYK->RGB JPEG
"""
import io
import os
import sys
import zlib

import numpy as np
import pikepdf
from pikepdf import Array, Dictionary, Name
from PIL import Image, ImageDraw, ImageFilter, ImageFont

TEXT = ("Çevre hukuku ve sürdürülebilir kalkınma ilkeleri. Madencilik faaliyetleri, ormanlık "
        "alanlar ve kamu yararı dengesi; idari yargı içtihatları ışığında değerlendirilmiştir.")


def font_path():
    cands = [
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        r"C:\Windows\Fonts\times.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    return next(p for p in cands if os.path.exists(p))


def setd(obj, **kw):
    for k, v in kw.items():
        obj[Name("/" + k)] = v


def page_with_image(pdf, img_stream, w_pt, h_pt, place):
    page = pdf.add_blank_page(page_size=(w_pt, h_pt))
    page.Resources = Dictionary(XObject=Dictionary(Im0=img_stream))
    x, y, w, h = place
    page.Contents = pdf.make_stream(f"q {w} 0 0 {h} {x} {y} cm /Im0 Do Q".encode())
    return page


def text_image(w, h, size):
    img = Image.new("L", (w, h), 235)  # hafif sararmış kağıt
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path(), size)
    words, line, y = TEXT.split(), "", int(h * 0.08)
    lines = []
    for _ in range(14):
        for wd in words:
            if d.textlength(line + " " + wd, font=font) > w * 0.8:
                lines.append(line)
                line = wd
            else:
                line = (line + " " + wd).strip()
    for ln in lines[:40]:
        d.text((int(w * 0.1), y), ln, fill=25, font=font)
        y += int(size * 1.45)
    rng = np.random.default_rng(1)
    a = np.asarray(img, np.int16) + rng.normal(0, 6, (h, w)).astype(np.int16)  # tarayıcı gürültüsü
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))


def make_tarama(path):
    pdf = pikepdf.new()
    img = text_image(2480, 3508, 42)  # A4 @ 300 DPI
    b = io.BytesIO()
    img.save(b, "JPEG", quality=92)
    st = pikepdf.Stream(pdf, b.getvalue())
    setd(st, Type=Name.XObject, Subtype=Name.Image, Width=img.width, Height=img.height,
              ColorSpace=Name.DeviceGray, BitsPerComponent=8, Filter=Name.DCTDecode)
    page_with_image(pdf, pdf.make_indirect(st), 595, 842, (0, 0, 595, 842))
    pdf.save(path)


def make_cmyk(path):
    pdf = pikepdf.new()
    w, h = 1800, 1200
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = np.stack([(xx / w * 255), (yy / h * 255), ((xx + yy) % 255)], -1).astype(np.uint8)
    img = Image.fromarray(rgb).filter(ImageFilter.GaussianBlur(3)).convert("CMYK")
    b = io.BytesIO()
    img.save(b, "JPEG", quality=95)  # Pillow CMYK JPEG'leri Adobe işaretiyle (ters) yazar
    st = pikepdf.Stream(pdf, b.getvalue())
    setd(st, Type=Name.XObject, Subtype=Name.Image, Width=w, Height=h,
              ColorSpace=Name.DeviceCMYK, BitsPerComponent=8, Filter=Name.DCTDecode,
              Decode=Array([1, 0, 1, 0, 1, 0, 1, 0]))
    # PDF okuyucular Adobe JPEG'deki ters değerleri Decode ile düzeltir: motor Decode'lu görüntüye
    # dokunmamalı. Aynı görüntünün Decode'suz ikinci kopyası dönüştürülmeli.
    st2 = pikepdf.Stream(pdf, b.getvalue())
    setd(st2, Type=Name.XObject, Subtype=Name.Image, Width=w, Height=h,
               ColorSpace=Name.DeviceCMYK, BitsPerComponent=8, Filter=Name.DCTDecode)
    page_with_image(pdf, pdf.make_indirect(st), 612, 792, (36, 400, 540, 360))
    page_with_image(pdf, pdf.make_indirect(st2), 612, 792, (36, 400, 540, 360))
    pdf.save(path)


def make_zincir(path):
    """iLovePDF/ABBYY gibi: JPEG'in üstüne bir de Flate ([/FlateDecode /DCTDecode]), 300 DPI."""
    pdf = pikepdf.new()
    w, h = 1800, 1200
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = np.stack([(xx / w * 255), (yy / h * 255), ((xx * yy) % 255)], -1).astype(np.uint8)
    img = Image.fromarray(rgb).filter(ImageFilter.GaussianBlur(2))
    b = io.BytesIO()
    img.save(b, "JPEG", quality=95)
    st = pikepdf.Stream(pdf, zlib.compress(b.getvalue()))
    setd(st, Type=Name.XObject, Subtype=Name.Image, Width=w, Height=h,
              ColorSpace=Name.DeviceRGB, BitsPerComponent=8,
              Filter=Array([Name.FlateDecode, Name.DCTDecode]))
    page_with_image(pdf, pdf.make_indirect(st), 612, 792, (90, 400, 432, 288))
    pdf.save(path)


def make_fotograf(path):
    """Fotoğraf makinesiyle çekilmiş kitap sayfası: gri kağıt, gri harfler, hafif renk; sayfa
    "piksel/72" boyutunda (60x88 cm) kaydedilmiş (bazı tarama servisleri böyle)."""
    pdf = pikepdf.new()
    w, h = 1740, 2540
    g = np.asarray(text_image(w, h, 30), np.float32)
    g = 50 + (g - 25) * (185 - 50) / (235 - 25)  # harfler ~50, kağıt ~185
    yy, xx = np.mgrid[0:h, 0:w]
    g = g * (0.9 + 0.1 * xx / w)  # ışık eğimi
    rgb = np.stack([g * 1.03, g, g * 0.92], -1)  # sarımsı ton
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    b = io.BytesIO()
    img.save(b, "JPEG", quality=90)
    st = pikepdf.Stream(pdf, b.getvalue())
    setd(st, Type=Name.XObject, Subtype=Name.Image, Width=w, Height=h,
              ColorSpace=Name.DeviceRGB, BitsPerComponent=8, Filter=Name.DCTDecode)
    page_with_image(pdf, pdf.make_indirect(st), w, h, (0, 0, w, h))
    pdf.save(path)


def make_cift(path):
    """Fotokopi çift sayfa: iki metin sayfası yan yana, ortada kitap sırtı gölgesi, altta ve
    kenarlarda siyah bant (gri JPEG, 200 DPI). İki sayfası da böyle."""
    pdf = pikepdf.new()
    pw, ph = 1650, 2340  # tek sayfa ~ A4 @ 200 DPI
    for k in range(2):
        left, right = text_image(pw, ph, 28), text_image(pw, ph, 28)
        spread = np.concatenate([np.asarray(left, np.float32), np.asarray(right, np.float32)], axis=1)
        w = spread.shape[1]
        xx = np.arange(w)
        spread *= (1 - 0.45 * np.exp(-((xx - w / 2) / 60.0) ** 2))[None, :]  # sırt gölgesi
        spread[:, :70] = 20
        spread[:, -70:] = 20
        spread[-120:, :] = 20
        img = Image.fromarray(np.clip(spread, 0, 255).astype(np.uint8))
        b = io.BytesIO()
        img.save(b, "JPEG", quality=85)
        st = pikepdf.Stream(pdf, b.getvalue())
        setd(st, Type=Name.XObject, Subtype=Name.Image, Width=img.width, Height=img.height,
                  ColorSpace=Name.DeviceGray, BitsPerComponent=8, Filter=Name.DCTDecode)
        wpt, hpt = img.width * 72 / 200, img.height * 72 / 200
        page_with_image(pdf, pdf.make_indirect(st), wpt, hpt, (0, 0, wpt, hpt))
    pdf.save(path)


def make_resimli(path):
    """Tarama sayfası: üstte metin, altta fotoğraf (gri JPEG, 300 DPI)."""
    pdf = pikepdf.new()
    g = np.asarray(text_image(2480, 3508, 42), np.float32)
    yy, xx = np.mgrid[0:1500, 0:1900]
    photo = 128 + 70 * np.sin(xx / 90.0) * np.cos(yy / 70.0) + 30 * np.sin((xx + yy) / 23.0)
    g[1800:3300, 290:2190] = photo
    img = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8))
    b = io.BytesIO()
    img.save(b, "JPEG", quality=90)
    st = pikepdf.Stream(pdf, b.getvalue())
    setd(st, Type=Name.XObject, Subtype=Name.Image, Width=img.width, Height=img.height,
              ColorSpace=Name.DeviceGray, BitsPerComponent=8, Filter=Name.DCTDecode)
    page_with_image(pdf, pdf.make_indirect(st), 595, 842, (0, 0, 595, 842))
    pdf.save(path)


def make_grafik(path):
    """Çok yüksek çözünürlükte (~1500 DPI) kayıpsız çizelge: az renkli, yazılı (LaTeX makaleleri)."""
    pdf = pikepdf.new()
    w, h = 6000, 3000
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path(), 90)
    for i in range(20):
        x = 300 + i * 280
        top = 2700 - (i * 37 % 11 + 3) * 200
        d.rectangle((x, top, x + 200, 2700), fill=(60, 90, 160), outline="black", width=6)
        d.text((x + 40, top - 110), str(i * 37 % 11 + 3), fill="black", font=font)
    d.line((250, 2700, 5900, 2700), fill="black", width=8)
    st = pikepdf.Stream(pdf, img.tobytes())
    setd(st, Type=Name.XObject, Subtype=Name.Image, Width=w, Height=h,
              ColorSpace=Name.DeviceRGB, BitsPerComponent=8)
    page_with_image(pdf, pdf.make_indirect(st), 612, 792, (90, 400, 432, 216))
    pdf.save(path)


def make_profil(path):
    """Küçük bir CMYK resme bağlı dev (300 KB) baskı renk profili (yayıncı/CorelDRAW çıktıları)."""
    pdf = pikepdf.new()
    prof = pikepdf.Stream(pdf, big_cmyk_icc())
    setd(prof, N=4, Alternate=Name.DeviceCMYK)
    img = Image.new("CMYK", (150, 150), (20, 160, 0, 10))
    st = pikepdf.Stream(pdf, img.tobytes())
    setd(st, Type=Name.XObject, Subtype=Name.Image, Width=150, Height=150, BitsPerComponent=8,
         ColorSpace=Array([Name.ICCBased, pdf.make_indirect(prof)]))
    page_with_image(pdf, pdf.make_indirect(st), 612, 792, (200, 400, 150, 150))
    pdf.save(path)


def big_cmyk_icc(nbytes=300_000):
    """Gerçek bir CMYK profili + sonunda sıkıştırılamayan dolgu (dev baskı profili yerine)."""
    icc_path = "/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc"
    if os.path.exists(icc_path):
        icc = open(icc_path, "rb").read()
    else:
        from PIL import ImageCms
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()  # yalnızca yer tutucu
    pad = np.random.default_rng(5).integers(0, 256, nbytes, dtype=np.uint8).tobytes()
    return (len(icc) + len(pad)).to_bytes(4, "big") + icc[4:] + pad


def make_baskihedef(path):
    """PDF/X: yalnızca baskı hedefi (OutputIntent) olarak gömülmüş 300 KB CMYK profil."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(595, 842))
    prof = pikepdf.Stream(pdf, big_cmyk_icc())
    setd(prof, N=4, Alternate=Name.DeviceCMYK)
    oi = Dictionary(Type=Name.OutputIntent, S=Name.GTS_PDFX, OutputConditionIdentifier=pikepdf.String("FOGRA27"),
                    DestOutputProfile=pdf.make_indirect(prof))
    pdf.Root.OutputIntents = Array([oi])
    pdf.save(path)


def make_jp2cmyk(path):
    """Baskı için CMYK JPEG 2000 fotoğraf (InDesign/Acrobat çıktıları), 150 DPI."""
    pdf = pikepdf.new()
    w, h = 900, 600
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = np.stack([(xx / w * 255), (yy / h * 255), ((xx * 3 + yy) % 255)], -1).astype(np.uint8)
    img = Image.fromarray(rgb).filter(ImageFilter.GaussianBlur(2)).convert("CMYK")
    b = io.BytesIO()
    img.save(b, "JPEG2000", irreversible=False)
    st = pikepdf.Stream(pdf, b.getvalue())
    setd(st, Type=Name.XObject, Subtype=Name.Image, Width=w, Height=h,
              ColorSpace=Name.DeviceCMYK, BitsPerComponent=8, Filter=Name.JPXDecode)
    page_with_image(pdf, pdf.make_indirect(st), 612, 792, (90, 400, 432, 288))
    pdf.save(path)


def make_siyahbeyaz(path):
    pdf = pikepdf.new()
    g = text_image(4960, 7016, 84)  # A4 @ 600 DPI
    bw = g.point(lambda v: 255 if v > 128 else 0).convert("1", dither=Image.Dither.NONE)
    st = pikepdf.Stream(pdf, bw.tobytes())  # pikepdf kaydederken Flate ile sıkıştırır
    setd(st, Type=Name.XObject, Subtype=Name.Image, Width=bw.width, Height=bw.height,
              ColorSpace=Name.DeviceGray, BitsPerComponent=1)
    page_with_image(pdf, pdf.make_indirect(st), 595, 842, (0, 0, 595, 842))
    pdf.save(path)


def make_ters(path):
    """1 bit tarama, örnekler ters saklanmış + /Decode [1 0] (eski Acrobat taramaları gibi)."""
    pdf = pikepdf.new()
    g = text_image(2480, 3508, 42)
    bw = g.point(lambda v: 0 if v > 128 else 255).convert("1", dither=Image.Dither.NONE)  # ters
    st = pikepdf.Stream(pdf, bw.tobytes())
    setd(st, Type=Name.XObject, Subtype=Name.Image, Width=bw.width, Height=bw.height,
         ColorSpace=Name.DeviceGray, BitsPerComponent=1, Decode=Array([1, 0]))
    # 400 DPI: Aşırı'da çözünürlük düşürme de ters görüntü üzerinde sınanır
    page_with_image(pdf, pdf.make_indirect(st), 595, 842, (74, 105, 446, 631))
    pdf.save(path)


def make_paletli(path):
    """256 renge indirilmiş fotoğraf (Indexed RGB, Flate) — eski iText/Word çıktılarında görülür."""
    pdf = pikepdf.new()
    w, h = 900, 700
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:h, 0:w]
    rgb = np.stack([(np.sin(xx / 40) + 1) * 110 + yy / 8, (np.cos(yy / 35) + 1) * 100 + xx / 9,
                    (xx + yy) % 200 + 20], -1) + rng.normal(0, 12, (h, w, 3))
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1))
    pal = img.quantize(256)
    lut = bytes(pal.getpalette()[: 256 * 3])
    st = pikepdf.Stream(pdf, pal.tobytes())
    setd(st, Type=Name.XObject, Subtype=Name.Image, Width=w, Height=h, BitsPerComponent=8,
         ColorSpace=Array([Name.Indexed, Name.DeviceRGB, 255, pikepdf.String(lut)]))
    page_with_image(pdf, pdf.make_indirect(st), 612, 792, (60, 300, 450, 350))
    pdf.save(path)


def make_vektor(path):
    """Metni harf harf eğri olarak çizen sayfa ("Yazdır > PDF" gibi): font yok, 6 ondalık."""
    from fontTools.pens.basePen import BasePen
    from fontTools.ttLib import TTFont
    tt = TTFont(font_path())
    gs, cmap, upm = tt.getGlyphSet(), tt.getBestCmap(), tt["head"].unitsPerEm
    out = []

    class Pen(BasePen):
        def __init__(self, s, ox, oy):
            super().__init__(gs)
            self.s, self.ox, self.oy = s, ox, oy

        def p(self, pt):
            return f"{self.ox + pt[0] * self.s:.6f} {self.oy + pt[1] * self.s:.6f}"

        def _moveTo(self, pt):
            out.append(f"{self.p(pt)} m")

        def _lineTo(self, pt):
            out.append(f"{self.p(pt)} l")

        def _curveToOne(self, a, b, c):
            out.append(f"{self.p(a)} {self.p(b)} {self.p(c)} c")

        def _closePath(self):
            out.append("h")

    size, s = 10.5, 10.5 / upm
    words, y = (TEXT + " ") * 30, 790.0
    x, line = 50.0, []
    out.append("0.1 0.1 0.1 rg")
    for ch in words:
        if y < 50:
            break
        name = cmap.get(ord(ch))
        if name is None:
            continue
        adv = gs[name].width * s
        if x + adv > 545 and ch == " ":
            x, y = 50.0, y - size * 1.4
            continue
        gs[name].draw(Pen(s, x, y))
        out.append("f")
        x += adv
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(595, 842))
    page.Contents = pdf.make_stream("\n".join(out).encode())
    pdf.save(path)


def make_font(path):
    from fontTools.ttLib import TTFont
    data = open(font_path(), "rb").read()
    tt = TTFont(io.BytesIO(data))
    upm = tt["head"].unitsPerEm
    cmap = tt.getBestCmap()
    order = tt.getGlyphOrder()
    gid = {n: i for i, n in enumerate(order)}
    hmtx = tt["hmtx"]
    pdf = pikepdf.new()
    ff = pdf.make_indirect(pikepdf.Stream(pdf, data))
    ff.Length1 = len(data)
    name = Name("/" + tt["name"].getDebugName(6).replace(" ", ""))

    def desc(flags):
        return pdf.make_indirect(Dictionary(
            Type=Name.FontDescriptor, FontName=name, Flags=flags, FontBBox=Array([-500, -300, 2000, 1000]),
            ItalicAngle=0, Ascent=900, Descent=-250, CapHeight=700, StemV=80, FontFile2=ff))

    # basit TrueType (WinAnsi): İngilizce metin
    widths = [round(hmtx[cmap.get(c, ".notdef")][0] * 1000 / upm) if c in cmap else 0 for c in range(32, 256)]
    simple = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.TrueType, BaseFont=name, FirstChar=32, LastChar=255,
        Widths=Array(widths), Encoding=Name.WinAnsiEncoding, FontDescriptor=desc(32)))

    # CID TrueType (Identity-H): Türkçe metin, ToUnicode ile
    used = sorted({ch for ch in TEXT if ord(ch) in cmap})
    gids = {ch: gid[cmap[ord(ch)]] for ch in used}
    tounicode = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /U def "
                 "/CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange "
                 f"{len(used)} beginbfchar " +
                 " ".join(f"<{gids[c]:04X}> <{ord(c):04X}>" for c in used) +
                 " endbfchar endcmap CMapName currentdict /CMap defineresource pop end end")
    w_arr = Array()
    for c in used:
        w_arr.extend([gids[c], Array([round(hmtx[cmap[ord(c)]][0] * 1000 / upm)])])
    cid = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.CIDFontType2, BaseFont=name, CIDToGIDMap=Name.Identity,
        CIDSystemInfo=Dictionary(Registry=pikepdf.String("Adobe"), Ordering=pikepdf.String("Identity"), Supplement=0),
        FontDescriptor=desc(32), W=w_arr))
    type0 = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type0, BaseFont=name, Encoding=Name("/Identity-H"),
        DescendantFonts=Array([cid]), ToUnicode=pdf.make_stream(tounicode.encode())))

    page = pdf.add_blank_page(page_size=(595, 842))
    page.Resources = Dictionary(Font=Dictionary(F1=simple, F2=type0))
    hexline = "".join(f"{gids[c]:04X}" for c in TEXT[:60] if c in gids)
    content = ("BT /F1 14 Tf 60 780 Td (Environmental law and sustainable development.) Tj ET\n"
               f"BT /F2 12 Tf 60 740 Td <{hexline}> Tj ET\n")
    page.Contents = pdf.make_stream(content.encode())
    pdf.save(path)


def main(out):
    os.makedirs(out, exist_ok=True)
    for name, fn in [("tarama", make_tarama), ("cmyk", make_cmyk),
                     ("siyahbeyaz", make_siyahbeyaz), ("ters", make_ters), ("font", make_font),
                     ("paletli", make_paletli), ("vektor", make_vektor), ("zincir", make_zincir),
                     ("fotograf", make_fotograf), ("grafik", make_grafik), ("profil", make_profil),
                     ("cift", make_cift), ("resimli", make_resimli), ("baskihedef", make_baskihedef),
                     ("jp2cmyk", make_jp2cmyk)]:
        p = os.path.join(out, name + ".pdf")
        fn(p)
        print(f"{name}.pdf  {os.path.getsize(p) / 1024:.0f} KB")


if __name__ == "__main__":
    main(sys.argv[1])
