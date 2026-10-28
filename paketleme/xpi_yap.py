"""Zotero eklentisini (zotero-eklenti/) .xpi olarak paketler; her platformda '/' ayraçlı zip üretir.

  python xpi_yap.py <çıktı.xpi>
"""
import os
import sys
import zipfile

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "zotero-eklenti")


def main(out):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(SRC):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                if f.startswith("."):
                    continue
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, SRC).replace(os.sep, "/"))
    print(out)


if __name__ == "__main__":
    main(sys.argv[1])
