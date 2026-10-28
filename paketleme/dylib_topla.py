"""Homebrew ile gelen komut satırı araçlarını (jbig2, jpegtran) bağımlı .dylib'leriyle birlikte
taşınabilir hale getirir: kütüphaneler bin/libs içine kopyalanır, yollar @executable_path /
@loader_path'e çevrilir, ad-hoc imzalanır. @rpath bağımlılıkları da çözülür.

  python dylib_topla.py <bin klasörü> <araç> [<araç> ...]
"""
import os
import shutil
import subprocess
import sys

SYSTEM = ("/usr/lib/", "/System/")


def deps(path):
    out = subprocess.run(["otool", "-L", path], capture_output=True, text=True, check=True).stdout
    return [ln.strip().split(" (")[0] for ln in out.splitlines()[1:] if ln.strip()]


def rpaths(path):
    out = subprocess.run(["otool", "-l", path], capture_output=True, text=True, check=True).stdout.splitlines()
    res = []
    for i, ln in enumerate(out):
        if "cmd LC_RPATH" in ln:
            for nxt in out[i + 1:i + 4]:
                if nxt.strip().startswith("path "):
                    res.append(nxt.strip()[5:].split(" (offset")[0])
    return res


def resolve(dep, referrer, origin):
    """'origin', referrer'ın Homebrew'daki gerçek konumu (@loader_path için)."""
    if dep.startswith("@rpath/"):
        for rp in rpaths(referrer):
            base = rp.replace("@loader_path", os.path.dirname(origin)).replace(
                "@executable_path", os.path.dirname(origin))
            cand = os.path.normpath(os.path.join(base, dep[len("@rpath/"):]))
            if os.path.exists(cand):
                return cand
        cand = os.path.join(os.path.dirname(origin), dep[len("@rpath/"):])
        return cand if os.path.exists(cand) else None
    if dep.startswith("@loader_path/"):
        cand = os.path.normpath(os.path.join(os.path.dirname(origin), dep[len("@loader_path/"):]))
        return cand if os.path.exists(cand) else None
    return dep if os.path.exists(dep) else None


def main(bindir, tools):
    libs = os.path.join(bindir, "libs")
    os.makedirs(libs, exist_ok=True)
    origins = {}  # kopyalanan dosya -> Homebrew'daki gerçek yolu
    queue = []
    for t in tools:
        p = os.path.join(bindir, t)
        origins[p] = os.path.realpath(shutil.which(t) or p)
        queue.append(p)
    done = set()
    while queue:
        f = queue.pop()
        if f in done:
            continue
        done.add(f)
        is_exe = os.path.dirname(f) == bindir
        os.chmod(f, 0o755)
        for d in deps(f):
            if d.startswith(SYSTEM):
                continue
            if os.path.basename(d) == os.path.basename(f) and not is_exe:
                continue  # kütüphanenin kendi kimliği
            real = resolve(d, f, origins[f])
            if real is None:
                raise SystemExit(f"çözülemedi: {d} ({f})")
            name = os.path.basename(real)
            dst = os.path.join(libs, name)
            if not os.path.exists(dst):
                shutil.copy2(os.path.realpath(real), dst)
                os.chmod(dst, 0o755)
                origins[dst] = os.path.realpath(real)
                queue.append(dst)
            new = f"@executable_path/libs/{name}" if is_exe else f"@loader_path/{name}"
            subprocess.run(["install_name_tool", "-change", d, new, f], check=True, capture_output=True)
        if not is_exe:
            subprocess.run(["install_name_tool", "-id", f"@loader_path/{os.path.basename(f)}", f],
                           check=True, capture_output=True)
        for rp in rpaths(f):
            subprocess.run(["install_name_tool", "-delete_rpath", rp, f], capture_output=True)
    for f in done:
        subprocess.run(["codesign", "-f", "-s", "-", f], check=True, capture_output=True)
    # doğrulama: Homebrew yolu kalmamalı
    for f in done:
        bad = [d for d in deps(f) if d.startswith(("/opt/homebrew", "/usr/local", "@rpath"))]
        if bad:
            raise SystemExit(f"{f} hâlâ dışa bağımlı: {bad}")
    print(f"{len(done) - len(tools)} kütüphane paketlendi")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
