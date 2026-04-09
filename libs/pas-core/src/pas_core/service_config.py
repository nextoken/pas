import json
import os
import shutil
import socket
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import _en_secretize, get_keychain_secret, load_pas_config, safe_write_json
from .config import resolve_pas_provider_dev_config_path


def _mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}{'*' * (len(secret) - 8)}{secret[-4:]}"


def _token_storage_hint(raw_value: Any) -> str:
    if isinstance(raw_value, str) and raw_value.startswith("SEC:"):
        return "keyring (SEC ref)"
    if isinstance(raw_value, str) and raw_value.strip():
        return "plain in developer config"
    return "not configured"


def normalize_supabase_toolkit_config(config: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(config or {})
    profiles = out.get("profiles")
    organizations = out.get("organizations")
    if not isinstance(profiles, dict) and isinstance(organizations, dict):
        out["profiles"] = organizations
    if not isinstance(out.get("profiles"), dict):
        out["profiles"] = {}

    active_profile_id = out.get("active_profile_id") or out.get("active_org_id") or out.get("current_profile")
    if not isinstance(active_profile_id, str):
        active_profile_id = ""
    out["active_profile_id"] = active_profile_id
    return out


def _resolve_service_value(service_block: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = service_block.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _yaml_database_password_raw(service_block: Dict[str, Any]) -> str:
    return _resolve_service_value(
        service_block,
        "db_password",
        "database_password",
        "postgres_password",
        "password",
    )


def _read_nested_profile_project_db_password_raw(
    data: Dict[str, Any],
    project_ref: str,
    profile_org_id: str = "",
) -> str:
    """Read db_password from profiles.<org_id>.projects.<project_id> (raw JSON)."""
    ref = (project_ref or "").strip()
    if not ref or not isinstance(data, dict):
        return ""
    pkey = (profile_org_id or "").strip()
    container = _supabase_json_profiles_container_key(data)
    profiles = data.get(container)
    if not isinstance(profiles, dict):
        return ""

    def from_profile(oid: str) -> str:
        prof = profiles.get(oid)
        if not isinstance(prof, dict):
            return ""
        projects = prof.get("projects")
        if not isinstance(projects, dict):
            return ""
        pent = projects.get(ref)
        if not isinstance(pent, dict):
            return ""
        v = pent.get("db_password")
        return str(v).strip() if v is not None else ""

    if pkey:
        got = from_profile(pkey)
        if got:
            return got
    for oid in profiles:
        if oid == pkey:
            continue
        got = from_profile(str(oid))
        if got:
            return got
    return ""


def read_toolkit_project_database_password_raw(
    project_ref: str,
    profile_org_id: str = "",
) -> str:
    """Read DB password from ~/.pas/supabase.json (raw): nested path first, then legacy map."""
    path = Path.home() / ".pas" / "supabase.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text())
    except Exception:
        return ""
    ref = (project_ref or "").strip()
    nested = _read_nested_profile_project_db_password_raw(data, ref, profile_org_id)
    if nested:
        return nested

    m = data.get("project_database_passwords")
    if not isinstance(m, dict):
        return ""
    entry = m.get(ref)
    if isinstance(entry, dict):
        return str(entry.get("database_password") or "")
    if isinstance(entry, str):
        return entry
    return ""


def resolve_supabase_database_password_raw(service_block: Dict[str, Any], project_ref: str) -> str:
    y = _yaml_database_password_raw(service_block)
    if y:
        return y
    org = _resolve_service_value(service_block, "org_id", "organization_id")
    return read_toolkit_project_database_password_raw(project_ref, org)


def supabase_database_password_storage_hint(service_block: Dict[str, Any], project_ref: str) -> str:
    if _yaml_database_password_raw(service_block):
        return "project .pas.yaml"
    org = _resolve_service_value(service_block, "org_id", "organization_id")
    raw = read_toolkit_project_database_password_raw(project_ref, org)
    if raw:
        return _token_storage_hint(raw)
    return "not configured"


def resolve_supabase_database_password_plain(service_block: Dict[str, Any], project_ref: str) -> str:
    raw = resolve_supabase_database_password_raw(service_block, project_ref)
    if not raw:
        return ""
    if raw.startswith("SEC:"):
        return str(get_keychain_secret(raw[4:]) or "")
    return raw


def _supabase_json_profiles_container_key(data: Dict[str, Any]) -> str:
    if isinstance(data.get("profiles"), dict):
        return "profiles"
    if isinstance(data.get("organizations"), dict):
        return "organizations"
    return "profiles"


def set_supabase_toolkit_profile_access_token(profile_id: str, plaintext: str) -> None:
    """Write access_token for a profile/org id into raw ~/.pas/supabase.json (secretized)."""
    pid = (profile_id or "").strip()
    if not pid:
        raise ValueError("profile_id is required")
    path = Path.home() / ".pas" / "supabase.json"
    data: Dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
    key = _supabase_json_profiles_container_key(data)
    profiles = data.get(key)
    if not isinstance(profiles, dict):
        profiles = {}
    prof = profiles.get(pid)
    if not isinstance(prof, dict):
        prof = {}
    prof["access_token"] = plaintext
    profiles[pid] = prof
    data[key] = profiles
    processed = _en_secretize(data, "supabase")
    safe_write_json(path, processed, indent=2)


def set_supabase_toolkit_database_password(
    project_ref: str,
    plaintext: str,
    profile_org_id: str = "",
) -> None:
    """
    Persist DB password under profiles.<org_id>.projects.<project_id>.db_password,
    and mirror under legacy project_database_passwords[project_id].database_password.
    Uses safe_write_json (timestamped backup before write).
    """
    ref = (project_ref or "").strip()
    if not ref:
        raise ValueError("project_ref is required")
    path = Path.home() / ".pas" / "supabase.json"
    data: Dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}

    # Legacy structure (unchanged key layout; still updated for backward compatibility)
    m = data.get("project_database_passwords")
    if not isinstance(m, dict):
        m = {}
    entry = m.get(ref)
    if not isinstance(entry, dict):
        entry = {}
    entry["database_password"] = plaintext
    m[ref] = entry
    data["project_database_passwords"] = m

    # New nested layout: profiles.<org_id>.projects.<project_ref>.db_password
    oid = (profile_org_id or "").strip()
    if oid:
        container = _supabase_json_profiles_container_key(data)
        profiles = data.get(container)
        if not isinstance(profiles, dict):
            profiles = {}
        prof = profiles.get(oid)
        if not isinstance(prof, dict):
            prof = {}
        projects = prof.get("projects")
        if not isinstance(projects, dict):
            projects = {}
        pent = projects.get(ref)
        if not isinstance(pent, dict):
            pent = {}
        pent["db_password"] = plaintext
        projects[ref] = pent
        prof["projects"] = projects
        profiles[oid] = prof
        data[container] = profiles

    processed = _en_secretize(data, "supabase")
    safe_write_json(path, processed, indent=2)


def _supabase_view_model(service_name: str, service_block: Dict[str, Any]) -> Dict[str, Any]:
    config_raw = load_pas_config("supabase", quiet=True)
    config = normalize_supabase_toolkit_config(config_raw)
    profiles = config.get("profiles", {})
    active_profile_id = config.get("active_profile_id", "")
    active_profile = profiles.get(active_profile_id, {}) if isinstance(profiles, dict) else {}
    if not isinstance(active_profile, dict):
        active_profile = {}

    raw_token = active_profile.get("access_token")
    active_profile_name = str(active_profile.get("name") or active_profile.get("org_name") or "")
    project_org_id = _resolve_service_value(service_block, "org_id", "organization_id")
    project_ref = _resolve_service_value(service_block, "project_ref", "project_id")
    project_name = _resolve_service_value(service_block, "project_name", "name")
    anon_key = _resolve_service_value(service_block, "anon_key", "supabase_anon_key")
    service_role_key = _resolve_service_value(service_block, "service_role_key", "supabase_service_role_key")
    env_lines = []
    if project_ref:
        env_lines.append(f"NEXT_PUBLIC_SUPABASE_URL=https://{project_ref}.supabase.co")
        env_lines.append(f"SUPABASE_URL=https://{project_ref}.supabase.co")
    if anon_key:
        env_lines.append(f"NEXT_PUBLIC_SUPABASE_ANON_KEY={anon_key}")
        env_lines.append(f"SUPABASE_ANON_KEY={anon_key}")
    if service_role_key:
        env_lines.append(f"SUPABASE_SERVICE_ROLE_KEY={service_role_key}")

    return {
        "provider": "supabase",
        "service_name": service_name,
        "toolkit_path": str(resolve_pas_provider_dev_config_path("supabase")),
        "active_profile_id": active_profile_id,
        "active_profile_name": active_profile_name,
        "project_org_id": project_org_id,
        "project_ref": project_ref,
        "project_name": project_name,
        "env_preview": "\n".join(env_lines),
        "token_masked": _mask_secret(str(raw_token or "")),
        "token_storage": _token_storage_hint(active_profile.get("access_token")),
        "token_available": bool(raw_token),
    }


def get_supabase_profile_options() -> List[Dict[str, str]]:
    config = normalize_supabase_toolkit_config(load_pas_config("supabase", quiet=True))
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        return []
    out: List[Dict[str, str]] = []
    for profile_id in sorted(profiles.keys()):
        raw = profiles.get(profile_id, {})
        if not isinstance(raw, dict):
            raw = {}
        name = str(raw.get("name") or raw.get("org_name") or profile_id)
        pid = str(profile_id)
        out.append(
            {
                "id": pid,
                "name": name,
                "display_label": f"{name} · {pid}",
            }
        )
    return out


def supabase_profile_token_storage_hint(profile_id: str) -> str:
    """Hint based on on-disk JSON (preserves SEC: refs before de-secretize)."""
    path = Path.home() / ".pas" / "supabase.json"
    if not path.is_file():
        return "not configured"
    try:
        data = json.loads(path.read_text())
    except Exception:
        return "not configured"
    profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else None
    if profiles is None and isinstance(data.get("organizations"), dict):
        profiles = data["organizations"]
    if not isinstance(profiles, dict):
        return "not configured"
    raw_prof = profiles.get((profile_id or "").strip(), {})
    if not isinstance(raw_prof, dict):
        return "not configured"
    return _token_storage_hint(raw_prof.get("access_token"))


def _list_supabase_projects_cli(token: str, expected_org_id: str = "") -> List[Dict[str, str]]:
    supabase_bin = shutil.which("supabase")
    if not supabase_bin or not token:
        return []
    env = dict(os.environ)
    env["SUPABASE_ACCESS_TOKEN"] = token
    cmd = [supabase_bin, "projects", "list", "--output", "json"]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
        env=env,
    )
    if res.returncode != 0:
        return []
    try:
        payload = json.loads(res.stdout or "[]")
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    out: List[Dict[str, str]] = []
    expected = (expected_org_id or "").strip()
    for item in payload:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("id") or "").strip()
        if not ref:
            continue
        pn = str(item.get("name") or "").strip()
        org_id = str(item.get("organization_id") or "").strip()
        if expected and org_id != expected:
            continue
        out.append(
            {
                "project_ref": ref,
                "project_name": pn,
                "org_id": org_id,
                "display_label": f"{pn} · {ref}" if pn else ref,
            }
        )
    return out


def get_supabase_projects_for_profile(profile_id: str) -> List[Dict[str, str]]:
    config = normalize_supabase_toolkit_config(load_pas_config("supabase", quiet=True))
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        return []
    profile = profiles.get(profile_id, {})
    if not isinstance(profile, dict):
        return []
    token = str(profile.get("access_token") or "")
    expected_org_id = (profile_id or "").strip()
    projects = _list_supabase_projects_cli(token, expected_org_id=expected_org_id)
    if projects:
        return projects

    # HTTP fallback if CLI is unavailable
    if not token:
        return []
    url = "https://api.supabase.com/v1/projects"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = json.loads(response.read().decode())
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    expected = expected_org_id
    for item in raw:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("id") or "").strip()
        if not ref:
            continue
        pn = str(item.get("name") or "").strip()
        org_id = str(item.get("organization_id") or "").strip()
        if expected and org_id != expected:
            continue
        out.append(
            {
                "project_ref": ref,
                "project_name": pn,
                "org_id": org_id,
                "display_label": f"{pn} · {ref}" if pn else ref,
            }
        )
    return out


def _trim_identifier_for_display(raw: str, head: int = 6, tail: int = 4) -> str:
    s = (raw or "").strip()
    if len(s) <= head + tail + 1:
        return s
    return f"{s[:head]}…{s[-tail:]}"


def _format_display_name_with_trimmed_id(
    display_name: str, id_str: str, *, sep: str = " · "
) -> str:
    trimmed = _trim_identifier_for_display(id_str)
    name = (display_name or "").strip()
    if name:
        return f"{name}{sep}{trimmed}"
    return trimmed


def _supabase_org_display_name_from_toolkit(org_id: str) -> str:
    oid = (org_id or "").strip()
    if not oid:
        return ""
    config = normalize_supabase_toolkit_config(load_pas_config("supabase", quiet=True))
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        return ""
    prof = profiles.get(oid, {})
    if not isinstance(prof, dict):
        return ""
    return str(prof.get("name") or prof.get("org_name") or "").strip()


def _supabase_access_token_for_org(org_id: str) -> str:
    oid = (org_id or "").strip()
    if not oid:
        return ""
    config = normalize_supabase_toolkit_config(load_pas_config("supabase", quiet=True))
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        return ""
    prof = profiles.get(oid, {})
    if not isinstance(prof, dict):
        return ""
    return str(prof.get("access_token") or "").strip()


def _supabase_management_get_json(subpath: str, token: str, *, timeout: float = 15) -> Any:
    """GET https://api.supabase.com/v1/{subpath} with bearer token; return parsed JSON or None."""
    tok = (token or "").strip()
    if not tok:
        return None
    path = subpath.lstrip("/")
    url = f"https://api.supabase.com/v1/{path}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "pas-console/1.0 (Supabase Management API)")
    ctx = ssl._create_unverified_context() if os.environ.get("VERIFY_SSL") == "false" else None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode())
    except Exception:
        return None


def fetch_supabase_management_project(
    token: str, project_ref: str, *, timeout: float = 15
) -> Dict[str, Any]:
    """Return the project object from ``GET /v1/projects/{ref}``, or ``{}`` if unavailable.

    Requires a valid Supabase personal access token (same as CLI / Management API).
    The response includes ``region`` when the call succeeds.
    """
    ref = (project_ref or "").strip()
    if not ref:
        return {}
    raw = _supabase_management_get_json(f"projects/{ref}", token, timeout=timeout)
    return raw if isinstance(raw, dict) else {}


def enrich_supabase_service_card_base_info(base_info_display: Dict[str, str]) -> None:
    """Mutate dashboard card ``base_info`` for Supabase: richer org/project lines; dynamic region.

    * **Org ID** shows ``<name> · <trimmed id>`` when the toolkit profile has a name.
    * **Project** shows ``<project-name> . <trimmed ref>``. The name comes from YAML
      (``project_name`` / ``name``) when set; otherwise from the same Management API
      ``GET /v1/projects/{ref}`` used for region (matches gear-modal project metadata), then
      from the project list for the org profile if needed.
    * **Region** is set from that project response when the call succeeds; YAML ``region`` is
      overwritten on success. Without a token, only YAML ``region`` (if present) applies.
    """
    oid = (base_info_display.get("org_id") or base_info_display.get("organization_id") or "").strip()
    pref = (base_info_display.get("project_ref") or base_info_display.get("project_id") or "").strip()
    yaml_project_name = (base_info_display.get("project_name") or base_info_display.get("name") or "").strip()

    org_label = _supabase_org_display_name_from_toolkit(oid)
    project_label = yaml_project_name
    token = _supabase_access_token_for_org(oid)

    details: Dict[str, Any] = {}
    if pref and token:
        # One call: name + region (same payload as gear modal / Test Project API).
        details = fetch_supabase_management_project(token, pref, timeout=5.0)

    if not project_label and pref:
        api_name = str(details.get("name") or "").strip()
        if api_name:
            project_label = api_name

    if not project_label and oid and pref and token:
        for row in get_supabase_projects_for_profile(oid):
            if row.get("project_ref") == pref:
                project_label = (row.get("project_name") or "").strip()
                break

    if oid:
        base_info_display["org_id"] = _format_display_name_with_trimmed_id(org_label, oid)
        raw_org = (base_info_display.get("organization_id") or "").strip()
        if raw_org == oid:
            base_info_display.pop("organization_id", None)
    if pref:
        base_info_display["project_ref"] = _format_display_name_with_trimmed_id(
            project_label, pref, sep=" . "
        )
        raw_pid = (base_info_display.get("project_id") or "").strip()
        if raw_pid == pref:
            base_info_display.pop("project_id", None)

    if pref and token:
        region = str(details.get("region") or "").strip()
        if region:
            base_info_display["region"] = region


# --- Cloudflare toolkit / API (PAS Console provider config) --------------------------------------

DEFAULT_CLOUDFLARE_TOOLKIT_CAPABILITIES: List[str] = [
    "frontend",
    "worker",
    "messaging",
    "storage",
    "network",
]

CLOUDFLARE_CAPABILITY_LABELS: Dict[str, str] = {
    "frontend": "Frontend (Pages)",
    "worker": "Worker (script)",
    "messaging": "Messaging (Queues)",
    "storage": "Storage (R2)",
    "network": "Network (Tunnel)",
    "domain": "Domain (DNS zone)",
}


def normalize_cloudflare_toolkit_config(config: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(config or {})
    profiles = out.get("profiles")
    if not isinstance(profiles, dict):
        out["profiles"] = {}
    active = (
        out.get("active_profile_id")
        or out.get("current_profile")
        or ""
    )
    out["active_profile_id"] = str(active) if isinstance(active, str) else ""
    caps = out.get("capabilities")
    if not isinstance(caps, list):
        caps = []
    out["capabilities"] = [str(x).strip() for x in caps if str(x).strip()]
    return out


def get_cloudflare_toolkit_capabilities() -> List[str]:
    cfg = normalize_cloudflare_toolkit_config(load_pas_config("cloudflare", quiet=True))
    got = cfg.get("capabilities", [])
    if isinstance(got, list) and got:
        return [str(x).strip() for x in got if str(x).strip()]
    return list(DEFAULT_CLOUDFLARE_TOOLKIT_CAPABILITIES)


def normalize_cloudflare_capability(raw: str, allowed: List[str]) -> str:
    r = (raw or "").strip()
    if r and r in allowed:
        return r
    return str(allowed[0]).strip() if allowed else ""


def get_cloudflare_capability_options() -> List[Dict[str, str]]:
    """Select options: id = capability slug, display_label = human text."""
    out: List[Dict[str, str]] = []
    for cap in get_cloudflare_toolkit_capabilities():
        label = CLOUDFLARE_CAPABILITY_LABELS.get(cap, cap.replace("_", " ").title())
        out.append({"id": cap, "display_label": label})
    return out


def get_cloudflare_profile_options() -> List[Dict[str, str]]:
    cfg = normalize_cloudflare_toolkit_config(load_pas_config("cloudflare", quiet=True))
    profiles = cfg.get("profiles", {})
    if not isinstance(profiles, dict):
        return []
    out: List[Dict[str, str]] = []
    for profile_id in sorted(profiles.keys()):
        raw = profiles.get(profile_id, {})
        if not isinstance(raw, dict):
            raw = {}
        name = str(raw.get("name") or profile_id)
        pid = str(profile_id)
        out.append({"id": pid, "name": name, "display_label": f"{name} · {pid}"})
    return out


def _cloudflare_profile_dict(config: Dict[str, Any], profile_id: str) -> Dict[str, Any]:
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        return {}
    p = profiles.get((profile_id or "").strip(), {})
    return dict(p) if isinstance(p, dict) else {}


def cloudflare_token_for_profile(profile_id: str) -> str:
    cfg = normalize_cloudflare_toolkit_config(load_pas_config("cloudflare", quiet=True))
    prof = _cloudflare_profile_dict(cfg, profile_id)
    return str(prof.get("CLOUDFLARE_API_TOKEN") or "").strip()


def cloudflare_profile_token_storage_hint(profile_id: str) -> str:
    path = Path.home() / ".pas" / "cloudflare.json"
    if not path.is_file():
        return "not configured"
    try:
        data = json.loads(path.read_text())
    except Exception:
        return "not configured"
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        return "not configured"
    raw_prof = profiles.get((profile_id or "").strip(), {})
    if not isinstance(raw_prof, dict):
        return "not configured"
    return _token_storage_hint(raw_prof.get("CLOUDFLARE_API_TOKEN"))


def set_cloudflare_toolkit_profile_api_token(profile_id: str, plaintext: str) -> None:
    pid = (profile_id or "").strip()
    if not pid:
        raise ValueError("profile_id is required")
    path = Path.home() / ".pas" / "cloudflare.json"
    data: Dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
    if "profiles" not in data or not isinstance(data.get("profiles"), dict):
        data["profiles"] = {}
    profiles = data["profiles"]
    prof = profiles.get(pid)
    if not isinstance(prof, dict):
        prof = {}
    prof["CLOUDFLARE_API_TOKEN"] = plaintext
    profiles[pid] = prof
    data["profiles"] = profiles
    processed = _en_secretize(data, "cloudflare")
    safe_write_json(path, processed, indent=2)


def _cloudflare_ssl_context():
    return ssl._create_unverified_context() if os.environ.get("VERIFY_SSL") == "false" else None


def _cloudflare_api_request_json(
    method: str,
    path: str,
    token: str,
    *,
    timeout: float = 20.0,
) -> tuple[Optional[Any], str]:
    tok = (token or "").strip()
    if not tok:
        return None, "No API token configured."
    p = path.lstrip("/")
    url = f"https://api.cloudflare.com/client/v4/{p}"
    req = urllib.request.Request(url, method=method.upper())
    req.add_header("Authorization", f"Bearer {tok}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "pas-console/1.0 (Cloudflare API)")
    ctx = _cloudflare_ssl_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            raw = json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = {}
        errs = body.get("errors") if isinstance(body, dict) else None
        if isinstance(errs, list) and errs:
            em = str((errs[0] or {}).get("message") or e.reason)
            return None, f"HTTP {e.code}: {em}"
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, str(e)
    if not isinstance(raw, dict):
        return None, "Unexpected API response shape"
    if raw.get("success") is False:
        errs = raw.get("errors")
        if isinstance(errs, list) and errs:
            return None, str((errs[0] or {}).get("message") or "API error")
        return None, "Cloudflare API success=false"
    return raw.get("result"), ""


def list_cloudflare_accounts(profile_id: str) -> List[Dict[str, str]]:
    token = cloudflare_token_for_profile(profile_id)
    cfg = normalize_cloudflare_toolkit_config(load_pas_config("cloudflare", quiet=True))
    prof = _cloudflare_profile_dict(cfg, profile_id)
    pinned = str(prof.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()

    result, err = _cloudflare_api_request_json("GET", "accounts", token)
    out: List[Dict[str, str]] = []
    if isinstance(result, list):
        for item in result:
            if not isinstance(item, dict):
                continue
            aid = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip() or aid
            if not aid:
                continue
            out.append(
                {
                    "id": aid,
                    "name": name,
                    "display_label": f"{name} · {aid}",
                }
            )
    if out:
        return out
    if pinned and not err:
        return [{"id": pinned, "name": pinned, "display_label": f"{pinned} · (from profile)"}]
    if pinned:
        return [{"id": pinned, "name": pinned, "display_label": f"{pinned} · (from profile, list failed)"}]
    return []


def _format_cf_resource_label(name: str, rid: str) -> str:
    n = (name or "").strip()
    r = (rid or "").strip()
    if n and r and n != r:
        return f"{n} · {r}"
    return n or r or ""


def list_cloudflare_pages_projects(profile_id: str, account_id: str) -> List[Dict[str, str]]:
    token = cloudflare_token_for_profile(profile_id)
    aid = (account_id or "").strip()
    if not aid:
        return []
    result, _err = _cloudflare_api_request_json(
        "GET", f"accounts/{aid}/pages/projects", token
    )
    out: List[Dict[str, str]] = []
    if not isinstance(result, list):
        return out
    for item in result:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        pid = str(item.get("id") or "").strip()
        if not name:
            continue
        out.append(
            {
                "resource_key": name,
                "project_ref": name,
                "project_name": name,
                "pages_project_id": pid,
                "display_label": _format_cf_resource_label(name, pid) or name,
            }
        )
    return out


def list_cloudflare_worker_scripts(profile_id: str, account_id: str) -> List[Dict[str, str]]:
    token = cloudflare_token_for_profile(profile_id)
    aid = (account_id or "").strip()
    if not aid:
        return []
    result, _err = _cloudflare_api_request_json(
        "GET", f"accounts/{aid}/workers/scripts", token
    )
    out: List[Dict[str, str]] = []
    if not isinstance(result, list):
        return out
    for item in result:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        if not sid:
            continue
        out.append(
            {
                "resource_key": sid,
                "worker_script_name": sid,
                "display_label": sid,
            }
        )
    return out


def list_cloudflare_queues(profile_id: str, account_id: str) -> List[Dict[str, str]]:
    token = cloudflare_token_for_profile(profile_id)
    aid = (account_id or "").strip()
    if not aid:
        return []
    result, _err = _cloudflare_api_request_json("GET", f"accounts/{aid}/queues", token)
    out: List[Dict[str, str]] = []
    if not isinstance(result, list):
        return out
    for item in result:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("queue_id") or "").strip()
        qname = str(item.get("queue_name") or "").strip()
        if not qid and not qname:
            continue
        key = qid or qname
        out.append(
            {
                "resource_key": key,
                "queue_id": qid,
                "queue_name": qname or qid,
                "display_label": _format_cf_resource_label(qname, qid) or key,
            }
        )
    return out


def list_cloudflare_r2_buckets(profile_id: str, account_id: str) -> List[Dict[str, str]]:
    token = cloudflare_token_for_profile(profile_id)
    aid = (account_id or "").strip()
    if not aid:
        return []
    raw, _err = _cloudflare_api_request_json("GET", f"accounts/{aid}/r2/buckets", token)
    out: List[Dict[str, str]] = []
    buckets: Any = None
    if isinstance(raw, dict):
        buckets = raw.get("buckets")
    if not isinstance(buckets, list):
        return out
    for item in buckets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        loc = str(item.get("location") or "").strip()
        label = f"{name} ({loc})" if loc else name
        out.append(
            {
                "resource_key": name,
                "r2_bucket_name": name,
                "display_label": label,
            }
        )
    return out


def list_cloudflare_tunnels(profile_id: str, account_id: str) -> List[Dict[str, str]]:
    token = cloudflare_token_for_profile(profile_id)
    aid = (account_id or "").strip()
    if not aid:
        return []
    result, _err = _cloudflare_api_request_json("GET", f"accounts/{aid}/tunnels", token)
    out: List[Dict[str, str]] = []
    if not isinstance(result, list):
        return out
    for item in result:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip() or tid
        if not tid:
            continue
        out.append(
            {
                "resource_key": tid,
                "tunnel_id": tid,
                "tunnel_name": name,
                "display_label": _format_cf_resource_label(name, tid),
            }
        )
    return out


def list_cloudflare_zones(profile_id: str, account_id: str) -> List[Dict[str, str]]:
    token = cloudflare_token_for_profile(profile_id)
    aid = (account_id or "").strip()
    if not aid:
        return []
    result, _err = _cloudflare_api_request_json("GET", "zones", token)
    out: List[Dict[str, str]] = []
    if not isinstance(result, list):
        return out
    for item in result:
        if not isinstance(item, dict):
            continue
        zid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        acct = item.get("account")
        acct_id = ""
        if isinstance(acct, dict):
            acct_id = str(acct.get("id") or "").strip()
        if acct_id and acct_id != aid:
            continue
        if not zid:
            continue
        out.append(
            {
                "resource_key": zid,
                "zone_id": zid,
                "zone_name": name or zid,
                "display_label": _format_cf_resource_label(name, zid),
            }
        )
    return out


def list_cloudflare_resource_options(
    capability: str,
    profile_id: str,
    account_id: str,
) -> List[Dict[str, str]]:
    cap = (capability or "").strip().lower()
    if cap == "frontend":
        return list_cloudflare_pages_projects(profile_id, account_id)
    if cap == "worker":
        return list_cloudflare_worker_scripts(profile_id, account_id)
    if cap == "messaging":
        return list_cloudflare_queues(profile_id, account_id)
    if cap == "storage":
        return list_cloudflare_r2_buckets(profile_id, account_id)
    if cap == "network":
        return list_cloudflare_tunnels(profile_id, account_id)
    if cap == "domain":
        return list_cloudflare_zones(profile_id, account_id)
    return []


def _cloudflare_resource_key_from_yaml(capability: str, block: Dict[str, Any]) -> str:
    cap = (capability or "").strip().lower()
    if cap == "frontend":
        return _resolve_service_value(block, "project_name", "project_ref", "name")
    if cap == "worker":
        return _resolve_service_value(block, "worker_script_name")
    if cap == "messaging":
        qid = _resolve_service_value(block, "queue_id")
        if qid:
            return qid
        return _resolve_service_value(block, "queue_name")
    if cap == "storage":
        return _resolve_service_value(block, "r2_bucket_name")
    if cap == "network":
        return _resolve_service_value(block, "tunnel_id")
    if cap == "domain":
        return _resolve_service_value(block, "zone_id")
    return ""


def _cloudflare_view_model(service_name: str, service_block: Dict[str, Any]) -> Dict[str, Any]:
    cfg = normalize_cloudflare_toolkit_config(load_pas_config("cloudflare", quiet=True))
    profiles = cfg.get("profiles", {})
    active_profile_id = str(cfg.get("active_profile_id") or "")
    active_profile = _cloudflare_profile_dict(cfg, active_profile_id)
    raw_token = active_profile.get("CLOUDFLARE_API_TOKEN")
    toolkit_caps = get_cloudflare_toolkit_capabilities()

    connection_id = _resolve_service_value(service_block, "connection_id", "org_id")
    account_id = _resolve_service_value(service_block, "account_id")
    capability = normalize_cloudflare_capability(
        _resolve_service_value(service_block, "cloudflare_capability", "capability"),
        toolkit_caps,
    )
    resource_key = _cloudflare_resource_key_from_yaml(capability, service_block)

    return {
        "provider": "cloudflare",
        "service_name": service_name,
        "toolkit_path": str(resolve_pas_provider_dev_config_path("cloudflare")),
        "toolkit_capabilities": toolkit_caps,
        "active_profile_id": active_profile_id,
        "active_profile_name": str(active_profile.get("name") or ""),
        "connection_id": connection_id,
        "account_id": account_id,
        "cloudflare_capability": capability,
        "cloudflare_resource_key": resource_key,
        "project_name": _resolve_service_value(service_block, "project_name", "project_ref", "name"),
        "pages_project_id": _resolve_service_value(service_block, "pages_project_id"),
        "worker_script_name": _resolve_service_value(service_block, "worker_script_name"),
        "queue_name": _resolve_service_value(service_block, "queue_name"),
        "queue_id": _resolve_service_value(service_block, "queue_id"),
        "r2_bucket_name": _resolve_service_value(service_block, "r2_bucket_name"),
        "tunnel_name": _resolve_service_value(service_block, "tunnel_name"),
        "tunnel_id": _resolve_service_value(service_block, "tunnel_id"),
        "zone_name": _resolve_service_value(service_block, "zone_name"),
        "zone_id": _resolve_service_value(service_block, "zone_id"),
        "env_preview": "",
        "token_masked": _mask_secret(str(raw_token or "")),
        "token_storage": _token_storage_hint(active_profile.get("CLOUDFLARE_API_TOKEN")),
        "token_available": bool(str(raw_token or "").strip()),
    }


def build_service_config_view_model(
    provider: str,
    service_name: str,
    service_block: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    p = (provider or "").strip().lower()
    if p == "supabase":
        return _supabase_view_model(service_name, service_block or {})
    if p == "cloudflare":
        return _cloudflare_view_model(service_name, service_block or {})
    return {
        "provider": p or "unknown",
        "service_name": service_name,
        "toolkit_path": str(resolve_pas_provider_dev_config_path(p or "service")),
    }


def reveal_service_secret(provider: str, view_model: Dict[str, Any]) -> str:
    """
    Resolve Supabase Management API bearer token (``SupabaseManager.get_active_token`` / ``get_token_for_org``).

    Tries profile ``access_token`` in order: ``view_model["active_profile_id"]`` (e.g. gear picker),
    then ``project_org_id`` / ``org_id`` / ``organization_id`` (toolkit YAML), then config
    ``active_profile_id``, then ``SUPABASE_ACCESS_TOKEN``. First non-empty wins. Needed so
    ``GET …/database/pooler`` runs and DB checks get multiple ``psql`` host candidates.

    For ``cloudflare``, resolves ``CLOUDFLARE_API_TOKEN`` from the selected toolkit profile
    (modal ``active_profile_id``), then YAML ``connection_id``, then toolkit default profile,
    then ``CLOUDFLARE_API_TOKEN`` env.
    """
    p = (provider or "").strip().lower()
    if p == "cloudflare":
        cfg = normalize_cloudflare_toolkit_config(load_pas_config("cloudflare", quiet=True))
        profiles = cfg.get("profiles", {})
        if not isinstance(profiles, dict):
            return os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()

        def token_for_profile_id(pid: str) -> str:
            prof = profiles.get((pid or "").strip(), {})
            if not isinstance(prof, dict):
                return ""
            return str(prof.get("CLOUDFLARE_API_TOKEN") or "").strip()

        vm_active = str(view_model.get("active_profile_id") or "").strip()
        conn = str(view_model.get("connection_id") or "").strip()
        cfg_active = str(cfg.get("active_profile_id") or "").strip()
        for pid in (vm_active, conn, cfg_active):
            if not pid:
                continue
            t = token_for_profile_id(pid)
            if t:
                return t
        return os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()

    if p != "supabase":
        return ""
    config = normalize_supabase_toolkit_config(load_pas_config("supabase", quiet=True))
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        return os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()

    def token_for_profile_id(pid: str) -> str:
        prof = profiles.get((pid or "").strip(), {})
        if not isinstance(prof, dict):
            return ""
        return str(prof.get("access_token") or "").strip()

    vm_active = str(view_model.get("active_profile_id") or "").strip()
    project_org = str(
        view_model.get("project_org_id")
        or view_model.get("org_id")
        or view_model.get("organization_id")
        or ""
    ).strip()
    cfg_active = str(config.get("active_profile_id") or "").strip()

    for pid in (vm_active, project_org, cfg_active):
        if not pid:
            continue
        t = token_for_profile_id(pid)
        if t:
            return t

    return os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()


def _supabase_api_request(endpoint: str, token: str) -> tuple[bool, str]:
    if not token:
        return False, "No access token configured."
    url = f"https://api.supabase.com/v1/{endpoint.lstrip('/')}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "pas-console/1.0 (Supabase Management API)")
    ctx = ssl._create_unverified_context() if os.environ.get("VERIFY_SSL") == "false" else None
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as response:
            return response.status == 200, f"HTTP {response.status}"
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return False, "HTTP 403 (token likely lacks Management API access or scope)"
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)


def _check_supabase_connectivity_cli(project_ref: str, token: str) -> tuple[bool, str]:
    supabase_bin = shutil.which("supabase")
    if not supabase_bin:
        return False, "Supabase CLI not found"
    if not token:
        return False, "No SUPABASE_ACCESS_TOKEN configured"

    env = dict(os.environ)
    env["SUPABASE_ACCESS_TOKEN"] = token
    cmd = [supabase_bin, "projects", "list", "--output", "json"]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
        env=env,
    )
    if res.returncode != 0:
        stderr = (res.stderr or "").strip()
        return False, f"CLI error: {stderr or f'exit {res.returncode}'}"

    try:
        payload = json.loads(res.stdout or "[]")
    except Exception:
        return False, "CLI returned non-JSON output"
    if not isinstance(payload, list):
        return False, "CLI returned unexpected payload"

    for item in payload:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == project_ref:
            return True, "Validated with Supabase CLI"
    return False, "Project not found in Supabase CLI account context"


def check_supabase_org_connectivity(view_model: Dict[str, Any]) -> Dict[str, str]:
    """
    Validate the resolved org access token against the Management API (no project ref required).

    Uses ``GET /v1/projects`` — a working response means the PAT can call the same API used
    for pooler discovery. Pair with :func:`check_service_connectivity` for a specific project.
    """
    token = reveal_service_secret("supabase", view_model)
    if not (token or "").strip():
        return {
            "status": "error",
            "message": "No access token (store a PAT for this profile or set SUPABASE_ACCESS_TOKEN).",
        }
    ok, msg = _supabase_api_request("projects", token)
    if ok:
        return {
            "status": "ok",
            "message": "Management API reachable; org access token is valid.",
        }
    return {"status": "error", "message": f"Org token check failed: {msg}"}


def check_service_connectivity(provider: str, view_model: Dict[str, Any]) -> Dict[str, str]:
    p = (provider or "").strip().lower()
    if p != "supabase":
        return {"status": "unsupported", "message": "Connectivity check unavailable for this provider."}

    token = reveal_service_secret("supabase", view_model)
    project_ref = str(view_model.get("project_ref") or "").strip()
    if not project_ref:
        return {"status": "error", "message": "Missing project_ref in project service block."}

    cli_ok, cli_msg = _check_supabase_connectivity_cli(project_ref, token)
    if cli_ok:
        return {"status": "ok", "message": f"Connected to project '{project_ref}' ({cli_msg})."}

    ok, msg = _supabase_api_request(f"projects/{project_ref}", token)
    if ok:
        return {"status": "ok", "message": f"Connected to project '{project_ref}' (HTTP fallback)."}

    detail = f"CLI: {cli_msg}; HTTP: {msg}"
    return {"status": "error", "message": f"Supabase check failed for '{project_ref}'. {detail}"}


def _supabase_default_db_host(project_ref: str) -> str:
    ref = (project_ref or "").strip()
    if not ref:
        return ""
    return f"db.{ref}.supabase.co"


def _parse_positive_int(raw: str, default: int) -> int:
    try:
        return max(1, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def _tcp_check(host: str, port: int, timeout: float = 8.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"TCP {host}:{port} reachable"
    except OSError as e:
        return False, str(e)


def _dns_ipv4_addresses(hostname: str) -> List[str]:
    """Resolved IPv4 addresses for hostname (stable order)."""
    hn = (hostname or "").strip()
    if not hn:
        return []
    try:
        infos = socket.getaddrinfo(hn, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return []
    out: List[str] = []
    seen: set[str] = set()
    for _fa, _ty, _pr, _cn, sockaddr in infos:
        ip = str(sockaddr[0])
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


def _tcp_check_with_ipv4_fallback(host: str, port: int, timeout: float = 8.0) -> tuple[bool, str]:
    """Try each IPv4 A record, then hostname (may prefer IPv6)."""
    last_err = ""
    for ip in _dns_ipv4_addresses(host):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True, f"TCP {ip}:{port} reachable (IPv4)"
        except OSError as e:
            last_err = str(e)
    ok, msg = _tcp_check(host, port, timeout=timeout)
    if ok:
        return ok, msg
    if last_err:
        return False, f"{msg}; IPv4 attempts: {last_err}"
    return False, msg


# --- Supabase Postgres connectivity (mirrors trustloop dev-console SupabaseManager) ---------------
#
# Correct path for a reliable "Test database" when direct ``db.<ref>.supabase.co`` fails (e.g.
# IPv6 resolved but no route): load **Management API pooler hosts** so ``psql`` tries more than
# one candidate. That requires a Supabase access token (PAT) tied to the project's org.
#
# Token resolution order: ``reveal_service_secret`` / YAML — (1) gear ``active_profile_id``,
# (2) ``project_org_id`` / ``org_id`` from toolkit YAML, (3) ``active_profile_id`` in
# ``~/.pas/supabase.json``, (4) ``SUPABASE_ACCESS_TOKEN``. Service YAML may also set
# ``access_token`` / ``supabase_access_token`` (merged in ``check_supabase_postgres_connectivity``).
#
# Pooler list: ``GET /v1/projects/{ref}/config/database/pooler`` (PRIMARY rows first), then legacy
# ``.../pooling`` if no rows. Scope ``database_pooling_config_read`` (or full management) on the PAT.
#
# DoH ``PGHOSTADDR`` retry applies only to **DNS-shaped** psql stderr (not "no route to host" on
# IPv6); dev-console behaves the same — pooler candidates are the main fix for IPv6-only breakage.


def _find_psql_binary() -> Optional[str]:
    """Find psql (GUI apps often ship a minimal PATH)."""
    candidates = [
        "/opt/homebrew/opt/libpq/bin/psql",
        "/opt/homebrew/bin/psql",
        "/usr/local/opt/libpq/bin/psql",
        "/usr/local/bin/psql",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return shutil.which("psql")


def _resolve_a_via_public_dns(hostname: str) -> Optional[str]:
    """One IPv4 (A) via Cloudflare DoH (SupabaseManager._resolve_a_via_public_dns parity)."""
    try:
        hn = (hostname or "").strip()
        if not hn:
            return None
        qs = urllib.parse.urlencode({"name": hn, "type": "A"})
        url = f"https://cloudflare-dns.com/dns-query?{qs}"
        req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
        ctx = ssl._create_unverified_context() if os.environ.get("VERIFY_SSL") == "false" else None
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        for ans in data.get("Answer", []) or []:
            if ans.get("type") == 1 and ans.get("data"):
                return str(ans["data"]).strip()
    except Exception:
        pass
    return None


def _stderr_looks_like_dns_failure(stderr: str) -> bool:
    s = (stderr or "").lower()
    return (
        "could not translate host name" in s
        or "nodename nor servname" in s
        or "name or service not known" in s
        or "temporary failure in name resolution" in s
        or "getaddrinfo failed" in s
    )


def _psql_libpq_env(
    password: str,
    host: str,
    port: int,
    user: str,
    dbname: str,
    hostaddr: Optional[str] = None,
) -> Dict[str, str]:
    """libpq env for psql; PGHOST + optional PGHOSTADDR preserves TLS SNI / cert hostname."""
    env = dict(os.environ)
    env["PGPASSWORD"] = password
    env["PGHOST"] = host
    env["PGPORT"] = str(port)
    env["PGUSER"] = user
    env["PGDATABASE"] = dbname
    env["PGSSLMODE"] = "require"
    if hostaddr:
        env["PGHOSTADDR"] = hostaddr
    else:
        env.pop("PGHOSTADDR", None)
    return env


def _supabase_management_api_get_json(endpoint: str, token: str) -> tuple[Optional[Any], str]:
    """
    GET JSON from Supabase Management API (parity with dev-console ``api_request``).
    Returns ``(payload, "")`` on success, or ``(None, short_error_message)`` on failure.
    """
    if not (token or "").strip():
        return None, ""
    url = f"https://api.supabase.com/v1/{endpoint.lstrip('/')}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "pas-console/1.0 (Supabase Management API)")
    ctx = ssl._create_unverified_context() if os.environ.get("VERIFY_SSL") == "false" else None
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as response:
            return json.loads(response.read().decode()), ""
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = (e.read().decode(errors="replace") or "")[:500]
        except Exception:
            pass
        return None, f"HTTP {e.code} {e.reason}: {body}".strip()
    except Exception as e:
        return None, str(e)[:500]


def _parse_pooling_connection_host_port(pool_json: Any) -> Optional[tuple[str, int]]:
    if not isinstance(pool_json, dict):
        return None
    for key in ("connection_string", "connectionString"):
        cs = pool_json.get(key)
        if isinstance(cs, str) and "@" in cs:
            try:
                tail = cs.split("@")[-1].split("/")[0].strip()
                if ":" in tail:
                    h, ps = tail.rsplit(":", 1)
                    return h.strip(), int(ps)
                return tail, 5432
            except (ValueError, IndexError):
                pass
    return None


def _supabase_psql_connection_candidates(
    project_ref: str,
    token: str,
    direct_host: str,
    direct_port: int,
    direct_user: str,
    direct_db: str,
) -> tuple[List[Tuple[str, int, str, str]], str]:
    """
    Same shape as ``SupabaseManager.get_database_connection_candidates``: canonical
    ``db.{ref}.supabase.co`` + ``postgres`` first, then YAML-derived row if it differs, then
    pooler API (PRIMARY first) and legacy pooling fallback.

    Second return value is a short diagnostic when the token is set but pooler rows could not
    be loaded (HTTP error text), for user-facing messages.
    """
    ref = (project_ref or "").strip()
    seen: set[tuple[str, int, str, str]] = set()
    out: List[Tuple[str, int, str, str]] = []
    api_notes: List[str] = []

    def add(h: str, p: int, u: str, d: str) -> None:
        h = (h or "").strip()
        if not h:
            return
        key = (h, p, u, d)
        if key in seen:
            return
        seen.add(key)
        out.append(key)

    add(_supabase_default_db_host(ref), 5432, "postgres", "postgres")
    add(direct_host, max(1, direct_port), direct_user, direct_db)

    tok = (token or "").strip()
    if tok:
        pooler_rows_added = 0
        data, perr = _supabase_management_api_get_json(
            f"projects/{ref}/config/database/pooler", tok
        )
        if perr:
            api_notes.append(f"GET …/database/pooler: {perr}")
        elif isinstance(data, list) and len(data) == 0:
            api_notes.append("GET …/database/pooler: empty list (wrong project ref or token org?)")
        if isinstance(data, list):
            primary = [
                p
                for p in data
                if isinstance(p, dict) and (p.get("database_type") or "").upper() == "PRIMARY"
            ]
            rest = [p for p in data if isinstance(p, dict) and p not in primary]
            before = len(out)
            for item in primary + rest:
                h = str(item.get("db_host") or "").strip()
                try:
                    po = int(item.get("db_port") if item.get("db_port") is not None else 5432)
                except (TypeError, ValueError):
                    po = 5432
                u = (str(item.get("db_user") or "postgres").strip() or "postgres")
                dbn = (str(item.get("db_name") or "postgres").strip() or "postgres")
                if h:
                    add(h, max(1, po), u, dbn)
                    continue
                cs = item.get("connection_string") or item.get("connectionString")
                if isinstance(cs, str):
                    hp = _parse_pooling_connection_host_port({"connection_string": cs})
                    if hp:
                        add(hp[0], max(1, hp[1]), _supabase_pooler_user(ref), dbn)
            pooler_rows_added = len(out) - before

        if pooler_rows_added == 0:
            legacy, lerr = _supabase_management_api_get_json(
                f"projects/{ref}/config/database/pooling", tok
            )
            if lerr:
                api_notes.append(f"GET …/database/pooling: {lerr}")
            hp = _parse_pooling_connection_host_port(legacy if isinstance(legacy, dict) else None)
            if hp:
                add(hp[0], max(1, hp[1]), _supabase_pooler_user(ref), "postgres")

    return out, " ".join(api_notes).strip()


def _psql_try_libpq_candidate(
    psql_bin: str,
    password: str,
    host: str,
    port: int,
    user: str,
    dbname: str,
) -> tuple[bool, str]:
    """
    ``SupabaseManager.test_connectivity`` parity: ``psql -c 'SELECT 1;'`` with libpq env only;
    on failure, a single ``PGHOSTADDR`` retry using Cloudflare DoH only when stderr looks like
    DNS failure (not IPv6 / no-route).
    """
    env0 = _psql_libpq_env(password, host, port, user, dbname, None)
    res = subprocess.run(
        [psql_bin, "-c", "SELECT 1;"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
        env=env0,
    )
    if res.returncode == 0:
        return True, f"{user}@{host}:{port}/{dbname}"

    err = (res.stderr or res.stdout or "").strip()
    last_err = err or f"psql exited with code {res.returncode}"

    if _stderr_looks_like_dns_failure(err):
        ip = _resolve_a_via_public_dns(host)
        if ip:
            env2 = _psql_libpq_env(password, host, port, user, dbname, ip)
            res2 = subprocess.run(
                [psql_bin, "-c", "SELECT 1;"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
                env=env2,
            )
            if res2.returncode == 0:
                return (
                    True,
                    f"{user}@{host}:{port}/{dbname} (PGHOSTADDR={ip}, TLS hostname {host})",
                )
            err2 = (res2.stderr or res2.stdout or "").strip()
            last_err = err2 or last_err

    return False, last_err


def _supabase_pooler_user(project_ref: str) -> str:
    ref = (project_ref or "").strip()
    return f"postgres.{ref}" if ref else "postgres"


def check_supabase_postgres_connectivity(
    service_block: Dict[str, Any],
    project_ref: str,
    password_plain: str,
    management_api_token: str = "",
) -> Dict[str, str]:
    """
    Verify Postgres with ``psql`` (aligned with dev-console ``SupabaseManager.test_connectivity``).

    **Connection candidates** (tried in order until one succeeds):

    1. ``db.<project_ref>.supabase.co``:5432 / ``postgres`` / ``postgres``
    2. YAML-derived host/port/user/database if different from (1)
    3. Hosts from Management API ``…/config/database/pooler`` (PRIMARY first), then legacy
       ``…/config/database/pooling`` if (3) added no rows

    **Token for (3–4)** merges, in order: ``management_api_token`` argument (usually from
    ``reveal_service_secret`` in the gear UI), then service YAML ``access_token`` /
    ``supabase_access_token`` / ``management_api_token``, then ``SUPABASE_ACCESS_TOKEN``.

    **Why this matters:** If only (1) runs and libpq uses unroutable IPv6, the check fails. Extra
    candidates from the pooler API typically use routable paths — same as dev-console.

    **DNS fallback:** One Cloudflare DoH ``PGHOSTADDR`` retry only when psql stderr looks like
    DNS failure (not for IPv6 "no route to host").

    Optional ``service_block`` keys: ``db_host``, ``postgres_host``, ``db_port``, ``db_user``,
    ``db_name``, ``access_token`` (and aliases above).
    """
    ref = (project_ref or "").strip()
    if not ref:
        return {"status": "error", "message": "Missing project ref."}

    pwd = str(password_plain or "").strip()
    if not pwd:
        return {
            "status": "error",
            "message": "No database password (paste in the field, reveal, or store in ~/.pas/supabase.json).",
        }

    host = (
        _resolve_service_value(service_block, "db_host", "postgres_host", "database_host").strip()
        or _supabase_default_db_host(ref)
    )
    port = _parse_positive_int(
        _resolve_service_value(service_block, "db_port", "postgres_port"),
        5432,
    )
    user = _resolve_service_value(service_block, "db_user", "postgres_user", "database_user") or "postgres"
    database = _resolve_service_value(service_block, "db_name", "database", "postgres_database") or "postgres"
    yaml_token = _resolve_service_value(
        service_block,
        "access_token",
        "supabase_access_token",
        "management_api_token",
        "supabase_management_api_token",
    )
    token = (
        (management_api_token or "").strip()
        or (yaml_token or "").strip()
        or os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    )

    psql_bin = _find_psql_binary()
    if psql_bin:
        candidates, pooler_api_hint = _supabase_psql_connection_candidates(
            ref, token, host, port, user, database
        )
        last_err = ""
        tried_labels: List[str] = []
        for ch, cp, cu, cd in candidates:
            tried_labels.append(f"{cu}@{ch}:{cp}/{cd}")
            ok, detail = _psql_try_libpq_candidate(psql_bin, pwd, ch, cp, cu, cd)
            if ok:
                return {"status": "ok", "message": f"Postgres OK ({detail})."}
            last_err = detail

        detail_parts: List[str] = [f"last error: {last_err or 'unknown'}"]
        detail_parts.append(f"tried {len(candidates)} candidate(s): " + "; ".join(tried_labels))
        if token and pooler_api_hint:
            detail_parts.append(f"Management API: {pooler_api_hint}")
        if not token:
            detail_parts.append(
                "Tip: set access_token / supabase_access_token in the service YAML, or in "
                "~/.pas/supabase.json, or SUPABASE_ACCESS_TOKEN, so pooler hosts load "
                "(GET /v1/projects/{ref}/config/database/pooler)."
            )
            yaml_org = _resolve_service_value(
                service_block, "org_id", "organization_id"
            ).strip()
            if yaml_org:
                cfg2 = normalize_supabase_toolkit_config(
                    load_pas_config("supabase", quiet=True)
                )
                pr2 = cfg2.get("profiles", {})
                if isinstance(pr2, dict) and yaml_org not in pr2:
                    detail_parts.append(
                        f"No ~/.pas/supabase.json profile matches org_id {yaml_org!r}; "
                        "token resolution uses active_profile_id then project org then config."
                    )
        elif token and len(candidates) <= 2 and pooler_api_hint:
            detail_parts.append(
                "Fix token scope (e.g. database_pooling_config_read) or org/project mismatch. "
                "Direct db.* often fails when IPv6 is unroutable; pooler candidates avoid that."
            )
        return {"status": "error", "message": "psql failed. " + " ".join(detail_parts)}

    ok, msg = _tcp_check_with_ipv4_fallback(host, port)
    if ok:
        return {
            "status": "warn",
            "message": (
                f"{msg}; psql not found — TCP only (credentials not verified). "
                f"Install PostgreSQL client (psql) for a full login test."
            ),
        }
    return {
        "status": "error",
        "message": f"psql not installed and TCP check failed ({host}:{port}): {msg}",
    }
