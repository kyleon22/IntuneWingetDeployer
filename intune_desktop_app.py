import json
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

WORKSPACE = Path(__file__).resolve().parent
POWERSHELL = "powershell.exe"
APP_DATA_DIR = WORKSPACE / ".intune_desktop_app"
PROFILES_PATH = APP_DATA_DIR / "profiles.json"
SETTINGS_PATH = APP_DATA_DIR / "settings.json"
BOOTSTRAP_STATE_PATH = APP_DATA_DIR / "bootstrap-state.json"
DEFAULT_APP_REG_NAME = "Intune Winget Deployer"


class ProcessRunner:
    def __init__(self, on_output, on_complete):
        self.on_output = on_output
        self.on_complete = on_complete
        self.process = None
        self.thread = None

    def run(self, args, cwd, extra_env=None):
        if self.process and self.process.poll() is None:
            raise RuntimeError("A process is already running.")

        import os
        child_env = os.environ.copy()
        child_env["DOTNET_EnableDiagnostics"] = "0"
        child_env["COMPlus_EnableDiagnostics"] = "0"
        if extra_env:
            child_env.update(extra_env)

        def target():
            rc = -1
            try:
                self.process = subprocess.Popen(
                    args,
                    cwd=str(cwd),
                    env=child_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                )
                assert self.process.stdout is not None
                for line in self.process.stdout:
                    self.on_output(line)
                rc = self.process.wait()
            except Exception as exc:
                self.on_output(f"[app-error] {exc}\n")
            finally:
                self.on_complete(rc)
                self.process = None

        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()

    def terminate(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()


class IntuneDesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Intune Winget Deployer")
        self.geometry("1220x860")
        self.minsize(1040, 760)

        APP_DATA_DIR.mkdir(exist_ok=True)

        self.output_queue = queue.Queue()
        self.runner = ProcessRunner(self.enqueue_output, self.enqueue_complete)
        self.current_metadata_path = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.progress_var = tk.DoubleVar(value=0)
        self.stage_var = tk.StringVar(value="Idle")
        self.profile_name_var = tk.StringVar(value="default")
        self.package_search_results = []
        self.history_entries = []
        self.settings = self._load_json_file(SETTINGS_PATH)
        self.bootstrap_state = self._load_json_file(BOOTSTRAP_STATE_PATH)
        self.preflight_status_var = tk.StringVar(value="Preflight not run yet")
        self.bootstrap_tenant_var = tk.StringVar(value=self.settings.get("tenantId", "dccf3875-5973-4c17-9178-891b3b5cafe5"))
        self.bootstrap_display_name_var = tk.StringVar(value=self.settings.get("appRegistrationDisplayName", DEFAULT_APP_REG_NAME))
        self.bootstrap_grant_consent_var = tk.BooleanVar(value=False)
        self.detected_client_id_var = tk.StringVar(value=self.settings.get("clientId", ""))
        self.detected_prep_tool_var = tk.StringVar(value=self.settings.get("prepToolPath", str(WORKSPACE / "IntuneWinAppUtil.exe")))

        self._setup_style()
        self._build_ui()
        self.after(100, self._process_queue)
        self._apply_saved_settings()
        self._autodetect_metadata()
        self.after(150, self.refresh_history)
        self.after(250, self.run_preflight_check)
        self.after(500, self._maybe_show_first_run_prompt)

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        self.configure(bg="#f4f7fb")
        style.configure("TFrame", background="#f4f7fb")
        style.configure("TLabel", background="#f4f7fb", font=("Segoe UI", 10))
        style.configure("Header.TLabel", background="#f4f7fb", font=("Segoe UI", 18, "bold"))
        style.configure("TButton", font=("Segoe UI", 10), padding=6)
        style.configure("TNotebook", background="#f4f7fb")
        style.configure("TNotebook.Tab", font=("Segoe UI", 10, "bold"), padding=[14, 8])
        style.configure("TLabelframe", background="#f4f7fb")
        style.configure("TLabelframe.Label", background="#f4f7fb", font=("Segoe UI", 10, "bold"))
        style.configure("Green.Horizontal.TProgressbar", troughcolor="#dfe7e2", background="#2e9f57", bordercolor="#dfe7e2", lightcolor="#49b86d", darkcolor="#238248")

    def _build_ui(self):
        top = ttk.Frame(self, padding=12)
        top.pack(fill="both", expand=True)

        header = ttk.Frame(top)
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="Intune Winget Deployer", style="Header.TLabel").pack(side="left")
        ttk.Label(header, textvariable=self.status_var, foreground="#1d4ed8").pack(side="right")

        progress_frame = ttk.Frame(top)
        progress_frame.pack(fill="x", pady=(0, 8))
        ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100, mode="determinate").pack(side="left", fill="x", expand=True)
        ttk.Label(progress_frame, textvariable=self.stage_var, width=28).pack(side="left", padx=(8, 0))

        notebook = ttk.Notebook(top)
        notebook.pack(fill="both", expand=True)

        self.package_tab = ttk.Frame(notebook, padding=12)
        self.publish_tab = ttk.Frame(notebook, padding=12)
        self.run_tab = ttk.Frame(notebook, padding=12)
        self.history_tab = ttk.Frame(notebook, padding=12)
        self.bulk_tab = ttk.Frame(notebook, padding=12)
        self.override_tab = ttk.Frame(notebook, padding=12)
        self.logs_tab = ttk.Frame(notebook, padding=12)

        notebook.add(self.package_tab, text="Package")
        notebook.add(self.publish_tab, text="Publish")
        notebook.add(self.run_tab, text="Package + Publish")
        notebook.add(self.history_tab, text="Profiles + History")
        notebook.add(self.bulk_tab, text="Bulk Deploy")
        notebook.add(self.override_tab, text="Override Editor")
        notebook.add(self.logs_tab, text="Logs")

        self._build_package_tab()
        self._build_publish_tab()
        self._build_run_tab()
        self._build_history_tab()
        self._build_bulk_tab()
        self._build_override_tab()
        self._build_logs_tab(top)

    def _labeled_entry(self, parent, label, variable, row, width=70, browse=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
        entry = ttk.Entry(parent, textvariable=variable, width=width)
        entry.grid(row=row, column=1, sticky="ew", pady=6)
        if browse:
            ttk.Button(parent, text="Browse", command=browse).grid(row=row, column=2, padx=(8, 0), pady=6)
        return entry

    def _build_setup_tab(self):
        frm = self.setup_tab
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Environment bootstrap", style="Header.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        ttk.Label(frm, text="Tenant ID").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(frm, textvariable=self.bootstrap_tenant_var, width=60).grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Label(frm, text="App registration name").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(frm, textvariable=self.bootstrap_display_name_var, width=60).grid(row=2, column=1, sticky="ew", pady=6)
        ttk.Checkbutton(frm, text="Grant admin consent during bootstrap", variable=self.bootstrap_grant_consent_var).grid(row=3, column=1, sticky="w", pady=6)
        ttk.Label(frm, text="Detected Client ID").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Entry(frm, textvariable=self.detected_client_id_var, width=60).grid(row=4, column=1, sticky="ew", pady=6)
        ttk.Label(frm, text="Detected IntuneWinAppUtil.exe").grid(row=5, column=0, sticky="w", pady=6)
        ttk.Entry(frm, textvariable=self.detected_prep_tool_var, width=60).grid(row=5, column=1, sticky="ew", pady=6)

        actions = ttk.Frame(frm)
        actions.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 10))
        ttk.Button(actions, text="Run Preflight Check", command=self.run_preflight_check).pack(side="left")
        ttk.Button(actions, text="Install Missing Dependencies", command=self.install_missing_dependencies).pack(side="left", padx=8)
        ttk.Button(actions, text="Bootstrap / Reuse App Registration", command=self.bootstrap_app_registration).pack(side="left", padx=8)
        ttk.Button(actions, text="Apply Saved Settings", command=self._apply_saved_settings).pack(side="left", padx=8)

        ttk.Label(frm, textvariable=self.preflight_status_var, foreground="#1d4ed8", wraplength=900, justify="left").grid(row=7, column=0, columnspan=3, sticky="w", pady=(0, 8))

    def _build_package_tab(self):
        frm = self.package_tab
        frm.columnconfigure(1, weight=1)

        self.pkg_app_name = tk.StringVar(value="foxit pdf")
        self.pkg_output_root = tk.StringVar(value=str(WORKSPACE / "output"))
        self.pkg_arch = tk.StringVar(value="x64")
        self.pkg_deployment = tk.StringVar(value="new")
        self.pkg_prep_tool = tk.StringVar(value=r"C:\Intune Prep Tool\IntuneWinAppUtil.exe")
        self.pkg_local_installer = tk.StringVar()
        self.pkg_override = tk.StringVar()
        self.pkg_selected_id = tk.StringVar()

        self._labeled_entry(frm, "App name", self.pkg_app_name, 0)
        self._labeled_entry(frm, "Output root", self.pkg_output_root, 1, browse=lambda: self._pick_directory(self.pkg_output_root))

        ttk.Label(frm, text="Architecture").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Combobox(frm, textvariable=self.pkg_arch, values=["x64", "x86", "arm64", "neutral", "any"], state="readonly").grid(row=2, column=1, sticky="w", pady=6)
        ttk.Label(frm, text="Deployment type").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Combobox(frm, textvariable=self.pkg_deployment, values=["new", "update"], state="readonly").grid(row=3, column=1, sticky="w", pady=6)

        self._labeled_entry(frm, "Intune prep tool", self.pkg_prep_tool, 4, browse=lambda: self._pick_file(self.pkg_prep_tool, [("Executable", "*.exe"), ("All files", "*.*")]))
        self._labeled_entry(frm, "Local installer", self.pkg_local_installer, 5, browse=lambda: self._pick_file(self.pkg_local_installer, [("Installer", "*.exe *.msi"), ("All files", "*.*")]))
        self._labeled_entry(frm, "Override config", self.pkg_override, 6, browse=lambda: self._pick_file(self.pkg_override, [("JSON", "*.json"), ("All files", "*.*")]))

        picker = ttk.LabelFrame(frm, text="Winget package selection", padding=8)
        picker.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        picker.columnconfigure(1, weight=1)
        ttk.Button(picker, text="Search Packages", command=self.search_packages).grid(row=0, column=0, sticky="w")
        self.pkg_combo = ttk.Combobox(picker, textvariable=self.pkg_selected_id, state="readonly", width=90)
        self.pkg_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.pkg_combo.bind("<<ComboboxSelected>>", self._on_package_combo_selected)

        actions = ttk.Frame(frm)
        actions.grid(row=8, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Button(actions, text="Build Package", command=self.run_package).pack(side="left")
        ttk.Button(actions, text="Autodetect Latest Metadata", command=self._autodetect_metadata).pack(side="left", padx=8)

    def _build_publish_tab(self):
        frm = self.publish_tab
        frm.columnconfigure(1, weight=1)

        self.pub_tenant = tk.StringVar(value="dccf3875-5973-4c17-9178-891b3b5cafe5")
        self.pub_metadata = self.current_metadata_path
        self.pub_assignment = tk.StringVar(value="all")
        self.pub_group = tk.StringVar()
        self.pub_auth_mode = tk.StringVar(value="Delegated")
        self.pub_client_id = tk.StringVar()
        self.pub_module_version = tk.StringVar(value="1.4.3")
        self.pub_device_code = tk.BooleanVar(value=False)
        self.pub_override = tk.StringVar()
        self.pub_use_azcopy = tk.BooleanVar(value=True)
        self.pub_display_name = tk.StringVar()
        self.pub_description = tk.StringVar()
        self.pub_publisher = tk.StringVar()
        self.pub_supersede_id = tk.StringVar()
        self.pub_supersedence_type = tk.StringVar(value="Update")

        self._labeled_entry(frm, "Tenant ID", self.pub_tenant, 0)
        self._labeled_entry(frm, "Metadata path", self.pub_metadata, 1, browse=lambda: self._pick_file(self.pub_metadata, [("JSON", "*.json"), ("All files", "*.*")]))

        ttk.Label(frm, text="Assignment").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Combobox(frm, textvariable=self.pub_assignment, values=["all", "group"], state="readonly").grid(row=2, column=1, sticky="w", pady=6)
        self._labeled_entry(frm, "Target group", self.pub_group, 3)

        ttk.Label(frm, text="Auth mode").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Combobox(frm, textvariable=self.pub_auth_mode, values=["Delegated", "AppRegistration"], state="readonly").grid(row=4, column=1, sticky="w", pady=6)
        self._labeled_entry(frm, "Client ID (optional)", self.pub_client_id, 5)
        self._labeled_entry(frm, "Intune module version", self.pub_module_version, 6)
        self._labeled_entry(frm, "Override config", self.pub_override, 7, browse=lambda: self._pick_file(self.pub_override, [("JSON", "*.json"), ("All files", "*.*")]))
        self._labeled_entry(frm, "Custom display name", self.pub_display_name, 8)
        self._labeled_entry(frm, "Custom publisher", self.pub_publisher, 9)
        self._labeled_entry(frm, "Custom description", self.pub_description, 10)
        self._labeled_entry(frm, "Supersede app ID", self.pub_supersede_id, 11)
        ttk.Label(frm, text="Supersedence type").grid(row=12, column=0, sticky="w", pady=6)
        ttk.Combobox(frm, textvariable=self.pub_supersedence_type, values=["Update", "Replace"], state="readonly").grid(row=12, column=1, sticky="w", pady=6)

        options = ttk.Frame(frm)
        options.grid(row=13, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(options, text="Use device code auth", variable=self.pub_device_code).pack(side="left")
        ttk.Checkbutton(options, text="Use AzCopy", variable=self.pub_use_azcopy).pack(side="left", padx=12)

        actions = ttk.Frame(frm)
        actions.grid(row=14, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Button(actions, text="Publish Existing Package", command=self.run_publish).pack(side="left")

    def _build_run_tab(self):
        frm = self.run_tab
        frm.columnconfigure(1, weight=1)

        self.run_app_name = tk.StringVar(value="foxit pdf")
        self.run_tenant = tk.StringVar(value="dccf3875-5973-4c17-9178-891b3b5cafe5")
        self.run_arch = tk.StringVar(value="x64")
        self.run_deployment = tk.StringVar(value="new")
        self.run_assignment = tk.StringVar(value="all")
        self.run_group = tk.StringVar()
        self.run_prep_tool = tk.StringVar(value=r"C:\Intune Prep Tool\IntuneWinAppUtil.exe")
        self.run_local_installer = tk.StringVar()
        self.run_client_id = tk.StringVar()
        self.run_module_version = tk.StringVar(value="1.4.3")
        self.run_device_code = tk.BooleanVar(value=False)
        self.run_use_azcopy = tk.BooleanVar(value=True)
        self.run_display_name = tk.StringVar()
        self.run_description = tk.StringVar()
        self.run_publisher = tk.StringVar()
        self.run_supersede_id = tk.StringVar()
        self.run_supersedence_type = tk.StringVar(value="Update")
        self.run_selected_id = tk.StringVar()

        self._labeled_entry(frm, "App name", self.run_app_name, 0)
        self._labeled_entry(frm, "Tenant ID", self.run_tenant, 1)
        ttk.Label(frm, text="Architecture").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Combobox(frm, textvariable=self.run_arch, values=["x64", "x86", "arm64", "neutral", "any"], state="readonly").grid(row=2, column=1, sticky="w", pady=6)
        ttk.Label(frm, text="Deployment type").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Combobox(frm, textvariable=self.run_deployment, values=["new", "update"], state="readonly").grid(row=3, column=1, sticky="w", pady=6)
        ttk.Label(frm, text="Assignment").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Combobox(frm, textvariable=self.run_assignment, values=["all", "group"], state="readonly").grid(row=4, column=1, sticky="w", pady=6)
        self._labeled_entry(frm, "Target group", self.run_group, 5)
        self._labeled_entry(frm, "Intune prep tool", self.run_prep_tool, 6, browse=lambda: self._pick_file(self.run_prep_tool, [("Executable", "*.exe"), ("All files", "*.*")]))
        self._labeled_entry(frm, "Local installer", self.run_local_installer, 7, browse=lambda: self._pick_file(self.run_local_installer, [("Installer", "*.exe *.msi"), ("All files", "*.*")]))
        self._labeled_entry(frm, "Client ID (optional)", self.run_client_id, 8)
        self._labeled_entry(frm, "Intune module version", self.run_module_version, 9)

        run_picker = ttk.LabelFrame(frm, text="Winget package selection", padding=8)
        run_picker.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        run_picker.columnconfigure(1, weight=1)
        ttk.Button(run_picker, text="Search Packages", command=self.search_packages_run).grid(row=0, column=0, sticky="w")
        self.run_combo = ttk.Combobox(run_picker, textvariable=self.run_selected_id, state="readonly", width=90)
        self.run_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.run_combo.bind("<<ComboboxSelected>>", self._on_run_combo_selected)

        self._labeled_entry(frm, "Custom display name", self.run_display_name, 11)
        self._labeled_entry(frm, "Custom publisher", self.run_publisher, 12)
        self._labeled_entry(frm, "Custom description", self.run_description, 13)
        self._labeled_entry(frm, "Supersede app ID", self.run_supersede_id, 14)
        ttk.Label(frm, text="Supersedence type").grid(row=15, column=0, sticky="w", pady=6)
        ttk.Combobox(frm, textvariable=self.run_supersedence_type, values=["Update", "Replace"], state="readonly").grid(row=15, column=1, sticky="w", pady=6)

        options = ttk.Frame(frm)
        options.grid(row=16, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(options, text="Use device code auth", variable=self.run_device_code).pack(side="left")
        ttk.Checkbutton(options, text="Use AzCopy", variable=self.run_use_azcopy).pack(side="left", padx=12)

        ttk.Button(frm, text="Run Full Flow", command=self.run_full_flow).grid(row=17, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _build_history_tab(self):
        frm = self.history_tab
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(3, weight=1)

        ttk.Label(frm, text="Profile / tenant name").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(frm, textvariable=self.profile_name_var, width=30).grid(row=0, column=1, sticky="w", pady=6)

        profile_actions = ttk.Frame(frm)
        profile_actions.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 10))
        ttk.Button(profile_actions, text="Save Current Profile", command=self.save_profile).pack(side="left")
        ttk.Button(profile_actions, text="Load Profile", command=self.load_profile).pack(side="left", padx=8)
        ttk.Button(profile_actions, text="Refresh History", command=self.refresh_history).pack(side="left", padx=8)

        ttk.Label(frm, text="Deployment history").grid(row=2, column=0, sticky="w", pady=(0, 6))
        self.history_list = tk.Listbox(frm, height=18)
        self.history_list.grid(row=3, column=0, columnspan=2, sticky="nsew")
        self.history_list.bind("<<ListboxSelect>>", self.on_history_select)

        history_scroll = ttk.Scrollbar(frm, orient="vertical", command=self.history_list.yview)
        history_scroll.grid(row=3, column=2, sticky="ns")
        self.history_list.configure(yscrollcommand=history_scroll.set)

        history_actions = ttk.Frame(frm)
        history_actions.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(history_actions, text="Use Selected Metadata in Publish", command=self.use_selected_history_metadata).pack(side="left")
        ttk.Button(history_actions, text="Open Output Folder", command=self.open_selected_history_folder).pack(side="left", padx=8)

    def _build_bulk_tab(self):
        frm = self.bulk_tab
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(2, weight=1)
        self.bulk_config_path = tk.StringVar(value=str(WORKSPACE / "bulk-deploy-sample.json"))
        ttk.Label(frm, text="Bulk config file").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(frm, textvariable=self.bulk_config_path, width=80).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(frm, text="Browse", command=lambda: self._pick_file(self.bulk_config_path, [("JSON", "*.json"), ("All files", "*.*")])).grid(row=0, column=2, padx=(8, 0), pady=6)
        help_text = "Use a JSON array of deployment objects. Start from bulk-deploy-sample.json. Each item can define AppName, PackageId, TenantId, DeploymentType, Assignment, auth settings, overrides, and supersedence."
        ttk.Label(frm, text=help_text, wraplength=850, justify="left").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.bulk_preview = ScrolledText(frm, height=18, font=("Consolas", 10))
        self.bulk_preview.grid(row=2, column=0, columnspan=3, sticky="nsew")
        bulk_actions = ttk.Frame(frm)
        bulk_actions.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(bulk_actions, text="Load Config Preview", command=self.load_bulk_preview).pack(side="left")
        ttk.Button(bulk_actions, text="Run Bulk Deployment", command=self.run_bulk_deploy).pack(side="left", padx=8)

    def _build_override_tab(self):
        frm = self.override_tab
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(1, weight=1)
        self.override_editor_path = tk.StringVar(value=str(WORKSPACE / "intune-winget-overrides.sample.json"))
        ttk.Label(frm, text="Override file").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(frm, textvariable=self.override_editor_path, width=80).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(frm, text="Browse", command=lambda: self._pick_file(self.override_editor_path, [("JSON", "*.json"), ("All files", "*.*")])).grid(row=0, column=2, padx=(8, 0), pady=6)
        self.override_editor = ScrolledText(frm, height=22, font=("Consolas", 10))
        self.override_editor.grid(row=1, column=0, columnspan=3, sticky="nsew")
        override_actions = ttk.Frame(frm)
        override_actions.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(override_actions, text="Load Override File", command=self.load_override_editor).pack(side="left")
        ttk.Button(override_actions, text="Save Override File", command=self.save_override_editor).pack(side="left", padx=8)

    def _build_logs_tab(self, root):
        frm = self.logs_tab
        frm.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)

        self.log_text = tk.Text(frm, wrap="word", font=("Consolas", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frm, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

        controls = ttk.Frame(root, padding=(12, 0, 12, 12))
        controls.pack(fill="x")
        ttk.Button(controls, text="Clear Log", command=lambda: self.log_text.delete("1.0", "end")).pack(side="left")
        ttk.Button(controls, text="Stop Running Task", command=self.stop_current).pack(side="left", padx=8)

    def enqueue_output(self, line):
        self.output_queue.put(("output", line))

    def enqueue_complete(self, code):
        self.output_queue.put(("complete", code))

    def _process_queue(self):
        try:
            while True:
                kind, payload = self.output_queue.get_nowait()
                if kind == "output":
                    self.log_text.insert("end", payload)
                    self.log_text.see("end")
                    self._detect_metadata_from_line(payload)
                elif kind == "complete":
                    if payload == 0:
                        self.status_var.set("Completed successfully")
                        self.progress_var.set(100)
                        self.stage_var.set("Done")
                    else:
                        self.status_var.set(f"Finished with exit code {payload}")
                        if self.progress_var.get() < 100:
                            self.stage_var.set("Failed")
        except queue.Empty:
            pass
        self.after(100, self._process_queue)

    def _pick_file(self, variable, filetypes):
        path = filedialog.askopenfilename(initialdir=str(WORKSPACE), filetypes=filetypes)
        if path:
            variable.set(path)

    def _pick_directory(self, variable):
        path = filedialog.askdirectory(initialdir=str(WORKSPACE))
        if path:
            variable.set(path)

    def _parse_winget_search(self, text):
        results = []
        for raw in text.splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("Name") or set(stripped) <= {"-", " ", "|"}:
                continue
            match = re.match(r"^(?P<name>.+?)\s{2,}(?P<id>[A-Za-z0-9_.+\-]+)\s{2,}(?P<version>\S+)(?:\s{2,}.+)?$", line)
            if match:
                results.append({
                    "name": match.group("name").strip(),
                    "id": match.group("id").strip(),
                    "version": match.group("version").strip(),
                })
        return results

    def _run_winget_search(self, query):
        return subprocess.run(
            [
                "winget",
                "search",
                "--source", "winget",
                "--query", query,
                "--accept-source-agreements",
            ],
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            check=False,
        )

    def _score_search_result(self, query, item):
        q = query.lower().strip()
        name = item.get("name", "").lower()
        package_id = item.get("id", "").lower()
        compact_q = re.sub(r"[^a-z0-9]+", "", q)
        compact_name = re.sub(r"[^a-z0-9]+", "", name)
        compact_id = re.sub(r"[^a-z0-9]+", "", package_id)
        score = 0
        if name == q or package_id == q:
            score += 100
        if compact_q and (compact_name == compact_q or compact_id == compact_q):
            score += 80
        if q and q in name:
            score += 45
        if q and q in package_id:
            score += 40
        if compact_q and compact_q in compact_name:
            score += 35
        if compact_q and compact_q in compact_id:
            score += 30
        for token in [t for t in re.split(r"[^a-z0-9]+", q) if t]:
            if token in name:
                score += 8
            if token in package_id:
                score += 6
        return score

    def _search_packages_common(self, query, target_combo, target_var):
        if not query:
            messagebox.showerror("Missing app name", "Enter an app name to search first.")
            return None
        self.status_var.set("Searching winget packages...")
        self.stage_var.set("Searching")
        self.progress_var.set(8)
        try:
            queries = [query]
            normalized = re.sub(r"[^A-Za-z0-9]+", " ", query).strip()
            if normalized and normalized.lower() != query.lower():
                queries.append(normalized)
            first_token = normalized.split()[0] if normalized else ""
            if first_token and first_token.lower() not in [q.lower() for q in queries]:
                queries.append(first_token)

            combined = []
            seen = set()
            raw_outputs = []
            for search_query in queries:
                proc = self._run_winget_search(search_query)
                output = (proc.stdout or "") + (proc.stderr or "")
                raw_outputs.append(f"[query={search_query}]\n{output}")
                for item in self._parse_winget_search(output):
                    key = item.get("id", "")
                    if key and key not in seen:
                        seen.add(key)
                        combined.append(item)
        except Exception as exc:
            messagebox.showerror("Search failed", str(exc))
            return None

        results = sorted(combined, key=lambda item: (-self._score_search_result(query, item), item.get("name", "")))
        self.package_search_results = results
        if not results:
            self.log_text.insert("end", f"\n[package-search] No parseable winget results for: {query}\n" + "\n\n".join(raw_outputs) + "\n")
            self.log_text.see("end")
            self.status_var.set("No packages found")
            self.stage_var.set("Idle")
            self.progress_var.set(0)
            messagebox.showwarning("No packages found", "No parseable winget results were found. Check the Logs tab.")
            return None

        values = [f"{item['name']} ({item['id']}) - {item['version']}" for item in results]
        target_combo["values"] = values
        target_combo.current(0)
        target_var.set(values[0])
        self.status_var.set(f"Found {len(results)} package(s)")
        self.stage_var.set("Package selected")
        self.progress_var.set(12)
        return results

    def search_packages(self):
        results = self._search_packages_common(self.pkg_app_name.get().strip(), self.pkg_combo, self.pkg_selected_id)
        if results:
            self._apply_metadata_defaults(results[0])
            self.run_app_name.set(self.pkg_app_name.get())

    def search_packages_run(self):
        results = self._search_packages_common(self.run_app_name.get().strip(), self.run_combo, self.run_selected_id)
        if results:
            self._apply_metadata_defaults(results[0])

    def _selected_package_id(self, selected_value):
        selected = selected_value.strip()
        if not selected:
            return ""
        for item in self.package_search_results:
            label = f"{item['name']} ({item['id']}) - {item['version']}"
            if label == selected:
                return item["id"]
        match = re.search(r"\(([^()]+)\)\s*-", selected)
        return match.group(1).strip() if match else ""

    def _selected_package_item(self, selected_value):
        selected = selected_value.strip()
        if not selected:
            return None
        for item in self.package_search_results:
            label = f"{item['name']} ({item['id']}) - {item['version']}"
            if label == selected:
                return item
        return None

    def _default_publisher_from_item(self, item):
        package_id = item.get("id", "")
        root = package_id.split(".")[0] if package_id else ""
        known = {
            "Foxit": "Foxit Software",
            "Google": "Google",
            "Microsoft": "Microsoft",
            "Mozilla": "Mozilla",
            "VideoLAN": "VideoLAN",
            "Notepad++": "Don Ho",
            "7zip": "Igor Pavlov",
        }
        if root in known:
            return known[root]
        return root.replace("_", " ") if root else "Unknown Publisher"

    def _default_description_from_item(self, item):
        name = item.get("name", "Selected application")
        return f"{name} for Windows devices."

    def _apply_metadata_defaults(self, item):
        if not item:
            return
        display_name = item.get("name", "")
        publisher = self._default_publisher_from_item(item)
        description = self._default_description_from_item(item)
        self.pub_display_name.set(display_name)
        self.pub_publisher.set(publisher)
        self.pub_description.set(description)
        self.run_display_name.set(display_name)
        self.run_publisher.set(publisher)
        self.run_description.set(description)

    def _on_package_combo_selected(self, _event=None):
        item = self._selected_package_item(self.pkg_selected_id.get())
        self._apply_metadata_defaults(item)
        self.run_app_name.set(self.pkg_app_name.get())

    def _on_run_combo_selected(self, _event=None):
        item = self._selected_package_item(self.run_selected_id.get())
        self._apply_metadata_defaults(item)

    def _load_json_file(self, path):
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_json_file(self, path, data):
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _read_profiles(self):
        if not PROFILES_PATH.exists():
            return {}
        try:
            return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_profiles(self, data):
        PROFILES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _apply_saved_settings(self):
        prep_tool = self.settings.get("prepToolPath") or self.detected_prep_tool_var.get()
        client_id = self.settings.get("clientId") or self.detected_client_id_var.get()
        tenant_id = self.settings.get("tenantId") or self.bootstrap_tenant_var.get()
        if hasattr(self, "pkg_prep_tool") and prep_tool:
            self.pkg_prep_tool.set(prep_tool)
        if hasattr(self, "run_prep_tool") and prep_tool:
            self.run_prep_tool.set(prep_tool)
        self.detected_prep_tool_var.set(prep_tool)
        if hasattr(self, "pub_client_id") and client_id:
            self.pub_client_id.set(client_id)
        if hasattr(self, "run_client_id") and client_id:
            self.run_client_id.set(client_id)
        self.detected_client_id_var.set(client_id)
        if hasattr(self, "pub_tenant") and tenant_id:
            self.pub_tenant.set(tenant_id)
        if hasattr(self, "run_tenant") and tenant_id:
            self.run_tenant.set(tenant_id)
        self.bootstrap_tenant_var.set(tenant_id)
        self.status_var.set("Applied saved bootstrap settings")

    def _maybe_show_first_run_prompt(self):
        if self.bootstrap_state.get("bootstrapped"):
            return
        answer = messagebox.askyesno(
            "First run setup",
            "No saved setup state was found yet.\n\nWould you like to run the prerequisites check now?\n\n(Delegated auth is used by default; app registration is optional.)"
        )
        if answer:
            self.run_preflight_check()

    def run_preflight_check(self):
        script_path = WORKSPACE / "Test-IntuneDesktopAppPrereqs.ps1"
        tool_path = self.detected_prep_tool_var.get().strip() or str(WORKSPACE / "IntuneWinAppUtil.exe")
        try:
            proc = subprocess.run(
                [POWERSHELL, "-ExecutionPolicy", "Bypass", "-File", str(script_path), "-IntuneWinAppUtilPath", tool_path],
                cwd=str(WORKSPACE), capture_output=True, text=True, check=False
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr or proc.stdout or "Preflight check failed")
            data = json.loads(proc.stdout)
        except Exception as exc:
            self.preflight_status_var.set(f"Preflight failed: {exc}")
            self.log_text.insert("end", f"\n[preflight-error] {exc}\n")
            self.log_text.see("end")
            return

        missing = []
        if not data.get("Python", {}).get("Installed"):
            missing.append("Python")
        if not data.get("Winget", {}).get("Installed"):
            missing.append("winget")
        if not data.get("IntuneWinAppUtil", {}).get("Installed"):
            missing.append("IntuneWinAppUtil.exe")
        for module in data.get("Modules", []):
            if not module.get("Installed"):
                missing.append(module.get("Name"))
        tool_path_found = data.get("IntuneWinAppUtil", {}).get("Path")
        if tool_path_found:
            self.detected_prep_tool_var.set(tool_path_found)
            self.pkg_prep_tool.set(tool_path_found)
            self.run_prep_tool.set(tool_path_found)
        summary = "All checked dependencies look present." if not missing else f"Missing dependencies: {', '.join(missing)}"
        self.preflight_status_var.set(summary)
        self.status_var.set(summary)

    def install_missing_dependencies(self):
        script_path = WORKSPACE / "Install-IntuneDesktopAppPrereqs.ps1"
        source_path = ""
        preferred = [self.detected_prep_tool_var.get().strip(), r"C:\Intune Prep Tool\IntuneWinAppUtil.exe", str(WORKSPACE / "IntuneWinAppUtil.exe")]
        for candidate in preferred:
            if candidate and os.path.exists(candidate):
                source_path = candidate
                break
        params = [POWERSHELL, "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
        if source_path:
            params += ["-IntuneWinAppUtilSourcePath", source_path, "-IntuneWinAppUtilDestinationPath", str(WORKSPACE / "IntuneWinAppUtil.exe")]
        self.log_text.insert("end", "\n=== Installing Intune app dependencies ===\n")
        self.log_text.see("end")
        try:
            self.runner.run(params, WORKSPACE)
            self.status_var.set("Installing dependencies...")
        except RuntimeError as exc:
            messagebox.showwarning("Already running", str(exc))

    def bootstrap_app_registration(self):
        tenant_id = self.bootstrap_tenant_var.get().strip()
        display_name = self.bootstrap_display_name_var.get().strip() or DEFAULT_APP_REG_NAME
        if not tenant_id:
            messagebox.showerror("Missing tenant ID", "Tenant ID is required before bootstrapping app registration.")
            return
        script_path = WORKSPACE / "bootstrap-intune-app-registration.ps1"
        params = [POWERSHELL, "-ExecutionPolicy", "Bypass", "-File", str(script_path), "-TenantId", tenant_id, "-DisplayName", display_name]
        if self.bootstrap_grant_consent_var.get():
            params.append("-GrantAdminConsent")
        try:
            proc = subprocess.run(params, cwd=str(WORKSPACE), capture_output=True, text=True, check=False)
            output = (proc.stdout or "") + (proc.stderr or "")
            self.log_text.insert("end", "\n=== Bootstrap app registration ===\n" + output + "\n")
            self.log_text.see("end")
            if proc.returncode != 0:
                raise RuntimeError(output or "App registration bootstrap failed")
            client_match = re.search(r"Client ID:\s*([0-9a-fA-F-]{36})", output)
            reused_match = re.search(r"Reused Existing App Registration:\s*(True|False)", output, re.IGNORECASE)
            if client_match:
                client_id = client_match.group(1)
                reused_existing = bool(reused_match and reused_match.group(1).lower() == "true")
                self.detected_client_id_var.set(client_id)
                self.pub_client_id.set(client_id)
                self.run_client_id.set(client_id)
                self.settings.update({
                    "tenantId": tenant_id,
                    "clientId": client_id,
                    "appRegistrationDisplayName": display_name,
                    "prepToolPath": self.detected_prep_tool_var.get().strip() or str(WORKSPACE / "IntuneWinAppUtil.exe")
                })
                self.bootstrap_state.update({"bootstrapped": True, "tenantId": tenant_id, "clientId": client_id, "displayName": display_name, "reusedExisting": reused_existing})
                self._save_json_file(SETTINGS_PATH, self.settings)
                self._save_json_file(BOOTSTRAP_STATE_PATH, self.bootstrap_state)
                self.status_var.set("Existing app registration reused and saved" if reused_existing else "App registration bootstrapped and saved")
            else:
                self.status_var.set("Bootstrap finished, but no Client ID was parsed")
        except Exception as exc:
            messagebox.showerror("Bootstrap failed", str(exc))

    def save_profile(self):
        name = self.profile_name_var.get().strip() or "default"
        profiles = self._read_profiles()
        profiles[name] = {
            "tenantId": self.pub_tenant.get(),
            "authMode": self.pub_auth_mode.get(),
            "clientId": self.pub_client_id.get(),
            "moduleVersion": self.pub_module_version.get(),
            "prepTool": self.pkg_prep_tool.get(),
            "assignment": self.pub_assignment.get(),
            "group": self.pub_group.get(),
            "useAzCopy": self.pub_use_azcopy.get(),
            "deviceCode": self.pub_device_code.get(),
            "architecture": self.pkg_arch.get(),
            "outputRoot": self.pkg_output_root.get(),
        }
        self._write_profiles(profiles)
        self.status_var.set(f"Saved profile: {name}")

    def load_profile(self):
        name = self.profile_name_var.get().strip() or "default"
        profiles = self._read_profiles()
        profile = profiles.get(name)
        if not profile:
            messagebox.showwarning("Profile not found", f"No saved profile named '{name}' was found.")
            return
        self.pub_tenant.set(profile.get("tenantId", self.pub_tenant.get()))
        self.run_tenant.set(profile.get("tenantId", self.run_tenant.get()))
        self.pub_auth_mode.set(profile.get("authMode", self.pub_auth_mode.get()))
        self.pub_client_id.set(profile.get("clientId", self.pub_client_id.get()))
        self.run_client_id.set(profile.get("clientId", self.run_client_id.get()))
        self.pub_module_version.set(profile.get("moduleVersion", self.pub_module_version.get()))
        self.run_module_version.set(profile.get("moduleVersion", self.run_module_version.get()))
        self.pkg_prep_tool.set(profile.get("prepTool", self.pkg_prep_tool.get()))
        self.run_prep_tool.set(profile.get("prepTool", self.run_prep_tool.get()))
        self.pub_assignment.set(profile.get("assignment", self.pub_assignment.get()))
        self.run_assignment.set(profile.get("assignment", self.run_assignment.get()))
        self.pub_group.set(profile.get("group", self.pub_group.get()))
        self.run_group.set(profile.get("group", self.run_group.get()))
        self.pub_use_azcopy.set(profile.get("useAzCopy", self.pub_use_azcopy.get()))
        self.run_use_azcopy.set(profile.get("useAzCopy", self.run_use_azcopy.get()))
        self.pub_device_code.set(profile.get("deviceCode", self.pub_device_code.get()))
        self.run_device_code.set(profile.get("deviceCode", self.run_device_code.get()))
        self.pkg_arch.set(profile.get("architecture", self.pkg_arch.get()))
        self.run_arch.set(profile.get("architecture", self.run_arch.get()))
        self.pkg_output_root.set(profile.get("outputRoot", self.pkg_output_root.get()))
        self.status_var.set(f"Loaded profile: {name}")

    def _history_entries(self):
        entries = []
        for metadata in WORKSPACE.glob("output/**/metadata.json"):
            try:
                data = json.loads(metadata.read_text(encoding="utf-8"))
            except Exception:
                continue
            publish_path = metadata.parent / "publish.json"
            app_id = ""
            if publish_path.exists():
                try:
                    publish = json.loads(publish_path.read_text(encoding="utf-8"))
                    app_id = publish.get("AppId", "")
                except Exception:
                    pass
            package = data.get("Package", {})
            label = f"{package.get('Name', package.get('Id', metadata.parent.name))} | {package.get('Id', '')} | {metadata.parent}"
            entries.append({"label": label, "metadata": str(metadata), "folder": str(metadata.parent), "appId": app_id})
        return sorted(entries, key=lambda x: os.path.getmtime(x["metadata"]), reverse=True)

    def refresh_history(self):
        self.history_entries = self._history_entries()
        if not hasattr(self, "history_list"):
            return
        self.history_list.delete(0, "end")
        for item in self.history_entries:
            self.history_list.insert("end", item["label"])
        if self.history_entries:
            self.history_list.selection_set(0)

    def on_history_select(self, _event=None):
        index = self.history_list.curselection()
        if not index:
            return
        entry = self.history_entries[index[0]]
        self.current_metadata_path.set(entry["metadata"])

    def use_selected_history_metadata(self):
        index = self.history_list.curselection()
        if not index:
            messagebox.showwarning("No history selection", "Select a deployment history item first.")
            return
        entry = self.history_entries[index[0]]
        self.current_metadata_path.set(entry["metadata"])
        self.status_var.set("Selected history metadata for publish")

    def open_selected_history_folder(self):
        index = self.history_list.curselection()
        if not index:
            messagebox.showwarning("No history selection", "Select a deployment history item first.")
            return
        entry = self.history_entries[index[0]]
        os.startfile(entry["folder"])

    def load_bulk_preview(self):
        path = self.bulk_config_path.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showwarning("Missing bulk config", "Select a valid bulk deployment JSON file first.")
            return
        self.bulk_preview.delete("1.0", "end")
        self.bulk_preview.insert("1.0", Path(path).read_text(encoding="utf-8"))

    def run_bulk_deploy(self):
        path = self.bulk_config_path.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showwarning("Missing bulk config", "Select a valid bulk deployment JSON file first.")
            return
        self._run_command("Bulk-Deploy-IntuneApps.ps1", ["-ConfigPath", path])

    def load_override_editor(self):
        path = self.override_editor_path.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showwarning("Missing override file", "Select a valid override JSON file first.")
            return
        self.override_editor.delete("1.0", "end")
        self.override_editor.insert("1.0", Path(path).read_text(encoding="utf-8"))

    def save_override_editor(self):
        path = self.override_editor_path.get().strip()
        if not path:
            messagebox.showwarning("Missing override file", "Choose where to save the override file first.")
            return
        try:
            parsed = json.loads(self.override_editor.get("1.0", "end").strip() or "{}")
        except Exception as exc:
            messagebox.showerror("Invalid JSON", str(exc))
            return
        Path(path).write_text(json.dumps(parsed, indent=2), encoding="utf-8")
        self.status_var.set("Override file saved")

    def _autodetect_metadata(self):
        candidates = sorted(WORKSPACE.glob("output/**/metadata.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            self.current_metadata_path.set(str(candidates[0]))

    def _detect_metadata_from_line(self, line):
        if "Metadata file:" in line:
            maybe = line.split("Metadata file:", 1)[1].strip()
            if maybe:
                self.current_metadata_path.set(maybe)
        if "Searching winget" in line:
            self.progress_var.set(10)
            self.stage_var.set("Searching")
        elif "Resolving installer metadata" in line:
            self.progress_var.set(25)
            self.stage_var.set("Resolving")
        elif "Using local installer" in line or "Downloading installer" in line:
            self.progress_var.set(40)
            self.stage_var.set("Preparing installer")
        elif "Wrapping as .intunewin" in line:
            self.progress_var.set(55)
            self.stage_var.set("Wrapping")
        elif "Connecting with IntuneWin32App auth" in line or "Connecting to Microsoft Graph" in line:
            self.progress_var.set(65)
            self.stage_var.set("Authenticating")
        elif "Uploading Win32 app to Intune" in line:
            self.progress_var.set(78)
            self.stage_var.set("Uploading")
        elif "Waiting for Intune app readiness" in line:
            self.progress_var.set(88)
            self.stage_var.set("Waiting for ready state")
        elif "Assigned '" in line:
            self.progress_var.set(98)
            self.stage_var.set("Assigning")
        elif "Publish completed" in line or "Completed successfully" in line or "Created Intune app:" in line:
            self.progress_var.set(100)
            self.stage_var.set("Done")

    def _ensure_graph_auth(self, tenant_id, use_device_code=False):
        """Pre-authenticate to Microsoft Graph using raw OAuth2 flows.

        Bypasses the Microsoft Graph PowerShell SDK entirely to avoid
        .NET EventSource crashes on PowerShell 5.1. Uses direct REST calls
        to Azure AD token endpoints, then passes the access token to the
        piped subprocess via environment variable.
        """
        import tempfile, uuid
        token_file = os.path.join(tempfile.gettempdir(), f".graph_token_{uuid.uuid4().hex}")
        script_file = token_file + ".ps1"
        # Microsoft Graph PowerShell well-known public client ID
        client_id = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
        scope = "DeviceManagementApps.ReadWrite.All DeviceManagementConfiguration.ReadWrite.All Group.Read.All offline_access openid profile"

        if use_device_code:
            ps_script = self._build_device_code_script(tenant_id, client_id, scope, token_file)
        else:
            ps_script = self._build_browser_auth_script(tenant_id, client_id, scope, token_file)

        with open(script_file, "w", encoding="utf-8") as f:
            f.write(ps_script)

        self.log_text.insert("end", "\n=== Pre-authenticating to Microsoft Graph ===\n")
        if use_device_code:
            self.log_text.insert("end", "A PowerShell window will open with a device code for sign-in...\n\n")
        else:
            self.log_text.insert("end", "A browser window will open for sign-in...\n\n")
        self.log_text.see("end")
        self.update_idletasks()
        self._cached_graph_token = None
        result = subprocess.run(
            [POWERSHELL, "-ExecutionPolicy", "Bypass", "-NoProfile", "-File", script_file],
            cwd=str(WORKSPACE),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        try:
            os.unlink(script_file)
        except OSError:
            pass
        if result.returncode != 0:
            self.log_text.insert("end", "[ERROR] Graph authentication failed. Check the PowerShell window for details.\n")
            self.log_text.see("end")
            return False
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                self._cached_graph_token = f.read().strip().lstrip('\ufeff')
            os.unlink(token_file)
        except FileNotFoundError:
            self.log_text.insert("end", "[ERROR] Token file not found. Authentication may have failed.\n")
            self.log_text.see("end")
            return False
        if not self._cached_graph_token:
            self.log_text.insert("end", "[ERROR] Empty token returned.\n")
            self.log_text.see("end")
            return False
        self.log_text.insert("end", "[OK] Graph authentication successful. Token acquired.\n\n")
        self.log_text.see("end")
        return True

    @staticmethod
    def _build_device_code_script(tenant_id, client_id, scope, token_file):
        token_file_escaped = token_file.replace("'", "''")
        return f"""[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$clientId = '{client_id}'
$tenantId = '{tenant_id}'
$scope    = '{scope}'
$tokenFile = '{token_file_escaped}'

try {{
    Write-Host 'Requesting device code from Azure AD...' -ForegroundColor Cyan
    Write-Host ''

    $dcResponse = Invoke-RestMethod -Method POST `
        -Uri "https://login.microsoftonline.com/$tenantId/oauth2/v2.0/devicecode" `
        -Body @{{ client_id = $clientId; scope = $scope }} `
        -ContentType 'application/x-www-form-urlencoded'

    Write-Host $dcResponse.message -ForegroundColor Yellow
    Write-Host ''
    try {{ Set-Clipboard -Value $dcResponse.user_code; Write-Host '(Code copied to clipboard)' -ForegroundColor Gray; Write-Host '' }} catch {{ }}

    $interval = [Math]::Max([int]$dcResponse.interval, 5)
    $expiry   = (Get-Date).AddSeconds([int]$dcResponse.expires_in)

    while ((Get-Date) -lt $expiry) {{
        Start-Sleep -Seconds $interval
        try {{
            $tokenResponse = Invoke-RestMethod -Method POST `
                -Uri "https://login.microsoftonline.com/$tenantId/oauth2/v2.0/token" `
                -Body @{{
                    grant_type  = 'urn:ietf:params:oauth:grant-type:device_code'
                    client_id   = $clientId
                    device_code = $dcResponse.device_code
                }} `
                -ContentType 'application/x-www-form-urlencoded'
            break
        }} catch {{
            $errBody = $null
            try {{ $errBody = $_.ErrorDetails.Message | ConvertFrom-Json }} catch {{ }}
            if ($errBody -and $errBody.error -eq 'authorization_pending') {{ continue }}
            if ($errBody -and $errBody.error -eq 'slow_down') {{ $interval += 5; continue }}
            if ($errBody -and $errBody.error -eq 'expired_token') {{ throw 'Device code expired. Please try again.' }}
            throw
        }}
    }}

    if (-not $tokenResponse -or -not $tokenResponse.access_token) {{
        throw 'Authentication timed out or no token received.'
    }}

    Write-Host 'Authentication successful!' -ForegroundColor Green
    [IO.File]::WriteAllText($tokenFile, $tokenResponse.access_token)
    Start-Sleep -Seconds 2
    exit 0
}} catch {{
    Write-Host "ERROR: $_" -ForegroundColor Red
    Write-Host ''
    Read-Host 'Press Enter to close'
    exit 1
}}
"""

    @staticmethod
    def _build_browser_auth_script(tenant_id, client_id, scope, token_file):
        token_file_escaped = token_file.replace("'", "''")
        return f"""[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$clientId  = '{client_id}'
$tenantId  = '{tenant_id}'
$scope     = '{scope}'
$tokenFile = '{token_file_escaped}'

try {{
    # Generate PKCE code verifier and challenge
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RNGCryptoServiceProvider]::new()
    $rng.GetBytes($bytes); $rng.Dispose()
    $codeVerifier = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $challengeBytes = $sha256.ComputeHash([System.Text.Encoding]::ASCII.GetBytes($codeVerifier))
    $sha256.Dispose()
    $codeChallenge = [Convert]::ToBase64String($challengeBytes).TrimEnd('=').Replace('+','-').Replace('/','_')

    # Start localhost HTTP listener
    $listener = [System.Net.HttpListener]::new()
    $port = 8400; $started = $false
    for ($i = 0; $i -lt 10; $i++) {{
        try {{
            $listener.Prefixes.Clear()
            $listener.Prefixes.Add("http://localhost:$port/")
            $listener.Start(); $started = $true; break
        }} catch {{ $port++ }}
    }}
    if (-not $started) {{ throw 'Could not start HTTP listener on localhost.' }}

    $redirectUri = "http://localhost:$port"
    $state = [guid]::NewGuid().ToString()

    $authUrl = "https://login.microsoftonline.com/$tenantId/oauth2/v2.0/authorize?" +
        "client_id=$clientId" +
        "&response_type=code" +
        "&redirect_uri=$([Uri]::EscapeDataString($redirectUri))" +
        "&scope=$([Uri]::EscapeDataString($scope))" +
        "&state=$state" +
        "&code_challenge=$codeChallenge" +
        "&code_challenge_method=S256" +
        "&prompt=select_account"

    Write-Host 'Opening browser for sign-in...' -ForegroundColor Cyan
    Write-Host ''
    Start-Process $authUrl

    # Wait for redirect callback (2 minute timeout)
    Write-Host 'Waiting for authentication...' -ForegroundColor Gray
    $async = $listener.BeginGetContext($null, $null)
    if (-not $async.AsyncWaitHandle.WaitOne(120000)) {{
        $listener.Stop()
        throw 'Browser authentication timed out (2 minutes).'
    }}

    $ctx = $listener.EndGetContext($async)

    # Parse query string
    $qs = $ctx.Request.Url.Query.TrimStart('?')
    $params = @{{}}
    foreach ($pair in $qs.Split('&')) {{
        $kv = $pair.Split('=', 2)
        if ($kv.Count -eq 2) {{ $params[$kv[0]] = [Uri]::UnescapeDataString($kv[1]) }}
    }}

    # Send success page to browser
    $html = '<html><body style="font-family:sans-serif;text-align:center;padding-top:50px">' +
            '<h2>Authentication complete!</h2><p>You can close this tab.</p></body></html>'
    $buf = [System.Text.Encoding]::UTF8.GetBytes($html)
    $ctx.Response.ContentLength64 = $buf.Length
    $ctx.Response.ContentType = 'text/html'
    $ctx.Response.OutputStream.Write($buf, 0, $buf.Length)
    $ctx.Response.Close(); $listener.Stop()

    if ($params['error']) {{ throw "Auth error: $($params['error_description'])" }}
    if ($params['state'] -ne $state) {{ throw 'State mismatch - possible CSRF.' }}
    $code = $params['code']
    if (-not $code) {{ throw 'No authorization code received.' }}

    Write-Host 'Exchanging code for token...' -ForegroundColor Cyan

    $tokenResponse = Invoke-RestMethod -Method POST `
        -Uri "https://login.microsoftonline.com/$tenantId/oauth2/v2.0/token" `
        -Body @{{
            grant_type    = 'authorization_code'
            client_id     = $clientId
            code          = $code
            redirect_uri  = $redirectUri
            code_verifier = $codeVerifier
        }} `
        -ContentType 'application/x-www-form-urlencoded'

    if (-not $tokenResponse -or -not $tokenResponse.access_token) {{
        throw 'Token exchange failed - no access token returned.'
    }}

    Write-Host 'Authentication successful!' -ForegroundColor Green
    [IO.File]::WriteAllText($tokenFile, $tokenResponse.access_token)
    Start-Sleep -Seconds 2
    exit 0
}} catch {{
    Write-Host "ERROR: $_" -ForegroundColor Red
    Write-Host ''
    Read-Host 'Press Enter to close'
    exit 1
}}
"""

    def _run_command(self, script_name, params, extra_env=None):
        script_path = WORKSPACE / script_name
        if not script_path.exists():
            messagebox.showerror("Missing script", f"Script not found: {script_path}")
            return
        args = [POWERSHELL, "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
        args.extend(params)
        self.status_var.set(f"Running {script_name}...")
        self.progress_var.set(2)
        self.stage_var.set("Starting")
        self.log_text.insert("end", f"\n=== Running {script_name} ===\n")
        self.log_text.insert("end", " ".join(f'"{a}"' if " " in a else a for a in args) + "\n\n")
        self.log_text.see("end")
        try:
            self.runner.run(args, WORKSPACE, extra_env=extra_env)
        except RuntimeError as exc:
            messagebox.showwarning("Already running", str(exc))

    def _validate_publish_inputs(self, tenant_id, use_app_registration, client_id, metadata_path=None):
        if not tenant_id.strip():
            messagebox.showerror("Missing tenant ID", "Tenant ID is required before publish can start.")
            return False
        if metadata_path is not None and not metadata_path.strip():
            messagebox.showerror("Missing metadata", "Select a metadata.json file before publishing.")
            return False
        if use_app_registration and not client_id.strip():
            messagebox.showerror("Missing Client ID", "Client ID is required when AppRegistration auth is selected.")
            return False
        return True

    def run_package(self):
        params = [
            "-AppName", self.pkg_app_name.get(),
            "-OutputRoot", self.pkg_output_root.get(),
            "-Architecture", self.pkg_arch.get(),
            "-DeploymentType", self.pkg_deployment.get(),
            "-IntuneWinAppUtilPath", self.pkg_prep_tool.get(),
        ]
        selected_id = self._selected_package_id(self.pkg_selected_id.get())
        if selected_id:
            params += ["-PackageId", selected_id]
        if self.pkg_local_installer.get():
            params += ["-LocalInstallerPath", self.pkg_local_installer.get()]
        if self.pkg_override.get():
            params += ["-OverrideConfigPath", self.pkg_override.get()]
        self._run_command("New-IntunePackage.ps1", params)

    def run_publish(self):
        use_app_registration = self.pub_auth_mode.get() == "AppRegistration"
        if not self._validate_publish_inputs(self.pub_tenant.get(), use_app_registration, self.pub_client_id.get(), self.pub_metadata.get()):
            return
        params = [
            "-TenantId", self.pub_tenant.get(),
            "-MetadataPath", self.pub_metadata.get(),
            "-NewAppAssignment", self.pub_assignment.get(),
        ]
        if self.pub_group.get():
            params += ["-TargetGroupName", self.pub_group.get()]
        if use_app_registration:
            params += ["-UseIntuneGraphAuth", "-IntuneClientId", self.pub_client_id.get()]
        else:
            params += ["-UseDelegatedAuth"]
        if self.pub_module_version.get():
            params += ["-IntuneModuleVersion", self.pub_module_version.get()]
        if self.pub_device_code.get():
            params += ["-IntuneDeviceCode"]
        if self.pub_override.get():
            params += ["-OverrideConfigPath", self.pub_override.get()]
        if self.pub_display_name.get():
            params += ["-CustomDisplayName", self.pub_display_name.get()]
        if self.pub_publisher.get():
            params += ["-CustomPublisher", self.pub_publisher.get()]
        if self.pub_description.get():
            params += ["-CustomDescription", self.pub_description.get()]
        if self.pub_supersede_id.get():
            params += ["-SupersedeAppId", self.pub_supersede_id.get(), "-SupersedenceType", self.pub_supersedence_type.get()]
        if self.pub_use_azcopy.get():
            params += ["-UseAzCopy"]
        extra_env = None
        if not use_app_registration:
            if not self._ensure_graph_auth(self.pub_tenant.get(), self.pub_device_code.get()):
                return
            if self._cached_graph_token:
                extra_env = {"GRAPH_ACCESS_TOKEN": self._cached_graph_token}
        self._run_command("Publish-IntuneWin32App.ps1", params, extra_env=extra_env)

    def run_full_flow(self):
        use_app_registration = bool(self.run_client_id.get().strip())
        if not self._validate_publish_inputs(self.run_tenant.get(), use_app_registration, self.run_client_id.get()):
            return
        params = [
            "-AppName", self.run_app_name.get(),
            "-TenantId", self.run_tenant.get(),
            "-DeploymentType", self.run_deployment.get(),
            "-NewAppAssignment", self.run_assignment.get(),
            "-Architecture", self.run_arch.get(),
            "-IntuneWinAppUtilPath", self.run_prep_tool.get(),
        ]
        selected_id = self._selected_package_id(self.run_selected_id.get())
        if selected_id:
            params += ["-PackageId", selected_id]
        if self.run_group.get():
            params += ["-TargetGroupName", self.run_group.get()]
        if self.run_local_installer.get():
            params += ["-LocalInstallerPath", self.run_local_installer.get()]
        if use_app_registration:
            params += ["-UseIntuneGraphAuth", "-IntuneClientId", self.run_client_id.get()]
        else:
            params += ["-UseDelegatedAuth"]
        if self.run_module_version.get():
            params += ["-IntuneModuleVersion", self.run_module_version.get()]
        if self.run_device_code.get():
            params += ["-IntuneDeviceCode"]
        if self.run_display_name.get():
            params += ["-CustomDisplayName", self.run_display_name.get()]
        if self.run_publisher.get():
            params += ["-CustomPublisher", self.run_publisher.get()]
        if self.run_description.get():
            params += ["-CustomDescription", self.run_description.get()]
        if self.run_supersede_id.get():
            params += ["-SupersedeAppId", self.run_supersede_id.get(), "-SupersedenceType", self.run_supersedence_type.get()]
        if self.run_use_azcopy.get():
            params += ["-UseAzCopy"]
        extra_env = None
        if not use_app_registration:
            if not self._ensure_graph_auth(self.run_tenant.get(), self.run_device_code.get()):
                return
            if self._cached_graph_token:
                extra_env = {"GRAPH_ACCESS_TOKEN": self._cached_graph_token}
        self._run_command("Invoke-IntuneJob.ps1", params, extra_env=extra_env)

    def stop_current(self):
        self.runner.terminate()
        self.status_var.set("Termination requested")


if __name__ == "__main__":
    app = IntuneDesktopApp()
    app.mainloop()
    app.mainloop()
