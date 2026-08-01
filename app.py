from __future__ import annotations

import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from processor import (
    ProcessorSettings,
    TonerSaverError,
    find_first_dark_page,
    make_output_path,
    preview_page,
    process_pdf,
)

APP_TITLE = "Toner Saver PDF | صرفه‌جویی تونر"


class TonerSaverApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("910x680")
        self.minsize(820, 620)
        self.option_add("*Font", ("Segoe UI", 10))

        self.files: list[Path] = []
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.output_var = tk.StringVar(value=str(Path.home() / "Desktop" / "TonerSaver_Output"))
        self.dpi_var = tk.StringVar(value="300")
        self.mode_var = tk.StringVar(value="متعادل")
        self.dark_threshold_var = tk.IntVar(value=125)
        self.coverage_var = tk.IntVar(value=42)
        self.preserve_images_var = tk.BooleanVar(value=True)
        self.force_all_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="آماده")

        self._build_ui()
        self.after(100, self._poll_events)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        header = ttk.Frame(self, padding=(18, 15, 18, 8))
        header.pack(fill="x")
        ttk.Label(header, text="Toner Saver PDF", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(
            header,
            text="حذف هوشمند پس‌زمینه‌های تیره PDFهای خروجی PowerPoint، بدون تبدیل متن فارسی",
        ).pack(anchor="w", pady=(3, 0))

        body = ttk.Frame(self, padding=(18, 8, 18, 12))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        files_frame = ttk.LabelFrame(body, text=" فایل‌های PDF ", padding=10)
        files_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        files_frame.rowconfigure(0, weight=1)
        files_frame.columnconfigure(0, weight=1)

        self.file_list = tk.Listbox(files_frame, selectmode=tk.EXTENDED, activestyle="dotbox")
        self.file_list.grid(row=0, column=0, columnspan=4, sticky="nsew")
        scrollbar = ttk.Scrollbar(files_frame, orient="vertical", command=self.file_list.yview)
        scrollbar.grid(row=0, column=4, sticky="ns")
        self.file_list.configure(yscrollcommand=scrollbar.set)

        ttk.Button(files_frame, text="افزودن PDF", command=self._add_files).grid(row=1, column=0, pady=(9, 0), sticky="ew")
        ttk.Button(files_frame, text="حذف انتخاب", command=self._remove_selected).grid(row=1, column=1, padx=6, pady=(9, 0), sticky="ew")
        ttk.Button(files_frame, text="پاک‌کردن لیست", command=self._clear_files).grid(row=1, column=2, pady=(9, 0), sticky="ew")
        ttk.Button(files_frame, text="پیش‌نمایش", command=self._show_preview).grid(row=1, column=3, padx=(6, 0), pady=(9, 0), sticky="ew")

        settings_frame = ttk.LabelFrame(body, text=" تنظیمات ", padding=12)
        settings_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        settings_frame.columnconfigure(1, weight=1)

        ttk.Label(settings_frame, text="کیفیت چاپ:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(settings_frame, textvariable=self.dpi_var, values=("200", "300", "400"), state="readonly", width=10).grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(settings_frame, text="حالت خروجی:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(settings_frame, textvariable=self.mode_var, values=("متعادل", "اقتصادی"), state="readonly", width=10).grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(settings_frame, text="حد تیرگی پس‌زمینه:").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Scale(settings_frame, from_=70, to=175, variable=self.dark_threshold_var, orient="horizontal").grid(row=3, column=0, columnspan=2, sticky="ew")
        self.threshold_value = ttk.Label(settings_frame, textvariable=self.dark_threshold_var)
        self.threshold_value.grid(row=4, column=1, sticky="e")

        ttk.Label(settings_frame, text="حداقل پوشش پس‌زمینه (%):").grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Scale(settings_frame, from_=25, to=75, variable=self.coverage_var, orient="horizontal").grid(row=6, column=0, columnspan=2, sticky="ew")
        ttk.Label(settings_frame, textvariable=self.coverage_var).grid(row=7, column=1, sticky="e")

        ttk.Checkbutton(settings_frame, text="عکس‌ها خاکستری و سالم بمانند", variable=self.preserve_images_var).grid(row=8, column=0, columnspan=2, sticky="w", pady=(12, 3))
        ttk.Checkbutton(settings_frame, text="اجبار پردازش همه صفحه‌ها", variable=self.force_all_var).grid(row=9, column=0, columnspan=2, sticky="w", pady=3)

        ttk.Separator(settings_frame).grid(row=10, column=0, columnspan=2, sticky="ew", pady=13)
        ttk.Label(
            settings_frame,
            text="متعادل: متن و نمودار خاکستری\nاقتصادی: متن و اشکال سیاه‌وسفید خالص",
            justify="left",
        ).grid(row=11, column=0, columnspan=2, sticky="w")

        output_frame = ttk.LabelFrame(self, text=" پوشه خروجی ", padding=(18, 10))
        output_frame.pack(fill="x", padx=18, pady=(0, 8))
        output_frame.columnconfigure(0, weight=1)
        ttk.Entry(output_frame, textvariable=self.output_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(output_frame, text="انتخاب پوشه", command=self._choose_output).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(output_frame, text="بازکردن پوشه", command=self._open_output).grid(row=0, column=2, padx=(8, 0))

        action_frame = ttk.Frame(self, padding=(18, 4, 18, 15))
        action_frame.pack(fill="x")
        action_frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(action_frame, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.start_button = ttk.Button(action_frame, text="شروع پردازش", command=self._start_processing)
        self.start_button.grid(row=0, column=1)
        ttk.Label(action_frame, textvariable=self.status_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(7, 0))

    def _settings(self) -> ProcessorSettings:
        return ProcessorSettings(
            dpi=int(self.dpi_var.get()),
            mode="economy" if self.mode_var.get() == "اقتصادی" else "balanced",
            dark_threshold=int(self.dark_threshold_var.get()),
            min_background_coverage=float(self.coverage_var.get()) / 100.0,
            preserve_images=self.preserve_images_var.get(),
            force_all_pages=self.force_all_var.get(),
        )

    def _add_files(self) -> None:
        selected = filedialog.askopenfilenames(title="انتخاب PDF", filetypes=[("PDF files", "*.pdf")])
        for item in selected:
            path = Path(item)
            if path not in self.files:
                self.files.append(path)
                self.file_list.insert(tk.END, str(path))

    def _remove_selected(self) -> None:
        indices = list(self.file_list.curselection())
        for index in reversed(indices):
            self.file_list.delete(index)
            del self.files[index]

    def _clear_files(self) -> None:
        self.files.clear()
        self.file_list.delete(0, tk.END)

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(title="انتخاب پوشه خروجی")
        if selected:
            self.output_var.set(selected)

    def _open_output(self) -> None:
        path = Path(self.output_var.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("خطا", str(exc))

    def _show_preview(self) -> None:
        if not self.files:
            messagebox.showwarning("فایل لازم است", "ابتدا یک فایل PDF اضافه کنید.")
            return
        index = self.file_list.curselection()[0] if self.file_list.curselection() else 0
        path = self.files[index]
        self.status_var.set("در حال ساخت پیش‌نمایش...")
        settings = self._settings()

        def work() -> None:
            try:
                page_index = find_first_dark_page(path, settings)
                before, after, analysis = preview_page(path, page_index, settings, max_dimension=780)
                self.events.put(("preview", (path, page_index, before, after, analysis)))
            except Exception as exc:
                self.events.put(("error", (str(exc), traceback.format_exc())))

        threading.Thread(target=work, daemon=True).start()

    def _display_preview(self, payload: object) -> None:
        path, page_index, before, after, analysis = payload  # type: ignore[misc]
        window = tk.Toplevel(self)
        window.title(f"پیش‌نمایش - {path.name} - صفحه {page_index + 1}")
        window.geometry("1100x670")

        content = ttk.Frame(window, padding=12)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(1, weight=1)

        ttk.Label(content, text="قبل", font=("Segoe UI", 12, "bold")).grid(row=0, column=0)
        ttk.Label(content, text="بعد", font=("Segoe UI", 12, "bold")).grid(row=0, column=1)

        target = (500, 520)
        before.thumbnail(target)
        after.thumbnail(target)
        before_tk = ImageTk.PhotoImage(before)
        after_tk = ImageTk.PhotoImage(after)
        left = ttk.Label(content, image=before_tk)
        right = ttk.Label(content, image=after_tk)
        left.image = before_tk
        right.image = after_tk
        left.grid(row=1, column=0, padx=8, pady=8)
        right.grid(row=1, column=1, padx=8, pady=8)

        info = (
            f"تشخیص صفحه تیره: {'بله' if analysis.is_dark else 'خیر'} | "
            f"روشنایی پس‌زمینه: {analysis.background_luma:.0f} | "
            f"پوشش مرز: {analysis.border_coverage * 100:.0f}%"
        )
        ttk.Label(content, text=info).grid(row=2, column=0, columnspan=2, pady=(8, 0))
        self.status_var.set("آماده")

    def _start_processing(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            messagebox.showwarning("فایل لازم است", "حداقل یک فایل PDF اضافه کنید.")
            return

        output_dir = Path(self.output_var.get()).expanduser()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("خطا", f"ساخت پوشه خروجی ممکن نیست:\n{exc}")
            return

        settings = self._settings()
        files = list(self.files)
        self.start_button.configure(state="disabled")
        self.progress.configure(value=0, maximum=max(1, len(files) * 100))
        self.status_var.set("شروع پردازش...")

        def work() -> None:
            completed = 0
            summaries: list[str] = []
            try:
                for file_index, path in enumerate(files):
                    output_path = make_output_path(path, output_dir)

                    def progress(page_index: int, total_pages: int, message: str) -> None:
                        percent = (page_index / max(1, total_pages)) * 100.0
                        overall = file_index * 100.0 + percent
                        self.events.put(("progress", (overall, f"{path.name}: {message}")))

                    result = process_pdf(path, output_path, settings, progress)
                    completed += 1
                    summaries.append(
                        f"{path.name}: {len(result.modified_pages)} صفحه اصلاح شد → {result.output_path.name}"
                    )
                self.events.put(("done", (completed, summaries, output_dir)))
            except Exception as exc:
                self.events.put(("error", (str(exc), traceback.format_exc())))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "progress":
                    value, text = payload  # type: ignore[misc]
                    self.progress.configure(value=value)
                    self.status_var.set(text)
                elif event == "preview":
                    self._display_preview(payload)
                elif event == "done":
                    completed, summaries, output_dir = payload  # type: ignore[misc]
                    self.progress.configure(value=len(self.files) * 100)
                    self.status_var.set(f"تمام شد؛ {completed} فایل ساخته شد.")
                    self.start_button.configure(state="normal")
                    messagebox.showinfo(
                        "پردازش کامل شد",
                        "\n".join(summaries) + f"\n\nپوشه خروجی:\n{output_dir}",
                    )
                elif event == "error":
                    message, details = payload  # type: ignore[misc]
                    self.status_var.set("خطا")
                    self.start_button.configure(state="normal")
                    messagebox.showerror("خطا", f"{message}\n\nجزئیات در فایل error.log ذخیره شد.")
                    try:
                        Path("error.log").write_text(details, encoding="utf-8")
                    except OSError:
                        pass
        except queue.Empty:
            pass
        self.after(100, self._poll_events)


def main() -> None:
    try:
        app = TonerSaverApp()
        app.mainloop()
    except TonerSaverError as exc:
        messagebox.showerror("خطا", str(exc))


if __name__ == "__main__":
    main()
