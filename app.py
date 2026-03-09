import bisect
import gzip
import json
import os
import re
import threading
import time
import tkinter as tk
import urllib.request
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText


@dataclass
class MatchResult:
    file_path: str
    line: int
    column: int
    snippet: str
    detail: str


@dataclass
class OffsetEntry:
    name: str
    old_value: str
    line_index: int


@dataclass
class OffsetResult:
    name: str
    old_value: str
    new_value: str
    status: str
    source_file: str
    changed: bool


class FileSearchApp(tk.Tk):
    DUMPSPACE_BASE = "https://raw.githubusercontent.com/Spuckwaffel/dumpspace/refs/heads/main/Games/"
    DUMPSPACE_GAMELIST_URL = DUMPSPACE_BASE + "GameList.json"

    def __init__(self) -> None:
        super().__init__()
        self.title("Files Parser Search")
        self.geometry("1200x780")
        self.minsize(980, 620)

        self._init_search_state()
        self._init_offset_state()

        self.results: list[MatchResult] = []
        self.tree_item_to_index: dict[str, int] = {}

        self._build_ui()

    def _init_search_state(self) -> None:
        self.folder_var = tk.StringVar(value=os.path.expanduser("~"))
        self.query_var = tk.StringVar()
        self.case_sensitive_var = tk.BooleanVar(value=False)
        self.context_chars_var = tk.IntVar(value=50)
        self.max_results_var = tk.IntVar(value=2000)
        self.search_status_var = tk.StringVar(value="Pick a folder and search text.")

    def _init_offset_state(self) -> None:
        self.offset_folder_var = tk.StringVar(value=os.path.expanduser("~"))
        self.offset_source_label_var = tk.StringVar(value="Folder with NEW offsets")
        self.use_dumpspace_api_var = tk.BooleanVar(value=False)
        self.offset_name_case_sensitive_var = tk.BooleanVar(value=False)
        self.offset_dump_age_var = tk.StringVar(value="")
        self.offset_status_var = tk.StringVar(value="Paste offsets, pick a folder, then click Find New Offsets.")

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(container)
        notebook.pack(fill=tk.BOTH, expand=True)

        self.search_tab = ttk.Frame(notebook)
        self.offset_tab = ttk.Frame(notebook)

        notebook.add(self.search_tab, text="File Search")
        notebook.add(self.offset_tab, text="Offset Updater")

        self._build_search_tab(self.search_tab)
        self._build_offset_tab(self.offset_tab)

    def _build_search_tab(self, parent: ttk.Frame) -> None:
        self.search_controls: list[tk.Widget] = []

        folder_frame = ttk.Frame(parent)
        folder_frame.pack(fill=tk.X)

        ttk.Label(folder_frame, text="Folder").pack(side=tk.LEFT)
        self.folder_entry = ttk.Entry(folder_frame, textvariable=self.folder_var)
        self.folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        self.search_controls.append(self.folder_entry)

        self.browse_btn = ttk.Button(folder_frame, text="Browse", command=self.pick_folder)
        self.browse_btn.pack(side=tk.LEFT)
        self.search_controls.append(self.browse_btn)

        query_frame = ttk.Frame(parent)
        query_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(query_frame, text="Search text").pack(side=tk.LEFT)
        self.query_entry = ttk.Entry(query_frame, textvariable=self.query_var)
        self.query_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        self.query_entry.bind("<Return>", lambda _e: self.start_search())
        self.search_controls.append(self.query_entry)

        options_frame = ttk.Frame(parent)
        options_frame.pack(fill=tk.X, pady=(8, 10))

        self.case_check = ttk.Checkbutton(options_frame, text="Case sensitive", variable=self.case_sensitive_var)
        self.case_check.pack(side=tk.LEFT)
        self.search_controls.append(self.case_check)

        ttk.Label(options_frame, text="Context chars").pack(side=tk.LEFT, padx=(16, 6))
        self.context_spin = ttk.Spinbox(options_frame, from_=10, to=500, textvariable=self.context_chars_var, width=6)
        self.context_spin.pack(side=tk.LEFT)
        self.search_controls.append(self.context_spin)

        ttk.Label(options_frame, text="Max hits").pack(side=tk.LEFT, padx=(16, 6))
        self.max_results_spin = ttk.Spinbox(
            options_frame,
            from_=100,
            to=20000,
            increment=100,
            textvariable=self.max_results_var,
            width=8,
        )
        self.max_results_spin.pack(side=tk.LEFT)
        self.search_controls.append(self.max_results_spin)

        self.search_btn = ttk.Button(options_frame, text="Search", command=self.start_search)
        self.search_btn.pack(side=tk.RIGHT)
        self.search_controls.append(self.search_btn)

        split = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        split.pack(fill=tk.BOTH, expand=True)

        top_panel = ttk.Frame(split)
        bottom_panel = ttk.Frame(split)
        split.add(top_panel, weight=4)
        split.add(bottom_panel, weight=2)

        columns = ("file", "line", "column", "snippet")
        self.tree = ttk.Treeview(top_panel, columns=columns, show="headings")
        self.tree.heading("file", text="File")
        self.tree.heading("line", text="Line")
        self.tree.heading("column", text="Col")
        self.tree.heading("snippet", text="Context Around Match")

        self.tree.column("file", width=320, stretch=True)
        self.tree.column("line", width=70, anchor=tk.CENTER, stretch=False)
        self.tree.column("column", width=70, anchor=tk.CENTER, stretch=False)
        self.tree.column("snippet", width=650, stretch=True)

        tree_scroll_y = ttk.Scrollbar(top_panel, orient=tk.VERTICAL, command=self.tree.yview)
        tree_scroll_x = ttk.Scrollbar(top_panel, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        tree_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)

        self.tree.bind("<<TreeviewSelect>>", self.on_result_selected)

        self.detail_text = ScrolledText(bottom_panel, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 10))
        self.detail_text.pack(fill=tk.BOTH, expand=True)

        status = ttk.Label(parent, textvariable=self.search_status_var, anchor=tk.W)
        status.pack(fill=tk.X, pady=(8, 0))

    def _build_offset_tab(self, parent: ttk.Frame) -> None:
        self.offset_controls: list[tk.Widget] = []

        folder_frame = ttk.Frame(parent)
        folder_frame.pack(fill=tk.X)

        self.offset_source_label = ttk.Label(folder_frame, textvariable=self.offset_source_label_var)
        self.offset_source_label.pack(side=tk.LEFT)
        self.offset_folder_entry = ttk.Entry(folder_frame, textvariable=self.offset_folder_var)
        self.offset_folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        self.offset_controls.append(self.offset_folder_entry)

        self.offset_browse_btn = ttk.Button(folder_frame, text="Browse", command=self.pick_offset_folder)
        self.offset_browse_btn.pack(side=tk.LEFT)
        self.offset_controls.append(self.offset_browse_btn)

        action_frame = ttk.Frame(parent)
        action_frame.pack(fill=tk.X, pady=(8, 8))

        self.offset_update_btn = ttk.Button(action_frame, text="Find New Offsets", command=self.start_offset_update)
        self.offset_update_btn.pack(side=tk.LEFT)
        self.offset_controls.append(self.offset_update_btn)

        self.offset_api_check = ttk.Checkbutton(
            action_frame,
            text="Use Dumpspace API",
            variable=self.use_dumpspace_api_var,
            command=self.on_offset_mode_changed,
        )
        self.offset_api_check.pack(side=tk.LEFT, padx=(8, 0))
        self.offset_controls.append(self.offset_api_check)

        self.offset_case_check = ttk.Checkbutton(
            action_frame,
            text="Case sensitive names",
            variable=self.offset_name_case_sensitive_var,
        )
        self.offset_case_check.pack(side=tk.LEFT, padx=(8, 0))
        self.offset_controls.append(self.offset_case_check)

        self.offset_copy_btn = ttk.Button(
            action_frame,
            text="Copy Updated Offsets",
            command=self.copy_updated_offsets,
        )
        self.offset_copy_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.offset_controls.append(self.offset_copy_btn)

        ttk.Label(
            action_frame,
            text="Paste old offsets on the left. Example: dwEntityList = 0x24AB1B8",
        ).pack(side=tk.LEFT, padx=(12, 0))

        top_split = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        top_split.pack(fill=tk.BOTH, expand=True)

        input_frame = ttk.LabelFrame(top_split, text="Old Offsets Input")
        output_frame = ttk.LabelFrame(top_split, text="Updated Offsets Output")
        top_split.add(input_frame, weight=1)
        top_split.add(output_frame, weight=1)

        self.offset_input_text = ScrolledText(input_frame, wrap=tk.NONE, height=14, font=("Consolas", 10))
        self.offset_input_text.pack(fill=tk.BOTH, expand=True)

        self.offset_dump_age_label = ttk.Label(output_frame, textvariable=self.offset_dump_age_var, anchor=tk.W)
        self.offset_dump_age_label.pack(fill=tk.X, pady=(0, 4))
        self.offset_output_text = ScrolledText(output_frame, wrap=tk.NONE, height=14, font=("Consolas", 10), state=tk.DISABLED)
        self.offset_output_text.pack(fill=tk.BOTH, expand=True)
        self.offset_output_text.tag_configure("changed", foreground="#0A7A16", background="#E7F7EA")
        self.offset_output_text.tag_configure("same", foreground="#8A6D00", background="#FFF7D6")
        self.offset_output_text.tag_configure("not_found", foreground="#B00020", background="#FDE7EA")

        result_frame = ttk.LabelFrame(parent, text="Per-Offset Results")
        result_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self.offset_results_text = ScrolledText(result_frame, wrap=tk.WORD, height=10, font=("Consolas", 10), state=tk.DISABLED)
        self.offset_results_text.pack(fill=tk.BOTH, expand=True)
        self.offset_results_text.tag_configure("changed", foreground="#0A7A16", background="#E7F7EA")
        self.offset_results_text.tag_configure("same", foreground="#8A6D00", background="#FFF7D6")
        self.offset_results_text.tag_configure("not_found", foreground="#B00020", background="#FDE7EA")

        legend_frame = ttk.Frame(parent)
        legend_frame.pack(fill=tk.X, pady=(8, 0))

        ttk.Label(legend_frame, text="Color key:").pack(side=tk.LEFT)
        tk.Label(legend_frame, text=" Green = updated value found and changed ", fg="#0A7A16").pack(side=tk.LEFT, padx=(8, 6))
        tk.Label(legend_frame, text=" Yellow = found but same value ", fg="#B8860B").pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(legend_frame, text=" Red = not found ", fg="#B00020").pack(side=tk.LEFT)

        status = ttk.Label(parent, textvariable=self.offset_status_var, anchor=tk.W)
        status.pack(fill=tk.X, pady=(8, 0))

        starter = (
            "constexpr uintptr_t dwEntityList = 0x24AB1B8;\n"
            "constexpr uintptr_t dwLocalPlayerPawn = 0x2065AF0;\n"
            "dwViewMatrix = 0x230BEE0;"
        )
        self.offset_input_text.insert("1.0", starter)
        self.on_offset_mode_changed()

    def pick_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.folder_var.get() or os.path.expanduser("~"))
        if selected:
            self.folder_var.set(selected)

    def pick_offset_folder(self) -> None:
        current = self.offset_folder_var.get().strip()
        start_dir = current if os.path.isdir(current) else os.path.expanduser("~")
        selected = filedialog.askdirectory(initialdir=start_dir)
        if selected:
            self.offset_folder_var.set(selected)

    def on_offset_mode_changed(self) -> None:
        use_api = self.use_dumpspace_api_var.get()
        if use_api:
            self.offset_source_label_var.set("Game name")
            self.offset_dump_age_var.set("Dumpspace dump last updated: -")
            if self.offset_browse_btn.winfo_manager():
                self.offset_browse_btn.pack_forget()
            if os.path.isdir(self.offset_folder_var.get().strip()):
                self.offset_folder_var.set("")
        else:
            self.offset_source_label_var.set("Folder with NEW offsets")
            self.offset_dump_age_var.set("")
            if not self.offset_browse_btn.winfo_manager():
                self.offset_browse_btn.pack(side=tk.LEFT)
            if not self.offset_folder_var.get().strip():
                self.offset_folder_var.set(os.path.expanduser("~"))

    def set_search_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in self.search_controls:
            widget.configure(state=state)

    def set_offset_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in self.offset_controls:
            widget.configure(state=state)
        self.offset_input_text.configure(state=state)

    def start_search(self) -> None:
        folder = self.folder_var.get().strip()
        query = self.query_var.get()

        if not folder:
            messagebox.showwarning("Missing folder", "Pick a folder first.")
            return
        if not os.path.isdir(folder):
            messagebox.showerror("Invalid folder", "The selected folder does not exist.")
            return
        if not query:
            messagebox.showwarning("Missing text", "Type a word or sentence to search for.")
            return

        context_chars = self.context_chars_var.get()
        max_results = self.max_results_var.get()
        case_sensitive = self.case_sensitive_var.get()

        self.search_status_var.set("Searching...")
        self.set_search_controls_enabled(False)
        self.clear_search_results()

        thread = threading.Thread(
            target=self._search_worker,
            args=(folder, query, case_sensitive, context_chars, max_results),
            daemon=True,
        )
        thread.start()

    def _search_worker(
        self,
        folder: str,
        query: str,
        case_sensitive: bool,
        context_chars: int,
        max_results: int,
    ) -> None:
        matches: list[MatchResult] = []
        files_scanned = 0
        binary_skipped = 0
        unreadable = 0

        query_cmp = query if case_sensitive else query.lower()

        for root, _, files in os.walk(folder):
            for name in files:
                file_path = os.path.join(root, name)
                files_scanned += 1

                try:
                    if self.is_probably_binary(file_path):
                        binary_skipped += 1
                        continue

                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except (OSError, UnicodeError):
                    unreadable += 1
                    continue

                file_matches = self.find_matches_in_text(
                    file_path=file_path,
                    text=text,
                    query=query,
                    query_cmp=query_cmp,
                    case_sensitive=case_sensitive,
                    context_chars=context_chars,
                )

                if file_matches:
                    matches.extend(file_matches)
                    if len(matches) >= max_results:
                        matches = matches[:max_results]
                        self.after(
                            0,
                            lambda m=matches, s=files_scanned, b=binary_skipped, u=unreadable: self.finish_search(
                                m,
                                s,
                                b,
                                u,
                                limited=True,
                            ),
                        )
                        return

        self.after(
            0,
            lambda m=matches, s=files_scanned, b=binary_skipped, u=unreadable: self.finish_search(
                m,
                s,
                b,
                u,
                limited=False,
            ),
        )

    def start_offset_update(self) -> None:
        source_input = self.offset_folder_var.get().strip()
        use_api = self.use_dumpspace_api_var.get()
        name_case_sensitive = self.offset_name_case_sensitive_var.get()
        raw_text = self.offset_input_text.get("1.0", "end-1c")

        if not source_input:
            if use_api:
                messagebox.showwarning("Missing game name", "Enter a game name to fetch offsets from Dumpspace.")
            else:
                messagebox.showwarning("Missing folder", "Pick the folder that has the new offsets.")
            return
        if not use_api and not os.path.isdir(source_input):
            messagebox.showerror("Invalid folder", "The selected folder does not exist.")
            return
        if not raw_text.strip():
            messagebox.showwarning("Missing input", "Paste at least one offset line.")
            return

        entries, lines = self.parse_offset_entries(raw_text)
        if not entries:
            messagebox.showwarning(
                "No offsets found",
                "No valid assignment lines were found. Example: dwEntityList = 0x24AB1B8",
            )
            return

        unique_count = len({entry.name for entry in entries})
        if use_api:
            self.offset_status_var.set(
                f"Scanning... [API mode] Targets: {unique_count} | Found: 0 | Updated: 0 | Not found: {unique_count} | "
                "Files scanned: 0"
            )
            self.offset_dump_age_var.set("Dumpspace dump last updated: loading...")
        else:
            self.offset_status_var.set(
                f"Scanning... Targets: {unique_count} | Found: 0 | Updated: 0 | Not found: {unique_count} | "
                "Files scanned: 0"
            )
            self.offset_dump_age_var.set("")
        self.set_offset_controls_enabled(False)
        self.set_offset_output_text(raw_text)
        self.set_offset_results_text("Live results:\n")

        if use_api:
            thread = threading.Thread(
                target=self._offset_api_worker,
                args=(source_input, entries, lines, name_case_sensitive),
                daemon=True,
            )
        else:
            thread = threading.Thread(
                target=self._offset_worker,
                args=(source_input, entries, lines, name_case_sensitive),
                daemon=True,
            )
        thread.start()

    def copy_updated_offsets(self) -> None:
        text = self.offset_output_text.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showwarning("Nothing to copy", "No updated offsets text is available yet.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        self.offset_status_var.set("Copied updated offsets to clipboard.")

    def _offset_worker(
        self,
        folder: str,
        entries: list[OffsetEntry],
        lines: list[str],
        name_case_sensitive: bool,
    ) -> None:
        unique_names = {entry.name for entry in entries}
        entries_by_name: dict[str, list[OffsetEntry]] = {}
        for entry in entries:
            entries_by_name.setdefault(entry.name, []).append(entry)

        patterns = {
            name: self.build_offset_lookup_pattern(name, name_case_sensitive)
            for name in unique_names
        }

        found_map: dict[str, tuple[str, str]] = {}
        results_by_name: dict[str, OffsetResult] = {}
        updated_lines = list(lines)
        files_scanned = 0
        binary_skipped = 0
        unreadable = 0
        updated_count = 0
        total_targets = len(unique_names)

        done = False
        for root, _, files in os.walk(folder):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                files_scanned += 1

                try:
                    if self.is_probably_binary(file_path):
                        binary_skipped += 1
                        continue
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except (OSError, UnicodeError):
                    unreadable += 1
                    continue

                lower_text = text.lower()
                for name in unique_names:
                    if name in found_map:
                        continue
                    # Fast pre-check to avoid running regex when the symbol doesn't exist in this file.
                    if name_case_sensitive:
                        if name not in text:
                            continue
                    elif name.lower() not in lower_text:
                        continue
                    match = patterns[name].search(text)
                    if match:
                        new_value = self.normalize_offset_value(match.group(1))
                        found_map[name] = (new_value, file_path)

                        entry_group = entries_by_name.get(name, [])
                        changed = False
                        old_display = entry_group[0].old_value if entry_group else new_value
                        for entry in entry_group:
                            if new_value != entry.old_value and 0 <= entry.line_index < len(updated_lines):
                                changed = True
                                updated_lines[entry.line_index] = self.replace_offset_value(
                                    updated_lines[entry.line_index],
                                    new_value,
                                )

                        if changed:
                            updated_count += 1

                        result = OffsetResult(
                            name=name,
                            old_value=old_display,
                            new_value=new_value,
                            status="Updated" if changed else "Found Same",
                            source_file=file_path,
                            changed=changed,
                        )
                        results_by_name[name] = result

                        self.after(
                            0,
                            lambda r=result, rf=folder: self.append_offset_result(r, rf),
                        )
                        self.after(
                            0,
                            lambda t=total_targets, f=len(found_map), u=updated_count, s=files_scanned: self.update_offset_status_progress(
                                total_targets=t,
                                found_count=f,
                                updated_count=u,
                                files_scanned=s,
                                done=False,
                            ),
                        )
                        status_snapshot = dict(results_by_name)
                        lines_snapshot = list(updated_lines)
                        entries_snapshot = list(entries)
                        self.after(
                            0,
                            lambda ls=lines_snapshot, es=entries_snapshot, rs=status_snapshot: self.render_offset_output(
                                ls,
                                es,
                                rs,
                                mark_not_found=False,
                            ),
                        )

                if files_scanned % 200 == 0:
                    self.after(
                        0,
                        lambda t=total_targets, f=len(found_map), u=updated_count, s=files_scanned: self.update_offset_status_progress(
                            total_targets=t,
                            found_count=f,
                            updated_count=u,
                            files_scanned=s,
                            done=False,
                        ),
                    )

                if len(found_map) == len(unique_names):
                    done = True
                    break
            if done:
                break

        for name in sorted(unique_names):
            if name in results_by_name:
                continue
            first_entry = entries_by_name[name][0]
            not_found_result = OffsetResult(
                name=name,
                old_value=first_entry.old_value,
                new_value=first_entry.old_value,
                status="Not Found",
                source_file="",
                changed=False,
            )
            results_by_name[name] = not_found_result
            self.after(0, lambda r=not_found_result, rf=folder: self.append_offset_result(r, rf))

        results: list[OffsetResult] = []
        for entry in entries:
            base_result = results_by_name.get(entry.name)
            if base_result is None:
                results.append(
                    OffsetResult(
                        name=entry.name,
                        old_value=entry.old_value,
                        new_value=entry.old_value,
                        status="Not Found",
                        source_file="",
                        changed=False,
                    )
                )
                continue

            changed = base_result.new_value != entry.old_value if base_result.status != "Not Found" else False
            results.append(
                OffsetResult(
                    name=entry.name,
                    old_value=entry.old_value,
                    new_value=base_result.new_value if base_result.status != "Not Found" else entry.old_value,
                    status="Updated" if changed else base_result.status,
                    source_file=base_result.source_file,
                    changed=changed,
                )
            )

        final_results_by_name = dict(results_by_name)
        final_lines = list(updated_lines)
        final_entries = list(entries)
        self.after(
            0,
            lambda: self.finish_offset_update(
                folder,
                results,
                final_lines,
                final_entries,
                final_results_by_name,
                files_scanned,
                len(found_map),
                updated_count,
            ),
        )

    def _offset_api_worker(
        self,
        game_name: str,
        entries: list[OffsetEntry],
        lines: list[str],
        name_case_sensitive: bool,
    ) -> None:
        try:
            matched_game_name, matched_hash, api_offsets, latest_update_ms = self.fetch_dumpspace_offsets(game_name)
        except Exception as ex:
            self.after(0, lambda e=str(ex): self.fail_offset_update(e))
            return

        dump_age_text = self.format_dumpspace_last_updated_text(latest_update_ms)
        self.after(0, lambda t=dump_age_text: self.offset_dump_age_var.set(t))

        unique_names = {entry.name for entry in entries}
        api_offsets_exact: dict[str, str] = {}
        api_offsets_casefold: dict[str, str] = {}
        api_offsets_normalized: dict[str, str] = {}
        for key, value in api_offsets.items():
            if key not in api_offsets_exact:
                api_offsets_exact[key] = value
            lower_key = key.lower()
            normalized_key = self.normalize_symbol_lookup_name(key)
            if lower_key not in api_offsets_casefold:
                api_offsets_casefold[lower_key] = value
            if normalized_key and normalized_key not in api_offsets_normalized:
                api_offsets_normalized[normalized_key] = value

        entries_by_name: dict[str, list[OffsetEntry]] = {}
        for entry in entries:
            entries_by_name.setdefault(entry.name, []).append(entry)

        results_by_name: dict[str, OffsetResult] = {}
        updated_lines = list(lines)
        found_count = 0
        updated_count = 0
        total_targets = len(unique_names)
        api_source = f"Dumpspace ({matched_game_name}, hash {matched_hash})"

        for name in unique_names:
            if name_case_sensitive:
                raw_new_value = api_offsets_exact.get(name)
            else:
                raw_new_value = api_offsets_casefold.get(name.lower())
                if raw_new_value is None:
                    raw_new_value = api_offsets_normalized.get(self.normalize_symbol_lookup_name(name))
            if raw_new_value is None:
                continue

            new_value = self.normalize_offset_value(raw_new_value)
            found_count += 1
            entry_group = entries_by_name.get(name, [])
            changed = False
            old_display = entry_group[0].old_value if entry_group else new_value
            for entry in entry_group:
                if new_value != entry.old_value and 0 <= entry.line_index < len(updated_lines):
                    changed = True
                    updated_lines[entry.line_index] = self.replace_offset_value(
                        updated_lines[entry.line_index],
                        new_value,
                    )

            if changed:
                updated_count += 1

            result = OffsetResult(
                name=name,
                old_value=old_display,
                new_value=new_value,
                status="Updated" if changed else "Found Same",
                source_file=api_source,
                changed=changed,
            )
            results_by_name[name] = result

            self.after(0, lambda r=result: self.append_offset_result(r, ""))
            self.after(
                0,
                lambda t=total_targets, f=found_count, u=updated_count, md=matched_game_name: self.update_offset_status_progress(
                    total_targets=t,
                    found_count=f,
                    updated_count=u,
                    files_scanned=0,
                    done=False,
                    mode_detail=f"API mode ({md})",
                ),
            )
            status_snapshot = dict(results_by_name)
            lines_snapshot = list(updated_lines)
            entries_snapshot = list(entries)
            self.after(
                0,
                lambda ls=lines_snapshot, es=entries_snapshot, rs=status_snapshot: self.render_offset_output(
                    ls,
                    es,
                    rs,
                    mark_not_found=False,
                ),
            )

        for name in sorted(unique_names):
            if name in results_by_name:
                continue
            first_entry = entries_by_name[name][0]
            not_found_result = OffsetResult(
                name=name,
                old_value=first_entry.old_value,
                new_value=first_entry.old_value,
                status="Not Found",
                source_file=api_source,
                changed=False,
            )
            results_by_name[name] = not_found_result
            self.after(0, lambda r=not_found_result: self.append_offset_result(r, ""))

        results: list[OffsetResult] = []
        for entry in entries:
            base_result = results_by_name.get(entry.name)
            if base_result is None:
                results.append(
                    OffsetResult(
                        name=entry.name,
                        old_value=entry.old_value,
                        new_value=entry.old_value,
                        status="Not Found",
                        source_file=api_source,
                        changed=False,
                    )
                )
                continue

            changed = base_result.new_value != entry.old_value if base_result.status != "Not Found" else False
            results.append(
                OffsetResult(
                    name=entry.name,
                    old_value=entry.old_value,
                    new_value=base_result.new_value if base_result.status != "Not Found" else entry.old_value,
                    status="Updated" if changed else base_result.status,
                    source_file=base_result.source_file,
                    changed=changed,
                )
            )

        final_results_by_name = dict(results_by_name)
        final_lines = list(updated_lines)
        final_entries = list(entries)
        self.after(
            0,
            lambda md=matched_game_name: self.finish_offset_update(
                "",
                results,
                final_lines,
                final_entries,
                final_results_by_name,
                files_scanned=0,
                found_count=found_count,
                updated_count=updated_count,
                mode_detail=f"API mode ({md})",
            ),
        )

    def fail_offset_update(self, error_message: str) -> None:
        if self.use_dumpspace_api_var.get():
            self.offset_dump_age_var.set("Dumpspace dump last updated: unavailable")
        self.offset_status_var.set(f"Offset update failed: {error_message}")
        self.set_offset_controls_enabled(True)
        messagebox.showerror("Offset updater error", error_message)

    def fetch_dumpspace_offsets(self, game_name: str) -> tuple[str, str, dict[str, str], int | None]:
        try:
            with urllib.request.urlopen(self.DUMPSPACE_GAMELIST_URL, timeout=30) as response:
                game_list_data = response.read().decode("utf-8", errors="replace")
            game_list_json = json.loads(game_list_data)
        except Exception as ex:
            raise RuntimeError(f"Could not load Dumpspace game list: {ex}") from ex

        games = game_list_json.get("games", []) if isinstance(game_list_json, dict) else []
        if not games:
            raise RuntimeError("Dumpspace game list is empty or invalid.")

        query_raw = game_name.strip().lower()
        query_normalized = self.normalize_game_lookup_name(query_raw)
        exact_matches = [
            game
            for game in games
            if self.normalize_game_lookup_name(str(game.get("name", ""))) == query_normalized
            or str(game.get("name", "")).strip().lower() == query_raw
        ]
        if exact_matches:
            matches = exact_matches
        else:
            matches = [
                game
                for game in games
                if query_raw in str(game.get("name", "")).lower()
                or (
                    query_normalized
                    and query_normalized in self.normalize_game_lookup_name(str(game.get("name", "")))
                )
            ]

        if not matches:
            raise RuntimeError(f"No game found on Dumpspace for name '{game_name}'.")

        matches.sort(key=lambda game: int(game.get("uploaded", 0)), reverse=True)
        selected_game = matches[0]

        engine = str(selected_game.get("engine", "")).strip()
        location = str(selected_game.get("location", "")).strip()
        selected_name = str(selected_game.get("name", "")).strip()
        selected_hash = str(selected_game.get("hash", "")).strip()

        if not engine or not location:
            raise RuntimeError(f"Selected Dumpspace game '{selected_name}' is missing engine/location fields.")

        base_url = f"{self.DUMPSPACE_BASE}{engine}/{location}/"
        payloads = {
            "offsets": self.fetch_dumpspace_gzip_json(base_url + "OffsetsInfo.json.gz"),
            "classes": self.fetch_dumpspace_gzip_json(base_url + "ClassesInfo.json.gz"),
            "structs": self.fetch_dumpspace_gzip_json(base_url + "StructsInfo.json.gz"),
            "functions": self.fetch_dumpspace_gzip_json(base_url + "FunctionsInfo.json.gz"),
            "enums": self.fetch_dumpspace_gzip_json(base_url + "EnumsInfo.json.gz"),
        }
        updated_values: list[int] = []
        for payload in payloads.values():
            if isinstance(payload, dict):
                updated_raw = payload.get("updated_at")
                if isinstance(updated_raw, (int, float)):
                    updated_values.append(int(updated_raw))
                elif isinstance(updated_raw, str) and updated_raw.isdigit():
                    updated_values.append(int(updated_raw))

        symbols: dict[str, str] = {}
        self.extract_offset_symbols(payloads.get("offsets"), symbols)
        self.extract_type_member_symbols(payloads.get("classes"), symbols, "class")
        self.extract_type_member_symbols(payloads.get("structs"), symbols, "struct")
        self.extract_function_symbols(payloads.get("functions"), symbols)
        self.extract_enum_symbols(payloads.get("enums"), symbols)

        latest_update_ms = max(updated_values) if updated_values else None
        return selected_name, selected_hash, symbols, latest_update_ms

    def fetch_dumpspace_gzip_json(self, url: str) -> dict | list:
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                compressed_data = response.read()
            decompressed = gzip.decompress(compressed_data).decode("utf-8", errors="replace")
            return json.loads(decompressed)
        except Exception as ex:
            raise RuntimeError(f"Could not load Dumpspace data from '{url}': {ex}") from ex

    def extract_offset_symbols(self, payload: dict | list | None, symbols: dict[str, str]) -> None:
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list):
            return
        for item in data:
            if not isinstance(item, list) or len(item) < 2:
                continue
            name = self.clean_symbol_name(item[0])
            value = self.to_normalized_offset_value(item[1])
            if not name or value is None:
                continue
            self.add_symbol(symbols, name, value)

    def extract_type_member_symbols(self, payload: dict | list | None, symbols: dict[str, str], kind: str) -> None:
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list):
            return

        for type_obj in data:
            if not isinstance(type_obj, dict) or not type_obj:
                continue
            type_name, entries = next(iter(type_obj.items()))
            clean_type = self.clean_symbol_name(type_name)
            if not isinstance(entries, list):
                continue

            for entry in entries:
                if not isinstance(entry, dict) or not entry:
                    continue
                member_name, member_data = next(iter(entry.items()))
                clean_member = self.clean_symbol_name(member_name)
                if not clean_member or clean_member.startswith("__"):
                    continue
                if not isinstance(member_data, list) or len(member_data) < 2:
                    continue
                value = self.to_normalized_offset_value(member_data[1])
                if value is None:
                    continue

                self.add_symbol(symbols, clean_member, value)
                if clean_type:
                    self.add_symbol(symbols, f"{clean_type}.{clean_member}", value)
                    self.add_symbol(symbols, f"{clean_type}::{clean_member}", value)
                    self.add_symbol(symbols, f"{kind}:{clean_type}.{clean_member}", value)

    def extract_function_symbols(self, payload: dict | list | None, symbols: dict[str, str]) -> None:
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list):
            return

        for type_obj in data:
            if not isinstance(type_obj, dict) or not type_obj:
                continue
            type_name, entries = next(iter(type_obj.items()))
            clean_type = self.clean_symbol_name(type_name)
            if not isinstance(entries, list):
                continue

            for entry in entries:
                if not isinstance(entry, dict) or not entry:
                    continue
                function_name, function_data = next(iter(entry.items()))
                clean_function = self.clean_symbol_name(function_name)
                if not clean_function:
                    continue
                if not isinstance(function_data, list) or len(function_data) < 3:
                    continue
                value = self.to_normalized_offset_value(function_data[2])
                if value is None:
                    continue

                self.add_symbol(symbols, clean_function, value)
                if clean_type:
                    self.add_symbol(symbols, f"{clean_type}.{clean_function}", value)
                    self.add_symbol(symbols, f"{clean_type}::{clean_function}", value)
                    self.add_symbol(symbols, f"function:{clean_type}.{clean_function}", value)

    def extract_enum_symbols(self, payload: dict | list | None, symbols: dict[str, str]) -> None:
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(data, list):
            return

        for enum_obj in data:
            if not isinstance(enum_obj, dict) or not enum_obj:
                continue
            enum_name, enum_data = next(iter(enum_obj.items()))
            clean_enum = self.clean_symbol_name(enum_name)
            if not isinstance(enum_data, list) or not enum_data:
                continue
            values_block = enum_data[0]
            if not isinstance(values_block, list):
                continue

            for value_item in values_block:
                if not isinstance(value_item, dict) or not value_item:
                    continue
                const_name, const_value = next(iter(value_item.items()))
                clean_const = self.clean_symbol_name(const_name)
                value = self.to_normalized_offset_value(const_value)
                if not clean_const or value is None:
                    continue

                self.add_symbol(symbols, clean_const, value)
                if clean_enum:
                    self.add_symbol(symbols, f"{clean_enum}.{clean_const}", value)
                    self.add_symbol(symbols, f"enum:{clean_enum}.{clean_const}", value)

    @staticmethod
    def clean_symbol_name(name: object) -> str:
        return str(name).strip().strip("\"'")

    def add_symbol(self, symbols: dict[str, str], name: str, value: str) -> None:
        if not name:
            return
        if name not in symbols:
            symbols[name] = value

    def to_normalized_offset_value(self, value: object) -> str | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return self.normalize_offset_value(str(int(value)))
        value_str = str(value).strip().strip("\"'")
        if not value_str:
            return None
        if re.fullmatch(r"0[xX][0-9A-Fa-f]+|\d+", value_str):
            return self.normalize_offset_value(value_str)
        return None

    @staticmethod
    def normalize_game_lookup_name(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", name.lower())

    @staticmethod
    def normalize_symbol_lookup_name(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", name.lower())

    def format_dumpspace_last_updated_text(self, updated_at_ms: int | None) -> str:
        if updated_at_ms is None:
            return "Dumpspace dump last updated: unknown"
        return f"Dumpspace dump last updated: {self.format_elapsed_since_ms(updated_at_ms)}"

    @staticmethod
    def format_elapsed_since_ms(updated_at_ms: int) -> str:
        now_ms = int(time.time() * 1000)
        delta_ms = max(now_ms - int(updated_at_ms), 0)

        seconds = delta_ms // 1000
        if seconds < 5:
            return "just now"
        if seconds < 60:
            return f"{seconds} seconds ago"

        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"

        hours = minutes // 60
        if hours < 24:
            return f"{hours} hour{'s' if hours != 1 else ''} ago"

        days = hours // 24
        if days < 7:
            return f"{days} day{'s' if days != 1 else ''} ago"

        weeks = days // 7
        if weeks < 5:
            return f"{weeks} week{'s' if weeks != 1 else ''} ago"

        months = days // 30
        if months < 12:
            return f"{months} month{'s' if months != 1 else ''} ago"

        years = days // 365
        return f"{years} year{'s' if years != 1 else ''} ago"

    @staticmethod
    def normalize_offset_value(value: str) -> str:
        try:
            parsed = int(value, 0)
        except ValueError:
            return value
        return f"0x{parsed:X}"

    def parse_offset_entries(self, raw_text: str) -> tuple[list[OffsetEntry], list[str]]:
        lines = raw_text.splitlines()
        entries: list[OffsetEntry] = []

        for idx, line in enumerate(lines):
            match = re.search(
                r'(?P<name>"[A-Za-z_][\w:.]*"|\'[A-Za-z_][\w:.]*\'|[A-Za-z_][\w:.]*)\s*[:=]\s*(?P<q>["\']?)(?P<value>0[xX][0-9A-Fa-f]+|\d+)(?P=q)',
                line,
            )
            if not match:
                continue

            entries.append(
                OffsetEntry(
                    name=match.group("name").strip("\"'"),
                    old_value=self.normalize_offset_value(match.group("value")),
                    line_index=idx,
                )
            )

        return entries, lines

    def finish_offset_update(
        self,
        folder: str,
        results: list[OffsetResult],
        updated_lines: list[str],
        entries: list[OffsetEntry],
        results_by_name: dict[str, OffsetResult],
        files_scanned: int,
        found_count: int,
        updated_count: int,
        mode_detail: str | None = None,
    ) -> None:
        total_targets = len({result.name for result in results})
        self.render_offset_output(updated_lines, entries, results_by_name, mark_not_found=True)
        self.update_offset_status_progress(
            total_targets=total_targets,
            found_count=found_count,
            updated_count=updated_count,
            files_scanned=files_scanned,
            done=True,
            mode_detail=mode_detail,
        )
        self.set_offset_controls_enabled(True)

    def set_offset_output_text(self, text: str) -> None:
        self.offset_output_text.configure(state=tk.NORMAL)
        self.offset_output_text.delete("1.0", tk.END)
        self.offset_output_text.insert("1.0", text)
        self.offset_output_text.configure(state=tk.DISABLED)

    def set_offset_results_text(self, text: str) -> None:
        self.offset_results_text.configure(state=tk.NORMAL)
        self.offset_results_text.delete("1.0", tk.END)
        self.offset_results_text.insert("1.0", text)
        self.offset_results_text.configure(state=tk.DISABLED)

    def append_offset_result(self, result: OffsetResult, root_folder: str) -> None:
        self.offset_results_text.configure(state=tk.NORMAL)
        if not result.source_file:
            source_display = "-"
        elif root_folder and os.path.isdir(root_folder):
            source_display = self.make_relative_path(result.source_file, root_folder)
        else:
            source_display = result.source_file
        line = (
            f"{result.name}: {result.old_value} -> {result.new_value} | "
            f"{result.status} | Source: {source_display}\n"
        )
        tag = self.get_offset_result_tag(result)
        self.offset_results_text.insert(tk.END, line, tag)
        self.offset_results_text.see(tk.END)
        self.offset_results_text.configure(state=tk.DISABLED)

    @staticmethod
    def get_offset_result_tag(result: OffsetResult) -> str:
        if result.status == "Not Found":
            return "not_found"
        if result.changed:
            return "changed"
        return "same"

    def update_offset_status_progress(
        self,
        total_targets: int,
        found_count: int,
        updated_count: int,
        files_scanned: int,
        done: bool,
        mode_detail: str | None = None,
    ) -> None:
        found_same_count = max(found_count - updated_count, 0)
        not_found_count = max(total_targets - found_count, 0)
        prefix = "Complete" if done else "Scanning..."
        if mode_detail:
            prefix += f" [{mode_detail}]"
        self.offset_status_var.set(
            f"{prefix} Targets: {total_targets} | Found: {found_count} | Updated: {updated_count} | "
            f"Found Same: {found_same_count} | Not Found: {not_found_count} | Files scanned: {files_scanned}"
        )

    def render_offset_output(
        self,
        lines: list[str],
        entries: list[OffsetEntry],
        results_by_name: dict[str, OffsetResult],
        mark_not_found: bool,
    ) -> None:
        line_tags: dict[int, str] = {}

        for entry in entries:
            result = results_by_name.get(entry.name)
            if result is None:
                if not mark_not_found:
                    continue
                tag = "not_found"
            else:
                tag = self.get_offset_result_tag(result)

            current = line_tags.get(entry.line_index)
            line_tags[entry.line_index] = self.pick_stronger_tag(current, tag)

        self.offset_output_text.configure(state=tk.NORMAL)
        self.offset_output_text.delete("1.0", tk.END)
        for idx, line in enumerate(lines):
            tag = line_tags.get(idx)
            if tag:
                self.offset_output_text.insert(tk.END, f"{line}\n", tag)
            else:
                self.offset_output_text.insert(tk.END, f"{line}\n")
        self.offset_output_text.configure(state=tk.DISABLED)

    @staticmethod
    def pick_stronger_tag(current: str | None, new_tag: str) -> str:
        priority = {"changed": 3, "same": 2, "not_found": 1}
        if current is None:
            return new_tag
        if priority.get(new_tag, 0) > priority.get(current, 0):
            return new_tag
        return current

    @staticmethod
    def build_offset_lookup_pattern(name: str, case_sensitive: bool) -> re.Pattern[str]:
        escaped = re.escape(name)
        key_pattern = rf"(?:\"{escaped}\"|'{escaped}'|\b{escaped}\b)"
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.compile(
            rf"{key_pattern}\s*[:=]\s*(?:[\"']?(0[xX][0-9A-Fa-f]+|\d+)[\"']?)",
            flags,
        )

    @staticmethod
    def replace_offset_value(line: str, new_value: str) -> str:
        return re.sub(
            r'([:=]\s*)(["\']?)(0[xX][0-9A-Fa-f]+|\d+)(\2)',
            lambda m: f"{m.group(1)}{m.group(2)}{new_value}{m.group(2)}",
            line,
            count=1,
        )

    @staticmethod
    def make_relative_path(file_path: str, root_folder: str) -> str:
        if not file_path:
            return ""
        try:
            return os.path.relpath(file_path, root_folder)
        except ValueError:
            return file_path

    @staticmethod
    def is_probably_binary(file_path: str) -> bool:
        try:
            with open(file_path, "rb") as f:
                chunk = f.read(8192)
            if not chunk:
                return False
            return b"\x00" in chunk
        except OSError:
            return True

    @staticmethod
    def find_matches_in_text(
        file_path: str,
        text: str,
        query: str,
        query_cmp: str,
        case_sensitive: bool,
        context_chars: int,
    ) -> list[MatchResult]:
        results: list[MatchResult] = []
        haystack = text if case_sensitive else text.lower()

        if not query_cmp:
            return results

        line_starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                line_starts.append(i + 1)
        lines = text.splitlines()

        start = 0
        qlen = len(query)

        while True:
            idx = haystack.find(query_cmp, start)
            if idx == -1:
                break

            line = bisect.bisect_right(line_starts, idx)
            col_start = line_starts[line - 1]
            col = (idx - col_start) + 1

            snippet_start = max(0, idx - context_chars)
            snippet_end = min(len(text), idx + qlen + context_chars)

            before = text[snippet_start:idx].replace("\n", " ")
            match_text = text[idx : idx + qlen].replace("\n", " ")
            after = text[idx + qlen : snippet_end].replace("\n", " ")
            snippet = f"{before}[[{match_text}]]{after}".strip()

            line_index = line - 1
            detail_start = max(0, line_index - 1)
            detail_end = min(len(lines), line_index + 2)
            detail_parts = [f"{n + 1}: {lines[n]}" for n in range(detail_start, detail_end)]
            detail = "\n".join(detail_parts)

            results.append(
                MatchResult(
                    file_path=file_path,
                    line=line,
                    column=col,
                    snippet=snippet,
                    detail=detail,
                )
            )

            start = idx + max(qlen, 1)

        return results

    def clear_search_results(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.results.clear()
        self.tree_item_to_index.clear()
        self.set_detail_text("")

    def finish_search(
        self,
        matches: list[MatchResult],
        files_scanned: int,
        binary_skipped: int,
        unreadable: int,
        limited: bool,
    ) -> None:
        self.results = matches

        for idx, result in enumerate(matches):
            rel_path = self.make_relative_path(result.file_path, self.folder_var.get().strip())
            item = self.tree.insert(
                "",
                tk.END,
                values=(rel_path, result.line, result.column, result.snippet),
            )
            self.tree_item_to_index[item] = idx

        file_count = len({m.file_path for m in matches})
        base_status = (
            f"Scanned {files_scanned} files | Matches: {len(matches)} | Files with hits: {file_count} | "
            f"Binary skipped: {binary_skipped} | Unreadable: {unreadable}"
        )
        if limited:
            base_status += " | Stopped at max hits limit"

        self.search_status_var.set(base_status)
        self.set_search_controls_enabled(True)

    def on_result_selected(self, _event: tk.Event) -> None:
        selected = self.tree.selection()
        if not selected:
            return

        item = selected[0]
        idx = self.tree_item_to_index.get(item)
        if idx is None or idx >= len(self.results):
            return

        result = self.results[idx]
        detail = (
            f"File: {result.file_path}\n"
            f"Line: {result.line}, Column: {result.column}\n"
            f"\n"
            f"{result.detail}\n"
        )
        self.set_detail_text(detail)

    def set_detail_text(self, text: str) -> None:
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert("1.0", text)
        self.detail_text.configure(state=tk.DISABLED)


if __name__ == "__main__":
    app = FileSearchApp()
    app.mainloop()
