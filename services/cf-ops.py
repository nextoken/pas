#!/usr/bin/env python3
"""
@pas-executable
Manage Cloudflare profiles in ~/.pas/cf.json.
"""

import os
import sys
import argparse
import json
from pathlib import Path
from typing import Optional, Dict, Any, List

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from rich.console import Console
from rich.panel import Panel
from helpers.cloudflare import (
    get_token_info,
    get_user_details
)
from helpers.core import (
    load_pas_config,
    save_pas_config,
    format_menu_choices,
    prompt_toolkit_menu,
    prompt_yes_no,
    console,
    get_pas_config_dir,
    safe_write_json
)

# --- Tool Identity ---
TOOL_ID = "cf-ops"
TOOL_TITLE = "Cloudflare Profile Manager"
TOOL_SHORT_DESC = "Manage Cloudflare profiles and authentication."
TOOL_DESCRIPTION = (
    "This tool allows you to manage multiple Cloudflare profiles (organizations/accounts) "
    "stored in ~/.pas/cf.json. You can list, switch, create, and delete profiles."
)
# ---------------------

def list_profiles() -> List[str]:
    """List all available profiles from cf.json."""
    config_file = get_pas_config_dir() / "cf.json"
    if not config_file.exists():
        return []
    try:
        data = json.loads(config_file.read_text())
        profiles = data.get("profiles", {})
        return sorted(list(profiles.keys()))
    except Exception:
        return []

def get_current_profile() -> Optional[str]:
    """Get the currently active profile."""
    config_file = get_pas_config_dir() / "cf.json"
    if not config_file.exists():
        return None
    try:
        data = json.loads(config_file.read_text())
        return data.get("current_profile")
    except Exception:
        return None

def get_raw_config() -> Dict[str, Any]:
    """Load the raw config file without secretization/profile merging."""
    config_file = get_pas_config_dir() / "cf.json"
    if not config_file.exists():
        return {}
    try:
        return json.loads(config_file.read_text())
    except Exception:
        return {}

def migrate_flat_to_profile(profile_name: Optional[str] = None):
    """Migrate flat config to a profile-based structure."""
    raw_config = get_raw_config()
    
    # Check if migration is needed (no profiles key)
    if "profiles" in raw_config:
        console.print("[yellow]Configuration is already profile-based.[/yellow]")
        return

    # Extract flat config (excluding metadata)
    flat_config = {k: v for k, v in raw_config.items() if not k.endswith("_meta")}
    if not flat_config:
        console.print("[yellow]No existing configuration found to migrate.[/yellow]")
        return

    token = load_pas_config("cf").get("CLOUDFLARE_API_TOKEN")
    
    if not profile_name and token:
        console.print("[cyan]Attempting to discover organization name via API token...[/cyan]")
        token_info = get_token_info(token)
        if token_info:
            profile_name = token_info.get("name") or token_info.get("label")
            if profile_name:
                console.print(f"[green]Discovered profile name: {profile_name}[/green]")
        
        if not profile_name:
            user_info = get_user_details(token)
            if user_info and user_info.get("success"):
                profile_name = user_info.get("result", {}).get("email")
                if profile_name:
                    console.print(f"[green]Using user email as profile name: {profile_name}[/green]")

    if not profile_name:
        profile_name = input("Enter name for the migrated profile [default]: ").strip() or "default"

    if prompt_yes_no(f"Migrate existing configuration to profile '{profile_name}'?", default=True):
        # We use save_pas_config with the flat config to create the profile structure
        # load_pas_config handles de-secretization so we get real values to re-secretize correctly
        config_to_save = load_pas_config("cf")
        save_pas_config("cf", config_to_save, profile=profile_name)
        console.print(f"[green]Migration complete. Profile '{profile_name}' is now active.[/green]")

def manage_profiles():
    """Interactive menu for profile management."""
    while True:
        current = get_current_profile()
        profiles = list_profiles()
        raw_config = get_raw_config()
        has_flat_config = "profiles" not in raw_config and any(k for k in raw_config if not k.endswith("_meta"))
        
        console.print(f"\n[bold cyan]Current Profile:[/bold cyan] {current or '[None]'}")
        
        menu_items = []
        if has_flat_config:
            menu_items.append({"title": "[MIGRATE] Move existing flat config to a profile", "value": ("migrate", None)})

        if profiles:
            for p in profiles:
                status = " (ACTIVE)" if p == current else ""
                menu_items.append({"title": f"Switch to: {p}{status}", "value": ("switch", p)})
        
        menu_items.append({"title": "Create New Profile", "value": ("create", None)})
        
        if profiles:
            menu_items.append({"title": "Delete a Profile", "value": ("delete", None)})
            
        menu_items.append({"title": "[Quit]", "value": ("quit", None)})
        
        formatted_choices = format_menu_choices(menu_items, title_field="title", value_field="value")
        console.print("\n[bold]Select an action:[/bold]")
        selected = prompt_toolkit_menu(formatted_choices)
        
        if not selected or selected[0] == "quit":
            break
            
        action, profile_name = selected
        
        if action == "migrate":
            migrate_flat_to_profile()
            
        elif action == "switch":
            # Just updating current_profile is enough as save_pas_config handles it
            # But we need to load the profile's data first
            config = load_pas_config("cf", profile=profile_name)
            save_pas_config("cf", config, profile=profile_name)
            console.print(f"[green]Switched to profile: {profile_name}[/green]")
            
        elif action == "create":
            new_name = input("Enter name for new profile: ").strip()
            if not new_name:
                continue
            if new_name in profiles:
                console.print(f"[yellow]Profile '{new_name}' already exists.[/yellow]")
                continue
            
            token = input("Enter Cloudflare API Token for this profile: ").strip()
            account_id = input("Enter Cloudflare Account ID for this profile (optional): ").strip()
            
            new_config = {"CLOUDFLARE_API_TOKEN": token}
            if account_id:
                new_config["CLOUDFLARE_ACCOUNT_ID"] = account_id
                
            save_pas_config("cf", new_config, profile=new_name)
            console.print(f"[green]Created and switched to profile: {new_name}[/green]")
            
        elif action == "delete":
            del_choices = [{"title": p, "value": p} for p in profiles]
            del_choices.append({"title": "[Back]", "value": None})
            formatted_del = format_menu_choices(del_choices, title_field="title", value_field="value")
            console.print("\n[bold red]Select profile to DELETE:[/bold red]")
            to_delete = prompt_toolkit_menu(formatted_del)
            
            if not to_delete:
                continue
                
            if prompt_yes_no(f"Are you sure you want to delete profile '{to_delete}'?", default=False):
                config_file = get_pas_config_dir() / "cf.json"
                try:
                    data = json.loads(config_file.read_text())
                    if "profiles" in data and to_delete in data["profiles"]:
                        del data["profiles"][to_delete]
                        if data.get("current_profile") == to_delete:
                            data["current_profile"] = None
                        # We use raw write here to preserve other SEC: refs
                        safe_write_json(config_file, data)
                        console.print(f"[green]Deleted profile: {to_delete}[/green]")
                except Exception as e:
                    console.print(f"[red]Error deleting profile: {e}[/red]")

def main():
    parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
    parser.add_argument("--list", action="store_true", help="List all profiles and exit")
    parser.add_argument("--current", action="store_true", help="Show current profile and exit")
    parser.add_argument("--switch", metavar="PROFILE", help="Switch to a specific profile")
    parser.add_argument("--migrate", metavar="NAME", nargs="?", const="", help="Migrate flat config to a profile (optionally provide a name)")
    args = parser.parse_args()

    if args.list:
        profiles = list_profiles()
        current = get_current_profile()
        for p in profiles:
            status = "*" if p == current else " "
            print(f"{status} {p}")
        return

    if args.current:
        current = get_current_profile()
        print(current or "None")
        return

    if args.switch:
        profiles = list_profiles()
        if args.switch not in profiles:
            console.print(f"[red]Profile '{args.switch}' not found.[/red]")
            sys.exit(1)
        config = load_pas_config("cf", profile=args.switch)
        save_pas_config("cf", config, profile=args.switch)
        console.print(f"[green]Switched to profile: {args.switch}[/green]")
        return

    if args.migrate is not None:
        migrate_flat_to_profile(args.migrate if args.migrate != "" else None)
        return

    # Default to interactive menu
    console.print(Panel(TOOL_DESCRIPTION, title=TOOL_TITLE, border_style="blue"))
    manage_profiles()

if __name__ == "__main__":
    main()
