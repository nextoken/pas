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
    "CLOUDFLARE_GLOBAL_API_KEY",
    "CLOUDFLARE_API_KEY",
    "TUNNEL_TOKEN",
    "key",
    "pypi_token",
    "testpypi_token",
    "client_secret",
    "database_password",
    "db_password",
}

# Always keychain these Cloudflare profile fields when saving ``cloudflare.json``, even if a stale
# ``pas_core`` build omitted them from ``SENSITIVE_KEYS`` (e.g. CLI imported an old site-packages copy).
_CLOUDFLARE_KEYCHAIN_KEYS = frozenset(
    ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_GLOBAL_API_KEY", "CLOUDFLARE_API_KEY")
)


def _key_should_use_keychain(key: str, service: str) -> bool:
    if key in SENSITIVE_KEYS:
        return True
    return service == "cloudflare" and key in _CLOUDFLARE_KEYCHAIN_KEYS


KEYCHAIN_SERVICE = "pas-toolkit"
SECRET_ROTATION_DAYS = 30

# Process-local cache: ``load_pas_config`` / ``_de_secretize`` may run many times per CLI invocation
# (e.g. ``cloudflare_profile_health``), resolving the same ``SEC:`` accounts each time. Without caching,
# each lookup spawns ``security find-generic-password`` on macOS and can trigger repeated Keychain prompts.
_KEYCHAIN_SECRET_CACHE: Dict[str, Optional[str]] = {}

JSON_BACKUP_KEEP_DEFAULT = 5
JSON_BACKUP_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"

# ``~/.pas/pas.json`` — max rotated timestamped backups for ``console.yaml`` (see ``get_console_yaml_backup_keep``).
CONSOLE_YAML_BACKUP_KEEP_KEY = "console_yaml_backup_keep"

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
            _KEYCHAIN_SECRET_CACHE[account] = value
            return
        except Exception:
            pass
    if sys.platform == "darwin":
        sec_bin = "/usr/bin/security" if os.path.isfile("/usr/bin/security") else "security"
        subprocess.run(
            [sec_bin, "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w", value, "-U"],
            capture_output=True,
            check=False,
        )
    _KEYCHAIN_SECRET_CACHE[account] = value


def get_keychain_secret(account: str) -> Optional[str]:
    """Read a secret from OS secure storage, with macOS security(1) fallback.

    Results are cached for the lifetime of the process so repeated config loads do not re-invoke
    keychain backends (avoids multiple macOS prompts per ``cf-ops`` run).
    """
    if account in _KEYCHAIN_SECRET_CACHE:
        return _KEYCHAIN_SECRET_CACHE[account]
    result: Optional[str] = None
    if keyring:
        try:
            result = keyring.get_password(KEYCHAIN_SERVICE, account)
        except Exception:
            result = None
    if result is None and sys.platform == "darwin":
        # Prefer the system binary so Keychain ACLs match what "Always Allow" records for `security`.
        sec_bin = "/usr/bin/security" if os.path.isfile("/usr/bin/security") else "security"
        res = subprocess.run(
            [sec_bin, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            result = res.stdout.strip() or None
    _KEYCHAIN_SECRET_CACHE[account] = result
    return result


# Matches SEC:… account ids embedded in ~/.pas JSON (quoted or unquoted).
_SEC_REF_ACCOUNT_RE = re.compile(r"SEC:([^\"\\s,}\]]+)")

def warm_pas_keychain_cache_from_disk() -> None:
    """Preload ``get_keychain_secret`` for every ``SEC:`` account referenced under ``~/.pas``.

    Reflex dev mode may spawn multiple Python processes (reload / backend workers); each has an
    empty process cache, so without warming, ``_de_secretize`` can invoke Keychain lookups many
    times during startup. Resolving each distinct account once up front keeps later loads on the
    cache. Install ``keyring`` so reads prefer the native backend instead of ``security`` CLI.

    Skips ``~/.pas/skills/**`` (same rule as the console service list).
    """
    root = get_pas_config_dir()
    if not root.is_dir():
        return
    accounts: set[str] = set()
    try:
        for path in root.rglob("*.json"):
            if "skills" in path.parts:
                continue
            try:
                txt = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for m in _SEC_REF_ACCOUNT_RE.finditer(txt):
                acct = (m.group(1) or "").strip()
                if acct:
                    accounts.add(acct)
    except OSError:
        return
    for acct in sorted(accounts):
        get_keychain_secret(acct)

def _needs_migration(data: Any, service: str = "") -> bool:
    if isinstance(data, dict):
        for k, v in data.items():
            if _key_should_use_keychain(k, service) and isinstance(v, str) and not v.startswith("SEC:"):
                return True
            if _needs_migration(v, service):
                return True
    elif isinstance(data, list):
        for item in data:
            if _needs_migration(item, service):
                return True
    return False

def _en_secretize(data: Any, service: str, path: str = "") -> Any:
    if isinstance(data, dict):
        new_dict = {}
        for k, v in data.items():
            full_path = f"{path}.{k}" if path else k
            if _key_should_use_keychain(k, service) and isinstance(v, str) and not v.startswith("SEC:"):
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
            elif k.endswith("_meta") and _key_should_use_keychain(k[:-5], service):
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


def get_console_yaml_backup_keep() -> int:
    """Return how many timestamped ``console.yaml`` backups to retain (rotation cap).

    Reads ``CONSOLE_YAML_BACKUP_KEEP_KEY`` (``console_yaml_backup_keep``) from
    ``~/.pas/pas.json``. Missing, invalid, or non-positive values fall back to
    ``JSON_BACKUP_KEEP_DEFAULT`` (5). Capped at 500. Used with
    ``backup_json_with_timestamp`` when saving ``console.yaml``.
    """
    path = get_pas_config_dir() / "pas.json"
    if not path.is_file():
        return JSON_BACKUP_KEEP_DEFAULT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return JSON_BACKUP_KEEP_DEFAULT
    if not isinstance(data, dict):
        return JSON_BACKUP_KEEP_DEFAULT
    raw = data.get(CONSOLE_YAML_BACKUP_KEEP_KEY)
    if raw is None:
        return JSON_BACKUP_KEEP_DEFAULT
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return JSON_BACKUP_KEEP_DEFAULT
    if n < 1:
        return JSON_BACKUP_KEEP_DEFAULT
    return min(n, 500)


def load_pas_config(service: str, quiet: bool = False, profile: Optional[str] = None) -> Dict[str, Any]:
    config_file = get_pas_config_dir() / f"{service}.json"
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text())
            if _needs_migration(data, service):
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

def run_command(
    cmd: List[str],
    capture_output: bool = True,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            check=False,
            cwd=cwd,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args=cmd, returncode=124, stdout="", stderr="timeout")
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

from .git_utils import (
    _github_url_from_path,
    github_remote_identity_key,
    github_url_for_path,
    normalize_github_remote_url,
    read_git_remote_raw_url,
)
from .git_remote_providers import (
    RemoteKind,
    compare_remote_pins,
    connection_url_to_browse_url,
    infer_remote_kind_from_pinned_url,
    normalize_generic_remote_url,
    read_remote_url_via_git_cli,
    remote_identity_key,
    repo_web_url_for_path,
    resolve_github_canonical_url_via_gh,
    resolve_remote_pin_at_path,
    to_preferred_ssh_remote_url,
)

from .ai_assistant_snippets import (
    AGENTS_MARKER_END,
    AGENTS_MARKER_START,
    AGENTS_REL,
    CURSOR_RULE_REL,
    PAS_AI_ASSISTANT_SUMMARY,
    agents_md_marked_block,
    agents_md_snippet,
    cursor_rule_mdc_snippet,
    validated_project_root,
    write_agents_md,
    write_cursor_rule,
)
from .config import (
    PASProjectConfig,
    GITIGNORE_RULE_PAS_PROJECT_YAML,
    InitPasProjectYamlResult,
    PAS_PROJECT_STANDARD_ENV_KEYS,
    PAS_PROJECT_YAML_FILENAME,
    PAS_PROJECT_YML_FILENAME,
    PAS_TARGET_ROOT_KEY,
    SERVICE_CARD_DETAIL_PRIMARY_FIELDS,
    apply_service_env_patch,
    coerce_environments_dict_keys,
    default_pas_project_document,
    get_environments_list,
    get_environments_list_for_target,
    get_metadata_cards,
    get_service_oriented_data,
    get_service_oriented_data_for_target,
    get_services_refs,
    init_pas_project_yaml,
    is_valid_env_key_slug,
    load_pas_project_config,
    load_pas_project_config_document,
    normalize_env_key,
    resolve_target_env_assignments,
    pas_provider_dev_config_path,
    pas_user_service_config_json_path,
    resolve_pas_project_config_path,
    resolve_pas_provider_dev_config_path,
    save_pas_project_config,
)
from .mtime_display import format_path_mtime_for_display, relative_mtime_ago
from .service_config import (
    build_service_config_view_model,
    check_service_connectivity,
    check_supabase_org_connectivity,
    check_supabase_postgres_connectivity,
    cloudflare_email_for_profile,
    cloudflare_discover_accounts_from_credentials,
    cloudflare_profile_credential_fields,
    cloudflare_profile_health,
    cloudflare_profile_token_storage_hint,
    cloudflare_token_for_profile,
    fetch_supabase_management_project,
    get_cloudflare_capability_options,
    get_cloudflare_profile_options,
    get_cloudflare_toolkit_capabilities,
    get_supabase_profile_options,
    get_supabase_projects_for_profile,
    list_cloudflare_accounts,
    list_cloudflare_resource_options,
    normalize_cloudflare_capability,
    normalize_cloudflare_toolkit_config,
    normalize_supabase_toolkit_config,
    resolve_supabase_database_password_plain,
    reveal_service_secret,
    set_cloudflare_toolkit_profile_api_token,
    set_cloudflare_toolkit_profile_global_api_key,
    set_supabase_toolkit_database_password,
    set_supabase_toolkit_profile_access_token,
    supabase_database_password_storage_hint,
    supabase_profile_token_storage_hint,
)
