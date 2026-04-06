"""
PAS Core System Helpers
This module is a bridge to the `pas_core` library.
"""

import sys
from pathlib import Path
from typing import Any, Dict, Optional, List
import datetime
import json


def normalize_path_input(raw: str) -> str:
    """
    Strip whitespace and one pair of matching outer single/double quotes.

    Pasted paths often look like '"/path/to/file"'; without stripping, pathlib
    treats the leading quote as a relative path segment. Use before
    Path(...).expanduser().resolve() for interactive path inputs.
    """
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s

# Bootstrap pas_core from local libs if not installed
def bootstrap_core():
    lib_path = Path(__file__).resolve().parent.parent / "libs" / "pas-core" / "src"
    if lib_path.exists() and str(lib_path) not in sys.path:
        sys.path.insert(0, str(lib_path))
    try:
        import yaml  # noqa: F401 — pas_core.config requires PyYAML at import time
    except ImportError as e:
        raise ImportError(
            "pas_core needs PyYAML (and other deps declared in libs/pas-core). "
            "From the PAS repo root run: make setup\n"
            "Or: make install-deps  /  pip install -e libs/pas-core"
        ) from e


bootstrap_core()

from pas_core import (
    load_pas_config,
    save_pas_config,
    run_command,
    get_git_info,
    get_pas_config_dir,
    atomic_write_text,
    safe_write_json,
    backup_json_with_timestamp,
    set_keychain_secret,
    get_keychain_secret,
    SECRET_ROTATION_DAYS,
)


def get_secret_age(service: str, key_path: str) -> Optional[int]:
    """
    Return secret age in days for a secretized key.

    `key_path` uses dot notation (e.g. "profiles.default.token"). We look for the
    corresponding "<key>_meta.created_at" field in the raw on-disk config.
    """
    try:
        config_file = get_pas_config_dir() / f"{service}.json"
        if not config_file.exists():
            return None
        raw = json.loads(config_file.read_text())

        parts = [p for p in key_path.split(".") if p]
        if not parts:
            return None

        cursor: Any = raw
        for part in parts[:-1]:
            if not isinstance(cursor, dict) or part not in cursor:
                return None
            cursor = cursor[part]

        last = parts[-1]
        if not isinstance(cursor, dict):
            return None
        meta = cursor.get(f"{last}_meta")
        if not isinstance(meta, dict):
            return None
        created_at_str = meta.get("created_at")
        if not created_at_str:
            return None

        created_at = datetime.datetime.fromisoformat(created_at_str)
        return (datetime.datetime.now() - created_at).days
    except Exception:
        return None

# Re-export UI components for convenience and backward compatibility
try:
    from ppui import (
        choice,
        console,
        prompt_yes_no,
        prompt_toolkit_menu,
        format_menu_choices,
        copy_to_clipboard,
        Menu,
        DataTable,
    )
except ImportError:
    # Fallback: If ppui isn't installed in the environment,
    # inject its source path into sys.path to ensure PAS remains "zero-setup".
    lib_path = Path(__file__).resolve().parent.parent / "libs" / "ppui" / "src"
    if lib_path.exists() and str(lib_path) not in sys.path:
        sys.path.append(str(lib_path))

    from ppui import (
        choice,
        console,
        prompt_yes_no,
        prompt_toolkit_menu,
        format_menu_choices,
        copy_to_clipboard,
        Menu,
        DataTable,
    )
from rich.panel import Panel

def detect_cloudflared_binary() -> Optional[Path]:
    """Find the cloudflared binary in common paths or PATH."""
    import shutil
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

def is_cloudflare_host(hostname: str, quiet: bool = False) -> bool:
    """
    Check if a hostname resolves to a Cloudflare Tunnel or is protected by Cloudflare Access.
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
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, socket.AI_CANONNAME)
        for info in addr_info:
            canonname = info[3]
            if canonname and "cfargotunnel.com" in canonname:
                return True
    except Exception:
        pass
    
    # --- Layer 2: Local PAS Configuration Cache ---
    try:
        config = load_pas_config("cf", quiet=quiet)
        if "tunnel_ssh_hosts" in config:
            for tunnel_id, cached_host in config["tunnel_ssh_hosts"].items():
                if hostname == cached_host:
                    return True
        if "local_tunnels" in config:
            for tunnel_id, info in config["local_tunnels"].items():
                if hostname == info.get("hostname") or h_lower == info.get("name", "").lower():
                    return True
    except Exception:
        pass

    # --- Layer 3: Cloudflare IP Range Check ---
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
                    return True
    except Exception:
        pass

    # --- Layer 4: Naming Heuristics ---
    if (h_lower.startswith("vnc-") or h_lower.startswith("ssh-") or 
        h_lower.endswith("-ssh") or h_lower.endswith("-vnc") or 
        ".vnc-" in h_lower or ".ssh-" in h_lower):
        return True

    return False

def detect_supabase_binary() -> Optional[Path]:
    """Find the supabase binary in common paths or PATH."""
    import shutil
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

def list_profiles_by_capability(capability: str) -> List[Dict[str, Any]]:
    """Aggregate profiles from all providers that support a specific capability."""
    import json
    all_profiles = []
    config_dir = get_pas_config_dir()
    for path in config_dir.glob("*.json"):
        if "-" in path.stem or path.stem in ["pas", "sync", "apis"]:
            continue
        try:
            # Note: We use raw json load here to check capabilities before full de-secretization
            data = json.loads(path.read_text())
            capabilities = data.get("capabilities", [])
            provider = data.get("provider", path.stem)
            
            is_match = capability in capabilities
            if not is_match and capability == "ai":
                if path.stem in ["ai-models", "google", "openrouter", "gemini", "openai", "anthropic"]:
                    is_match = True
            
            if is_match:
                # Use the library's load_pas_config to get de-secretized profiles
                full_config = load_pas_config(path.stem, quiet=True)
                profiles = full_config.get("profiles", {})
                if not profiles and path.stem == "supabase":
                    profiles = full_config.get("organizations", {})
                
                for p_id, p_data in profiles.items():
                    all_profiles.append({
                        "connection_id": p_id,
                        "provider": provider,
                        **p_data
                    })
        except Exception:
            continue
    return all_profiles

def get_active_profile_id(service: str) -> Optional[str]:
    """Get the active profile ID for a specific service."""
    config = load_pas_config(service, quiet=True)
    return config.get("active_profile_id") or config.get("current_profile")

def get_ssh_keys() -> List[Path]:
    """List potential private SSH keys in ~/.ssh/."""
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
    """Verify gcloud CLI is installed."""
    res = run_command(["gcloud", "--version"])
    if res.returncode != 0:
        console.print("[bold red]Error: gcloud CLI not found.[/bold red]")
        if prompt_yes_no("Would you like to install Google Cloud CLI via Homebrew?"):
            console.print("Installing gcloud-cli...")
            import subprocess
            install_res = subprocess.run(["brew", "install", "--cask", "gcloud-cli"], check=False)
            if install_res.returncode == 0:
                console.print("[green]gcloud-cli installed successfully![/green]")
                return True
        return False
    return True

def check_gcloud_auth() -> bool:
    """Check if gcloud is authenticated."""
    import json
    import subprocess
    res = run_command(["gcloud", "auth", "list", "--format=json"])
    accounts = []
    if res.returncode == 0:
        try:
            accounts = json.loads(res.stdout)
        except json.JSONDecodeError:
            pass
    if not accounts:
        if prompt_yes_no("No gcloud accounts found. Run 'gcloud auth login' now?"):
            subprocess.run(["gcloud", "auth", "login"], check=True)
            return True
        return False
    active_account = next((a for a in accounts if a.get("status") == "ACTIVE"), None)
    choices = []
    if active_account:
        acc_email = active_account.get("account")
        choices.append({"title": f"Continue as active: {acc_email} (ACTIVE)", "value": ("continue", acc_email)})
    for a in accounts:
        if a.get("status") != "ACTIVE":
            acc_email = a.get("account")
            choices.append({"title": f"Switch to: {acc_email}", "value": ("switch", acc_email)})
    choices.append({"title": "Authenticate as another user (gcloud login)", "value": ("login", None)})
    choices.append({"title": "[Quit]", "value": ("quit", None)})
    formatted_choices = format_menu_choices(choices, title_field="title", value_field="value")
    console.print("\n[bold cyan]Google Cloud Authentication:[/bold cyan]")
    selected_result = prompt_toolkit_menu(formatted_choices)
    selected_action, selected_email = selected_result if selected_result else ("quit", None)
    if selected_action == "quit": return None
    elif selected_action == "continue": return True
    elif selected_action == "switch":
        res = run_command(["gcloud", "config", "set", "account", selected_email])
        return res.returncode == 0
    elif selected_action == "login":
        subprocess.run(["gcloud", "auth", "login"], check=True)
        return True
    return False

def get_gcp_projects() -> List[dict]:
    """Fetch list of GCP projects and their billing status."""
    import json
    res_projects = run_command(["gcloud", "projects", "list", "--format=json"])
    if res_projects.returncode != 0: return []
    try:
        projects = json.loads(res_projects.stdout)
    except json.JSONDecodeError: return []
    res_accounts = run_command(["gcloud", "billing", "accounts", "list", "--format=json"])
    billing_map = {p["projectId"]: (False, "N/A") for p in projects}
    if res_accounts.returncode == 0:
        try:
            accounts = json.loads(res_accounts.stdout)
            for acc in accounts:
                acc_id = acc["name"].split("/")[-1]
                acc_display_name = acc.get("displayName", acc_id)
                res_bill_projs = run_command(["gcloud", "billing", "projects", "list", f"--billing-account={acc_id}", "--format=json"])
                if res_bill_projs.returncode == 0:
                    bill_projs = json.loads(res_bill_projs.stdout)
                    for bp in bill_projs:
                        if bp["projectId"] in billing_map:
                            billing_map[bp["projectId"]] = (bp.get("billingEnabled", True), acc_display_name)
        except Exception: pass
    for p in projects:
        enabled, account_name = billing_map.get(p["projectId"], (False, "N/A"))
        p["billingEnabled"] = enabled
        p["billingAccountName"] = account_name
    return projects

def select_gcp_project(requested_id: Optional[str] = None) -> Optional[str]:
    """Interactively select or create a GCP project."""
    projects = get_gcp_projects()
    if requested_id:
        match = next((p for p in projects if p["projectId"] == requested_id), None)
        if match: return requested_id
        if prompt_yes_no(f"Project '{requested_id}' not found. Create it?"):
            res = run_command(["gcloud", "projects", "create", requested_id])
            if res.returncode == 0: return requested_id
        return None
    if not projects:
        new_id = input("Enter a new Project ID to create: ").strip()
        if new_id:
            res = run_command(["gcloud", "projects", "create", new_id])
            if res.returncode == 0: return new_id
        return None
    menu_choices = []
    for p in projects:
        billing_status = "✓" if p.get("billingEnabled") else "✗"
        title = f"{p['projectId']:<25} | {p.get('name', 'N/A'):<20} | Billing: {billing_status}"
        menu_choices.append({"title": title, "value": p["projectId"]})
    menu_choices.append({"title": "[Create a new project]", "value": "__create__"})
    menu_choices.append({"title": "[Back]", "value": "__quit__"})
    formatted_choices = format_menu_choices(menu_choices, title_field="title", value_field="value")
    selected_value = prompt_toolkit_menu(formatted_choices)
    if not selected_value or selected_value == "__quit__": return None
    if selected_value == "__create__":
        new_id = input("Enter new Project ID: ").strip()
        if new_id:
            res = run_command(["gcloud", "projects", "create", new_id])
            if res.returncode == 0: return new_id
        return None
    return selected_value

def ensure_gcp_apis_enabled(project_id: str, apis: List[str]):
    """Enable specified APIs for a project."""
    for api in apis:
        run_command(["gcloud", "services", "enable", api, "--project", project_id])

def check_gcp_billing(project_id: str) -> bool:
    """Check if billing is enabled for the project."""
    import json
    res = run_command(["gcloud", "billing", "projects", "describe", project_id, "--format=json"])
    if res.returncode != 0: return False
    try:
        data = json.loads(res.stdout)
        return data.get("billingEnabled", False)
    except json.JSONDecodeError: return False
