"""
PAS Core System Helpers
This module contains the domain logic for the PAS toolkit, including secret management,
persistent configuration, and service-specific integrations (GCP, GitHub).

AI AGENT GUIDELINES:
- SECRETS: NEVER print or store raw tokens. Use `load_pas_config` and `save_pas_config`.
- CONFIG: Use `load_pas_config(service)` to retrieve settings from ~/.pas/service.json.
- COMMANDS: Use `run_command` instead of raw `subprocess.run` for consistent error handling.
- UI: This module re-exports TUI helpers from `.tui`. Use them for all interactivity.
- PATHS: Always use `pathlib.Path` and handle `~` expansion for user inputs.
"""

import os
import shutil
import json
import subprocess
import sys
import datetime
import re
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List

# Re-export UI components for convenience and backward compatibility
from .tui import (
    console, 
    prompt_yes_no, 
    prompt_toolkit_menu, 
    format_menu_choices, 
    copy_to_clipboard,
    Menu
)
from rich.panel import Panel

# Keys that should be stored securely in the macOS Keychain.
# If you add a new service token key name, add it here.
SENSITIVE_KEYS = {
    "token",
    "access_token",
    "api_key",
    "CLOUDFLARE_API_TOKEN",
    "TUNNEL_TOKEN",
    "key",
    "pypi_token",
    "testpypi_token",
    # Generic client secrets (e.g., Google OAuth client_secret used by rclone-ops)
    "client_secret",
}
KEYCHAIN_SERVICE = "pas-toolkit"
SECRET_ROTATION_DAYS = 30

# --- JSON Persistence Helpers ---
JSON_BACKUP_KEEP_DEFAULT = 5
JSON_BACKUP_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"  # yyyymmddhhmmss
# ------------------------------


def _json_backup_path(path: Path, ts: str, counter: int = 0) -> Path:
    """
    abc.json -> abc-yyyymmddhhmmss.json (or abc-yyyymmddhhmmss-1.json on collision)
    """
    suffix = path.suffix or ".json"
    base = path.with_suffix("").name
    if counter <= 0:
        return path.with_name(f"{base}-{ts}{suffix}")
    return path.with_name(f"{base}-{ts}-{counter}{suffix}")


def _rotate_json_backups(path: Path, keep: int) -> None:
    """
    Keep only the newest `keep` backups named like abc-yyyymmddhhmmss(.json) or abc-yyyymmddhhmmss-<n>.json.
    """
    if keep <= 0:
        return
    parent = path.parent
    suffix = path.suffix or ".json"
    base = path.with_suffix("").name
    pattern = re.compile(rf"^{re.escape(base)}-(\d{{14}})(?:-(\d+))?{re.escape(suffix)}$")

    matches: List[tuple[str, int, Path]] = []
    try:
        for p in parent.iterdir():
            if not p.is_file():
                continue
            m = pattern.match(p.name)
            if not m:
                continue
            ts = m.group(1)
            ctr = int(m.group(2) or "0")
            matches.append((ts, ctr, p))
    except Exception:
        return

    # Newest first by (timestamp, counter)
    matches.sort(key=lambda t: (t[0], t[1]), reverse=True)
    for _, _, p in matches[keep:]:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass


def backup_json_with_timestamp(path: Path, keep: int = JSON_BACKUP_KEEP_DEFAULT) -> Optional[Path]:
    """
    If `path` exists, copy it to a timestamped backup next to it, then rotate backups.
    Returns the created backup path, or None if no backup was created.
    """
    if not path.exists() or not path.is_file():
        return None

    ts = datetime.datetime.now().strftime(JSON_BACKUP_TIMESTAMP_FORMAT)
    backup_path = _json_backup_path(path, ts, 0)
    counter = 0
    while backup_path.exists():
        counter += 1
        backup_path = _json_backup_path(path, ts, counter)

    try:
        shutil.copy2(path, backup_path)
    except Exception:
        return None

    _rotate_json_backups(path, keep=keep)
    return backup_path


def atomic_write_text(path: Path, content: str) -> None:
    """
    Atomically write text by writing to a temp file in the same directory and renaming.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as tf:
        tf.write(content)
        tf.flush()
        os.fsync(tf.fileno())
        tmp_path = Path(tf.name)
    os.replace(tmp_path, path)


def safe_write_json(
    path: Path,
    data: Any,
    *,
    keep_backups: int = JSON_BACKUP_KEEP_DEFAULT,
    indent: int = 2,
) -> None:
    """
    Safe JSON persistence:
    - Create a timestamped backup of the existing file (rotated to `keep_backups`)
    - Atomically replace the file with new JSON content
    """
    backup_json_with_timestamp(path, keep=keep_backups)
    atomic_write_text(path, json.dumps(data, indent=indent))

def set_keychain_secret(account: str, value: str):
    """Store a secret in the macOS Keychain. Only works on Darwin (macOS)."""
    if sys.platform != "darwin":
        return
    subprocess.run(
        ["security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w", value, "-U"],
        capture_output=True, check=False
    )

def get_keychain_secret(account: str) -> Optional[str]:
    """Retrieve a secret from the macOS Keychain."""
    if sys.platform != "darwin":
        return None
    res = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
        capture_output=True, text=True, check=False
    )
    if res.returncode == 0:
        return res.stdout.strip()
    return None

def _needs_migration(data: Any) -> bool:
    """Check if any sensitive keys are stored in plain text instead of SEC: references."""
    if isinstance(data, dict):
        for k, v in data.items():
            if k in SENSITIVE_KEYS and isinstance(v, str) and not v.startswith("SEC:"):
                return True
            if _needs_migration(v):
                return True
    elif isinstance(data, list):
        for item in data:
            if _needs_migration(item):
                return True
    return False

def _en_secretize(data: Any, service: str, path: str = "") -> Any:
    """
    Recursively move sensitive keys to Keychain and replace with 'SEC:' references.
    This ensures that the JSON files in ~/.pas/ only contain pointers to secrets.
    """
    if isinstance(data, dict):
        new_dict = {}
        for k, v in data.items():
            full_path = f"{path}.{k}" if path else k
            if k in SENSITIVE_KEYS and isinstance(v, str) and not v.startswith("SEC:"):
                account = f"{service}.{full_path}"
                set_keychain_secret(account, v)
                
                # Update/Create metadata for rotation tracking
                metadata_key = f"{k}_meta"
                existing_meta = data.get(metadata_key, {})
                now = datetime.datetime.now().isoformat()
                
                new_dict[k] = f"SEC:{account}"
                new_dict[metadata_key] = {
                    "created_at": existing_meta.get("created_at") or now,
                    "last_used_at": now
                }
            elif k.endswith("_meta") and k[:-5] in SENSITIVE_KEYS:
                # Metadata is handled alongside its key
                continue
            else:
                new_dict[k] = _en_secretize(v, service, full_path)
        return new_dict
    elif isinstance(data, list):
        return [_en_secretize(item, service, f"{path}[{i}]") for i, item in enumerate(data)]
    return data

def _de_secretize(data: Any, service: str) -> Any:
    """
    Recursively fetch secrets from Keychain and replace 'SEC:' references with real values.
    Also handles age warnings for old secrets.
    """
    if isinstance(data, dict):
        new_dict = {}
        now = datetime.datetime.now()
        for k, v in data.items():
            if isinstance(v, str) and v.startswith("SEC:"):
                account = v[4:]
                secret = get_keychain_secret(account)
                if secret:
                    new_dict[k] = secret
                    # Check age and print warning to stderr
                    meta = data.get(f"{k}_meta", {})
                    created_at_str = meta.get("created_at")
                    if created_at_str:
                        try:
                            created_at = datetime.datetime.fromisoformat(created_at_str)
                            days_old = (now - created_at).days
                            if days_old > SECRET_ROTATION_DAYS:
                                sys.stderr.write(f"\x1b[33m\n[!] WARNING: The {k} for {service} is {days_old} days old. Consider rotating it.\x1b[0m\n")
                        except Exception:
                            pass
                else:
                    new_dict[k] = v # Keep reference if secret not found
            else:
                new_dict[k] = _de_secretize(v, service)
        return new_dict
    elif isinstance(data, list):
        return [_de_secretize(item, service) for item in data]
    return data

def detect_cloudflared_binary() -> Optional[Path]:
    """Find the cloudflared binary in common paths or PATH."""
    candidates = [
        Path("/opt/homebrew/bin/cloudflared"),
        Path("/usr/local/bin/cloudflared"),
    ]
    for path in candidates:
        if path.exists():
            return path
    which = shutil.which("cloudflared")
    if which:
        return Path(which)
    return None

def is_cloudflare_host(hostname: str) -> bool:
    """
    Check if a hostname resolves to a Cloudflare Tunnel or is protected by Cloudflare Access.
    Uses multiple layers of detection:
    1. DNS CNAME lookup for 'cfargotunnel.com' (The most definitive proof for tunnels).
    2. Local PAS configuration cache (~/.pas/cf.json).
    3. IP resolution check against known Cloudflare IP ranges.
    4. Naming heuristics (as a last resort).
    """
    import socket
    import ipaddress
    
    if "@" in hostname: # Handle user@host
        hostname = hostname.split("@")[-1]
    
    # Strip port if present
    if ":" in hostname:
        hostname = hostname.split(":")[0]

    h_lower = hostname.lower()

    # --- Layer 1: DNS CNAME Check ---
    try:
        # We use getaddrinfo with AI_CANONNAME to find the underlying CNAME
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, socket.AI_CANONNAME)
        for info in addr_info:
            canonname = info[3]
            if canonname and "cfargotunnel.com" in canonname:
                return True
    except Exception:
        pass
    
    # --- Layer 2: Local PAS Configuration Cache ---
    try:
        config = load_pas_config("cf")
        # Check cached SSH hosts
        if "tunnel_ssh_hosts" in config:
            for tunnel_id, cached_host in config["tunnel_ssh_hosts"].items():
                if hostname == cached_host:
                    return True
        
        # Check local tunnels and their hostnames
        if "local_tunnels" in config:
            for tunnel_id, info in config["local_tunnels"].items():
                if hostname == info.get("hostname") or h_lower == info.get("name", "").lower():
                    return True
    except Exception:
        pass

    # --- Layer 3: Cloudflare IP Range Check ---
    # Cloudflare published IP ranges (simplified list of common ones)
    CF_IPV4_RANGES = [
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
        "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
        "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22"
    ]
    try:
        ips = socket.gethostbyname_ex(hostname)[2]
        for ip in ips:
            addr = ipaddress.ip_address(ip)
            for net in CF_IPV4_RANGES:
                if addr in ipaddress.ip_network(net):
                    # If it resolves to a CF IP, it's at least behind CF.
                    # For SSH/VNC, this almost always means a Tunnel/Access is required.
                    return True
    except Exception:
        pass

    # --- Layer 4: Naming Heuristics (Last Resort) ---
    if (h_lower.startswith("vnc-") or h_lower.startswith("ssh-") or 
        h_lower.endswith("-ssh") or h_lower.endswith("-vnc") or 
        ".vnc-" in h_lower or ".ssh-" in h_lower):
        return True

    return False

def detect_supabase_binary() -> Optional[Path]:
    """Find the supabase binary in common paths or PATH."""
    candidates = [
        Path("/opt/homebrew/bin/supabase"),
        Path("/usr/local/bin/supabase"),
    ]
    for path in candidates:
        if path.exists():
            return path
    which = shutil.which("supabase")
    if which:
        return Path(which)
    return None

def get_pas_config_dir() -> Path:
    """Return the path to the PAS common config directory ~/.pas/."""
    config_dir = Path.home() / ".pas"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir

def load_pas_config(service: str) -> Dict[str, Any]:
    """
    Load JSON config for a specific service from ~/.pas/service.json.
    Automatically handles secret retrieval from Keychain if SEC: tags are found.
    """
    config_file = get_pas_config_dir() / f"{service}.json"
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text())
            
            # Migration: if raw tokens found, save them to Keychain immediately
            if _needs_migration(data):
                save_pas_config(service, data)
                # Re-read after migration to get the SEC: references
                data = json.loads(config_file.read_text())
                
            return _de_secretize(data, service)
        except json.JSONDecodeError:
            print(f"Warning: Could not parse config for {service}. Returning empty dict.")
    return {}

def get_secret_age(service: str, key_path: str) -> Optional[int]:
    """
    Get the age in days of a specific secret.
    key_path can be nested like 'providers.openrouter.token'
    """
    config_file = get_pas_config_dir() / f"{service}.json"
    if not config_file.exists():
        return None
    try:
        data = json.loads(config_file.read_text())
        
        # Traverse to the dictionary containing the key
        parts = key_path.split(".")
        target_key = parts[-1]
        container = data
        for part in parts[:-1]:
            container = container.get(part, {})
            if not isinstance(container, dict):
                return None
        
        meta = container.get(f"{target_key}_meta", {})
        created_at_str = meta.get("created_at")
        if created_at_str:
            created_at = datetime.datetime.fromisoformat(created_at_str)
            return (datetime.datetime.now() - created_at).days
    except Exception:
        pass
    return None

def save_pas_config(service: str, data: Dict[str, Any]):
    """
    Save JSON config for a specific service to ~/.pas/service.json.
    Automatically moves sensitive keys to Keychain and stores SEC: references.
    """
    config_file = get_pas_config_dir() / f"{service}.json"
    # Move secrets to Keychain and store references
    processed_data = _en_secretize(data, service)
    safe_write_json(config_file, processed_data, keep_backups=JSON_BACKUP_KEEP_DEFAULT, indent=2)

def run_command(cmd: List[str], capture_output: bool = True, cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    """
    Robust wrapper for subprocess execution.
    Handles encoding, output capture, and provides a mock response on failure.
    """
    try:
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=False, # We usually want to handle errors manually
            cwd=cwd,
            env=env
        )
    except Exception as e:
        # Create a mock CompletedProcess for catastrophic failures
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr=str(e))

def get_ssh_keys() -> List[Path]:
    """List potential private SSH keys in ~/.ssh/ by checking for -----BEGIN headers."""
    ssh_dir = Path.home() / ".ssh"
    if not ssh_dir.exists():
        return []
    
    keys = []
    ignore_names = {"config", "known_hosts", "authorized_keys", "known_hosts.old", "known_hosts.bak"}
    
    for item in ssh_dir.iterdir():
        if item.is_file():
            if item.suffix == ".pub" or item.name in ignore_names or item.name.startswith("."):
                continue
            
            try:
                with open(item, "r", errors="ignore") as f:
                    content = f.read(100)
                    if "-----BEGIN" in content:
                        keys.append(item)
            except Exception:
                continue
                
    return sorted(keys)

def get_git_info() -> Optional[Dict[str, str]]:
    """Check if in a git repo and return root path and origin URL."""
    res = run_command(["git", "rev-parse", "--show-toplevel"])
    if res.returncode != 0:
        return None
        
    root = res.stdout.strip()
    origin = ""
    res_origin = run_command(["git", "remote", "get-url", "origin"])
    if res_origin.returncode == 0:
        origin = res_origin.stdout.strip()
        
    return {"root": root, "origin": origin}

def check_gh_auth() -> bool:
    """Verify GitHub CLI (gh) authentication status."""
    res = run_command(["gh", "auth", "status"], capture_output=False)
    return res.returncode == 0

def get_gh_protocol() -> str:
    """Get the default git protocol (ssh/https) for GitHub CLI."""
    res = run_command(["gh", "config", "get", "git_protocol"])
    if res.returncode == 0:
        return res.stdout.strip() or "https"
    return "https"

def check_gcloud_installed() -> bool:
    """Verify gcloud CLI is installed, offer to install via Homebrew if missing."""
    res = run_command(["gcloud", "--version"])
    if res.returncode != 0:
        console.print("[bold red]Error: gcloud CLI not found.[/bold red]")
        if prompt_yes_no("Would you like to install Google Cloud CLI via Homebrew?"):
            console.print("Installing gcloud-cli...")
            # Use --cask for the official CLI bundle
            install_res = subprocess.run(["brew", "install", "--cask", "gcloud-cli"], check=False)
            if install_res.returncode == 0:
                console.print("[green]gcloud-cli installed successfully![/green]")
                console.print("[yellow]Note: You might need to restart your terminal or run 'rehash' to use the gcloud command.[/yellow]")
                return True
            else:
                console.print("[red]Failed to install gcloud-cli via Homebrew.[/red]")
                console.print("Please install it manually: https://cloud.google.com/sdk/docs/install")
                return False
        else:
            console.print("Please install the Google Cloud SDK manually: https://cloud.google.com/sdk/docs/install")
            return False
    return True

def check_gcloud_auth() -> bool:
    """Check if gcloud is authenticated, show active user, and offer to switch/add accounts."""
    res = run_command(["gcloud", "auth", "list", "--format=json"])
    
    accounts = []
    if res.returncode == 0:
        try:
            accounts = json.loads(res.stdout)
        except json.JSONDecodeError:
            pass

    if not accounts:
        console.print("[yellow]No authenticated gcloud accounts found.[/yellow]")
        if prompt_yes_no("Would you like to run 'gcloud auth login' now?"):
            subprocess.run(["gcloud", "auth", "login"], check=True)
            return True
        return False

    active_account = next((a for a in accounts if a.get("status") == "ACTIVE"), None)
    
    # Prepare menu choices
    choices = []
    
    if active_account:
        acc_email = active_account.get("account")
        choices.append({"title": f"Continue as active: {acc_email} (ACTIVE)", "value": ("continue", acc_email)})
    
    # Add other existing accounts
    for a in accounts:
        if a.get("status") != "ACTIVE":
            acc_email = a.get("account")
            choices.append({"title": f"Switch to: {acc_email}", "value": ("switch", acc_email)})
            
    choices.append({"title": "Authenticate as another Google user (gcloud login)", "value": ("login", None)})
    choices.append({"title": "[Quit]", "value": ("quit", None)})

    formatted_choices = format_menu_choices(choices, title_field="title", value_field="value")

    console.print("\n[bold cyan]Google Cloud Authentication:[/bold cyan]")
    selected_result = prompt_toolkit_menu(formatted_choices)
    selected_action, selected_email = selected_result if selected_result else ("quit", None)

    if selected_action == "quit":
        return None
    elif selected_action == "continue":
        return True
    elif selected_action == "switch":
        console.print(f"Switching to account: [bold]{selected_email}[/bold]...")
        res = run_command(["gcloud", "config", "set", "account", selected_email])
        if res.returncode == 0:
            console.print("[green]Account switched successfully.[/green]")
            return True
        else:
            console.print(f"[red]Failed to switch account: {res.stderr}[/red]")
            return False
    elif selected_action == "login":
        console.print("Running 'gcloud auth login'...")
        subprocess.run(["gcloud", "auth", "login"], check=True)
        return True

    return False

def get_gcp_projects() -> List[Dict[str, Any]]:
    """Fetch list of GCP projects and their billing status via gcloud CLI."""
    # Fetch projects
    res_projects = run_command(["gcloud", "projects", "list", "--format=json"])
    if res_projects.returncode != 0:
        return []
    
    try:
        projects = json.loads(res_projects.stdout)
    except json.JSONDecodeError:
        return []

    # Fetch all billing accounts first to merge with project info
    res_accounts = run_command(["gcloud", "billing", "accounts", "list", "--format=json"])
    # Map projectId to (billingEnabled, accountDisplayName)
    billing_map = {p["projectId"]: (False, "N/A") for p in projects}
    
    if res_accounts.returncode == 0:
        try:
            accounts = json.loads(res_accounts.stdout)
            for acc in accounts:
                acc_id = acc["name"].split("/")[-1]
                acc_display_name = acc.get("displayName", acc_id)
                # For each billing account, list projects linked to it
                res_bill_projs = run_command(["gcloud", "billing", "projects", "list", f"--billing-account={acc_id}", "--format=json"])
                if res_bill_projs.returncode == 0:
                    bill_projs = json.loads(res_bill_projs.stdout)
                    for bp in bill_projs:
                        if bp["projectId"] in billing_map:
                            billing_map[bp["projectId"]] = (bp.get("billingEnabled", True), acc_display_name)
        except (json.JSONDecodeError, KeyError):
            pass

    # Merge billing info into project dicts
    for p in projects:
        enabled, account_name = billing_map.get(p["projectId"], (False, "N/A"))
        p["billingEnabled"] = enabled
        p["billingAccountName"] = account_name
        
    return projects

def select_gcp_project(requested_id: Optional[str] = None) -> Optional[str]:
    """Interactively select or create a GCP project."""
    console.print("[cyan]Fetching projects and billing status...[/cyan]")
    projects = get_gcp_projects()
    
    if requested_id:
        # Check if project exists
        match = next((p for p in projects if p["projectId"] == requested_id), None)
        if match:
            console.print(f"[green]Using project: [bold]{requested_id}[/bold][/green]")
            return requested_id
        else:
            console.print(f"[yellow]Project '{requested_id}' not found.[/yellow]")
            if prompt_yes_no(f"Would you like to create project '{requested_id}'?"):
                res = run_command(["gcloud", "projects", "create", requested_id])
                if res.returncode == 0:
                    console.print(f"[green]Project '{requested_id}' created successfully.[/green]")
                    return requested_id
                else:
                    console.print(f"[red]Failed to create project: {res.stderr}[/red]")
                    return None
            return None

    if not projects:
        console.print("[yellow]No GCP projects found.[/yellow]")
        new_id = input("Enter a new Project ID to create (or press Enter to cancel): ").strip()
        if not new_id:
            return None
        res = run_command(["gcloud", "projects", "create", new_id])
        if res.returncode == 0:
            return new_id
        console.print(f"[red]Failed to create project: {res.stderr}[/red]")
        return None

    # Show project selection menu
    menu_choices = []
    for p in projects:
        billing_status = "✓" if p.get("billingEnabled") else "✗"
        proj_id = p["projectId"]
        proj_name = p.get("name", "N/A")[:20]
        billing_acc = p.get("billingAccountName", "N/A")[:20]
        
        title = f"{proj_id:<25} | {proj_name:<20} | Billing: {billing_status} ({billing_acc})"
        menu_choices.append({"title": title, "value": proj_id})
    
    menu_choices.append({"title": "[Create a new project]", "value": "__create__"})
    menu_choices.append({"title": "[Back]", "value": "__quit__"})
    menu_choices.append({"title": "[Quit]", "value": "__quit__"})

    formatted_choices = format_menu_choices(menu_choices, title_field="title", value_field="value")

    console.print("\n[bold cyan]Select a GCP Project:[/bold cyan]")
    selected_value = prompt_toolkit_menu(formatted_choices)

    if not selected_value or selected_value == "__quit__":
        return None
    
    if selected_value == "__create__":
        new_id = input("Enter new Project ID: ").strip()
        if new_id:
            res = run_command(["gcloud", "projects", "create", new_id])
            if res.returncode == 0:
                console.print(f"[green]Project '{new_id}' created successfully.[/green]")
                return new_id
            console.print(f"[red]Error creating project: {res.stderr}[/red]")
        return None

    console.print(f"[green]Selected project: [bold]{selected_value}[/bold][/green]")
    return selected_value

def ensure_gcp_apis_enabled(project_id: str, apis: List[str]):
    """Enable specified APIs for a project via gcloud CLI."""
    for api in apis:
        console.print(f"Enabling {api}...")
        run_command(["gcloud", "services", "enable", api, "--project", project_id])

def check_gcp_billing(project_id: str) -> bool:
    """Check if billing is enabled for the project. Critical for many GCP APIs."""
    res = run_command(["gcloud", "billing", "projects", "describe", project_id, "--format=json"])
    if res.returncode != 0:
        console.print(f"[yellow]Warning: Could not verify billing status for {project_id}.[/yellow]")
        return False
    
    try:
        data = json.loads(res.stdout)
        if data.get("billingEnabled"):
            console.print(f"[green]Billing is enabled for project: [bold]{project_id}[/bold][/green]")
            return True
        else:
            console.print(f"[bold red]Billing is NOT enabled for project: {project_id}[/bold red]")
            console.print("Many APIs require billing to be enabled.")
            if prompt_yes_no("Would you like to continue anyway?", default=True):
                return False
            else:
                sys.exit(1)
    except json.JSONDecodeError:
        return False
