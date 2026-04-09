import copy
import json
import re
import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, List, Union
from pydantic import BaseModel

PAS_PROJECT_STANDARD_ENV_KEYS: tuple[str, ...] = ("local", "development", "production")

# Reserved target key for the repo root dashboard (“Overview”).
PAS_TARGET_ROOT_KEY = "__root__"

PAS_PROJECT_YAML_FILENAME = ".pas.yaml"
PAS_PROJECT_YML_FILENAME = ".pas.yml"
GITIGNORE_RULE_PAS_PROJECT_YAML = ".pas.yaml"


def default_pas_project_document() -> Dict[str, Any]:
    """Skeleton for a new project YAML (``project``, ``services``, ``environments``)."""
    return {"project": {}, "services": {}, "environments": {}}


@dataclass(frozen=True)
class InitPasProjectYamlResult:
    path: Path
    created: bool
    skipped: bool
    gitignore_path: Optional[Path]
    gitignore_updated: bool

class ProjectMeta(BaseModel):
    types_path: Optional[str] = None
    core_tables: Optional[List[str]] = None
    storage: Optional[Dict[str, str]] = None
    app_version: Optional[str] = None
    app_base_url: Optional[str] = None

    model_config = {"extra": "allow"}

class EnvironmentConfig(BaseModel):
    """Overrides merged with ``services.<slot>`` where env keys match ``services`` keys.

    Declared fields are optional, commonly used slot names—not an exhaustive list.
    Any other slot (e.g. ``email``, ``sms``, ``payments``) is valid via ``extra``.
    """

    backend: Optional[Dict[str, Any]] = None
    frontend: Optional[Dict[str, Any]] = None
    messaging: Optional[Dict[str, Any]] = None
    intelligence: Optional[Dict[str, Any]] = None

    model_config = {"extra": "allow"}

class PASProjectConfig(BaseModel):
    active_env: Optional[str] = None
    project: Optional[ProjectMeta] = None
    environments: Optional[Dict[str, EnvironmentConfig]] = None
    services: Optional[Union[List[str], Dict[str, Any]]] = None
    # Per-target env assignments (participation + overrides). Values are free-form maps.
    # Schema shape:
    # targets:
    #   __root__:
    #     environments:
    #       dev:
    #         backend: { ...overrides... }
    #   apps/web:
    #     environments:
    #       dev:
    #         backend: { ...overrides... }
    targets: Optional[Dict[str, Any]] = None
    
    model_config = {"extra": "allow"}

SERVICE_CARD_DETAIL_PRIMARY_FIELDS: List[tuple[str, str]] = [
    ("Connection ID", "connection_id"),
    ("Org ID", "org_id"),
    ("Project", "project_ref"),
    ("Region", "region"),
    ("Account ID", "account_id"),
    ("Project Name", "project_name"),
]
SERVICE_CARD_DETAIL_PRIMARY_KEYS = frozenset(k for _, k in SERVICE_CARD_DETAIL_PRIMARY_FIELDS)

def load_pas_project_config(project_root: Path) -> Optional[PASProjectConfig]:
    for config_name in [".pas.yaml", ".pas.yml"]:
        config_path = project_root / config_name
        if config_path.exists():
            try:
                content = config_path.read_text()
                data = yaml.safe_load(content)
                if isinstance(data, dict):
                    return PASProjectConfig(**data)
            except Exception:
                continue
    return None


def resolve_pas_project_config_path(project_root: Path) -> Path:
    yaml_p = project_root / ".pas.yaml"
    yml = project_root / ".pas.yml"
    if yaml_p.is_file():
        return yaml_p
    if yml.is_file():
        return yml
    return yaml_p


def load_pas_project_config_document(project_root: Path) -> tuple[Path, Dict[str, Any]]:
    path = resolve_pas_project_config_path(project_root)
    if not path.is_file():
        return path, {}
    try:
        raw = yaml.safe_load(path.read_text())
        return path, raw if isinstance(raw, dict) else {}
    except Exception:
        return path, {}


def pas_user_service_config_json_path(
    service_name: str,
    connection_id: Optional[str] = None,
) -> Path:
    """Legacy slug path using connection_id or service name (matches load_pas_config(service) naming)."""
    slug = (connection_id or "").strip() or (service_name or "").strip() or "service"
    return Path.home() / ".pas" / f"{slug}.json"


def pas_provider_dev_config_path(provider: str) -> Path:
    """Toolkit JSON for a provider, e.g. supabase → ~/.pas/supabase.json.

    Used when the UI should follow provider SDK/CLI config (not connection_id or project service id).
    """
    p = (provider or "").strip().lower()
    if not p or p == "unknown":
        p = "service"
    p = re.sub(r"[^a-z0-9_-]+", "", p) or "service"
    return Path.home() / ".pas" / f"{p}.json"


def pas_provider_dev_config_candidates(provider: str) -> List[Path]:
    """Ordered config candidates for a provider, JSON first then YAML."""
    json_path = pas_provider_dev_config_path(provider)
    stem = json_path.with_suffix("")
    return [json_path, stem.with_suffix(".yaml"), stem.with_suffix(".yml")]


def resolve_pas_provider_dev_config_path(provider: str) -> Path:
    """Resolve the first existing provider developer config path.

    Falls back to JSON path when no candidate exists.
    """
    candidates = pas_provider_dev_config_candidates(provider)
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def apply_service_env_patch(
    full: Dict[str, Any],
    service_name: str,
    env_participation: Dict[str, bool],
    env_overrides: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    out = copy.deepcopy(full)
    envs = out.get("environments")
    if envs is None or not isinstance(envs, dict):
        envs = {}
        out["environments"] = envs

    for env_key in PAS_PROJECT_STANDARD_ENV_KEYS:
        participating = bool(env_participation.get(env_key, False))
        env_block = envs.get(env_key)
        if not isinstance(env_block, dict):
            env_block = {}
            envs[env_key] = env_block

        if not participating:
            env_block.pop(service_name, None)
            if not env_block:
                envs.pop(env_key, None)
            continue

        override = env_overrides.get(env_key)
        if not isinstance(override, dict):
            override = {}
        env_block[service_name] = override

    if not envs:
        out["environments"] = {}
    return out


def save_pas_project_config(
    project_root: Path,
    data: Dict[str, Any],
    *,
    keep_backups: Optional[int] = None,
) -> Path:
    PASProjectConfig(**data)
    from pas_core import (
        backup_json_with_timestamp,
        atomic_write_text,
        JSON_BACKUP_KEEP_DEFAULT,
    )
    keep = JSON_BACKUP_KEEP_DEFAULT if keep_backups is None else keep_backups
    path = resolve_pas_project_config_path(project_root)
    content = yaml.safe_dump(
        data,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    if path.exists():
        backup_json_with_timestamp(path, keep=keep)
    atomic_write_text(path, content)
    return path


def _path_is_under_or_equal(root: Path, path: Path) -> bool:
    root_r = root.resolve()
    path_r = path.resolve()
    if path_r == root_r:
        return True
    try:
        path_r.relative_to(root_r)
        return True
    except ValueError:
        return False


def _gitignore_lines_have_rule(lines: List[str], rule: str) -> bool:
    target = rule.strip()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s == target:
            return True
    return False


def _append_gitignore_rule(gitignore_path: Path, rule: str) -> None:
    from pas_core import atomic_write_text

    if gitignore_path.is_file():
        text = gitignore_path.read_text(encoding="utf-8")
    else:
        text = ""
    lines = text.splitlines()
    if _gitignore_lines_have_rule(lines, rule):
        return
    if text and not text.endswith("\n"):
        text += "\n"
    text += f"{rule}\n"
    atomic_write_text(gitignore_path, text)


def _git_toplevel_for_path(path: Path) -> Optional[Path]:
    """Return Git work tree root for ``path``, or None if not inside a repository."""
    from pas_core import run_command

    res = run_command(["git", "rev-parse", "--show-toplevel"], cwd=path)
    if res.returncode != 0 or not (res.stdout or "").strip():
        return None
    return Path(res.stdout.strip()).resolve()


def init_pas_project_yaml(
    workdir: Path,
    *,
    ensure_gitignore: bool = True,
) -> InitPasProjectYamlResult:
    """Create ``.pas.yaml`` with a default skeleton if no project YAML exists in ``workdir``.

    If ``.pas.yaml`` or ``.pas.yml`` already exists, does not overwrite. When ``ensure_gitignore``
    is true and ``workdir`` lies inside a Git work tree, ensures ``.pas.yaml`` is listed in the
    repository root ``.gitignore``.
    """
    wd = workdir.resolve()
    yaml_p = wd / PAS_PROJECT_YAML_FILENAME
    yml_p = wd / PAS_PROJECT_YML_FILENAME

    if yaml_p.is_file() or yml_p.is_file():
        return InitPasProjectYamlResult(
            path=resolve_pas_project_config_path(wd),
            created=False,
            skipped=True,
            gitignore_path=None,
            gitignore_updated=False,
        )

    data = default_pas_project_document()
    out_path = save_pas_project_config(wd, data)

    gitignore_path: Optional[Path] = None
    gitignore_updated = False
    if ensure_gitignore:
        root = _git_toplevel_for_path(wd)
        if root is not None and _path_is_under_or_equal(root, wd):
                gitignore_path = root / ".gitignore"
                before = gitignore_path.is_file()
                prev = gitignore_path.read_text(encoding="utf-8") if before else ""
                _append_gitignore_rule(gitignore_path, GITIGNORE_RULE_PAS_PROJECT_YAML)
                after = gitignore_path.read_text(encoding="utf-8") if gitignore_path.is_file() else ""
                gitignore_updated = after != prev

    return InitPasProjectYamlResult(
        path=out_path,
        created=True,
        skipped=False,
        gitignore_path=gitignore_path,
        gitignore_updated=gitignore_updated,
    )


def deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    result = base.copy()
    for key, value in overrides.items():
        if isinstance(value, dict) and key in result and isinstance(result[key], dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


_ENV_KEY_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def normalize_env_key(raw: str) -> str:
    """Lowercase slug for comparisons; trim whitespace."""
    return (raw or "").strip().lower()


def is_valid_env_key_slug(s: str) -> bool:
    """True if non-empty: letters, digits, underscore, hyphen; must start with a letter."""
    if not s:
        return False
    return bool(_ENV_KEY_SLUG_RE.match(s))


def coerce_environments_dict_keys(raw: Any) -> Dict[str, Any]:
    """Normalize ``environments`` map keys (case/spacing); merge dict values when keys collide."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        nk = normalize_env_key(str(k))
        if not nk:
            continue
        if nk in out:
            cur = out[nk]
            if isinstance(cur, dict) and isinstance(v, dict):
                out[nk] = deep_merge(dict(cur), v)
            else:
                out[nk] = copy.deepcopy(v) if isinstance(v, dict) else v
        else:
            out[nk] = copy.deepcopy(v) if isinstance(v, dict) else v
    return out


def normalize_target_key(raw: Optional[str]) -> str:
    """Normalize a target key for storage/lookup.

    - Empty/None => reserved root key.
    - Otherwise use repo-relative paths (forward slashes) without leading/trailing slashes.
    """
    if raw is None:
        return PAS_TARGET_ROOT_KEY
    s = str(raw).strip()
    if not s:
        return PAS_TARGET_ROOT_KEY
    s = s.replace("\\", "/").strip().strip("/")
    return s or PAS_TARGET_ROOT_KEY


def resolve_target_env_assignments(
    config: PASProjectConfig,
    target_key: Optional[str] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Return per-target env assignments as a normalized map.

    Output shape:
      { env_key: { service_slot: {override_kv} } }

    Legacy compatibility:
    - If `config.targets` does not exist, interpret legacy `config.environments`
      content (which historically stored env->slot->overrides) as assignments for
      the root target only.
    """
    tk = normalize_target_key(target_key)

    # New format: targets.<tk>.environments
    if isinstance(config.targets, dict):
        tblock = config.targets.get(tk)
        if isinstance(tblock, dict):
            envs = tblock.get("environments")
            if isinstance(envs, dict):
                out: Dict[str, Dict[str, Dict[str, Any]]] = {}
                for env_k, env_block in envs.items():
                    ek = normalize_env_key(str(env_k))
                    if not ek or not isinstance(env_block, dict):
                        continue
                    per_service: Dict[str, Dict[str, Any]] = {}
                    for s_name, overrides in env_block.items():
                        if overrides is None:
                            # Treat explicit null as “not assigned”.
                            continue
                        if isinstance(overrides, dict):
                            per_service[str(s_name)] = dict(overrides)
                        else:
                            # Participation with no override map: normalize to empty map.
                            per_service[str(s_name)] = {}
                    if per_service:
                        out[ek] = per_service
                return out

    # Legacy: environments.<env>.<slot> stored at top-level, only for root.
    if tk != PAS_TARGET_ROOT_KEY:
        return {}

    envs_legacy = config.environments or {}
    out_legacy: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for env_name, env_cfg in envs_legacy.items():
        ek = normalize_env_key(str(env_name))
        if not ek:
            continue
        if hasattr(env_cfg, "model_dump"):
            env_dict = env_cfg.model_dump(exclude_unset=True)
        else:
            env_dict = env_cfg
        if not isinstance(env_dict, dict):
            continue
        per_service: Dict[str, Dict[str, Any]] = {}
        for s_name, s_overrides in env_dict.items():
            if s_overrides is None:
                continue
            if isinstance(s_overrides, dict):
                per_service[str(s_name)] = dict(s_overrides)
            else:
                per_service[str(s_name)] = {}
        if per_service:
            out_legacy[ek] = per_service
    return out_legacy


def get_metadata_cards(config: PASProjectConfig) -> List[Dict[str, str]]:
    cards = []
    data = config.model_dump(exclude={"environments", "services"})
    for key, value in data.items():
        if value is not None:
            cards.append({
                "title": str(key),
                "value": json.dumps(value, indent=2) if isinstance(value, (dict, list)) else str(value)
            })
    return sorted(cards, key=lambda x: x["title"])

def get_environments_list_for_target(
    config: PASProjectConfig,
    target_key: Optional[str] = None,
    *,
    allowed_services: Optional[set[str]] = None,
) -> List[Dict[str, str]]:
    """Return merged environment JSON previews for a given target.

    - `environments` keys are repo-wide definitions.
    - Participation + overrides come from per-target assignments.
    - `allowed_services` filters top-level service keys in the merged JSON.
    """
    services_def = config.services if isinstance(config.services, dict) else {}
    env_defs = config.environments or {}

    assignments = resolve_target_env_assignments(config, target_key)
    result: List[Dict[str, str]] = []

    # Preserve YAML ordering as much as possible by iterating env_defs keys first.
    for env_name in env_defs.keys():
        ek = normalize_env_key(str(env_name))
        env_assign = assignments.get(ek, {})
        resolved_env: Dict[str, Any] = {}
        for s_name, s_overrides in env_assign.items():
            if allowed_services is not None and str(s_name) not in allowed_services:
                continue
            base_info = services_def.get(s_name, {})
            if isinstance(s_overrides, dict):
                resolved_env[str(s_name)] = deep_merge(base_info, s_overrides)
            else:
                resolved_env[str(s_name)] = base_info
        result.append({"name": ek, "data": json.dumps(resolved_env, indent=2)})

    # Include env keys that exist in assignments but not in repo-wide definitions (should be rare).
    for ek in sorted(assignments.keys()):
        if any(normalize_env_key(str(n)) == ek for n in env_defs.keys()):
            continue
        env_assign = assignments.get(ek, {})
        resolved_env: Dict[str, Any] = {}
        for s_name, s_overrides in env_assign.items():
            if allowed_services is not None and str(s_name) not in allowed_services:
                continue
            base_info = services_def.get(s_name, {})
            if isinstance(s_overrides, dict):
                resolved_env[str(s_name)] = deep_merge(base_info, s_overrides)
            else:
                resolved_env[str(s_name)] = base_info
        result.append({"name": ek, "data": json.dumps(resolved_env, indent=2)})

    return result


def get_environments_list(config: PASProjectConfig) -> List[Dict[str, str]]:
    """Backward-compatible default: root target env previews."""
    return get_environments_list_for_target(config, PAS_TARGET_ROOT_KEY)

def get_services_refs(config: PASProjectConfig) -> List[Dict[str, Any]]:
    project_services = []
    services_raw = config.services or {}
    if isinstance(services_raw, dict):
        for s_id, s_info in services_raw.items():
            project_services.append({"id": s_id, "config": s_info})
    elif isinstance(services_raw, list):
        for s_id in services_raw:
            if isinstance(s_id, str):
                project_services.append({"id": s_id, "config": {}})
    return project_services

def get_service_oriented_data_for_target(
    config: PASProjectConfig,
    target_key: Optional[str] = None,
    *,
    allowed_services: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    services_data = []
    services_def = config.services if isinstance(config.services, dict) else {}
    env_defs = config.environments or {}
    assignments = resolve_target_env_assignments(config, target_key)
    active_env_name = config.active_env
    all_service_names = sorted(services_def.keys())
    if allowed_services is not None:
        all_service_names = [n for n in all_service_names if str(n) in allowed_services]

    for s_name in all_service_names:
        base_info = services_def.get(s_name, {})
        if not isinstance(base_info, dict):
            base_info = {}
        active_envs = []
        resolved_info = dict(base_info)
        # Determine env participation from per-target assignments, using env_defs order.
        for env_name in env_defs.keys():
            ek = normalize_env_key(str(env_name))
            env_block = assignments.get(ek, {})
            if not isinstance(env_block, dict) or s_name not in env_block:
                continue
            active_envs.append(ek)
            if ek == normalize_env_key(str(active_env_name or "")):
                s_overrides = env_block.get(s_name, {})
                if isinstance(s_overrides, dict):
                    resolved_info = deep_merge(dict(base_info), s_overrides)

        base_info_display: Dict[str, str] = {}
        for k, v in base_info.items():
            base_info_display[k] = "" if v is None else str(v)
        connection_id = base_info_display.get("connection_id", "")

        if (base_info_display.get("provider") or "").strip().lower() == "supabase":
            from .service_config import enrich_supabase_service_card_base_info

            enrich_supabase_service_card_base_info(base_info_display)

        base_skip = SERVICE_CARD_DETAIL_PRIMARY_KEYS | {"provider"}
        base_info_extra: List[Dict[str, str]] = []
        for k in sorted(base_info_display.keys()):
            if k in base_skip:
                continue
            base_info_extra.append({
                "key": k,
                "label": k.replace("_", " ").title(),
                "value": base_info_display[k],
            })

        display_info = {}
        if isinstance(resolved_info, dict):
            for k, v in resolved_info.items():
                if any(sk in k.lower() for sk in ["key", "token", "password", "secret"]):
                    display_info[k] = "********"
                else:
                    display_info[k] = str(v)

        active_set = {str(e).lower() for e in active_envs}
        skip_extra = SERVICE_CARD_DETAIL_PRIMARY_KEYS | {"provider"}
        info_extra: List[Dict[str, str]] = []
        for k in sorted(display_info.keys()):
            if k in skip_extra:
                continue
            info_extra.append({
                "key": k,
                "label": k.replace("_", " ").title(),
                "value": display_info[k],
            })

        services_data.append({
            "name": s_name,
            "provider": resolved_info.get("provider", "unknown") if isinstance(resolved_info, dict) else "unknown",
            "connection_id": connection_id,
            "active_envs": active_envs,
            "env_local": "local" in active_set,
            "env_development": "development" in active_set,
            "env_production": "production" in active_set,
            "info": display_info,
            "info_extra": info_extra,
            "base_info": base_info_display,
            "base_info_extra": base_info_extra,
        })

    return services_data


def get_service_oriented_data(config: PASProjectConfig) -> List[Dict[str, Any]]:
    """Backward-compatible default: root target service card data."""
    return get_service_oriented_data_for_target(config, PAS_TARGET_ROOT_KEY)
