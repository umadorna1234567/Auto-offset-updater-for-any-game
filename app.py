import bisect
import gzip
import json
import os
import re
import threading
import time
import tkinter as tk
import urllib.request
import urllib.error
import sys
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
    entry_type: str = "offset"
    source_file: str = ""


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
        self._init_pattern_state()

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
        self.offset_file_mode_var = tk.BooleanVar(value=False)
        self.offset_files_summary_var = tk.StringVar(value="No files selected for copy-update mode.")
        desktop_dir = os.path.join(os.path.expanduser("~"), "Desktop")
        self.offset_output_folder_var = tk.StringVar(value=os.path.join(desktop_dir, "OffsetUpdaterOutput"))
        self.offset_dump_age_var = tk.StringVar(value="")
        self.offset_status_var = tk.StringVar(value="Paste offsets, pick a folder, then click Find New Offsets.")
        self.offset_selected_files: list[str] = []

    def _init_pattern_state(self) -> None:
        app_dir = self.get_app_directory()
        self.pattern_bridge_url_var = tk.StringVar(value="http://127.0.0.1:8765")
        self.pattern_name_case_sensitive_var = tk.BooleanVar(value=False)
        self.pattern_status_var = tk.StringVar(
            value="Click Connect IDA, then paste old patterns and click Find New Patterns."
        )
        self.pattern_bridge_status_var = tk.StringVar(
            value="IDA bridge: not connected"
        )
        self.pattern_selected_script_var = tk.StringVar(
            value=os.path.abspath(os.path.join(app_dir, "ida_pattern_bridge.py"))
        )

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(container)
        notebook.pack(fill=tk.BOTH, expand=True)

        self.search_tab = ttk.Frame(notebook)
        self.offset_tab = ttk.Frame(notebook)
        self.pattern_tab = ttk.Frame(notebook)

        notebook.add(self.search_tab, text="File Search")
        notebook.add(self.offset_tab, text="Offset Updater")
        notebook.add(self.pattern_tab, text="Pattern Updater")

        self._build_search_tab(self.search_tab)
        self._build_offset_tab(self.offset_tab)
        self._build_pattern_tab(self.pattern_tab)

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

        self.offset_file_mode_check = ttk.Checkbutton(
            action_frame,
            text="File mode (copy updated files)",
            variable=self.offset_file_mode_var,
            command=self.on_offset_file_mode_changed,
        )
        self.offset_file_mode_check.pack(side=tk.LEFT, padx=(8, 0))
        self.offset_controls.append(self.offset_file_mode_check)

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

        self.file_mode_frame = ttk.Frame(parent)
        self.file_mode_frame.pack(fill=tk.X, pady=(0, 6))

        self.select_files_btn = ttk.Button(
            self.file_mode_frame,
            text="Select files to update",
            command=self.pick_offset_target_files,
        )
        self.select_files_btn.pack(side=tk.LEFT)
        self.offset_controls.append(self.select_files_btn)

        self.clear_files_btn = ttk.Button(
            self.file_mode_frame,
            text="Clear",
            command=self.clear_offset_target_files,
        )
        self.clear_files_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.offset_controls.append(self.clear_files_btn)

        self.file_mode_summary_label = ttk.Label(
            self.file_mode_frame,
            textvariable=self.offset_files_summary_var,
            anchor=tk.W,
        )
        self.file_mode_summary_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 0))

        self.file_output_frame = ttk.Frame(parent)
        self.file_output_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(self.file_output_frame, text="Output folder").pack(side=tk.LEFT)
        self.output_folder_entry = ttk.Entry(self.file_output_frame, textvariable=self.offset_output_folder_var)
        self.output_folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        self.offset_controls.append(self.output_folder_entry)

        self.output_folder_browse_btn = ttk.Button(
            self.file_output_frame,
            text="Browse",
            command=self.pick_offset_output_folder,
        )
        self.output_folder_browse_btn.pack(side=tk.LEFT)
        self.offset_controls.append(self.output_folder_browse_btn)

        top_split = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        self.offset_top_split = top_split
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

        self.offset_progress = ttk.Progressbar(parent, orient=tk.HORIZONTAL, mode="determinate")
        self.offset_progress.pack(fill=tk.X, pady=(8, 0))

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
        self.on_offset_file_mode_changed()

    def _build_pattern_tab(self, parent: ttk.Frame) -> None:
        self.pattern_controls: list[tk.Widget] = []

        bridge_frame = ttk.Frame(parent)
        bridge_frame.pack(fill=tk.X)

        self.pattern_connect_btn = ttk.Button(
            bridge_frame,
            text="Connect IDA",
            command=self.connect_pattern_bridge,
        )
        self.pattern_connect_btn.pack(side=tk.LEFT)
        self.pattern_controls.append(self.pattern_connect_btn)

        bridge_status = ttk.Label(parent, textvariable=self.pattern_bridge_status_var, anchor=tk.W)
        bridge_status.pack(fill=tk.X, pady=(6, 0))

        helper_frame = ttk.Frame(parent)
        helper_frame.pack(fill=tk.X, pady=(6, 8))
        ttk.Label(
            helper_frame,
            text="Connect IDA checks the live bridge. If IDA is not connected yet, the app copies the bridge script path and tells you the single action to run inside IDA.",
        ).pack(side=tk.LEFT)

        action_frame = ttk.Frame(parent)
        action_frame.pack(fill=tk.X, pady=(0, 8))

        self.pattern_update_btn = ttk.Button(
            action_frame,
            text="Find New Patterns",
            command=self.start_pattern_update,
        )
        self.pattern_update_btn.pack(side=tk.LEFT)
        self.pattern_controls.append(self.pattern_update_btn)

        self.pattern_case_check = ttk.Checkbutton(
            action_frame,
            text="Case sensitive names",
            variable=self.pattern_name_case_sensitive_var,
        )
        self.pattern_case_check.pack(side=tk.LEFT, padx=(8, 0))
        self.pattern_controls.append(self.pattern_case_check)

        self.pattern_copy_btn = ttk.Button(
            action_frame,
            text="Copy Updated Patterns",
            command=self.copy_updated_patterns,
        )
        self.pattern_copy_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.pattern_controls.append(self.pattern_copy_btn)

        ttk.Label(
            action_frame,
            text='Paste old patterns on the left. Example: g_opcodes->scan(..., "48 89 5C 24 ?")',
        ).pack(side=tk.LEFT, padx=(12, 0))

        top_split = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        top_split.pack(fill=tk.BOTH, expand=True)

        input_frame = ttk.LabelFrame(top_split, text="Old Patterns Input")
        output_frame = ttk.LabelFrame(top_split, text="Updated Patterns Output")
        top_split.add(input_frame, weight=1)
        top_split.add(output_frame, weight=1)

        self.pattern_input_text = ScrolledText(input_frame, wrap=tk.NONE, height=14, font=("Consolas", 10))
        self.pattern_input_text.pack(fill=tk.BOTH, expand=True)

        self.pattern_output_text = ScrolledText(output_frame, wrap=tk.NONE, height=14, font=("Consolas", 10), state=tk.DISABLED)
        self.pattern_output_text.pack(fill=tk.BOTH, expand=True)
        self.pattern_output_text.tag_configure("changed", foreground="#0A7A16", background="#E7F7EA")
        self.pattern_output_text.tag_configure("same", foreground="#8A6D00", background="#FFF7D6")
        self.pattern_output_text.tag_configure("not_found", foreground="#B00020", background="#FDE7EA")

        self.pattern_progress = ttk.Progressbar(parent, orient=tk.HORIZONTAL, mode="determinate")
        self.pattern_progress.pack(fill=tk.X, pady=(8, 0))

        result_frame = ttk.LabelFrame(parent, text="Per-Pattern Results")
        result_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self.pattern_results_text = ScrolledText(result_frame, wrap=tk.WORD, height=10, font=("Consolas", 10), state=tk.DISABLED)
        self.pattern_results_text.pack(fill=tk.BOTH, expand=True)
        self.pattern_results_text.tag_configure("changed", foreground="#0A7A16", background="#E7F7EA")
        self.pattern_results_text.tag_configure("same", foreground="#8A6D00", background="#FFF7D6")
        self.pattern_results_text.tag_configure("not_found", foreground="#B00020", background="#FDE7EA")

        legend_frame = ttk.Frame(parent)
        legend_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(legend_frame, text="Color key:").pack(side=tk.LEFT)
        tk.Label(legend_frame, text=" Green = updated pattern found and changed ", fg="#0A7A16").pack(side=tk.LEFT, padx=(8, 6))
        tk.Label(legend_frame, text=" Yellow = found but same pattern ", fg="#B8860B").pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(legend_frame, text=" Red = not found ", fg="#B00020").pack(side=tk.LEFT)

        status = ttk.Label(parent, textvariable=self.pattern_status_var, anchor=tk.W)
        status.pack(fill=tk.X, pady=(8, 0))

        starter = (
            'static auto fn = reinterpret_cast<void(__fastcall*)(void*, int, unsigned int)>(g_opcodes->scan(g_modules->m_modules.client_dll.get_name(), "85 D2 0F 88 ? ? ? ? 55 56 57"));\n'
            'static GetBonePosition_t fn = reinterpret_cast<GetBonePosition_t>(g_opcodes->scan(g_modules->m_modules.client_dll.get_name(), "48 89 6C 24 ? 48 89 74 24 ? 48 89 7C 24 ? 41 56 48 83 EC ? 4D 8B F1 49 8B E8"));'
        )
        self.pattern_input_text.insert("1.0", starter)

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

    def pick_offset_target_files(self) -> None:
        initial_dir = os.path.expanduser("~")
        if self.offset_selected_files:
            first_dir = os.path.dirname(self.offset_selected_files[0])
            if os.path.isdir(first_dir):
                initial_dir = first_dir
        selected = filedialog.askopenfilenames(
            initialdir=initial_dir,
            title="Select files to create updated copies from",
            filetypes=[
                ("Code/Text files", "*.cpp *.h *.hpp *.c *.cs *.lua *.txt *.json *.ini *.cfg *.xml *.js *.ts"),
                ("All files", "*.*"),
            ],
        )
        if selected:
            self.offset_selected_files = [str(path) for path in selected]
            self.refresh_offset_files_summary()
            if self.offset_file_mode_var.get():
                self.populate_offset_input_from_selected_files()

    def clear_offset_target_files(self) -> None:
        self.offset_selected_files = []
        self.refresh_offset_files_summary()
        if self.offset_file_mode_var.get():
            self.offset_input_text.delete("1.0", tk.END)

    def pick_offset_output_folder(self) -> None:
        current = self.offset_output_folder_var.get().strip()
        start_dir = current if os.path.isdir(current) else os.path.join(os.path.expanduser("~"), "Desktop")
        selected = filedialog.askdirectory(initialdir=start_dir)
        if selected:
            self.offset_output_folder_var.set(selected)

    def refresh_offset_files_summary(self) -> None:
        count = len(self.offset_selected_files)
        if count == 0:
            self.offset_files_summary_var.set("No files selected for copy-update mode.")
            return
        preview = os.path.basename(self.offset_selected_files[0])
        if count == 1:
            self.offset_files_summary_var.set(f"1 file selected: {preview}")
        else:
            self.offset_files_summary_var.set(f"{count} files selected. First: {preview}")

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

    def on_offset_file_mode_changed(self) -> None:
        if self.offset_file_mode_var.get():
            if not self.file_mode_frame.winfo_manager():
                self.file_mode_frame.pack(fill=tk.X, pady=(0, 6), before=self.offset_top_split)
            if not self.file_output_frame.winfo_manager():
                self.file_output_frame.pack(fill=tk.X, pady=(0, 8), before=self.offset_top_split)
            if self.offset_selected_files:
                self.populate_offset_input_from_selected_files()
        else:
            if self.file_mode_frame.winfo_manager():
                self.file_mode_frame.pack_forget()
            if self.file_output_frame.winfo_manager():
                self.file_output_frame.pack_forget()

    def populate_offset_input_from_selected_files(self) -> None:
        if not self.offset_selected_files:
            return
        blocks: list[str] = []
        for file_path in self.offset_selected_files:
            abs_path = os.path.abspath(file_path)
            blocks.append(f"### FILE: {abs_path} ###")
            blocks.append(f"// {os.path.basename(abs_path)}")
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError as ex:
                blocks.append(f"// Could not read file: {ex}")
                blocks.append("")
                continue
            blocks.append(content.rstrip("\n"))
            blocks.append("")

        text = "\n".join(blocks).rstrip() + "\n"
        self.offset_input_text.delete("1.0", tk.END)
        self.offset_input_text.insert("1.0", text)

    def set_search_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in self.search_controls:
            widget.configure(state=state)

    def set_offset_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in self.offset_controls:
            widget.configure(state=state)
        self.offset_input_text.configure(state=state)

    def set_pattern_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in self.pattern_controls:
            widget.configure(state=state)
        self.pattern_input_text.configure(state=state)

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
        file_mode_enabled = self.offset_file_mode_var.get()
        target_files = list(self.offset_selected_files)
        output_folder = self.offset_output_folder_var.get().strip()
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
        if file_mode_enabled and not target_files:
            messagebox.showwarning("No files selected", "Select at least one file to create updated copies from.")
            return
        if file_mode_enabled and not output_folder:
            output_folder = os.path.join(os.path.expanduser("~"), "Desktop", "OffsetUpdaterOutput")
            self.offset_output_folder_var.set(output_folder)
        if file_mode_enabled and target_files:
            self.populate_offset_input_from_selected_files()
            raw_text = self.offset_input_text.get("1.0", "end-1c")

        entries, lines = self.parse_offset_entries(raw_text)
        if file_mode_enabled and target_files:
            file_entries, file_lines = self.collect_file_mode_entries(target_files)
            if file_entries:
                entries = file_entries
                lines = file_lines
                raw_text = "\n".join(file_lines)
        if not entries:
            messagebox.showwarning(
                "No offsets found",
                "No valid offsets were found in the input.",
            )
            return

        unique_count = len({self.get_entry_key(entry) for entry in entries})
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
        self.set_offset_progress(0, unique_count)
        self.set_offset_output_text(raw_text)
        self.set_offset_results_text("Live results:\n")

        if use_api:
            thread = threading.Thread(
                target=self._offset_api_worker,
                args=(
                    source_input,
                    entries,
                    lines,
                    name_case_sensitive,
                    file_mode_enabled,
                    target_files,
                    output_folder,
                ),
                daemon=True,
            )
        else:
            thread = threading.Thread(
                target=self._offset_worker,
                args=(
                    source_input,
                    entries,
                    lines,
                    name_case_sensitive,
                    file_mode_enabled,
                    target_files,
                    output_folder,
                ),
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

    def copy_updated_patterns(self) -> None:
        text = self.pattern_output_text.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showwarning("Nothing to copy", "No updated pattern text is available yet.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        self.pattern_status_var.set("Copied updated patterns to clipboard.")

    def copy_pattern_script_path(self) -> None:
        script_path = self.ensure_bridge_script_available()
        if not script_path:
            messagebox.showwarning("Missing script path", "The IDA bridge script could not be prepared.")
            return
        self.pattern_selected_script_var.set(script_path)
        self.clipboard_clear()
        self.clipboard_append(script_path)
        self.update_idletasks()
        self.pattern_bridge_status_var.set(f"IDA bridge script path copied: {script_path}")

    def connect_pattern_bridge(self) -> None:
        bridge_url = self.pattern_bridge_url_var.get().strip()
        if not bridge_url:
            messagebox.showwarning("Missing bridge URL", "Enter the IDA bridge URL first.")
            return
        self.pattern_bridge_status_var.set("IDA bridge: connecting...")
        thread = threading.Thread(
            target=self._pattern_bridge_connect_worker,
            args=(bridge_url,),
            daemon=True,
        )
        thread.start()

    def _pattern_bridge_connect_worker(self, bridge_url: str) -> None:
        try:
            payload = self.call_pattern_bridge(bridge_url, "/health", None)
        except Exception as ex:
            self.after(0, lambda e=str(ex): self.handle_pattern_bridge_offline(e))
            return

        ida_file = str(payload.get("input_file", "")).strip()
        version = str(payload.get("version", "")).strip()
        if ida_file:
            status = f"IDA bridge: connected | Database: {ida_file}"
        else:
            status = "IDA bridge: connected"
        if version:
            status += f" | Version: {version}"
        self.after(0, lambda s=status: self.pattern_bridge_status_var.set(s))

    def handle_pattern_bridge_offline(self, error_message: str) -> None:
        self.copy_pattern_script_path()
        self.pattern_bridge_status_var.set(f"IDA bridge: offline | Script path copied | {error_message}")
        messagebox.showinfo(
            "Connect IDA",
            "IDA is not connected yet.\n\n"
            "The bridge script path was copied to your clipboard.\n\n"
            "In IDA:\n"
            "1. Press Alt+F7\n"
            "2. Paste the copied path\n"
            "3. Run the script\n"
            "4. Click Connect IDA again",
        )

    def start_pattern_update(self) -> None:
        bridge_url = self.pattern_bridge_url_var.get().strip()
        name_case_sensitive = self.pattern_name_case_sensitive_var.get()
        raw_text = self.pattern_input_text.get("1.0", "end-1c")

        if not bridge_url:
            messagebox.showwarning("Missing bridge URL", "Enter the IDA bridge URL.")
            return
        if not raw_text.strip():
            messagebox.showwarning("Missing input", "Paste at least one pattern line.")
            return

        entries, lines = self.parse_pattern_entries(raw_text)
        if not entries:
            messagebox.showwarning("No patterns found", "No valid patterns were found in the input.")
            return

        unique_count = len({self.get_entry_key(entry) for entry in entries})
        self.pattern_status_var.set(
            f"Scanning... Targets: {unique_count} | Found: 0 | Updated: 0 | Found Same: 0 | Not Found: {unique_count}"
        )
        self.set_pattern_controls_enabled(False)
        self.set_pattern_progress(0, unique_count)
        self.set_pattern_output_text(raw_text)
        self.set_pattern_results_text("Live results:\n")

        thread = threading.Thread(
            target=self._pattern_ida_worker,
            args=(bridge_url, entries, lines, name_case_sensitive),
            daemon=True,
        )
        thread.start()

    def _pattern_ida_worker(
        self,
        bridge_url: str,
        entries: list[OffsetEntry],
        lines: list[str],
        name_case_sensitive: bool,
    ) -> None:
        unique_keys = {self.get_entry_key(entry) for entry in entries}
        entries_by_key: dict[str, list[OffsetEntry]] = {}
        for entry in entries:
            entry_key = self.get_entry_key(entry)
            entries_by_key.setdefault(entry_key, []).append(entry)

        try:
            payload = self.call_pattern_bridge(
                bridge_url,
                "/update-patterns",
                {
                    "case_sensitive": name_case_sensitive,
                    "entries": [
                        {
                            "name": entries_by_key[key][0].name,
                            "old_value": entries_by_key[key][0].old_value,
                        }
                        for key in sorted(unique_keys)
                    ],
                },
            )
        except Exception as ex:
            self.after(0, lambda e=str(ex): self.fail_pattern_update(e))
            return

        bridge_status_bits = ["IDA bridge: connected"]
        input_file = str(payload.get("input_file", "")).strip()
        if input_file:
            bridge_status_bits.append(f"Database: {input_file}")
        self.after(0, lambda s=" | ".join(bridge_status_bits): self.pattern_bridge_status_var.set(s))

        result_items = payload.get("results", [])
        result_map: dict[str, dict[str, object]] = {}
        if isinstance(result_items, list):
            for item in result_items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                result_map[f"signature|{name}"] = item

        results_by_key: dict[str, OffsetResult] = {}
        updated_lines = list(lines)
        found_count = 0
        updated_count = 0
        total_targets = len(unique_keys)
        source_root = input_file or bridge_url

        for entry_key in sorted(unique_keys):
            sample_entry = entries_by_key[entry_key][0]
            item = result_map.get(entry_key)
            if item is None:
                result = OffsetResult(
                    name=self.format_pattern_display_name(sample_entry),
                    old_value=sample_entry.old_value,
                    new_value=sample_entry.old_value,
                    status="Not Found",
                    source_file="",
                    changed=False,
                )
                results_by_key[entry_key] = result
                self.after(0, lambda r=result: self.append_pattern_result(r, source_root))
                continue

            new_value = self.normalize_signature_value(str(item.get("new_value", sample_entry.old_value)))
            source = str(item.get("source", "")).strip()
            item_status = str(item.get("status", "Not Found")).strip() or "Not Found"
            if item_status == "Not Found":
                result = OffsetResult(
                    name=self.format_pattern_display_name(sample_entry),
                    old_value=sample_entry.old_value,
                    new_value=sample_entry.old_value,
                    status="Not Found",
                    source_file=source,
                    changed=False,
                )
                results_by_key[entry_key] = result
                self.after(0, lambda r=result: self.append_pattern_result(r, source_root))
                continue

            changed = False
            for entry in entries_by_key.get(entry_key, []):
                if new_value != entry.old_value and 0 <= entry.line_index < len(updated_lines):
                    changed = True
                    updated_lines[entry.line_index] = self.replace_signature_value(
                        updated_lines[entry.line_index],
                        new_value,
                    )

            found_count += 1
            if changed:
                updated_count += 1

            status = "Updated" if changed else "Found Same"
            result = OffsetResult(
                name=self.format_pattern_display_name(sample_entry),
                old_value=sample_entry.old_value,
                new_value=new_value,
                status=status,
                source_file=source,
                changed=changed,
            )
            results_by_key[entry_key] = result
            self.after(0, lambda r=result: self.append_pattern_result(r, source_root))
            self.after(
                0,
                lambda t=total_targets, f=found_count, u=updated_count: self.update_pattern_status_progress(
                    total_targets=t,
                    found_count=f,
                    updated_count=u,
                    done=False,
                ),
            )
            lines_snapshot = list(updated_lines)
            entries_snapshot = list(entries)
            results_snapshot = dict(results_by_key)
            self.after(
                0,
                lambda ls=lines_snapshot, es=entries_snapshot, rs=results_snapshot: self.render_pattern_output(
                    ls,
                    es,
                    rs,
                    mark_not_found=False,
                ),
            )

        results: list[OffsetResult] = []
        for entry in entries:
            entry_key = self.get_entry_key(entry)
            base_result = results_by_key.get(entry_key)
            display_name = self.format_pattern_display_name(entry)
            if base_result is None:
                results.append(
                    OffsetResult(
                        name=display_name,
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
                    name=display_name,
                    old_value=entry.old_value,
                    new_value=base_result.new_value if base_result.status != "Not Found" else entry.old_value,
                    status="Updated" if changed else base_result.status,
                    source_file=base_result.source_file,
                    changed=changed,
                )
            )

        self.after(
            0,
            lambda rs=dict(results_by_key), rl=list(updated_lines), re=list(entries): self.finish_pattern_update(
                results=results,
                updated_lines=rl,
                entries=re,
                results_by_key=rs,
                found_count=found_count,
                updated_count=updated_count,
            ),
        )

    def fail_pattern_update(self, error_message: str) -> None:
        self.pattern_status_var.set(f"Pattern update failed: {error_message}")
        self.set_pattern_controls_enabled(True)
        messagebox.showerror("Pattern updater error", error_message)

    def finish_pattern_update(
        self,
        results: list[OffsetResult],
        updated_lines: list[str],
        entries: list[OffsetEntry],
        results_by_key: dict[str, OffsetResult],
        found_count: int,
        updated_count: int,
    ) -> None:
        total_targets = len({result.name for result in results})
        self.update_pattern_status_progress(
            total_targets=total_targets,
            found_count=found_count,
            updated_count=updated_count,
            done=True,
        )
        self.render_pattern_output(updated_lines, entries, results_by_key, mark_not_found=True)
        self.set_pattern_controls_enabled(True)

    def set_pattern_output_text(self, text: str) -> None:
        self.pattern_output_text.configure(state=tk.NORMAL)
        self.pattern_output_text.delete("1.0", tk.END)
        self.pattern_output_text.insert("1.0", text)
        self.pattern_output_text.configure(state=tk.DISABLED)

    def set_pattern_results_text(self, text: str) -> None:
        self.pattern_results_text.configure(state=tk.NORMAL)
        self.pattern_results_text.delete("1.0", tk.END)
        self.pattern_results_text.insert("1.0", text)
        self.pattern_results_text.configure(state=tk.DISABLED)

    def append_pattern_result(self, result: OffsetResult, source_root: str) -> None:
        self.pattern_results_text.configure(state=tk.NORMAL)
        source_display = result.source_file or source_root or "-"
        line = (
            f"{result.name}: {result.old_value} -> {result.new_value} | "
            f"{result.status} | Source: {source_display}\n"
        )
        tag = self.get_offset_result_tag(result)
        self.pattern_results_text.insert(tk.END, line, tag)
        self.pattern_results_text.see(tk.END)
        self.pattern_results_text.configure(state=tk.DISABLED)

    def update_pattern_status_progress(
        self,
        total_targets: int,
        found_count: int,
        updated_count: int,
        done: bool,
    ) -> None:
        found_same_count = max(found_count - updated_count, 0)
        not_found_count = max(total_targets - found_count, 0)
        prefix = "Complete" if done else "Scanning..."
        self.pattern_status_var.set(
            f"{prefix} Targets: {total_targets} | Found: {found_count} | Updated: {updated_count} | "
            f"Found Same: {found_same_count} | Not Found: {not_found_count}"
        )
        self.set_pattern_progress(found_count, total_targets)

    def set_pattern_progress(self, found_count: int, total_targets: int) -> None:
        if total_targets <= 0:
            self.pattern_progress["maximum"] = 1
            self.pattern_progress["value"] = 0
            return
        self.pattern_progress["maximum"] = total_targets
        self.pattern_progress["value"] = min(found_count, total_targets)

    def render_pattern_output(
        self,
        lines: list[str],
        entries: list[OffsetEntry],
        results_by_key: dict[str, OffsetResult],
        mark_not_found: bool,
    ) -> None:
        line_tags: dict[int, str] = {}

        for entry in entries:
            result = results_by_key.get(self.get_entry_key(entry))
            if result is None:
                if not mark_not_found:
                    continue
                tag = "not_found"
            else:
                tag = self.get_offset_result_tag(result)
            current = line_tags.get(entry.line_index)
            line_tags[entry.line_index] = self.pick_stronger_tag(current, tag)

        self.pattern_output_text.configure(state=tk.NORMAL)
        self.pattern_output_text.delete("1.0", tk.END)
        for idx, line in enumerate(lines):
            tag = line_tags.get(idx)
            if tag:
                self.pattern_output_text.insert(tk.END, f"{line}\n", tag)
            else:
                self.pattern_output_text.insert(tk.END, f"{line}\n")
        self.pattern_output_text.configure(state=tk.DISABLED)

    def call_pattern_bridge(self, bridge_url: str, path: str, payload: dict | None) -> dict[str, object]:
        url = bridge_url.rstrip("/") + path
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as ex:
            raise RuntimeError(f"Could not reach IDA bridge at {bridge_url}: {ex}") from ex
        except Exception as ex:
            raise RuntimeError(f"IDA bridge request failed: {ex}") from ex

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as ex:
            raise RuntimeError(f"IDA bridge returned invalid JSON: {ex}") from ex
        if not isinstance(parsed, dict):
            raise RuntimeError("IDA bridge returned an invalid payload.")
        if parsed.get("ok") is False:
            raise RuntimeError(str(parsed.get("error", "IDA bridge returned an error.")))
        return parsed

    def ensure_bridge_script_available(self) -> str:
        target_path = os.path.abspath(os.path.join(self.get_app_directory(), "ida_pattern_bridge.py"))
        self.pattern_selected_script_var.set(target_path)
        if os.path.isfile(target_path):
            return target_path

        source_path = self.get_bundled_resource_path("ida_pattern_bridge.py")
        if not source_path or not os.path.isfile(source_path):
            return ""

        try:
            with open(source_path, "r", encoding="utf-8") as src:
                script_text = src.read()
            with open(target_path, "w", encoding="utf-8", newline="\n") as dst:
                dst.write(script_text)
        except OSError:
            return ""
        return target_path

    def _offset_worker(
        self,
        folder: str,
        entries: list[OffsetEntry],
        lines: list[str],
        name_case_sensitive: bool,
        file_mode_enabled: bool,
        target_files: list[str],
        output_folder: str,
    ) -> None:
        unique_keys = {self.get_entry_key(entry) for entry in entries}
        entries_by_key: dict[str, list[OffsetEntry]] = {}
        for entry in entries:
            entry_key = self.get_entry_key(entry)
            entries_by_key.setdefault(entry_key, []).append(entry)

        patterns = {
            key: self.build_offset_lookup_pattern(entries_by_key[key][0].name, name_case_sensitive)
            for key in unique_keys
            if entries_by_key[key][0].entry_type == "offset"
        }

        found_map: dict[str, tuple[str, str]] = {}
        results_by_key: dict[str, OffsetResult] = {}
        updated_lines = list(lines)
        files_scanned = 0
        binary_skipped = 0
        unreadable = 0
        updated_count = 0
        total_targets = len(unique_keys)

        scan_files = self.list_offset_scan_files(folder)
        total_scan_bytes = sum(file_size for _, file_size in scan_files)
        processed_scan_bytes = 0
        total_scan_files = len(scan_files)

        done = False
        for file_index, (file_path, file_size) in enumerate(scan_files, start=1):
            files_scanned += 1

            try:
                if self.is_probably_binary(file_path):
                    binary_skipped += 1
                    self.after(
                        0,
                        lambda t=total_targets, f=len(found_map), u=updated_count, s=files_scanned, p=processed_scan_bytes + file_size, ts=total_scan_bytes, cf=file_path, cfi=file_index, tf=total_scan_files: self.update_offset_status_progress(
                            total_targets=t,
                            found_count=f,
                            updated_count=u,
                            files_scanned=s,
                            done=False,
                            scan_processed=p,
                            scan_total=ts,
                            current_file=cf,
                            current_file_index=cfi,
                            total_files=tf,
                            stage_name="reading",
                            stage_progress=1.0,
                        ),
                    )
                    processed_scan_bytes += file_size
                    continue

                text = self.read_text_file_with_progress(
                    file_path=file_path,
                    base_processed=processed_scan_bytes,
                    total_scan_bytes=total_scan_bytes,
                    total_targets=total_targets,
                    found_count=len(found_map),
                    updated_count=updated_count,
                    files_scanned=files_scanned,
                    current_file_index=file_index,
                    total_files=total_scan_files,
                )
                processed_scan_bytes += file_size
            except (OSError, UnicodeError):
                unreadable += 1
                self.after(
                    0,
                    lambda t=total_targets, f=len(found_map), u=updated_count, s=files_scanned, p=processed_scan_bytes + file_size, ts=total_scan_bytes, cf=file_path, cfi=file_index, tf=total_scan_files: self.update_offset_status_progress(
                        total_targets=t,
                        found_count=f,
                        updated_count=u,
                        files_scanned=s,
                        done=False,
                        scan_processed=p,
                        scan_total=ts,
                        current_file=cf,
                        current_file_index=cfi,
                        total_files=tf,
                        stage_name="reading",
                        stage_progress=1.0,
                    ),
                )
                processed_scan_bytes += file_size
                continue

            lower_text = text.lower()
            pending_keys = [entry_key for entry_key in unique_keys if entry_key not in found_map]
            pending_total = len(pending_keys)
            for pending_index, entry_key in enumerate(pending_keys, start=1):
                if entry_key in found_map:
                    continue

                sample_entry = entries_by_key[entry_key][0]
                new_value: str | None = None

                if sample_entry.entry_type == "offset":
                    if name_case_sensitive:
                        if sample_entry.name not in text:
                            continue
                    elif sample_entry.name.lower() not in lower_text:
                        continue
                    match = patterns[entry_key].search(text)
                    if match:
                        new_value = self.normalize_offset_value(match.group(1))
                else:
                    signature_value = self.find_signature_for_function(
                        text=text,
                        function_name=sample_entry.name,
                        old_signature=sample_entry.old_value,
                        case_sensitive=name_case_sensitive,
                    )
                    if signature_value is not None:
                        new_value = self.normalize_signature_value(signature_value)

                if new_value is None:
                    continue

                found_map[entry_key] = (new_value, file_path)

                entry_group = entries_by_key.get(entry_key, [])
                changed = False
                old_display = entry_group[0].old_value if entry_group else new_value
                for entry in entry_group:
                    if new_value != entry.old_value and 0 <= entry.line_index < len(updated_lines):
                        changed = True
                        if entry.entry_type == "signature":
                            updated_lines[entry.line_index] = self.replace_signature_value(
                                updated_lines[entry.line_index],
                                new_value,
                            )
                        else:
                            updated_lines[entry.line_index] = self.replace_offset_value(
                                updated_lines[entry.line_index],
                                new_value,
                            )

                if changed:
                    updated_count += 1

                result = OffsetResult(
                    name=self.format_entry_display_name(sample_entry),
                    old_value=old_display,
                    new_value=new_value,
                    status="Updated" if changed else "Found Same",
                    source_file=file_path,
                    changed=changed,
                )
                results_by_key[entry_key] = result

                self.after(
                    0,
                    lambda r=result, rf=folder: self.append_offset_result(r, rf),
                )
                self.after(
                    0,
                    lambda t=total_targets, f=len(found_map), u=updated_count, s=files_scanned, p=processed_scan_bytes, ts=total_scan_bytes, cf=file_path, cfi=file_index, tf=total_scan_files, spi=(pending_index / pending_total if pending_total else 1.0): self.update_offset_status_progress(
                        total_targets=t,
                        found_count=f,
                        updated_count=u,
                        files_scanned=s,
                        done=False,
                        scan_processed=p,
                        scan_total=ts,
                        current_file=cf,
                        current_file_index=cfi,
                        total_files=tf,
                        stage_name="matching",
                        stage_progress=spi,
                    ),
                )
                status_snapshot = dict(results_by_key)
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

            if files_scanned % 20 == 0 or processed_scan_bytes == total_scan_bytes:
                self.after(
                    0,
                    lambda t=total_targets, f=len(found_map), u=updated_count, s=files_scanned, p=processed_scan_bytes, ts=total_scan_bytes, cf=file_path, cfi=file_index, tf=total_scan_files: self.update_offset_status_progress(
                        total_targets=t,
                        found_count=f,
                        updated_count=u,
                        files_scanned=s,
                        done=False,
                        scan_processed=p,
                        scan_total=ts,
                        current_file=cf,
                        current_file_index=cfi,
                        total_files=tf,
                        stage_name="matching",
                        stage_progress=1.0,
                    ),
                )

            if len(found_map) == len(unique_keys):
                done = True
                break

        for entry_key in sorted(unique_keys):
            if entry_key in results_by_key:
                continue
            first_entry = entries_by_key[entry_key][0]
            not_found_result = OffsetResult(
                name=self.format_entry_display_name(first_entry),
                old_value=first_entry.old_value,
                new_value=first_entry.old_value,
                status="Not Found",
                source_file="",
                changed=False,
            )
            results_by_key[entry_key] = not_found_result
            self.after(0, lambda r=not_found_result, rf=folder: self.append_offset_result(r, rf))

        results: list[OffsetResult] = []
        for entry in entries:
            entry_key = self.get_entry_key(entry)
            base_result = results_by_key.get(entry_key)
            display_name = self.format_entry_display_name(entry)
            if base_result is None:
                results.append(
                    OffsetResult(
                        name=display_name,
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
                    name=display_name,
                    old_value=entry.old_value,
                    new_value=base_result.new_value if base_result.status != "Not Found" else entry.old_value,
                    status="Updated" if changed else base_result.status,
                    source_file=base_result.source_file,
                    changed=changed,
                )
            )

        final_results_by_key = dict(results_by_key)
        final_lines = list(updated_lines)
        final_entries = list(entries)
        self.after(
            0,
            lambda: self.finish_offset_update(
                folder,
                results,
                final_lines,
                final_entries,
                final_results_by_key,
                files_scanned,
                len(found_map),
                updated_count,
                file_mode_enabled=file_mode_enabled,
                target_files=target_files,
                output_folder=output_folder,
                name_case_sensitive=name_case_sensitive,
            ),
        )

    def _offset_api_worker(
        self,
        game_name: str,
        entries: list[OffsetEntry],
        lines: list[str],
        name_case_sensitive: bool,
        file_mode_enabled: bool,
        target_files: list[str],
        output_folder: str,
    ) -> None:
        try:
            matched_game_name, matched_hash, api_offsets, latest_update_ms = self.fetch_dumpspace_offsets(game_name)
        except Exception as ex:
            self.after(0, lambda e=str(ex): self.fail_offset_update(e))
            return

        dump_age_text = self.format_dumpspace_last_updated_text(latest_update_ms)
        self.after(0, lambda t=dump_age_text: self.offset_dump_age_var.set(t))

        unique_keys = {self.get_entry_key(entry) for entry in entries}
        entries_by_key: dict[str, list[OffsetEntry]] = {}
        for entry in entries:
            entry_key = self.get_entry_key(entry)
            entries_by_key.setdefault(entry_key, []).append(entry)

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

        results_by_key: dict[str, OffsetResult] = {}
        updated_lines = list(lines)
        found_count = 0
        updated_count = 0
        total_targets = len(unique_keys)
        api_source = f"Dumpspace ({matched_game_name}, hash {matched_hash})"

        for entry_key in unique_keys:
            sample_entry = entries_by_key[entry_key][0]
            if sample_entry.entry_type == "signature":
                continue

            if name_case_sensitive:
                raw_new_value = api_offsets_exact.get(sample_entry.name)
            else:
                raw_new_value = api_offsets_casefold.get(sample_entry.name.lower())
                if raw_new_value is None:
                    raw_new_value = api_offsets_normalized.get(self.normalize_symbol_lookup_name(sample_entry.name))
            if raw_new_value is None:
                continue

            new_value = self.normalize_offset_value(raw_new_value)
            found_count += 1
            entry_group = entries_by_key.get(entry_key, [])
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
                name=self.format_entry_display_name(sample_entry),
                old_value=old_display,
                new_value=new_value,
                status="Updated" if changed else "Found Same",
                source_file=api_source,
                changed=changed,
            )
            results_by_key[entry_key] = result

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
            status_snapshot = dict(results_by_key)
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

        for entry_key in sorted(unique_keys):
            if entry_key in results_by_key:
                continue
            first_entry = entries_by_key[entry_key][0]
            not_found_result = OffsetResult(
                name=self.format_entry_display_name(first_entry),
                old_value=first_entry.old_value,
                new_value=first_entry.old_value,
                status="Not Found",
                source_file=api_source,
                changed=False,
            )
            results_by_key[entry_key] = not_found_result
            self.after(0, lambda r=not_found_result: self.append_offset_result(r, ""))

        results: list[OffsetResult] = []
        for entry in entries:
            entry_key = self.get_entry_key(entry)
            base_result = results_by_key.get(entry_key)
            display_name = self.format_entry_display_name(entry)
            if base_result is None:
                results.append(
                    OffsetResult(
                        name=display_name,
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
                    name=display_name,
                    old_value=entry.old_value,
                    new_value=base_result.new_value if base_result.status != "Not Found" else entry.old_value,
                    status="Updated" if changed else base_result.status,
                    source_file=base_result.source_file,
                    changed=changed,
                )
            )

        final_results_by_key = dict(results_by_key)
        final_lines = list(updated_lines)
        final_entries = list(entries)
        self.after(
            0,
            lambda md=matched_game_name: self.finish_offset_update(
                "",
                results,
                final_lines,
                final_entries,
                final_results_by_key,
                files_scanned=0,
                found_count=found_count,
                updated_count=updated_count,
                mode_detail=f"API mode ({md})",
                file_mode_enabled=file_mode_enabled,
                target_files=target_files,
                output_folder=output_folder,
                name_case_sensitive=name_case_sensitive,
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

    @staticmethod
    def normalize_signature_value(value: str) -> str:
        compact = " ".join(value.strip().split())
        return compact.upper()

    @staticmethod
    def get_entry_key(entry: OffsetEntry) -> str:
        return f"{entry.entry_type}|{entry.name}"

    @staticmethod
    def format_entry_display_name(entry: OffsetEntry) -> str:
        if entry.entry_type == "signature":
            return f"signature:{entry.name}"
        return entry.name

    @staticmethod
    def format_pattern_display_name(entry: OffsetEntry) -> str:
        if entry.name.startswith("signature_line_"):
            return entry.name
        return entry.name

    @staticmethod
    def parse_function_name_from_line(line: str) -> str | None:
        if "{" not in line:
            return None
        if ";" in line:
            return None
        match = re.search(r"\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*[^;{}]*\{", line)
        if not match:
            return None
        name = match.group(1)
        if name in {"if", "for", "while", "switch", "catch"}:
            return None
        return name

    @staticmethod
    def looks_like_signature(value: str) -> bool:
        return bool(re.fullmatch(r"(?:[0-9A-F?]{1,2}\s+){2,}[0-9A-F?]{1,2}", value))

    def parse_signature_value_from_text(self, text: str, case_sensitive: bool = False) -> str | None:
        scan_present = "scan" in text if case_sensitive else "scan" in text.lower()
        if not scan_present:
            return None
        for quoted in re.finditer(r'(?P<q>["\'])(?P<val>[^"\']+)(?P=q)', text):
            normalized = self.normalize_signature_value(quoted.group("val"))
            if self.looks_like_signature(normalized):
                return normalized
        return None

    def parse_signature_value_from_line(self, line: str) -> str | None:
        return self.parse_signature_value_from_text(line, case_sensitive=False)

    def find_signature_for_function(
        self,
        text: str,
        function_name: str,
        old_signature: str,
        case_sensitive: bool,
    ) -> str | None:
        flags = 0 if case_sensitive else re.IGNORECASE
        function_pattern = re.compile(
            rf"\b{re.escape(function_name)}\s*\([^;{{}}]*\)[^;{{}}]*\{{",
            flags,
        )

        for match in function_pattern.finditer(text):
            brace_pos = match.end() - 1
            body = self.extract_braced_block(text, brace_pos, max_len=120000)
            if body is None:
                continue
            signature = self.parse_signature_value_from_text(body, case_sensitive=case_sensitive)
            if signature:
                return signature

        if function_name.startswith("signature_line_"):
            signature = self.parse_signature_value_from_text(text, case_sensitive=case_sensitive)
            if signature:
                return signature

        return self.find_best_matching_signature(text, old_signature)

    def find_best_matching_signature(self, text: str, old_signature: str) -> str | None:
        best_signature: str | None = None
        best_score = -1
        old_tokens = self.tokenize_signature(old_signature)
        if len(old_tokens) < 4:
            return None

        for candidate in self.iter_signature_literals(text):
            score = self.score_signature_match(old_tokens, self.tokenize_signature(candidate))
            if score > best_score:
                best_signature = candidate
                best_score = score

        min_run = max(6, min(len(old_tokens), 12) // 2)
        if best_signature is None or best_score < min_run * 10:
            return None
        return best_signature

    def iter_signature_literals(self, text: str) -> list[str]:
        signatures: list[str] = []
        for quoted in re.finditer(r'(?P<q>["\'])(?P<val>[^"\']+)(?P=q)', text):
            normalized = self.normalize_signature_value(quoted.group("val"))
            if self.looks_like_signature(normalized):
                signatures.append(normalized)
        return signatures

    @staticmethod
    def tokenize_signature(signature: str) -> list[str]:
        return [token for token in signature.split() if token]

    @staticmethod
    def score_signature_match(old_tokens: list[str], candidate_tokens: list[str]) -> int:
        limit = min(len(old_tokens), len(candidate_tokens))
        exact_matches = 0
        wildcard_matches = 0
        longest_run = 0
        current_run = 0

        for idx in range(limit):
            old_token = old_tokens[idx]
            candidate_token = candidate_tokens[idx]
            compatible = (
                old_token == candidate_token
                or old_token == "?"
                or candidate_token == "?"
            )
            if compatible:
                current_run += 1
                longest_run = max(longest_run, current_run)
                if old_token == candidate_token:
                    exact_matches += 1
                else:
                    wildcard_matches += 1
            else:
                current_run = 0

        return (longest_run * 10) + (exact_matches * 3) + wildcard_matches - abs(len(old_tokens) - len(candidate_tokens))

    @staticmethod
    def extract_braced_block(text: str, open_brace_index: int, max_len: int = 120000) -> str | None:
        brace_end = FileSearchApp.find_matching_brace_end(text, open_brace_index, max_len=max_len)
        if brace_end is None:
            return None
        return text[open_brace_index : brace_end + 1]

    @staticmethod
    def find_matching_brace_end(text: str, open_brace_index: int, max_len: int = 120000) -> int | None:
        if open_brace_index < 0 or open_brace_index >= len(text) or text[open_brace_index] != "{":
            return None
        depth = 0
        end_limit = min(len(text), open_brace_index + max_len)
        for i in range(open_brace_index, end_limit):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        return None

    def parse_offset_entries(
        self,
        raw_text: str,
        default_source_file: str | None = None,
        allow_file_markers: bool = True,
    ) -> tuple[list[OffsetEntry], list[str]]:
        lines = raw_text.splitlines()
        entries: list[OffsetEntry] = []
        current_function_name: str | None = None
        current_source_file = ""

        for idx, line in enumerate(lines):
            if allow_file_markers and not default_source_file:
                file_marker = re.match(r"^\s*#{3}\s*FILE:\s*(?P<path>.+?)\s*#{3}\s*$", line)
                if file_marker:
                    current_source_file = os.path.abspath(file_marker.group("path").strip())
                    current_function_name = None
                    continue

            if default_source_file:
                current_source_file = os.path.abspath(default_source_file)

            offset_match = re.search(
                r'(?P<name>"[A-Za-z_][\w:.]*"|\'[A-Za-z_][\w:.]*\'|[A-Za-z_][\w:.]*)\s*[:=]\s*(?P<q>["\']?)(?P<value>0[xX][0-9A-Fa-f]+|\d+)(?P=q)',
                line,
            )
            if offset_match:
                entries.append(
                    OffsetEntry(
                        name=offset_match.group("name").strip("\"'"),
                        old_value=self.normalize_offset_value(offset_match.group("value")),
                        line_index=idx,
                        entry_type="offset",
                        source_file=current_source_file,
                    )
                )

        return entries, lines

    def parse_pattern_entries(
        self,
        raw_text: str,
        default_source_file: str | None = None,
        allow_file_markers: bool = True,
    ) -> tuple[list[OffsetEntry], list[str]]:
        lines = raw_text.splitlines()
        entries: list[OffsetEntry] = []
        current_function_name: str | None = None
        current_source_file = ""

        for idx, line in enumerate(lines):
            if allow_file_markers and not default_source_file:
                file_marker = re.match(r"^\s*#{3}\s*FILE:\s*(?P<path>.+?)\s*#{3}\s*$", line)
                if file_marker:
                    current_source_file = os.path.abspath(file_marker.group("path").strip())
                    current_function_name = None
                    continue

            if default_source_file:
                current_source_file = os.path.abspath(default_source_file)

            function_match = self.parse_function_name_from_line(line)
            if function_match:
                current_function_name = function_match

            signature_match = self.parse_signature_value_from_line(line)
            if signature_match:
                signature_name = current_function_name or f"signature_line_{idx + 1}"
                entries.append(
                    OffsetEntry(
                        name=signature_name,
                        old_value=self.normalize_signature_value(signature_match),
                        line_index=idx,
                        entry_type="signature",
                        source_file=current_source_file,
                    )
                )

        return entries, lines

    def collect_file_mode_entries(self, target_files: list[str]) -> tuple[list[OffsetEntry], list[str]]:
        entries: list[OffsetEntry] = []
        lines: list[str] = []
        for file_path in target_files:
            abs_file_path = os.path.abspath(file_path)
            file_name = os.path.basename(abs_file_path)
            try:
                with open(abs_file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError as ex:
                lines.append(f"{file_name}: could not read ({ex})")
                continue

            file_entries, _ = self.parse_offset_entries(
                content,
                default_source_file=abs_file_path,
                allow_file_markers=False,
            )
            entries.extend(file_entries)
            if file_entries:
                limit = min(200, len(file_entries))
                for entry in file_entries[:limit]:
                    lines.append(f"{file_name}: {entry.name} = {entry.old_value}")
                if len(file_entries) > limit:
                    lines.append(f"{file_name}: ... {len(file_entries) - limit} more entries omitted")
            else:
                lines.append(f"{file_name}: no offsets detected")

        if not entries:
            lines.append("No offsets found in the selected files.")
        return entries, lines

    def finish_offset_update(
        self,
        folder: str,
        results: list[OffsetResult],
        updated_lines: list[str],
        entries: list[OffsetEntry],
        results_by_key: dict[str, OffsetResult],
        files_scanned: int,
        found_count: int,
        updated_count: int,
        mode_detail: str | None = None,
        file_mode_enabled: bool = False,
        target_files: list[str] | None = None,
        output_folder: str = "",
        name_case_sensitive: bool = False,
    ) -> None:
        total_targets = len({result.name for result in results})
        self.update_offset_status_progress(
            total_targets=total_targets,
            found_count=found_count,
            updated_count=updated_count,
            files_scanned=files_scanned,
            done=True,
            mode_detail=mode_detail,
        )
        if file_mode_enabled:
            export_summary, file_stats = self.export_updated_file_copies(
                target_files=target_files or [],
                output_folder=output_folder,
                entries=entries,
                results_by_key=results_by_key,
                name_case_sensitive=name_case_sensitive,
            )
            self.render_file_mode_output(file_stats)
            self.offset_results_text.configure(state=tk.NORMAL)
            self.offset_results_text.insert(tk.END, f"\n{export_summary}\n")
            self.offset_results_text.see(tk.END)
            self.offset_results_text.configure(state=tk.DISABLED)
            self.offset_status_var.set(f"{self.offset_status_var.get()} | {export_summary}")
        else:
            self.render_offset_output(updated_lines, entries, results_by_key, mark_not_found=True)
        self.set_offset_controls_enabled(True)

    def export_updated_file_copies(
        self,
        target_files: list[str],
        output_folder: str,
        entries: list[OffsetEntry],
        results_by_key: dict[str, OffsetResult],
        name_case_sensitive: bool,
    ) -> tuple[str, list[dict[str, object]]]:
        if not target_files:
            return "File mode: no files selected.", []
        if not output_folder:
            output_folder = os.path.join(os.path.expanduser("~"), "Desktop", "OffsetUpdaterOutput")

        try:
            os.makedirs(output_folder, exist_ok=True)
        except OSError as ex:
            return f"File mode failed: could not create output folder '{output_folder}' ({ex}).", []

        common_root = self.get_common_parent_directory(target_files)
        written = 0
        changed = 0
        unchanged = 0
        errors = 0
        file_stats: list[dict[str, object]] = []

        for file_path in target_files:
            abs_file_path = os.path.abspath(file_path)
            stats = {
                "file_path": abs_file_path,
                "display_path": self.make_relative_path(abs_file_path, common_root),
                "updated": 0,
                "same": 0,
                "not_found": 0,
                "error": "",
            }
            try:
                with open(abs_file_path, "r", encoding="utf-8", errors="replace") as f:
                    original_text = f.read()
            except OSError as ex:
                stats["error"] = str(ex)
                errors += 1
                file_stats.append(stats)
                continue

            updated_text = original_text
            file_changed = False

            per_file_entries: list[OffsetEntry] = []
            file_norm = os.path.normcase(abs_file_path)
            for entry in entries:
                if not entry.source_file:
                    continue
                entry_norm = os.path.normcase(os.path.abspath(entry.source_file))
                if entry_norm != file_norm:
                    continue
                per_file_entries.append(entry)

            for entry in per_file_entries:
                key = self.get_entry_key(entry)
                base_result = results_by_key.get(key)
                if base_result is None or base_result.status == "Not Found":
                    stats["not_found"] = int(stats["not_found"]) + 1
                    continue
                new_value = base_result.new_value
                if entry.entry_type == "offset":
                    updated_text, state = self.apply_offset_update_to_text(
                        updated_text,
                        entry.name,
                        new_value,
                        name_case_sensitive,
                    )
                else:
                    updated_text, state = self.apply_signature_update_to_text(
                        updated_text,
                        entry.name,
                        new_value,
                        name_case_sensitive,
                    )
                stats[state] = int(stats[state]) + 1
                if state == "updated":
                    file_changed = True

            rel_path = self.make_relative_path(abs_file_path, common_root)
            out_path = os.path.join(output_folder, rel_path)
            out_dir = os.path.dirname(out_path)
            try:
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                with open(out_path, "w", encoding="utf-8", newline="") as f:
                    f.write(updated_text)
            except OSError as ex:
                stats["error"] = str(ex)
                errors += 1
                file_stats.append(stats)
                continue

            written += 1
            if file_changed:
                changed += 1
            else:
                unchanged += 1
            file_stats.append(stats)

        return (
            f"File mode output: wrote {written} file(s) to {output_folder} | "
            f"changed: {changed}, unchanged: {unchanged}, errors: {errors}",
            file_stats,
        )

    def render_file_mode_output(self, file_stats: list[dict[str, object]]) -> None:
        self.offset_output_text.configure(state=tk.NORMAL)
        self.offset_output_text.delete("1.0", tk.END)
        if not file_stats:
            self.offset_output_text.insert(tk.END, "No file mode results.\n")
            self.offset_output_text.configure(state=tk.DISABLED)
            return

        for stats in file_stats:
            display = str(stats.get("display_path", stats.get("file_path", "")))
            self.offset_output_text.insert(tk.END, f"{display} | ")
            counts = [
                ("updated", "changed"),
                ("same", "same"),
                ("not_found", "not_found"),
            ]

            for idx, (key, tag) in enumerate(counts):
                if idx:
                    self.offset_output_text.insert(tk.END, "/")
                self.offset_output_text.insert(
                    tk.END,
                    str(stats.get(key, 0)),
                    tag,
                )
            error_text = str(stats.get("error", "")).strip()
            if error_text:
                self.offset_output_text.insert(tk.END, f" | Error: {error_text}", "not_found")
            self.offset_output_text.insert(tk.END, "\n")
        self.offset_output_text.configure(state=tk.DISABLED)

    @staticmethod
    def get_common_parent_directory(paths: list[str]) -> str:
        if not paths:
            return os.path.expanduser("~")
        normalized = [os.path.abspath(path) for path in paths]
        try:
            common_path = os.path.commonpath(normalized)
        except ValueError:
            return os.path.dirname(normalized[0])
        if os.path.isfile(common_path):
            return os.path.dirname(common_path)
        return common_path

    def apply_offset_update_to_text(
        self,
        text: str,
        name: str,
        new_value: str,
        case_sensitive: bool,
    ) -> tuple[str, str]:
        pattern = self.build_offset_replace_pattern(name, case_sensitive)
        saw_match = False
        saw_change = False

        def replace_match(match: re.Match[str]) -> str:
            nonlocal saw_match, saw_change
            saw_match = True
            old_value = self.normalize_offset_value(match.group("value"))
            if old_value == new_value:
                return match.group(0)
            saw_change = True
            quote = match.group("quote")
            return f"{match.group('prefix')}{quote}{new_value}{quote}"

        updated = pattern.sub(replace_match, text)
        if saw_change:
            return updated, "updated"
        if saw_match:
            return updated, "same"
        return updated, "not_found"

    def apply_signature_update_to_text(
        self,
        text: str,
        function_name: str,
        new_signature: str,
        case_sensitive: bool,
    ) -> tuple[str, str]:
        if not function_name:
            return text, "not_found"
        if function_name.startswith("signature_line_"):
            return self.replace_first_signature_literal(text, new_signature, case_sensitive)

        flags = 0 if case_sensitive else re.IGNORECASE
        function_pattern = re.compile(
            rf"\b{re.escape(function_name)}\s*\([^;{{}}]*\)[^;{{}}]*\{{",
            flags,
        )
        cursor = 0
        updated_text = text
        status = "not_found"

        while True:
            match = function_pattern.search(updated_text, cursor)
            if match is None:
                break

            brace_start = match.end() - 1
            brace_end = self.find_matching_brace_end(updated_text, brace_start, max_len=120000)
            if brace_end is None:
                cursor = match.end()
                continue

            body = updated_text[brace_start : brace_end + 1]
            new_body, body_status = self.replace_first_signature_literal(body, new_signature, case_sensitive)
            if body_status == "updated":
                updated_text = updated_text[:brace_start] + new_body + updated_text[brace_end + 1 :]
                status = "updated"
                break
            else:
                if body_status == "same" and status != "updated":
                    status = "same"
                cursor = brace_end + 1

        return updated_text, status

    def replace_first_signature_literal(
        self,
        text: str,
        new_signature: str,
        case_sensitive: bool,
    ) -> tuple[str, str]:
        scan_present = "scan" in text if case_sensitive else "scan" in text.lower()
        if not scan_present:
            return text, "not_found"

        for quoted in re.finditer(r'(?P<q>["\'])(?P<val>[^"\']+)(?P=q)', text):
            old_signature = self.normalize_signature_value(quoted.group("val"))
            if not self.looks_like_signature(old_signature):
                continue
            if old_signature == new_signature:
                return text, "same"
            start = quoted.start("val")
            end = quoted.end("val")
            return text[:start] + new_signature + text[end:], "updated"
        return text, "not_found"

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
        scan_processed: int | None = None,
        scan_total: int | None = None,
        current_file: str | None = None,
        current_file_index: int | None = None,
        total_files: int | None = None,
        stage_name: str | None = None,
        stage_progress: float | None = None,
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
        self.set_offset_progress(found_count, total_targets)

    def set_offset_progress(self, found_count: int, total_targets: int) -> None:
        if total_targets <= 0:
            self.offset_progress["maximum"] = 1
            self.offset_progress["value"] = 0
            return
        self.offset_progress["maximum"] = total_targets
        self.offset_progress["value"] = min(found_count, total_targets)

    @staticmethod
    def list_offset_scan_files(folder: str) -> list[tuple[str, int]]:
        scan_files: list[tuple[str, int]] = []
        for root, _, files in os.walk(folder):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                try:
                    file_size = os.path.getsize(file_path)
                except OSError:
                    file_size = 0
                scan_files.append((file_path, file_size))
        return scan_files

    def read_text_file_with_progress(
        self,
        file_path: str,
        base_processed: int,
        total_scan_bytes: int,
        total_targets: int,
        found_count: int,
        updated_count: int,
        files_scanned: int,
        current_file_index: int,
        total_files: int,
    ) -> str:
        chunks: list[bytes] = []
        bytes_read = 0
        chunk_size = 1024 * 1024
        next_report = 4 * 1024 * 1024
        try:
            file_size = max(os.path.getsize(file_path), 1)
        except OSError:
            file_size = 1

        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                chunks.append(chunk)
                bytes_read += len(chunk)
                if bytes_read >= next_report:
                    self.after(
                        0,
                        lambda t=total_targets, fnd=found_count, upd=updated_count, s=files_scanned, p=base_processed + bytes_read, ts=total_scan_bytes, cf=file_path, cfi=current_file_index, tf=total_files, sp=min(bytes_read / file_size, 1.0): self.update_offset_status_progress(
                            total_targets=t,
                            found_count=fnd,
                            updated_count=upd,
                            files_scanned=s,
                            done=False,
                            scan_processed=p,
                            scan_total=ts,
                            current_file=cf,
                            current_file_index=cfi,
                            total_files=tf,
                            stage_name="reading",
                            stage_progress=sp,
                        ),
                    )
                    next_report += 4 * 1024 * 1024

        return b"".join(chunks).decode("utf-8", errors="replace")

    def render_offset_output(
        self,
        lines: list[str],
        entries: list[OffsetEntry],
        results_by_key: dict[str, OffsetResult],
        mark_not_found: bool,
    ) -> None:
        line_tags: dict[int, str] = {}

        for entry in entries:
            result = results_by_key.get(self.get_entry_key(entry))
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
    def build_offset_replace_pattern(name: str, case_sensitive: bool) -> re.Pattern[str]:
        escaped = re.escape(name)
        key_pattern = rf"(?:\"{escaped}\"|'{escaped}'|\b{escaped}\b)"
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.compile(
            rf"(?P<prefix>{key_pattern}\s*[:=]\s*)(?P<quote>[\"']?)(?P<value>0[xX][0-9A-Fa-f]+|\d+)(?P=quote)",
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
    def replace_signature_value(line: str, new_signature: str) -> str:
        for quoted in re.finditer(r'(?P<q>["\'])(?P<val>[^"\']+)(?P=q)', line):
            normalized = FileSearchApp.normalize_signature_value(quoted.group("val"))
            if FileSearchApp.looks_like_signature(normalized):
                start = quoted.start("val")
                end = quoted.end("val")
                return line[:start] + new_signature + line[end:]
        return line

    @staticmethod
    def make_relative_path(file_path: str, root_folder: str) -> str:
        if not file_path:
            return ""
        try:
            return os.path.relpath(file_path, root_folder)
        except ValueError:
            return file_path

    @staticmethod
    def get_app_directory() -> str:
        if getattr(sys, "frozen", False):
            return os.path.dirname(os.path.abspath(sys.executable))
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def get_bundled_resource_path(name: str) -> str:
        if getattr(sys, "frozen", False):
            base_dir = getattr(sys, "_MEIPASS", "")
            if base_dir:
                return os.path.join(base_dir, name)
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)

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
