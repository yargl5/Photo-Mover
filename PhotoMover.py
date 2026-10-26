import os
import queue
import shutil
import threading
import time
import hashlib
import ntpath
import sys
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import mtp_backend


class PhoneMover:
    def __init__(self, root):
        self.root = root
        self.root.title("Photo Mover")
        self.root.geometry("900x680")
        # Action controls stay reachable while the log area shrinks.
        self.root.minsize(640, 560)
        self.root.resizable(True, True)
        self.root.configure(bg="#0f172a")
        try:
            self.root.tk.call("tk", "scaling", max(1.0, self.root.winfo_fpixels("1i") / 72.0))
        except tk.TclError:
            pass
        self.source = tk.StringVar()
        self.destination = tk.StringVar()
        self.mtp_destination = None
        self.status = tk.StringVar(value="Готово к переносу")
        self.details = tk.StringVar(value="Выберите папки и нажмите «Начать перенос»")
        self.percent = tk.DoubleVar(value=0)
        self.cancelled = False
        self.running = False
        self.fullscreen = False
        self._last_width = 0
        self.events = queue.Queue()
        self._style()
        self._build()
        self.root.bind("<F11>", lambda _event: self.toggle_fullscreen())
        self.root.bind("<Escape>", lambda _event: self._leave_fullscreen())
        self.root.bind("<Configure>", self._on_resize)
        self.root.after(100, self._poll)

    def _style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#0f172a")
        style.configure("Card.TFrame", background="#172033")
        style.configure("TLabel", background="#0f172a", foreground="#e5e7eb", font=("Segoe UI", 10))
        style.configure("Title.TLabel", background="#0f172a", foreground="#f8fafc", font=("Segoe UI Semibold", 24))
        style.configure("Muted.TLabel", background="#0f172a", foreground="#94a3b8", font=("Segoe UI", 10))
        style.configure("CardLabel.TLabel", background="#172033", foreground="#cbd5e1", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI Semibold", 10), padding=(12, 8))
        style.configure("Accent.TButton", background="#38bdf8", foreground="#082f49")
        style.map("Accent.TButton", background=[("active", "#7dd3fc")])
        style.configure("Danger.TButton", background="#334155", foreground="#f8fafc")
        style.configure("Horizontal.TProgressbar", troughcolor="#263449", background="#38bdf8", bordercolor="#263449", lightcolor="#38bdf8", darkcolor="#38bdf8", thickness=18)

    def _build(self):
        outer = ttk.Frame(self.root, padding=28)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        title_box = ttk.Frame(header)
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="Photo Mover", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="Перенос фото на телефон без лишних копий", style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        self.mode_btn = ttk.Button(header, text="⛶  Полный экран", command=self.toggle_fullscreen)
        self.mode_btn.pack(side="right", anchor="n", pady=(3, 0))
        ttk.Label(outer, text="Источник останется без изменений, если проверка копии не пройдена", style="Muted.TLabel").pack(anchor="w", pady=(8, 24))

        card = ttk.Frame(outer, style="Card.TFrame", padding=20)
        card.pack(fill="x")
        self._folder_row(card, "Откуда переносить", self.source, self._pick_source, 0)
        self._folder_row(card, "Куда переносить", self.destination, self._pick_destination, 1)
        self.phone_btn = ttk.Button(card, text="Телефон USB / MTP…", command=self._pick_phone)
        self.phone_btn.grid(row=2, column=1, sticky="w", padx=16, pady=(8, 0))

        progress = ttk.Frame(outer, style="Card.TFrame", padding=20)
        progress.pack(fill="x", pady=(18, 0))
        ttk.Label(progress, textvariable=self.status, style="CardLabel.TLabel", font=("Segoe UI Semibold", 12)).pack(anchor="w")
        ttk.Label(progress, textvariable=self.details, style="CardLabel.TLabel").pack(anchor="w", pady=(4, 14))
        ttk.Progressbar(progress, variable=self.percent, maximum=100, style="Horizontal.TProgressbar").pack(fill="x")
        self.percent_label = ttk.Label(progress, text="0%", style="CardLabel.TLabel")
        self.percent_label.pack(anchor="e", pady=(5, 0))

        bottom = ttk.Frame(outer)
        bottom.pack(fill="both", expand=True, pady=(18, 0))
        # Pack actions first at bottom. The growing log can never push them out of view.
        buttons = ttk.Frame(bottom)
        buttons.pack(side="bottom", fill="x", pady=(14, 0))
        self.start_btn = ttk.Button(buttons, text="Начать перенос", style="Accent.TButton", command=self.start)
        self.start_btn.pack(side="left")
        self.cancel_btn = ttk.Button(buttons, text="Остановить", style="Danger.TButton", command=self.cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=(10, 0))
        ttk.Label(bottom, text="Журнал", style="Muted.TLabel").pack(anchor="w")
        self.log = tk.Text(bottom, height=8, bg="#0b1220", fg="#94a3b8", insertbackground="#e5e7eb", relief="flat", font=("Consolas", 9), padx=12, pady=10, state="disabled")
        self.log.pack(fill="both", expand=True, pady=(6, 0))

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)
        self.mode_btn.configure(text="▣  Оконный режим" if self.fullscreen else "⛶  Полный экран")
        self.status.set("Полный экран включён" if self.fullscreen else "Оконный режим включён")

    def _leave_fullscreen(self):
        if self.fullscreen:
            self.fullscreen = False
            self.root.attributes("-fullscreen", False)
            self.mode_btn.configure(text="⛶  Полный экран")

    def _on_resize(self, event):
        if event.widget is not self.root or event.width == self._last_width:
            return
        self._last_width = event.width
        # Keep compact spacing on small screens; give content more breathing room on wide screens.
        pad = 18 if event.width < 760 else 28
        for child in self.root.winfo_children():
            if isinstance(child, ttk.Frame):
                try:
                    child.configure(padding=pad)
                except tk.TclError:
                    pass

    def _folder_row(self, parent, label, variable, command, row):
        ttk.Label(parent, text=label, style="CardLabel.TLabel").grid(row=row, column=0, sticky="w", pady=8)
        entry = tk.Entry(parent, textvariable=variable, bg="#0f172a", fg="#e5e7eb", insertbackground="#e5e7eb", relief="flat", font=("Segoe UI", 10))
        if variable is self.destination:
            entry.configure(state="readonly", readonlybackground="#0f172a")
        entry.grid(row=row, column=1, sticky="ew", padx=16, ipady=7)
        ttk.Button(parent, text="Выбрать…", command=command).grid(row=row, column=2)
        parent.columnconfigure(1, weight=1)

    def _pick_source(self):
        value = filedialog.askdirectory(title="Выберите папку с файлами")
        if value: self.source.set(value)

    def _pick_destination(self):
        if self.running: return
        value = filedialog.askdirectory(title="Выберите папку на телефоне")
        if value:
            self.mtp_destination = None
            self.destination.set(value)

    def _pick_phone(self):
        if self.running: return
        dialog = tk.Toplevel(self.root)
        dialog.title("Телефон USB / MTP")
        dialog.geometry("660x470")
        dialog.minsize(480, 360)
        dialog.transient(self.root)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Разблокируйте телефон. USB → «Передача файлов».", wraplength=590).pack(anchor="w")
        location = tk.StringVar(value="Подключённые устройства")
        ttk.Label(body, textvariable=location, wraplength=590).pack(fill="x", pady=10)
        toolbar = ttk.Frame(body)
        toolbar.pack(side="bottom", fill="x", pady=(12, 0))
        listing = tk.Listbox(body, bg="#172033", fg="#e5e7eb", selectbackground="#0369a1", font=("Segoe UI", 11), relief="flat", exportselection=False)
        listing.pack(fill="both", expand=True)
        state = {"device": None, "path": "\\", "rows": [], "busy": False}
        answers = queue.Queue()

        def load(dev=None, path="\\"):
            if state["busy"]: return
            state["busy"] = True
            listing.delete(0, "end")
            location.set("Подключение…")
            choose.configure(state="disabled")
            def work():
                try:
                    result = mtp_backend.request("directories", device=dev["id"], path=path) if dev else mtp_backend.request("devices")
                    answers.put((dev, path, result, None))
                except Exception as exc:
                    answers.put((dev, path, None, str(exc)))
            threading.Thread(target=work, daemon=True).start()

        def poll():
            if not dialog.winfo_exists(): return
            try:
                dev, path, result, error = answers.get_nowait()
                state.update(busy=False, device=dev, path=path)
                if error:
                    state["rows"] = []
                    location.set(error)
                else:
                    rows = result["directories"] if dev else result["devices"]
                    state["rows"] = rows
                    location.set((dev["name"] + " · " + path) if dev else "Выберите телефон двойным щелчком" if rows else "Устройств нет. Включите передачу файлов и нажмите «Обновить».")
                    for row in rows:
                        listing.insert("end", ntpath.basename(row.rstrip("\\")) if dev else row["name"])
                    choose.configure(state="normal" if dev and path != "\\" else "disabled")
            except queue.Empty: pass
            dialog.after(100, poll)

        def enter(_event=None):
            if state["busy"] or not listing.curselection(): return
            row = state["rows"][listing.curselection()[0]]
            load(state["device"], row) if state["device"] else load(row)

        def up():
            if not state["device"] or state["path"] == "\\": load()
            else: load(state["device"], ntpath.dirname(state["path"].rstrip("\\")) or "\\")

        def select():
            if state["busy"] or not state["device"] or state["path"] == "\\": return
            self.mtp_destination = (state["device"]["id"], state["path"])
            self.destination.set(state["device"]["name"] + " · " + state["path"])
            dialog.destroy()

        ttk.Button(toolbar, text="Назад", command=up).pack(side="left")
        ttk.Button(toolbar, text="Обновить", command=lambda: load(state["device"], state["path"])).pack(side="left", padx=8)
        choose = ttk.Button(toolbar, text="Выбрать эту папку", command=select, state="disabled")
        choose.pack(side="right")
        listing.bind("<Double-Button-1>", enter)
        listing.bind("<Return>", enter)
        load()
        poll()

    def _write_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self):
        if self.running: return
        src, dst = Path(self.source.get()), Path(self.destination.get())
        if not self.source.get().strip() or not self.destination.get().strip() or not src.is_dir() or (not self.mtp_destination and not dst.is_dir()):
            messagebox.showwarning("Нужны папки", "Выберите существующие папки источника и назначения.")
            return
        try:
            if not self.mtp_destination and (src.resolve() == dst.resolve() or dst.resolve().is_relative_to(src.resolve())):
                messagebox.showwarning("Неверные папки", "Папка назначения не должна быть внутри папки источника.")
                return
        except (OSError, ValueError):
            pass
        self.running, self.cancelled = True, False
        self.start_btn.configure(state="disabled"); self.cancel_btn.configure(state="normal")
        self.percent.set(0); self.percent_label.configure(text="0%")
        threading.Thread(target=self._transfer, args=(src, self.mtp_destination or dst), daemon=True).start()

    def cancel(self):
        if self.running:
            self.cancelled = True
            self.status.set("Остановка…")

    def _transfer(self, src, dst):
        try:
            if isinstance(dst, tuple):
                with mtp_backend.Session() as session:
                    self._transfer_files(src, dst, session)
            else:
                self._transfer_files(src, dst)
        except Exception as exc:
            self.events.put(("info", str(exc)))
            self.events.put(("finish", "Перенос прерван. Непроверенные оригиналы сохранены."))

    def _transfer_files(self, src, dst, session=None):
        files = []
        for root, _, names in os.walk(src):
            for name in names:
                p = Path(root) / name
                try: files.append((p, p.relative_to(src), p.stat().st_size))
                except OSError: pass
        total = sum(x[2] for x in files); done = 0; started = time.monotonic(); moved = 0
        self.events.put(("info", f"Найдено файлов: {len(files)}"))
        if not files: self.events.put(("finish", "В папке нет файлов для переноса.")); return
        for index, (source, relative, size) in enumerate(files, 1):
            if self.cancelled: break
            try:
                self.events.put(("phase", "Передача и проверка: " + source.name))
                if isinstance(dst, tuple):
                    result = session.request(
                        "upload", device=dst[0], path=ntpath.join(dst[1], str(relative)),
                        source=str(source.resolve()), cancelled=lambda: self.cancelled, timeout=86400,
                    )
                    expected = result["sha256"]
                    upload_time = result.get("upload_seconds", 0)
                    verify_time = result.get("verify_seconds", 0)
                    self.events.put(("info", f"{source.name}: запись {upload_time:.2f} с, проверка {verify_time:.2f} с"))
                else:
                    target = dst / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # Exclusive creation prevents overwriting existing photos.
                    with source.open("rb") as inp, target.open("xb") as out:
                        shutil.copyfileobj(inp, out, 1024 * 1024)
                        out.flush()
                        os.fsync(out.fileno())
                    expected = self._hash(target)
                if self.cancelled:
                    raise mtp_backend.TransferCancelled("Остановлено. Оригинал сохранён.")
                if source.stat().st_size != size or self._hash(source) != expected:
                    raise IOError("содержимое копии не совпадает с оригиналом")
                source.unlink()
                moved += 1; done += size
                elapsed = max(time.monotonic() - started, 0.001)
                speed = done / elapsed; remaining = (total - done) / speed if speed else 0
                self.events.put(("progress", done / total * 100 if total else index / len(files) * 100, f"{index}/{len(files)}  •  {source.name}", f"{speed / 1048576:.1f} МБ/с  •  осталось примерно {self._eta(remaining)}"))
            except Exception as exc:
                self.events.put(("info", f"Ошибка: {relative} — {exc}"))
        if self.cancelled: self.events.put(("finish", f"Остановлено. Успешно перенесено: {moved} из {len(files)}."))
        else: self.events.put(("finish", f"Готово. Перенесено и удалено с ПК: {moved} файлов."))

    @staticmethod
    def _hash(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    @staticmethod
    def _eta(seconds):
        if seconds < 60: return f"{int(seconds)} сек"
        return f"{int(seconds // 60)} мин {int(seconds % 60):02d} сек"

    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "info": self._write_log(event[1])
                elif event[0] == "phase": self.status.set(event[1])
                elif event[0] == "progress":
                    _, value, line, detail = event; self.percent.set(value); self.percent_label.configure(text=f"{value:.1f}%"); self.status.set(line); self.details.set(detail)
                elif event[0] == "finish":
                    self.running = False; self.start_btn.configure(state="normal"); self.cancel_btn.configure(state="disabled"); self.status.set(event[1]); self.details.set("Можно выбрать новую папку и запустить следующий перенос"); self._write_log(event[1])
        except queue.Empty: pass
        self.root.after(100, self._poll)


if __name__ == "__main__":
    root = tk.Tk()
    app = PhoneMover(root)
    if len(sys.argv) == 3 and sys.argv[1] == "--diagnose-mtp":
        root.geometry("640x560")
        root.update()
        report = {"window": root.geometry(), "ui_started": True}
        try:
            report["devices"] = mtp_backend.request("devices")["devices"]
            report["storages"] = [mtp_backend.request("directories", device=d["id"], path="\\")["directories"] for d in report["devices"]]
        except Exception as exc:
            report["error"] = str(exc)
        Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        root.destroy()
    else:
        root.mainloop()
