#!/usr/bin/env python3
"""
@pas-executable
Manage high-permission Cloudflare credentials in ~/.pas/cloudflare.json (profiles, API token or Global API Key).
Terminal-first API checks without loading the full PAS Console — same auth logic as pas_core / the Reflex UI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Prefer vendored pas-core (same layout as pas_console.pas_core_bootstrap); otherwise an older
# site-packages pas_core may omit Cloudflare Global API Key from secretization.
_cf_ops_file = Path(__file__).resolve()
_repo_root = _cf_ops_file.parents[2]  # pas-console root: pas/services/cf-ops.py
_pas_core_src = _repo_root / "pas" / "libs" / "pas-core" / "src"
if _pas_core_src.is_dir():
    sys.path.insert(0, str(_pas_core_src))

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from rich.panel import Panel
from rich.table import Table

from helpers.core import (
    console,
    format_menu_choices,
    get_pas_config_dir,
    load_pas_config,
    prompt_toolkit_menu,
    prompt_yes_no,
    save_pas_config,
)

from pas_core.service_config import (
    cloudflare_discover_accounts_from_credentials,
    cloudflare_profile_credential_fields,
    cloudflare_profile_health,
)

# --- Tool Identity ---
TOOL_ID = "cf-ops"
TOOL_TITLE = "Cloudflare toolkit (cf-ops)"
TOOL_DESCRIPTION = (
    "Manage ~/.pas/cloudflare.json profiles with a high-permission API token or Global API Key. "
    "Discover Cloudflare accounts (GET /accounts), pick one to store, validate scopes, "
    "and switch, create, edit, or delete profiles. "
    "Global API Key requires your dashboard login email (CLOUDFLARE_EMAIL or `email`)."
)
# ---------------------

DEFAULT_NEW_PROFILE_CAPABILITIES: List[str] = [
    "frontend",
    "worker",
    "messaging",
    "ai",
    "storage",
    "network",
    "domain",
]

ROOT_KEYS_TO_MIGRATE_INTO_PROFILE = (
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "DEFAULT_DOMAIN",
    "TUNNEL_TOKEN",
    "CLOUDFLARE_EMAIL",
    "CLOUDFLARE_GLOBAL_API_KEY",
    "CLOUDFLARE_API_KEY",
    "email",
    "name",
)

DASH_HOME_URL = "https://dash.cloudflare.com"


def print_cloudflare_accounts_table(
    rows: List[Dict[str, Any]],
    *,
    subtitle: str = "",
) -> None:
    """Print a table of account id + name for every row returned by GET /accounts."""
    if not rows:
        return
    table = Table(title="Accounts this credential can access" + (f" — {subtitle}" if subtitle else ""))
    table.add_column("Name", overflow="fold")
    table.add_column("Account ID", overflow="fold")
    for r in rows:
        table.add_row(
            str(r.get("name") or "").strip() or "—",
            str(r.get("id") or "").strip() or "—",
        )
    console.print(table)


def prompt_account_id_friendly(current: str = "") -> str:
    """Prompt for Cloudflare account id with plain-language discovery help (stores as CLOUDFLARE_ACCOUNT_ID)."""
    cur = (current or "").strip()
    console.print(
        "[dim]If automatic lookup did not run or failed, paste your account id below. "
        f"Otherwise open {DASH_HOME_URL}, open your account, then either:[/dim]\n"
        "[dim]  • Copy from the address bar: …/accounts/[bold]this-long-hex-id[/bold]/…[/dim]\n"
        "[dim]  • Or on Overview, copy “Account ID” from the right sidebar.[/dim]\n"
        "[dim]You can leave this blank and paste it later (e.g. when you run Validate); "
        "it’s needed to check Pages, Workers, R2, tunnels, and DNS for this profile.[/dim]"
    )
    tail = f"[{cur}]" if cur else "[not set]"
    return input(f"Paste account id {tail}: ").strip()


def pick_account_id_after_discovery(
    rows: List[Dict[str, Any]],
    err: str,
    *,
    saved_account_id: str = "",
) -> tuple[bool, str]:
    """Return ``(should_apply, account_id)`` for ``CLOUDFLARE_ACCOUNT_ID``.

    - ``should_apply`` False: leave the field unchanged (user chose “keep”).
    - ``should_apply`` True + non-empty id: set to that id.
    - ``should_apply`` True + empty id: remove / omit account id (clear or skip without id).
    """
    cur = (saved_account_id or "").strip()

    if rows:
        print_cloudflare_accounts_table(rows)

    if rows and len(rows) == 1:
        only_id = str(rows[0].get("id") or "").strip()
        label = str(rows[0].get("display_label") or rows[0].get("name") or only_id)
        console.print(
            f"[green]One account — using [bold]{label}[/bold]. "
            "Use Edit profile to pick a different account if needed.[/green]"
        )
        return (True, only_id)

    if rows and len(rows) > 1:
        console.print(
            "[bold]Choose which Cloudflare account to use[/bold] "
            "(Global API Key / token may access several accounts)."
        )
        menu_items: List[Dict[str, Any]] = []
        if cur and any(str(r.get("id") or "").strip() == cur for r in rows):
            menu_items.append(
                {
                    "title": f"Keep saved account id ({cur})",
                    "value": "__keep__",
                }
            )
        for r in rows:
            rid = str(r.get("id") or "").strip()
            if not rid:
                continue
            label = str(r.get("display_label") or r.get("name") or rid)
            menu_items.append({"title": label, "value": rid})
        menu_items.append(
            {
                "title": "Do not set account id (omit for now)",
                "value": "__omit__",
            }
        )
        menu_items.append(
            {
                "title": "Remove saved account id from profile" if cur else "Skip (no account id)",
                "value": "__clear__",
            }
        )
        menu_items.append({"title": "Enter account id manually …", "value": "__manual__"})
        formatted = format_menu_choices(menu_items, title_field="title", value_field="value")
        picked = prompt_toolkit_menu(formatted)
        if picked == "__keep__" or picked is None:
            return (False, "")
        if picked == "__omit__":
            return (False, "")
        if picked == "__clear__":
            return (True, "")
        if picked == "__manual__":
            manual = prompt_account_id_friendly(cur)
            return (True, manual.strip())
        return (True, str(picked).strip())

    if err:
        console.print(f"[yellow]Could not list accounts: {err}[/yellow]")
    elif not rows:
        console.print("[yellow]No accounts returned (check token scopes or email + Global API Key).[/yellow]")
    console.print("[dim]Enter an account id manually, or leave blank to leave this field unchanged.[/dim]")
    manual = prompt_account_id_friendly(cur).strip()
    if manual:
        return (True, manual)
    return (False, "")


def run_list_accounts(profile_id: Optional[str]) -> None:
    """Print GET /accounts for a profile (discovery only)."""
    pid = (profile_id or "").strip() or (get_current_profile() or "").strip()
    if not pid:
        console.print("[red]No profile — pass a profile id or set an active profile.[/red]")
        sys.exit(1)
    if pid not in list_profiles():
        console.print(f"[red]Unknown profile: {pid!r}[/red]")
        sys.exit(1)
    console.print(f"[dim]GET /accounts using profile [bold]{pid}[/bold]…[/dim]")
    prof_try = cloudflare_profile_credential_fields(pid)
    rows, err = cloudflare_discover_accounts_from_credentials(prof_try)
    if err:
        console.print(f"[red]{err}[/red]")
        sys.exit(1)
    if not rows:
        console.print("[yellow]No accounts returned.[/yellow]")
        sys.exit(0)
    print_cloudflare_accounts_table(rows, subtitle=f"profile {pid}")
    console.print(f"[dim]{len(rows)} account(s). Use Edit profile to store one as CLOUDFLARE_ACCOUNT_ID.[/dim]")


def list_profiles(provider: str = "cloudflare") -> List[str]:
    """List all available profiles from provider config."""
    config_file = get_pas_config_dir() / f"{provider}.json"
    if not config_file.exists():
        return []
    try:
        data = json.loads(config_file.read_text())
        profiles = data.get("profiles", {})
        return sorted(list(profiles.keys()))
    except Exception:
        return []


def get_current_profile(provider: str = "cloudflare") -> Optional[str]:
    """Get the currently active profile."""
    config_file = get_pas_config_dir() / f"{provider}.json"
    if not config_file.exists():
        return None
    try:
        data = json.loads(config_file.read_text())
        return data.get("active_profile_id") or data.get("current_profile")
    except Exception:
        return None


def get_raw_config(provider: str = "cloudflare") -> Dict[str, Any]:
    """Load the raw config file without secretization/profile merging."""
    config_file = get_pas_config_dir() / f"{provider}.json"
    if not config_file.exists():
        return {}
    try:
        return json.loads(config_file.read_text())
    except Exception:
        return {}


def set_active_profile_on_config(config: Dict[str, Any], profile_name: str) -> None:
    config["active_profile_id"] = profile_name
    config["current_profile"] = profile_name


def _profile_has_global_key_material(prof: Dict[str, Any]) -> bool:
    return bool(
        str(prof.get("CLOUDFLARE_GLOBAL_API_KEY") or prof.get("CLOUDFLARE_API_KEY") or "").strip()
    )


def _profile_current_email(prof: Dict[str, Any]) -> str:
    return str(prof.get("CLOUDFLARE_EMAIL") or prof.get("email") or "").strip()


def _apply_secret_to_profile(prof: Dict[str, Any], secret_new: str, email_input: str) -> None:
    """Apply new API token or Global API Key; mutates ``prof`` like the create flow."""
    em_raw = email_input.strip()
    em_lower = em_raw.lower()
    cur_email = _profile_current_email(prof)
    force_token = em_lower in ("-", "token", "bearer", ".")
    if not secret_new:
        return
    if force_token:
        prof["CLOUDFLARE_API_TOKEN"] = secret_new
        for k in ("CLOUDFLARE_EMAIL", "CLOUDFLARE_GLOBAL_API_KEY", "CLOUDFLARE_API_KEY", "email"):
            prof.pop(k, None)
        return
    if em_raw:
        prof["CLOUDFLARE_EMAIL"] = em_raw
        prof.pop("email", None)
        prof["CLOUDFLARE_GLOBAL_API_KEY"] = secret_new
        prof.pop("CLOUDFLARE_API_KEY", None)
        prof.pop("CLOUDFLARE_API_TOKEN", None)
        return
    if cur_email and _profile_has_global_key_material(prof):
        prof["CLOUDFLARE_EMAIL"] = cur_email
        prof.pop("email", None)
        prof["CLOUDFLARE_GLOBAL_API_KEY"] = secret_new
        prof.pop("CLOUDFLARE_API_KEY", None)
        prof.pop("CLOUDFLARE_API_TOKEN", None)
        return
    prof["CLOUDFLARE_API_TOKEN"] = secret_new
    for k in ("CLOUDFLARE_EMAIL", "CLOUDFLARE_GLOBAL_API_KEY", "CLOUDFLARE_API_KEY", "email"):
        prof.pop(k, None)


def edit_profile(profile_id: str, *, run_health: bool = True) -> None:
    """Edit an existing profile's display name, account id, domain, email, and credentials."""
    pid = (profile_id or "").strip()
    names = list_profiles()
    if not pid or pid not in names:
        console.print(f"[red]Unknown profile: {profile_id!r}[/red]")
        return

    cfg = load_pas_config("cloudflare", quiet=True)
    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict) or pid not in profiles:
        console.print("[red]Profile not found in config.[/red]")
        return

    prof: Dict[str, Any] = dict(profiles.get(pid) or {})
    cur_name = str(prof.get("name") or "").strip()
    cur_aid = str(prof.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    cur_domain = str(prof.get("DEFAULT_DOMAIN") or "").strip()
    cur_email = _profile_current_email(prof)

    console.print(f"\n[bold]Edit profile[/bold] [cyan]{pid}[/cyan]")
    console.print(
        "[dim]Leave a line empty to keep the current value. "
        "Cloudflare account discovery runs after credentials: for Global API Key, set dashboard email first, "
        "then paste the key; on the email line, type token / - / bearer and enter a new secret below to switch "
        "to a Bearer API token (and drop the global-key path).[/dim]\n"
    )

    name_in = input(f"Display name [{cur_name}]: ").strip()
    if name_in:
        prof["name"] = name_in

    # Profile slice only — merged load_pas_config(..., profile=) injects legacy root CLOUDFLARE_API_TOKEN
    # and Bearer is tried first, causing 403 when the profile uses Global API Key only.
    work: Dict[str, Any] = dict(prof)

    email_in = input(
        f"Dashboard email (Global API Key only; token / - / bearer = switch to API token) [{cur_email}]: "
    )

    console.print("[dim]New API token or Global API Key [Enter to leave unchanged]:[/dim]")
    secret_in = input("Secret: ").strip()

    if secret_in:
        _apply_secret_to_profile(work, secret_in, email_in)
        _apply_secret_to_profile(prof, secret_in, email_in)
    else:
        em_only = email_in.strip()
        em_lower = em_only.lower()
        if em_only and em_lower not in ("-", "token", "bearer", "."):
            work["CLOUDFLARE_EMAIL"] = em_only
            work.pop("email", None)
            prof["CLOUDFLARE_EMAIL"] = em_only
            prof.pop("email", None)

    console.print("[dim]Discovering Cloudflare accounts (GET /accounts)…[/dim]")
    rows, err = cloudflare_discover_accounts_from_credentials(work)
    should_apply, aid_in = pick_account_id_after_discovery(rows, err, saved_account_id=cur_aid)
    if should_apply:
        if aid_in:
            prof["CLOUDFLARE_ACCOUNT_ID"] = aid_in
        else:
            prof.pop("CLOUDFLARE_ACCOUNT_ID", None)

    dom_in = input(f"DEFAULT_DOMAIN [{cur_domain}]: ").strip()
    if dom_in:
        prof["DEFAULT_DOMAIN"] = dom_in

    profiles[pid] = prof
    cfg["profiles"] = profiles
    try:
        save_pas_config("cloudflare", cfg)
        console.print(f"[green]Saved profile '{pid}'.[/green]")
    except Exception as e:
        console.print(f"[red]Save failed: {e}[/red]")
        return

    if run_health:
        run_validate(pid, None)


def migrate_flat_to_profile(name: Optional[str] = None) -> None:
    """Move root-level Cloudflare keys into profiles[name] when the file is still flat."""
    path = get_pas_config_dir() / "cloudflare.json"
    if not path.is_file():
        console.print("[red]~/.pas/cloudflare.json not found.[/red]")
        return
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        console.print(f"[red]Could not parse cloudflare.json: {e}[/red]")
        return
    profiles = data.get("profiles")
    if isinstance(profiles, dict) and profiles:
        console.print("[yellow]Already has profiles; nothing to migrate.[/yellow]")
        return

    profile_name = (name or "default").strip() or "default"
    prof: Dict[str, Any] = {}
    for k in ROOT_KEYS_TO_MIGRATE_INTO_PROFILE:
        if k not in data:
            continue
        v = data[k]
        if v is None or v == "":
            continue
        prof[k] = v
        meta_key = f"{k}_meta"
        if meta_key in data:
            prof[meta_key] = data[meta_key]
        del data[k]
        data.pop(meta_key, None)

    if not prof:
        console.print("[yellow]No root Cloudflare keys found to migrate.[/yellow]")
        return

    data["profiles"] = {profile_name: prof}
    set_active_profile_on_config(data, profile_name)
    if "capabilities" not in data or not isinstance(data.get("capabilities"), list):
        data["capabilities"] = list(DEFAULT_NEW_PROFILE_CAPABILITIES)
    if "provider" not in data:
        data["provider"] = "cloudflare"
    try:
        save_pas_config("cloudflare", data)
        console.print(f"[green]Migrated root keys into profile '{profile_name}'.[/green]")
    except Exception as e:
        console.print(f"[red]Migration save failed: {e}[/red]")


def _print_health_rich(health: Dict[str, Any]) -> None:
    if health.get("credentials_error"):
        console.print(f"[red]{health['credentials_error']}[/red]")
        return

    auth = health.get("auth_mode_human") or "(could not determine — check accounts error below)"
    console.print(f"\n[bold]Auth in use:[/bold] {auth}")

    if health.get("email_required_message"):
        console.print(f"[yellow]{health['email_required_message']}[/yellow]")

    if health.get("accounts_error"):
        console.print(f"[red]Accounts:[/red] {health['accounts_error']}")
    else:
        acct = health.get("accounts") or []
        console.print(f"[green]Accounts visible:[/green] {len(acct)}")
        for a in acct[:20]:
            console.print(f"  • {a.get('display_label', a.get('id', ''))}")
        if len(acct) > 20:
            console.print(f"  … ({len(acct) - 20} more)")

    if health.get("account_id_missing_playbook") and not health.get("account_id_for_probes"):
        console.print(f"\n[yellow]{health['account_id_missing_playbook']}[/yellow]")

    table = Table(title="Capability probes (GET list)")
    table.add_column("Capability")
    table.add_column("Status")
    table.add_column("Detail")
    for row in health.get("capabilities") or []:
        cap = str(row.get("label") or row.get("id") or "")
        if row.get("skipped_reason"):
            st = "[dim]skipped[/dim]"
            detail = str(row.get("skipped_reason") or "")
        elif row.get("ok"):
            st = "[green]ok[/green]"
            detail = f"count={row.get('count', 0)}"
        else:
            st = "[red]fail[/red]"
            detail = str(row.get("error_hint") or "")
        table.add_row(cap, st, detail)
    console.print(table)
    console.print(f"\n[dim]{health.get('playbook_tail', '')}[/dim]\n")


def run_validate(profile_id: Optional[str], account_id: Optional[str]) -> None:
    pid = (profile_id or "").strip() or (get_current_profile() or "").strip()
    if not pid:
        console.print("[red]No profile id (use --validate PROFILE or create a profile).[/red]")
        sys.exit(1)
    profiles = list_profiles()
    if pid not in profiles:
        console.print(f"[red]Profile '{pid}' not found.[/red]")
        sys.exit(1)
    health = cloudflare_profile_health(pid, account_id=account_id)
    _print_health_rich(health)


def manage_profiles() -> None:
    """Interactive menu for profile management."""
    while True:
        current = get_current_profile()
        profiles = list_profiles()

        console.print(f"\n[bold cyan]Current profile:[/bold cyan] {current or '[None]'}")

        menu_items: List[Dict[str, Any]] = []
        if profiles:
            for p in profiles:
                status = " (ACTIVE)" if p == current else ""
                menu_items.append({"title": f"Switch to: {p}{status}", "value": ("switch", p)})

        menu_items.append({"title": "Validate API access (current profile)", "value": ("validate", None)})
        if profiles:
            menu_items.append({"title": "List Cloudflare accounts (GET /accounts)", "value": ("list_accounts", None)})
        menu_items.append({"title": "Create new profile", "value": ("create", None)})

        if profiles:
            menu_items.append({"title": "Edit a profile", "value": ("edit", None)})

        if profiles:
            menu_items.append({"title": "Delete a profile", "value": ("delete", None)})

        menu_items.append({"title": "[Quit]", "value": ("quit", None)})

        formatted_choices = format_menu_choices(menu_items, title_field="title", value_field="value")
        console.print("\n[bold]Select an action:[/bold]")
        selected = prompt_toolkit_menu(formatted_choices)

        if not selected or selected[0] == "quit":
            break

        action, profile_name = selected

        if action == "switch":
            config = load_pas_config("cloudflare")
            set_active_profile_on_config(config, profile_name)
            save_pas_config("cloudflare", config)
            console.print(f"[green]Switched to profile: {profile_name}[/green]")
            run_validate(profile_name, None)

        elif action == "validate":
            run_validate(current, None)

        elif action == "list_accounts":
            pick_p = [
                {
                    "title": f"{p}  (active)" if p == current else p,
                    "value": p,
                }
                for p in profiles
            ]
            formatted_lp = format_menu_choices(pick_p, title_field="title", value_field="value")
            console.print("\n[bold]List accounts for which profile?[/bold]")
            lp = prompt_toolkit_menu(formatted_lp)
            if lp:
                run_list_accounts(lp)

        elif action == "edit":
            pick = [{"title": p, "value": p} for p in profiles]
            pick.append({"title": "[Back]", "value": None})
            formatted_pick = format_menu_choices(pick, title_field="title", value_field="value")
            console.print("\n[bold]Select profile to edit:[/bold]")
            to_edit = prompt_toolkit_menu(formatted_pick)
            if to_edit:
                edit_profile(to_edit, run_health=True)

        elif action == "create":
            new_name = input("Profile id (short name): ").strip()
            if not new_name:
                continue
            if new_name in profiles:
                console.print(f"[yellow]Profile '{new_name}' already exists.[/yellow]")
                continue

            secret = input(
                "API token or Global API Key (paste, then Enter): "
            ).strip()
            if not secret:
                console.print("[yellow]No secret entered.[/yellow]")
                continue
            email = input(
                "Dashboard login email — required for Global API Key; leave empty for API token: "
            ).strip()
            if email:
                prof_try: Dict[str, Any] = {
                    "CLOUDFLARE_EMAIL": email,
                    "CLOUDFLARE_GLOBAL_API_KEY": secret,
                }
            else:
                prof_try = {"CLOUDFLARE_API_TOKEN": secret}
            console.print("[dim]Looking up Cloudflare accounts (GET /accounts)…[/dim]")
            rows, acc_err = cloudflare_discover_accounts_from_credentials(prof_try)
            should_apply, account_id = pick_account_id_after_discovery(
                rows, acc_err, saved_account_id=""
            )

            config = load_pas_config("cloudflare")
            if "profiles" not in config:
                config["profiles"] = {}
            if "capabilities" not in config or not isinstance(config.get("capabilities"), list):
                config["capabilities"] = list(DEFAULT_NEW_PROFILE_CAPABILITIES)
            if "provider" not in config:
                config["provider"] = "cloudflare"

            if email:
                config["profiles"][new_name] = {
                    "CLOUDFLARE_EMAIL": email,
                    "CLOUDFLARE_GLOBAL_API_KEY": secret,
                }
            else:
                config["profiles"][new_name] = {"CLOUDFLARE_API_TOKEN": secret}
            if should_apply and account_id:
                config["profiles"][new_name]["CLOUDFLARE_ACCOUNT_ID"] = account_id

            set_active_profile_on_config(config, new_name)
            save_pas_config("cloudflare", config)
            console.print(f"[green]Created and switched to profile: {new_name}[/green]")
            run_validate(new_name, None)

        elif action == "delete":
            del_choices = [{"title": p, "value": p} for p in profiles]
            del_choices.append({"title": "[Back]", "value": None})
            formatted_del = format_menu_choices(del_choices, title_field="title", value_field="value")
            console.print("\n[bold red]Select profile to DELETE:[/bold red]")
            to_delete = prompt_toolkit_menu(formatted_del)

            if not to_delete:
                continue

            if prompt_yes_no(f"Delete profile '{to_delete}'?", default=False):
                config_file = get_pas_config_dir() / "cloudflare.json"
                try:
                    raw = json.loads(config_file.read_text())
                    if "profiles" in raw and to_delete in raw["profiles"]:
                        del raw["profiles"][to_delete]
                        if raw.get("active_profile_id") == to_delete:
                            raw["active_profile_id"] = None
                        if raw.get("current_profile") == to_delete:
                            raw["current_profile"] = None
                        save_pas_config("cloudflare", raw)
                        console.print(f"[green]Deleted profile: {to_delete}[/green]")
                except Exception as e:
                    console.print(f"[red]Error deleting profile: {e}[/red]")


def main() -> None:
    parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
    parser.add_argument("--list", action="store_true", help="List all profiles and exit")
    parser.add_argument("--current", action="store_true", help="Show current profile and exit")
    parser.add_argument("--switch", metavar="PROFILE", help="Switch to a specific profile")
    parser.add_argument(
        "--validate",
        metavar="PROFILE",
        nargs="?",
        const="",
        help="Run API health check; default = active profile",
    )
    parser.add_argument(
        "--account-id",
        metavar="ID",
        dest="account_id",
        help=(
            "Account id (hex from dash.cloudflare.com URL /accounts/<id>/… or Overview sidebar); "
            "overrides saved value for --validate probes"
        ),
    )
    parser.add_argument(
        "--migrate",
        metavar="NAME",
        nargs="?",
        const="",
        help="Migrate flat root keys into profiles[NAME] (default name: default)",
    )
    parser.add_argument("--edit", metavar="PROFILE", help="Edit an existing profile (interactive prompts)")
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="With --edit, skip API health check after save",
    )
    parser.add_argument(
        "--list-accounts",
        nargs="?",
        const="",
        default=None,
        metavar="PROFILE",
        help="List Cloudflare account names/ids (GET /accounts); default = active profile",
    )
    parser.add_argument(
        "--secretize",
        action="store_true",
        help="Rewrite ~/.pas/cloudflare.json so credential fields use SEC: keychain refs (run after upgrading pas_core)",
    )
    args = parser.parse_args()

    if args.list:
        profiles = list_profiles()
        current = get_current_profile()
        for p in profiles:
            status = "*" if p == current else " "
            print(f"{status} {p}")
        return

    if args.current:
        print(get_current_profile() or "None")
        return

    if args.switch:
        profiles = list_profiles()
        if args.switch not in profiles:
            console.print(f"[red]Profile '{args.switch}' not found.[/red]")
            sys.exit(1)
        config = load_pas_config("cloudflare")
        set_active_profile_on_config(config, args.switch)
        save_pas_config("cloudflare", config)
        console.print(f"[green]Switched to profile: {args.switch}[/green]")
        return

    if args.validate is not None:
        pid = args.validate if args.validate != "" else None
        run_validate(pid, args.account_id)
        return

    if args.migrate is not None:
        migrate_flat_to_profile(args.migrate if args.migrate != "" else None)
        return

    if args.list_accounts is not None:
        run_list_accounts(args.list_accounts if args.list_accounts != "" else None)
        return

    if args.secretize:
        path = get_pas_config_dir() / "cloudflare.json"
        if not path.is_file():
            console.print("[red]~/.pas/cloudflare.json not found.[/red]")
            sys.exit(1)
        try:
            raw = json.loads(path.read_text())
        except Exception as e:
            console.print(f"[red]Could not parse cloudflare.json: {e}[/red]")
            sys.exit(1)
        save_pas_config("cloudflare", raw)
        console.print(
            "[green]Rewrote ~/.pas/cloudflare.json: credentials stored via SEC: + OS keychain where applicable.[/green]"
        )
        return

    if args.edit:
        edit_profile(args.edit, run_health=not args.no_validate)
        return

    console.print(Panel(TOOL_DESCRIPTION, title=TOOL_TITLE, border_style="blue"))
    manage_profiles()


if __name__ == "__main__":
    main()
