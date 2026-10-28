"""PDF Küçült — Windows arayüzü (tkinter).

Masaüstündeki simgeye ya da "Gönder › PDF Küçült" menüsüyle gelen dosyalar listede hazır olur.
Motor aynı uygulamanın '--motor' kipiyle ayrı bir süreçte çalışır; ilerleme, Mac uygulamasıyla
aynı durum dosyası protokolüyle (T/D/S satırları) izlenir.
"""
import os
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

NO_WINDOW = 0x08000000


def engine_cmd():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--motor"]
    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "motor.py")]


def human_paths(paths):
    return [os.path.abspath(p) for p in paths if os.path.isdir(p) or p.lower().endswith(".pdf")]


class App:
    def __init__(self, root, initial):
        self.root = root
        self.proc = None
        self.status_file = None
        root.title("PDF Küçült")
        root.geometry("640x460")
        root.minsize(520, 380)
        try:
            root.iconbitmap(os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(__file__)), "simge.ico"))
        except Exception:
            pass

        pad = {"padx": 14, "pady": 6}
        ttk.Label(root, text="PDF Küçült", font=("Segoe UI", 16, "bold")).pack(anchor="w", **pad)
        ttk.Label(root, text="PDF dosyalarını veya klasörleri ekleyin (pencereye sürükleyebilirsiniz). "
                             "Akıllı sıkıştırma metni okunur tutar; görseller gri, taranmış sayfalar "
                             "siyah-beyaz olur. Orijinal dosyalara dokunulmaz.", wraplength=580).pack(anchor="w", padx=14)

        frm = ttk.Frame(root)
        frm.pack(fill="both", expand=True, **pad)
        self.listbox = tk.Listbox(frm, height=8, activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frm, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)

        btns = ttk.Frame(root)
        btns.pack(fill="x", padx=14)
        ttk.Button(btns, text="PDF dosyaları ekle…", command=self.add_files).pack(side="left")
        ttk.Button(btns, text="Klasör ekle…", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(btns, text="Listeyi temizle", command=lambda: self.listbox.delete(0, "end")).pack(side="left")

        self.ocr = tk.BooleanVar(value=True)
        ttk.Checkbutton(root, text="Metni olmayan taranmış sayfaları oku (OCR) — aranabilir ve kopyalanabilir olur",
                        variable=self.ocr).pack(anchor="w", padx=14)

        self.progress = ttk.Progressbar(root, mode="determinate")
        self.progress.pack(fill="x", **pad)
        self.msg = ttk.Label(root, text="", wraplength=580)
        self.msg.pack(anchor="w", padx=14)

        act = ttk.Frame(root)
        act.pack(fill="x", padx=14, pady=10)
        self.go = ttk.Button(act, text="Akıllı sıkıştır", command=self.start)
        self.go.pack(side="right")
        self.stop_btn = ttk.Button(act, text="Durdur", command=self.stop, state="disabled")
        self.stop_btn.pack(side="right", padx=6)

        self._enable_drop()
        for p in human_paths(initial):
            self.listbox.insert("end", p)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---- dosya ekleme
    def add_files(self):
        for p in filedialog.askopenfilenames(title="PDF dosyaları", filetypes=[("PDF", "*.pdf")]):
            self.listbox.insert("end", os.path.abspath(p))

    def add_folder(self):
        p = filedialog.askdirectory(title="PDF'lerin bulunduğu klasör (alt klasörler dahil)")
        if p:
            self.listbox.insert("end", os.path.abspath(p))

    def _enable_drop(self):
        try:  # isteğe bağlı: pencereye sürükle-bırak
            import tkinterdnd2
            self.root.drop_target_register(tkinterdnd2.DND_FILES)
            self.root.dnd_bind("<<Drop>>", lambda e: [self.listbox.insert("end", p)
                                                      for p in human_paths(self.root.tk.splitlist(e.data))])
        except Exception:
            pass

    # ---- çalıştırma
    def start(self):
        items = list(self.listbox.get(0, "end"))
        if not items:
            messagebox.showinfo("PDF Küçült", "Önce PDF dosyası veya klasör ekleyin.")
            return
        fd, self.status_file = tempfile.mkstemp(prefix="pdfkucult-", suffix=".txt")
        os.close(fd)
        cmd = engine_cmd() + ["--durum", self.status_file]
        if not self.ocr.get():
            cmd.append("--ocr-yok")
        cmd += items
        self.proc = subprocess.Popen(cmd, creationflags=NO_WINDOW if sys.platform == "win32" else 0,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.go.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress.config(value=0, maximum=1)
        self.msg.config(text="Dosyalar taranıyor…")
        self.root.after(400, self.poll)

    def poll(self):
        report, done = None, False
        try:
            with open(self.status_file, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            lines = []
        for ln in lines:
            parts = ln.split("\t")
            if parts[0] == "T":
                self.progress.config(maximum=max(1, int(parts[1])))
            elif parts[0] == "D" and len(parts) >= 4:
                self.progress.config(value=int(parts[1]))
                self.msg.config(text=f"{parts[1]} / {parts[2]} tamamlandı — {parts[3]}")
            elif parts[0] == "S":
                report, done = (parts[1] if len(parts) > 1 else ""), True
        if not done and self.proc.poll() is not None:
            err = (self.proc.stderr.read() or b"").decode("utf-8", "replace").strip().splitlines()[-5:]
            self.finish()
            messagebox.showerror("PDF Küçült", "Beklenmeyen bir hata oluştu.\n\n" + "\n".join(err))
            return
        if done:
            self.proc.wait()
            summary = ""
            try:
                with open(self.status_file + ".ozet", encoding="utf-8") as f:
                    summary = f.read()
            except OSError:
                pass
            first = self.listbox.get(0)
            self.finish()
            self.show_result(summary, report, first)
            return
        self.root.after(400, self.poll)

    def finish(self):
        self.go.config(state="normal")
        self.stop_btn.config(state="disabled")
        for p in (self.status_file, (self.status_file or "") + ".ozet"):
            try:
                if p:
                    os.remove(p)
            except OSError:
                pass
        self.proc = None

    def show_result(self, summary, report, first):
        self.msg.config(text=summary.replace("\n", "   "))
        dlg = tk.Toplevel(self.root)
        dlg.title("PDF Küçült — tamamlandı")
        dlg.transient(self.root)
        ttk.Label(dlg, text=summary, justify="left", wraplength=460).pack(padx=18, pady=14)
        row = ttk.Frame(dlg)
        row.pack(pady=(0, 14))
        target = first.rstrip("\\/") + " (küçültülmüş)" if os.path.isdir(first) else os.path.dirname(first)
        ttk.Button(row, text="Klasörü aç", command=lambda: os.startfile(target)).pack(side="left", padx=4)
        if report:
            ttk.Button(row, text="Raporu aç", command=lambda: os.startfile(report)).pack(side="left", padx=4)
        ttk.Button(row, text="Tamam", command=dlg.destroy).pack(side="left", padx=4)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                           creationflags=NO_WINDOW, capture_output=True)
        self.finish()
        self.msg.config(text="Durduruldu. Tamamlanan dosyalar yerinde; orijinallere dokunulmadı.")

    def on_close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("PDF Küçült", "İşlem sürüyor. Durdurup çıkılsın mı?"):
                return
            self.stop()
        self.root.destroy()


def run(args):
    try:
        import tkinterdnd2
        root = tkinterdnd2.TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    App(root, args)
    root.mainloop()
    return 0
