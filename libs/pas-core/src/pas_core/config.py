import copy
import json
import re
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, List, Union
from pydantic import BaseModel

PAS_PROJECT_STANDARD_ENV_KEYS: tuple[str, ...] = ("local", "development", "production")

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

def deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    result = base.copy()
    for key, value in overrides.items():
        if isinstance(value, dict) and key in result and isinstance(result[key], dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result

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

def get_environments_list(config: PASProjectConfig) -> List[Dict[str, str]]:
    envs = config.environments or {}
    services_def = config.services if isinstance(config.services, dict) else {}
    result = []
    for env_name, env_config in envs.items():
        if hasattr(env_config, "model_dump"):
            env_dict = env_config.model_dump(exclude_unset=True)
        else:
            env_dict = env_config
        resolved_env = {}
        if isinstance(env_dict, dict):
            for s_name, s_overrides in env_dict.items():
                if s_overrides is None:
                    continue
                base_info = services_def.get(s_name, {})
                if isinstance(s_overrides, dict):
                    resolved_env[s_name] = deep_merge(base_info, s_overrides)
                else:
                    resolved_env[s_name] = base_info
        result.append({
            "name": str(env_name),
            "data": json.dumps(resolved_env, indent=2)
        })
    return result

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

def get_service_oriented_data(config: PASProjectConfig) -> List[Dict[str, Any]]:
    services_data = []
    services_def = config.services if isinstance(config.services, dict) else {}
    envs = config.environments or {}
    active_env_name = config.active_env
    all_service_names = sorted(services_def.keys())

    for s_name in all_service_names:
        base_info = services_def.get(s_name, {})
        if not isinstance(base_info, dict):
            base_info = {}
        active_envs = []
        resolved_info = dict(base_info)
        for env_name, env_config in envs.items():
            if hasattr(env_config, "model_dump"):
                env_dict = env_config.model_dump(exclude_unset=True)
            else:
                env_dict = env_config
            if isinstance(env_dict, dict) and s_name in env_dict:
                active_envs.append(env_name)
                if env_name == active_env_name:
                    s_overrides = env_dict.get(s_name, {})
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
