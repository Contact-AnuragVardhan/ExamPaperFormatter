"""Windows window for Exam Formatter REV 2.

Python collects the subject, grade, and exam file, finds the stored reference,
and shows the Word file ChatGPT returns.
"""

from __future__ import annotations

import ctypes
import hmac
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import job
import records
import reporting

HEADLINES = {
    "OK": "No significant formatting or structural problems detected.",
    "FORMAT ERROR": "Exam content is present, but presentation differs materially from the Reference.",
    "STRUCTURAL ERROR": (
        "Something genuinely necessary for the exam structure appears to be absent "
        "or cannot safely be repaired without inventing teacher academic content."
    ),
    "PASS": "Revised Word file is ready.",
    "REFERENCE NOT FOUND": "No Reference exists for this Subject and Grade.",
}
COLORS = {
    "OK": "#0b6b2f",
    "PASS": "#0b6b2f",
    "ALL GOOD": "#0b6b2f",
    "FORMAT ERROR": "#8a5a00",
    "FORMATTING": "#8a5a00",
    "STRUCTURAL ERROR": "#8d1d1d",
    "STRUCTURAL": "#8d1d1d",
    "REFERENCE NOT FOUND": "#8d1d1d",
    "SYSTEM ERROR": "#8d1d1d",
    "Ready": "#333333",
    "Working": "#1f4b99",
}


def enable_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Exam Formatter — REV 2")
        self.geometry("720x680")
        self.minsize(640, 620)
        self.subject = tk.StringVar()
        self.grade = tk.StringVar()
        self.exam = tk.StringVar()
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.result_path: Path | None = None
        self.admin_window: AdminWindow | None = None
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._show("Ready", "Choose a subject, a grade, and a Word exam file.")

    def _build(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("Muted.TLabel", foreground="#555555")
        style.configure("Result.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Hint.TLabel", foreground="#555555")

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Exam Formatter", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="REV 2 — LOCAL", style="Muted.TLabel").pack(anchor="w", pady=(0, 12))

        ttk.Label(frame, text="Subject").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.subject).pack(fill="x", pady=(2, 8))
        ttk.Label(frame, text="Grade").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.grade).pack(fill="x", pady=(2, 8))
        ttk.Label(frame, text="Exam file").pack(anchor="w")

        exam_row = ttk.Frame(frame)
        exam_row.pack(fill="x", pady=(2, 4))
        ttk.Entry(exam_row, textvariable=self.exam).pack(side="left", fill="x", expand=True)
        ttk.Button(exam_row, text="Browse", command=self._browse).pack(side="left", padx=(8, 0))
        ttk.Label(
            frame,
            text="Subject and grade match the stored reference.",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(0, 12))

        buttons = ttk.Frame(frame)
        buttons.pack(anchor="w", pady=(0, 16))
        self.check_button = ttk.Button(buttons, text="CHECK EXAM", width=18, command=self._check)
        self.format_button = ttk.Button(buttons, text="FORMAT EXAM", width=18, command=self._format)
        self.check_button.pack(side="left")
        self.format_button.pack(side="left", padx=(8, 0))

        self.result_label = ttk.Label(frame, text="Ready", style="Result.TLabel")
        self.result_label.pack(anchor="w", pady=(0, 8))

        detail_row = ttk.Frame(frame)
        detail_row.pack(fill="both", expand=True)
        self.detail = tk.Text(
            detail_row,
            height=10,
            wrap="word",
            font=("Segoe UI", 10),
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=8,
        )
        scroll = ttk.Scrollbar(detail_row, command=self.detail.yview)
        self.detail.configure(yscrollcommand=scroll.set, state="disabled")
        self.detail.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.open_button = ttk.Button(frame, text="Open file", command=self._open_result, state="disabled")
        self.open_button.pack(anchor="w", pady=(8, 8))
        self.progress = ttk.Label(frame, text="", style="Hint.TLabel")
        self.progress.pack(anchor="w")
        ttk.Separator(frame).pack(fill="x", pady=12)
        ttk.Button(frame, text="Admin", command=self._admin).pack(anchor="w")

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Teacher exam",
            filetypes=[("Word document", "*.docx")],
        )
        if path:
            self.exam.set(path)

    def _check(self) -> None:
        self._start("check")

    def _format(self) -> None:
        self._start("format")

    def _start(self, kind: str) -> None:
        if self.busy:
            return
        subject = self.subject.get().strip()
        grade = self.grade.get().strip()
        exam = self.exam.get().strip()
        action = "CHECK" if kind == "check" else "FORMAT"
        if not subject or not grade or not exam:
            self._show("SYSTEM ERROR", "Choose a subject, a grade, and an exam file.")
            return
        exam_path = Path(exam)
        if exam_path.suffix.lower() != ".docx" or not exam_path.is_file():
            self._show("SYSTEM ERROR", "Choose a Word .docx exam file.")
            return
        try:
            package = exam_path.read_bytes()
        except OSError as exc:
            self._show("SYSTEM ERROR", str(exc))
            return
        if not records.is_word_package(package):
            self._show("SYSTEM ERROR", "That exam file is not a Word .docx.")
            return
        try:
            ref = records.find_reference(subject, grade)
        except (OSError, RuntimeError, ValueError) as exc:
            self._show("SYSTEM ERROR", str(exc))
            return
        if ref is None:
            records.log(subject, grade, action, "REFERENCE NOT FOUND")
            self._show("REFERENCE NOT FOUND", HEADLINES["REFERENCE NOT FOUND"])
            return
        if not ref.path.is_file():
            records.log(ref.subject, ref.grade, action, "SYSTEM ERROR")
            self._show("SYSTEM ERROR", "The stored reference file is missing.")
            return

        self.busy = True
        self._buttons(False)
        self._show("Working", f"Reference: {ref.subject} {ref.grade}")
        self.progress.configure(text="Sending the Reference and the Teacher Exam to ChatGPT...")
        thread = threading.Thread(
            target=self._worker,
            args=(kind, ref.subject, ref.grade, ref.path, exam_path),
            daemon=True,
        )
        thread.start()
        self.after(200, self._poll)

    def _worker(self, kind: str, subject: str, grade: str, reference: Path, exam: Path) -> None:
        def progress(message: str) -> None:
            self.events.put(("progress", message))

        result = job.run(kind, subject, grade, reference, exam, progress)
        self.events.put(("done", kind, subject, grade, result))

    def _poll(self) -> None:
        while True:
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                break
            if item[0] == "progress":
                self.progress.configure(text=item[1])
                continue
            _marker, kind, subject, grade, result = item
            action = "CHECK" if kind == "check" else "FORMAT"
            records.log(subject, grade, action, result.status)
            self.busy = False
            self._buttons(True)
            self.progress.configure(text="")
            self._show_result(result)
            if result.saved_path is not None:
                _open_path(result.saved_path)
        if self.busy:
            self.after(200, self._poll)

    def _show_result(self, result: job.Result) -> None:
        report = None if result.status == "SYSTEM ERROR" else reporting.assess_engine(result.status, result.detail)
        if report is None:
            if result.status == "SYSTEM ERROR":
                detail = result.detail
            else:
                parts = [HEADLINES.get(result.status, "")]
                if result.detail:
                    parts.append(result.detail)
                if result.saved_path is not None:
                    parts.append(str(result.saved_path))
                detail = "\n\n".join(part for part in parts if part)
            self._show(result.status, detail, result.saved_path)
            return
        reporting.remember(report, engine_status=result.status, engine_detail=result.detail)
        self._show(report.status, report.message, result.saved_path)

    def _show(self, status: str, detail: str, path: Path | None = None) -> None:
        self.result_label.configure(text=status)
        style = ttk.Style()
        style.configure("Result.TLabel", foreground=COLORS.get(status, "#333333"))
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", detail)
        self.detail.configure(state="disabled")
        self.result_path = path
        self.open_button.configure(state="normal" if path else "disabled")

    def _open_result(self) -> None:
        if self.result_path is not None:
            _open_path(self.result_path)

    def _buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.check_button.configure(state=state)
        self.format_button.configure(state=state)
        self.configure(cursor="" if enabled else "watch")

    def _admin(self) -> None:
        if self.admin_window is not None and self.admin_window.winfo_exists():
            self.admin_window.lift()
            return
        expected = os.environ.get("ADMIN_PASSWORD", "")
        if not expected.strip():
            messagebox.showerror("Admin", "Set ADMIN_PASSWORD in the environment.", parent=self)
            return
        while True:
            typed = simpledialog.askstring("Admin", "Password", show="*", parent=self)
            if typed is None:
                return
            if _password_ok(typed, expected):
                self.admin_window = AdminWindow(self)
                return
            messagebox.showerror("Admin", "Wrong password.", parent=self)

    def _close(self) -> None:
        if self.busy:
            if not messagebox.askokcancel(
                "Exam Formatter",
                "ChatGPT is still working. Close anyway?",
                parent=self,
            ):
                return
        self.destroy()


class AdminWindow(tk.Toplevel):
    def __init__(self, master: App) -> None:
        super().__init__(master)
        self.title("Admin — REV 2")
        self.geometry("760x720")
        self.minsize(680, 640)
        self.subject = tk.StringVar()
        self.grade = tk.StringVar()
        self.rows: list[records.Reference] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        style = ttk.Style()
        style.configure("AdminTitle.TLabel", font=("Segoe UI", 16, "bold"))
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="REV 2 — LOCAL", style="AdminTitle.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Reference files").pack(anchor="w", pady=(12, 4))

        list_row = ttk.Frame(frame)
        list_row.pack(fill="x")
        self.listbox = tk.Listbox(list_row, height=8, font=("Segoe UI", 10), activestyle="dotbox")
        scroll = ttk.Scrollbar(list_row, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="x", expand=True)
        scroll.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda _event: self.view())

        form = ttk.Frame(frame)
        form.pack(fill="x", pady=(8, 4))
        ttk.Label(form, text="Subject").pack(side="left")
        ttk.Entry(form, textvariable=self.subject, width=18).pack(side="left", padx=(4, 8))
        ttk.Label(form, text="Grade").pack(side="left")
        ttk.Entry(form, textvariable=self.grade, width=10).pack(side="left", padx=(4, 8))
        ttk.Button(form, text="Add", command=self.add).pack(side="left")

        actions = ttk.Frame(frame)
        actions.pack(anchor="w", pady=(0, 8))
        ttk.Button(actions, text="View", command=self.view).pack(side="left")
        ttk.Button(actions, text="Replace", command=self.replace).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Delete", command=self.delete).pack(side="left", padx=(8, 0))
        ttk.Label(
            frame,
            text="One reference for each Subject and Grade. Adding the same pair replaces it.",
        ).pack(anchor="w", pady=(0, 12))

        ttk.Label(frame, text="Activity").pack(anchor="w")
        log_row = ttk.Frame(frame)
        log_row.pack(fill="both", expand=True, pady=(2, 8))
        self.log = tk.Text(log_row, height=12, wrap="none", font=("Consolas", 9), state="disabled")
        log_scroll = ttk.Scrollbar(log_row, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        ttk.Label(frame, text="OpenAI cost").pack(anchor="w", pady=(4, 2))
        self.today = ttk.Label(frame, text="Today  $0.00")
        self.month = ttk.Label(frame, text="This month  $0.00")
        self.all_time = ttk.Label(frame, text="All time  $0.00")
        self.today.pack(anchor="w")
        self.month.pack(anchor="w")
        self.all_time.pack(anchor="w")
        self.cost_note = ttk.Label(frame, text="")
        self.cost_note.pack(anchor="w", pady=(2, 0))

    def refresh(self) -> None:
        try:
            self.rows = records.list_references()
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("References", str(exc), parent=self)
            self.rows = []
        self.listbox.delete(0, "end")
        for ref in self.rows:
            self.listbox.insert("end", f"{ref.subject}    {ref.grade}")
        self._set_text(self.log, records.read_log() or "No activity yet.")
        today, month, all_time, unpriced = records.cost_totals()
        self.today.configure(text=f"Today  ${today:.2f}")
        self.month.configure(text=f"This month  ${month:.2f}")
        self.all_time.configure(text=f"All time  ${all_time:.2f}")
        note = ""
        if unpriced:
            note = "Token prices for a model other than gpt-6-astra were left out."
        self.cost_note.configure(text=note)

    def add(self) -> None:
        subject = self.subject.get().strip()
        grade = self.grade.get().strip()
        if not subject or not grade:
            messagebox.showerror("Add reference", "Enter a subject and a grade.", parent=self)
            return
        try:
            existing = records.find_reference(subject, grade)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Add reference", str(exc), parent=self)
            return
        if existing is not None:
            replace = messagebox.askyesno(
                "Replace reference",
                f"{existing.subject} {existing.grade} already has a reference. Replace it?",
                parent=self,
            )
            if not replace:
                return
        data = self._choose_docx()
        if data is None:
            return
        try:
            records.save_reference(subject, grade, data)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Add reference", str(exc), parent=self)
            return
        self.refresh()

    def replace(self) -> None:
        ref = self._selected()
        if ref is None:
            return
        data = self._choose_docx()
        if data is None:
            return
        try:
            records.save_reference(ref.subject, ref.grade, data)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Replace reference", str(exc), parent=self)
            return
        self.refresh()

    def delete(self) -> None:
        ref = self._selected()
        if ref is None:
            return
        confirmed = messagebox.askyesno(
            "Delete reference",
            f"Delete the reference for {ref.subject} {ref.grade}?",
            parent=self,
        )
        if not confirmed:
            return
        try:
            records.delete_reference(ref.subject, ref.grade)
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("Delete reference", str(exc), parent=self)
            return
        self.refresh()

    def view(self) -> None:
        ref = self._selected()
        if ref is None:
            return
        if not ref.path.is_file():
            messagebox.showerror("View reference", "The stored reference file is missing.", parent=self)
            return
        _open_path(ref.path)

    def _selected(self) -> records.Reference | None:
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showerror("References", "Select a reference.", parent=self)
            return None
        return self.rows[selection[0]]

    def _choose_docx(self) -> bytes | None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Reference Word file",
            filetypes=[("Word document", "*.docx")],
        )
        if not path:
            return None
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            messagebox.showerror("Reference", str(exc), parent=self)
            return None
        if not records.is_word_package(data):
            messagebox.showerror("Reference", "Choose a Word .docx file.", parent=self)
            return None
        return data

    def _set_text(self, widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")
        widget.see("end")


def _open_path(path: Path) -> None:
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except OSError as exc:
        messagebox.showerror("Open file", f"{path}\n\n{exc}")


def _password_ok(typed: str, expected: str) -> bool:
    left = typed.encode("utf-8")
    right = expected.encode("utf-8")
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def main() -> None:
    records.load_env()
    enable_dpi()
    App().mainloop()


if __name__ == "__main__":
    main()
