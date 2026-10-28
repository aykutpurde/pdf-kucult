"""PDF Küçült uygulama simgesini (iconset) üretir: kırmızı belge + aşağı ok."""
import os
import sys

from PIL import Image, ImageDraw, ImageFont


def draw(size):
    s = size / 1024
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # belge
    d.rounded_rectangle([200 * s, 90 * s, 824 * s, 934 * s], radius=70 * s, fill=(255, 255, 255, 255),
                        outline=(205, 210, 218, 255), width=max(1, int(10 * s)))
    # başlık şeridi
    d.rounded_rectangle([200 * s, 90 * s, 824 * s, 400 * s], radius=70 * s, fill=(214, 48, 49, 255))
    d.rectangle([200 * s, 300 * s, 824 * s, 400 * s], fill=(214, 48, 49, 255))
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", int(170 * s))
        d.text((512 * s, 245 * s), "PDF", fill="white", font=font, anchor="mm")
    except Exception:
        pass
    # sıkıştırma oku
    c = (45, 52, 64, 255)
    d.rectangle([462 * s, 450 * s, 562 * s, 640 * s], fill=c)
    d.polygon([(362 * s, 620 * s), (662 * s, 620 * s), (512 * s, 800 * s)], fill=c)
    d.rectangle([330 * s, 830 * s, 694 * s, 870 * s], fill=c)
    return im


def main(out):
    os.makedirs(out, exist_ok=True)
    big = draw(1024)
    for px in (16, 32, 128, 256, 512):
        big.resize((px, px), Image.LANCZOS).save(os.path.join(out, f"icon_{px}x{px}.png"))
        big.resize((px * 2, px * 2), Image.LANCZOS).save(os.path.join(out, f"icon_{px}x{px}@2x.png"))


if __name__ == "__main__":
    if sys.argv[1] == "--ico":  # Windows simgesi
        draw(256).save(sys.argv[2], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    else:
        main(sys.argv[1])
