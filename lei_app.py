"""LEI Lookup – Tkinter desktop GUI.

Reuses all lookup logic from lei_lookup.py; adds a graphical interface for
browsing a CSV, selecting the entity-name column, running lookups with a
live progress bar, viewing colour-coded results, and exporting to CSV.
"""

import csv
import json
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import requests

from lei_lookup import (
    GleifAPIError,
    _best_match,
    _classify,
    _detect_name_column,
    lookup_lei,
)

# ---------------------------------------------------------------------------
# Colour tags for the results treeview
# ---------------------------------------------------------------------------
TAG_AUTO = "auto"
TAG_REVIEW = "review"
TAG_NONE = "nomatch"
TAG_REVIEWED = "reviewed"

# LEI output columns appended to each row during processing
LEI_FIELDS = [
    "lei",
    "lei_legal_name",
    "lei_jurisdiction",
    "lei_status",
    "lei_confidence",
    "lei_match_status",
    "lei_candidates",
]

_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lei_cache.json")


class LeiApp(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("LEI Lookup")
        self.minsize(820, 520)

        # State ---------------------------------------------------------------
        self._csv_path: str = ""
        self._input_headers: list[str] = []
        self._rows: list[dict] = []          # original CSV rows
        self._result_rows: list[dict] = []   # rows with LEI fields added
        self._running = False
        self._cache: dict[str, dict] = {}
        self._cache_validating = False
        self._load_cache()

        # Widgets -------------------------------------------------------------
        self._build_ui()

        # Validate cached LEIs in background on startup
        if self._cache:
            self._cache_validating = True
            threading.Thread(target=self._validate_cache, daemon=True).start()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # --- File picker row -------------------------------------------------
        frm_file = ttk.Frame(self)
        frm_file.pack(fill="x", **pad)

        ttk.Label(frm_file, text="CSV File:").pack(side="left")
        self._var_path = tk.StringVar()
        ttk.Entry(frm_file, textvariable=self._var_path, width=50).pack(
            side="left", fill="x", expand=True, padx=(4, 4)
        )
        ttk.Button(frm_file, text="Browse", command=self._browse).pack(side="left")

        # --- Column picker row -----------------------------------------------
        frm_col = ttk.Frame(self)
        frm_col.pack(fill="x", **pad)

        ttk.Label(frm_col, text="Entity Column:").pack(side="left")
        self._var_col = tk.StringVar()
        self._combo_col = ttk.Combobox(
            frm_col, textvariable=self._var_col, state="readonly", width=30
        )
        self._combo_col.pack(side="left", padx=(4, 0))

        # --- Run button ------------------------------------------------------
        self._btn_run = ttk.Button(self, text="Run Lookup", command=self._run_lookup)
        self._btn_run.pack(**pad)

        # --- Progress bar + status -------------------------------------------
        frm_prog = ttk.Frame(self)
        frm_prog.pack(fill="x", **pad)

        self._progress = ttk.Progressbar(frm_prog, length=400, mode="determinate")
        self._progress.pack(side="left", fill="x", expand=True)
        self._lbl_count = ttk.Label(frm_prog, text="")
        self._lbl_count.pack(side="left", padx=(8, 0))

        self._lbl_status = ttk.Label(self, text="", anchor="w")
        self._lbl_status.pack(fill="x", **pad)

        # --- Results treeview ------------------------------------------------
        cols = (
            "entity_name",
            "lei",
            "legal_name",
            "jurisdiction",
            "status",
            "confidence",
            "match_status",
        )
        frm_tree = ttk.Frame(self)
        frm_tree.pack(fill="both", expand=True, **pad)

        self._tree = ttk.Treeview(frm_tree, columns=cols, show="headings", height=12)
        headings = {
            "entity_name": "Entity Name",
            "lei": "LEI",
            "legal_name": "Legal Name",
            "jurisdiction": "Jurisdiction",
            "status": "Status",
            "confidence": "Confidence",
            "match_status": "Match Status",
        }
        for c in cols:
            self._tree.heading(c, text=headings[c])
            width = 180 if c in ("entity_name", "lei", "legal_name") else 100
            self._tree.column(c, width=width, minwidth=60)

        vsb = ttk.Scrollbar(frm_tree, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(frm_tree, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frm_tree.rowconfigure(0, weight=1)
        frm_tree.columnconfigure(0, weight=1)

        # Row colour tags
        self._tree.tag_configure(TAG_AUTO, background="#d4edda")     # green
        self._tree.tag_configure(TAG_REVIEW, background="#fff3cd")   # orange/yellow
        self._tree.tag_configure(TAG_NONE, background="#f8d7da")     # red
        self._tree.tag_configure(TAG_REVIEWED, background="#cce5ff") # blue

        # Double-click to edit cells
        self._tree.bind("<Double-1>", self._on_double_click)
        self._edit_entry: tk.Widget | None = None

        # --- Summary label ---------------------------------------------------
        self._lbl_summary = ttk.Label(self, text="", anchor="w")
        self._lbl_summary.pack(fill="x", **pad)

        # --- Export + Clear Cache buttons ------------------------------------
        frm_buttons = ttk.Frame(self)
        frm_buttons.pack(pady=(0, 8))

        self._btn_export = ttk.Button(
            frm_buttons, text="Export CSV", command=self._export_csv, state="disabled"
        )
        self._btn_export.pack(side="left", padx=4)

        self._btn_clear_cache = ttk.Button(
            frm_buttons, text="Clear Cache", command=self._clear_cache
        )
        self._btn_clear_cache.pack(side="left", padx=4)

    # --------------------------------------------------------- Cache helpers
    def _load_cache(self) -> None:
        try:
            with open(_CACHE_PATH, encoding="utf-8") as f:
                self._cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._cache = {}

    def _save_cache(self) -> None:
        try:
            with open(_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2)
        except OSError:
            pass

    def _cache_row(self, company: str, row_data: dict) -> None:
        key = company.strip().lower()
        if not key:
            return
        self._cache[key] = {
            "lei": row_data.get("lei", ""),
            "legal_name": row_data.get("lei_legal_name", ""),
            "jurisdiction": row_data.get("lei_jurisdiction", ""),
            "status": row_data.get("lei_status", ""),
            "confidence": row_data.get("lei_confidence", ""),
            "match_status": row_data.get("lei_match_status", ""),
        }
        self._save_cache()

    def _validate_cache(self) -> None:
        """Background thread: validate all cached LEIs against GLEIF on startup."""
        import time

        entries = [
            (key, entry)
            for key, entry in self._cache.items()
            if entry.get("lei")
        ]
        total = len(entries)
        if total == 0:
            self._cache_validating = False
            return

        removed = 0
        for i, (key, entry) in enumerate(entries):
            if not self._cache_validating:
                # Cancelled by a user lookup
                break

            self.after(0, self._lbl_status.config,
                       {"text": f"Validating cache: {i + 1} / {total}\u2026"})

            lei_code = entry["lei"]
            try:
                resp = requests.get(
                    f"https://api.gleif.org/api/v1/lei-records/{lei_code}",
                    timeout=15,
                )
                if resp.status_code == 200:
                    reg = resp.json().get("data", {}).get("attributes", {}).get("registration", {})
                    if reg.get("status") == "ACTIVE":
                        # Still active, keep it
                        if i < total - 1:
                            time.sleep(0.5)
                        continue
                # Non-active or non-200 → remove
                self._cache.pop(key, None)
                removed += 1
            except Exception:
                # Network error → remove to be safe
                self._cache.pop(key, None)
                removed += 1

            if i < total - 1:
                time.sleep(0.5)

        if self._cache_validating:
            self._save_cache()
            if removed > 0:
                msg = f"Cache validated: {removed} inactive entr{'y' if removed == 1 else 'ies'} removed."
            else:
                msg = "Cache validated: all entries active."
            self.after(0, self._lbl_status.config, {"text": msg})
            self.after(0, self._refresh_summary)

        self._cache_validating = False

    def _clear_cache(self) -> None:
        if not messagebox.askyesno("Clear Cache",
                                   f"Delete all {len(self._cache)} cached entities?"):
            return
        self._cache.clear()
        try:
            os.remove(_CACHE_PATH)
        except FileNotFoundError:
            pass
        self._refresh_summary()
        self._lbl_status.config(text="Cache cleared.")

    # ----------------------------------------------------------- File browse
    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Select CSV file",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        self._csv_path = path
        self._var_path.set(path)
        self._load_headers()

    def _load_headers(self) -> None:
        try:
            with open(self._csv_path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                self._input_headers = list(reader.fieldnames or [])
                self._rows = list(reader)
        except Exception as exc:
            messagebox.showerror("Error reading CSV", str(exc))
            return

        self._combo_col["values"] = self._input_headers
        guess = _detect_name_column(self._input_headers)
        if guess:
            self._var_col.set(guess)
        elif self._input_headers:
            self._var_col.set(self._input_headers[0])

    # ---------------------------------------------------------- Run lookup
    def _run_lookup(self) -> None:
        if self._running:
            return
        # Cancel any in-progress cache validation
        if self._cache_validating:
            self._cache_validating = False
        if not self._csv_path:
            messagebox.showwarning("No file", "Please select a CSV file first.")
            return
        col = self._var_col.get()
        if not col or col not in self._input_headers:
            messagebox.showwarning(
                "No column", "Please select the entity name column."
            )
            return

        # Reset UI state
        self._result_rows.clear()
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._lbl_summary.config(text="")
        self._btn_export.config(state="disabled")
        self._progress["value"] = 0
        self._progress["maximum"] = len(self._rows)
        self._lbl_count.config(text=f"0 / {len(self._rows)}")
        self._lbl_status.config(text="Starting…")
        self._running = True
        self._btn_run.config(state="disabled")

        # Shared mutable state between worker thread and main thread
        self._pending_updates: list[dict] = []
        self._lock = threading.Lock()
        self._worker_done = False

        thread = threading.Thread(
            target=self._worker, args=(col,), daemon=True
        )
        thread.start()
        self.after(100, self._poll_updates)

    # ----- Background worker (runs in its own thread) -----
    def _worker(self, col: str) -> None:
        for i, row in enumerate(self._rows):
            company = row.get(col, "").strip()
            out = dict(row)
            use_delay = True

            if not company:
                out.update({h: "" for h in LEI_FIELDS})
                out["lei_match_status"] = "NO MATCH"
                match_status = "NO MATCH"
                use_delay = False
            elif company.strip().lower() in self._cache:
                cached = self._cache[company.strip().lower()]
                out["lei"] = cached["lei"]
                out["lei_legal_name"] = cached["legal_name"]
                out["lei_jurisdiction"] = cached["jurisdiction"]
                out["lei_status"] = cached["status"]
                out["lei_confidence"] = cached["confidence"]
                out["lei_match_status"] = cached["match_status"]
                out["lei_candidates"] = ""
                match_status = cached["match_status"]
                use_delay = False
            else:
                try:
                    result = lookup_lei(company)
                except (requests.RequestException, GleifAPIError) as exc:
                    out.update({h: "" for h in LEI_FIELDS})
                    out["lei_match_status"] = f"ERROR: {exc}"
                    match_status = "ERROR"
                else:
                    match_status = _classify(result)
                    best = _best_match(result)

                    if match_status == "AUTO-MATCHED" and best:
                        out["lei"] = best["lei"]
                        out["lei_legal_name"] = best["legal_name"]
                        out["lei_jurisdiction"] = best["jurisdiction"]
                        out["lei_status"] = best["status"]
                        out["lei_confidence"] = best["confidence"]
                        out["lei_candidates"] = ""
                    elif match_status == "REVIEW NEEDED":
                        top = result["results"][0]
                        out["lei"] = top["lei"]
                        out["lei_legal_name"] = top["legal_name"]
                        out["lei_jurisdiction"] = top["jurisdiction"]
                        out["lei_status"] = top["status"]
                        out["lei_confidence"] = top["confidence"]
                        candidates = []
                        for r in result["results"]:
                            candidates.append(
                                f"{r['legal_name']} | {r['lei']} | "
                                f"{r['jurisdiction']} | {r['confidence']}"
                            )
                        out["lei_candidates"] = "; ".join(candidates)
                    else:
                        out.update({h: "" for h in LEI_FIELDS})
                        out["lei_match_status"] = "NO MATCH"

                    out["lei_match_status"] = match_status

            update = {
                "index": i,
                "total": len(self._rows),
                "company": company,
                "row": out,
                "match_status": match_status,
            }

            with self._lock:
                self._pending_updates.append(update)

            import time
            if use_delay and i < len(self._rows) - 1:
                time.sleep(0.5)

        with self._lock:
            self._worker_done = True

    # ----- Main-thread poller -----
    def _poll_updates(self) -> None:
        with self._lock:
            updates = list(self._pending_updates)
            self._pending_updates.clear()
            done = self._worker_done

        for u in updates:
            self._apply_update(u)

        if done and not updates:
            # Truly finished
            self._finish_lookup()
            return

        if done and updates:
            # Process remaining, then finish on next tick
            self.after(50, self._poll_updates)
            return

        self.after(100, self._poll_updates)

    def _apply_update(self, u: dict) -> None:
        row = u["row"]
        ms = u["match_status"]
        idx = u["index"]
        total = u["total"]

        self._result_rows.append(row)

        # Progress
        self._progress["value"] = idx + 1
        self._lbl_count.config(text=f"{idx + 1} / {total}")
        self._lbl_status.config(text=f"Looking up: {u['company']}" if u["company"] else "Skipped empty row")

        # Cache AUTO-MATCHED rows
        if ms == "AUTO-MATCHED" and u["company"]:
            self._cache_row(u["company"], row)

        # Treeview row
        if ms in ("AUTO-MATCHED", "REVIEWED"):
            tag = TAG_AUTO
        elif ms == "REVIEW NEEDED":
            tag = TAG_REVIEW
        else:
            tag = TAG_NONE

        self._tree.insert(
            "",
            "end",
            values=(
                u["company"],
                row.get("lei", ""),
                row.get("lei_legal_name", ""),
                row.get("lei_jurisdiction", ""),
                row.get("lei_status", ""),
                row.get("lei_confidence", ""),
                row.get("lei_match_status", ""),
            ),
            tags=(tag,),
        )
        # Auto-scroll to bottom
        children = self._tree.get_children()
        if children:
            self._tree.see(children[-1])

    def _finish_lookup(self) -> None:
        self._running = False
        self._btn_run.config(state="normal")
        self._lbl_status.config(text="Done.")
        self._refresh_summary()
        if self._result_rows:
            self._btn_export.config(state="normal")

    # -------------------------------------------------------- Inline edit
    # Editable columns (treeview column ids)
    _EDITABLE_COLS = {"lei", "legal_name", "jurisdiction", "status", "confidence"}

    # Map treeview column id → _result_rows dict key
    _COL_TO_KEY = {
        "lei": "lei",
        "legal_name": "lei_legal_name",
        "jurisdiction": "lei_jurisdiction",
        "status": "lei_status",
        "confidence": "lei_confidence",
    }

    def _on_double_click(self, event: tk.Event) -> None:
        """Open an Entry overlay on the double-clicked cell."""
        if self._running:
            return

        item = self._tree.identify_row(event.y)
        col_id = self._tree.identify_column(event.x)  # e.g. "#2"
        if not item or not col_id:
            return

        # Convert "#N" → 0-based index → column name
        col_index = int(col_id.lstrip("#")) - 1
        cols = self._tree["columns"]
        if col_index < 0 or col_index >= len(cols):
            return
        col_name = cols[col_index]

        if col_name not in self._EDITABLE_COLS:
            return

        # Destroy any existing edit widget
        if self._edit_entry is not None:
            self._edit_entry.destroy()
            self._edit_entry = None

        # Get cell bounding box
        bbox = self._tree.bbox(item, col_id)
        if not bbox:
            return
        x, y, w, h = bbox

        old_value = self._tree.set(item, col_name)

        entry = ttk.Entry(self._tree)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, old_value)
        entry.select_range(0, "end")
        entry.focus_set()
        self._edit_entry = entry

        entry.bind("<Return>", lambda e: self._commit_edit(item, col_name, entry, old_value))
        entry.bind("<FocusOut>", lambda e: self._commit_edit(item, col_name, entry, old_value))
        entry.bind("<Escape>", lambda e: self._cancel_edit(entry))

    def _cancel_edit(self, entry: ttk.Entry) -> None:
        entry.destroy()
        if self._edit_entry is entry:
            self._edit_entry = None

    def _commit_edit(self, item: str, col_name: str, entry: ttk.Entry, old_value: str) -> None:
        """Save the edited value and trigger validation if needed."""
        try:
            new_value = entry.get()
        except tk.TclError:
            return  # widget already destroyed
        entry.destroy()
        if self._edit_entry is entry:
            self._edit_entry = None

        if new_value == old_value:
            return

        # Find the row index in _result_rows
        children = self._tree.get_children()
        try:
            row_index = list(children).index(item)
        except ValueError:
            return

        # Update treeview cell
        self._tree.set(item, col_name, new_value)

        # Update backing data
        key = self._COL_TO_KEY[col_name]
        self._result_rows[row_index][key] = new_value

        if col_name == "lei":
            # Validate via GLEIF API in background
            self._lbl_status.config(text=f"Validating LEI: {new_value}…")
            threading.Thread(
                target=self._validate_lei,
                args=(item, new_value, old_value, row_index),
                daemon=True,
            ).start()
        else:
            # Non-LEI edit: mark as REVIEWED immediately
            self._tree.set(item, "match_status", "REVIEWED")
            self._result_rows[row_index]["lei_match_status"] = "REVIEWED"
            self._tree.item(item, tags=(TAG_AUTO,))

            # Cache the reviewed row
            entity_name = self._tree.set(item, "entity_name")
            if entity_name:
                self._cache_row(entity_name, self._result_rows[row_index])

            self._refresh_summary()

    def _validate_lei(self, item: str, lei_code: str, old_value: str, row_index: int) -> None:
        """Background thread: validate an LEI code against the GLEIF API."""
        try:
            resp = requests.get(
                f"https://api.gleif.org/api/v1/lei-records/{lei_code}",
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                attr = data.get("attributes", {}).get("entity", {})
                legal_name = attr.get("legalName", {}).get("name", "")
                jurisdiction = attr.get("jurisdiction", "")
                reg = data.get("attributes", {}).get("registration", {})
                status = reg.get("status", "")
                record = {
                    "legal_name": legal_name,
                    "jurisdiction": jurisdiction,
                    "status": status,
                }
                self.after(0, self._apply_lei_validation, item, row_index, record)
            else:
                self.after(0, self._revert_lei, item, row_index, old_value,
                           f"LEI '{lei_code}' not found (HTTP {resp.status_code}).")
        except Exception as exc:
            self.after(0, self._revert_lei, item, row_index, old_value,
                       f"Validation error: {exc}")

    def _apply_lei_validation(self, item: str, row_index: int, record: dict) -> None:
        """Main-thread callback after successful LEI validation."""
        row = self._result_rows[row_index]
        row["lei_legal_name"] = record["legal_name"]
        row["lei_jurisdiction"] = record["jurisdiction"]
        row["lei_status"] = record["status"]
        row["lei_match_status"] = "REVIEWED"

        self._tree.set(item, "legal_name", record["legal_name"])
        self._tree.set(item, "jurisdiction", record["jurisdiction"])
        self._tree.set(item, "status", record["status"])
        self._tree.set(item, "match_status", "REVIEWED")
        self._tree.item(item, tags=(TAG_AUTO,))

        self._lbl_status.config(text="LEI validated successfully.")

        # Cache the reviewed row
        entity_name = self._tree.set(item, "entity_name")
        if entity_name:
            self._cache_row(entity_name, row)

        self._refresh_summary()

    def _revert_lei(self, item: str, row_index: int, old_value: str, message: str) -> None:
        """Main-thread callback: revert LEI on failed validation."""
        self._tree.set(item, "lei", old_value)
        self._result_rows[row_index]["lei"] = old_value
        self._lbl_status.config(text="")
        messagebox.showwarning("Invalid LEI", message)

    def _refresh_summary(self) -> None:
        """Recalculate and update the summary label."""
        auto = sum(1 for r in self._result_rows if r.get("lei_match_status") == "AUTO-MATCHED")
        review = sum(1 for r in self._result_rows if r.get("lei_match_status") == "REVIEW NEEDED")
        reviewed = sum(1 for r in self._result_rows if r.get("lei_match_status") == "REVIEWED")
        none_ = sum(1 for r in self._result_rows if r.get("lei_match_status") == "NO MATCH")
        errs = sum(1 for r in self._result_rows if str(r.get("lei_match_status", "")).startswith("ERROR"))

        self._lbl_summary.config(
            text=f"Summary:  {auto} Auto-matched,  {review} Review needed,  "
                 f"{reviewed} Reviewed,  {none_} No match,  {errs} Errors  |  "
                 f"Cache: {len(self._cache)} entities"
        )

    # ---------------------------------------------------------- Export CSV
    def _export_csv(self) -> None:
        if not self._result_rows:
            return
        path = filedialog.asksaveasfilename(
            title="Save results CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        output_headers = list(self._input_headers) + LEI_FIELDS
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=output_headers, extrasaction="ignore"
                )
                writer.writeheader()
                for row in self._result_rows:
                    writer.writerow(row)
            messagebox.showinfo("Exported", f"Results saved to:\n{path}")
        except Exception as exc:
            messagebox.showerror("Export error", str(exc))


if __name__ == "__main__":
    app = LeiApp()
    app.mainloop()
