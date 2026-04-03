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

# Optional: cross-platform secret storage (Keychain, Windows Credential Locker, etc.)
try:
    import keyring
except ImportError:
    keyring = None

# Keys that should be stored securely in the OS keychain / credential store.
SENSITIVE_KEYS = {
    "token",
    "access_token",
    "api_key",
    "CLOUDFLARE_API_TOKEN",
    "TUNNEL_TOKEN",
    "key",
    "pypi_token",
    "testpypi_token",
    "client_secret",
    "database_password",
    "db_password",
}
KEYCHAIN_SERVICE = "pas-toolkit"
SECRET_ROTATION_DAYS = 30

JSON_BACKUP_KEEP_DEFAULT = 5
JSON_BACKUP_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"

def _json_backup_path(path: Path, ts: str, counter: int = 0) -> Path:
    suffix = path.suffix or ".json"
    base = path.with_suffix("").name
    if counter <= 0:
        return path.with_name(f"{base}-{ts}{suffix}")
    return path.with_name(f"{base}-{ts}-{counter}{suffix}")

def _rotate_json_backups(path: Path, keep: int) -> None:
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

    matches.sort(key=lambda t: (t[0], t[1]), reverse=True)
    for _, _, p in matches[keep:]:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

def backup_json_with_timestamp(path: Path, keep: int = JSON_BACKUP_KEEP_DEFAULT) -> Optional[Path]:
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
    backup_json_with_timestamp(path, keep=keep_backups)
    atomic_write_text(path, json.dumps(data, indent=indent))

def set_keychain_secret(account: str, value: str) -> None:
    """Store a secret in OS secure storage, with macOS security(1) fallback."""
    if keyring:
        try:
            keyring.set_password(KEYCHAIN_SERVICE, account, value)
            return
        except Exception:
            pass
    if sys.platform == "darwin":
        subprocess.run(
            ["security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w", value, "-U"],
            capture_output=True,
            check=False,
        )


def get_keychain_secret(account: str) -> Optional[str]:
    """Read a secret from OS secure storage, with macOS security(1) fallback."""
    if keyring:
        try:
            return keyring.get_password(KEYCHAIN_SERVICE, account)
        except Exception:
            pass
    if sys.platform == "darwin":
        res = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    return None

def _needs_migration(data: Any) -> bool:
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
    if isinstance(data, dict):
        new_dict = {}
        for k, v in data.items():
            full_path = f"{path}.{k}" if path else k
            if k in SENSITIVE_KEYS and isinstance(v, str) and not v.startswith("SEC:"):
                account = f"{service}.{full_path}"
                set_keychain_secret(account, v)
                
                metadata_key = f"{k}_meta"
                existing_meta = data.get(metadata_key, {})
                now = datetime.datetime.now().isoformat()
                
                new_dict[k] = f"SEC:{account}"
                new_dict[metadata_key] = {
                    "created_at": existing_meta.get("created_at") or now,
                    "last_used_at": now
                }
            elif k.endswith("_meta") and k[:-5] in SENSITIVE_KEYS:
                continue
            else:
                new_dict[k] = _en_secretize(v, service, full_path)
        return new_dict
    elif isinstance(data, list):
        return [_en_secretize(item, service, f"{path}[{i}]") for i, item in enumerate(data)]
    return data

def _de_secretize(data: Any, service: str, quiet: bool = False) -> Any:
    if isinstance(data, dict):
        new_dict = {}
        now = datetime.datetime.now()
        for k, v in data.items():
            if isinstance(v, str) and v.startswith("SEC:"):
                account = v[4:]
                secret = get_keychain_secret(account)
                if secret:
                    new_dict[k] = secret
                    if not quiet:
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
                    new_dict[k] = v
            else:
                new_dict[k] = _de_secretize(v, service, quiet=quiet)
        return new_dict
    elif isinstance(data, list):
        return [_de_secretize(item, service, quiet=quiet) for item in data]
    return data

def get_pas_config_dir() -> Path:
    config_dir = Path.home() / ".pas"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir

def load_pas_config(service: str, quiet: bool = False, profile: Optional[str] = None) -> Dict[str, Any]:
    config_file = get_pas_config_dir() / f"{service}.json"
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text())
            if _needs_migration(data):
                save_pas_config(service, data)
                data = json.loads(config_file.read_text())
            
            config = _de_secretize(data, service, quiet=quiet)
            
            if profile:
                profiles = config.get("profiles", {})
                if profile in profiles:
                    profile_config = profiles[profile]
                    return {**config, **profile_config, "current_profile": profile}
                elif not quiet:
                    print(f"Warning: Profile '{profile}' not found in {service} config.")
            
            return config
        except json.JSONDecodeError:
            if not quiet:
                print(f"Warning: Could not parse config for {service}. Returning empty dict.")
    return {}

def save_pas_config(service: str, data: Dict[str, Any], profile: Optional[str] = None):
    config_file = get_pas_config_dir() / f"{service}.json"
    if profile:
        existing_config = {}
        if config_file.exists():
            try:
                existing_config = json.loads(config_file.read_text())
            except Exception:
                pass
        if "profiles" not in existing_config:
            existing_config["profiles"] = {}
        existing_config["profiles"][profile] = data
        existing_config["current_profile"] = profile
        data = existing_config
    if "provider" not in data:
        data["provider"] = service
    processed_data = _en_secretize(data, service)
    safe_write_json(config_file, processed_data, keep_backups=JSON_BACKUP_KEEP_DEFAULT, indent=2)

def run_command(cmd: List[str], capture_output: bool = True, cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=False,
            cwd=cwd,
            env=env
        )
    except Exception as e:
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr=str(e))

def get_git_info() -> Optional[Dict[str, str]]:
    res = run_command(["git", "rev-parse", "--show-toplevel"])
    if res.returncode != 0:
        return None
    root = res.stdout.strip()
    origin = ""
    res_origin = run_command(["git", "remote", "get-url", "origin"])
    if res_origin.returncode == 0:
        origin = res_origin.stdout.strip()
    return {"root": root, "origin": origin}

from .git_utils import _github_url_from_path

from .config import (
    PASProjectConfig,
    PAS_PROJECT_STANDARD_ENV_KEYS,
    SERVICE_CARD_DETAIL_PRIMARY_FIELDS,
    apply_service_env_patch,
    load_pas_project_config,
    load_pas_project_config_document,
    pas_provider_dev_config_path,
    pas_user_service_config_json_path,
    resolve_pas_project_config_path,
    save_pas_project_config,
    get_metadata_cards,
    get_environments_list,
    get_services_refs,
    get_service_oriented_data,
    resolve_pas_provider_dev_config_path,
)
from .mtime_display import format_path_mtime_for_display, relative_mtime_ago
from .service_config import (
    build_service_config_view_model,
    check_service_connectivity,
    check_supabase_org_connectivity,
    check_supabase_postgres_connectivity,
    fetch_supabase_management_project,
    get_supabase_profile_options,
    get_supabase_projects_for_profile,
    normalize_supabase_toolkit_config,
    resolve_supabase_database_password_plain,
    reveal_service_secret,
    set_supabase_toolkit_database_password,
    set_supabase_toolkit_profile_access_token,
    supabase_database_password_storage_hint,
    supabase_profile_token_storage_hint,
)
