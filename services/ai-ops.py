#!/usr/bin/env python3
"""
@pas-executable
AI Operations Manager for PAS.
Manages AI providers, profiles, and model configurations in ~/.pas/ai-models.json.

Subcommands:
- list: List all configured profiles and models.
- add-profile: Add a new AI provider profile (e.g., OpenRouter with a specific key).
- add-config: Create a configuration combining a profile and a model.
- set-active: Set the active configuration for a specific app (e.g., 'pas').
- remove-profile: Remove an existing profile.
- remove-config: Remove an existing configuration.
- test: Test a configuration by sending a simple query.
"""

import argparse
import json
import sys
import urllib.request
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List

# Add project root to sys.path
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    load_pas_config,
    save_pas_config,
    format_menu_choices,
    prompt_toolkit_menu,
    console,
    choice,
    Menu
)
from rich.panel import Panel
from rich.table import Table
import questionary

CONFIG_SERVICE = "ai-models"

def get_config() -> Dict[str, Any]:
    """Load the ai-models configuration."""
    return load_pas_config(CONFIG_SERVICE)

def save_config(config: Dict[str, Any]):
    """Save the ai-models configuration."""
    save_pas_config(CONFIG_SERVICE, config)

def validate_openrouter_token(token: str) -> tuple[bool, Dict[str, Any]]:
    """Validate an OpenRouter token."""
    url = "https://openrouter.ai/api/v1/auth/key"
    headers = {
        "Authorization": f"Bearer {token}",
        "HTTP-Referer": "https://github.com/nextoken/pas",
        "X-Title": "PAS Toolkit",
        "User-Agent": "PAS-Toolkit/1.0"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as res:
            if res.getcode() == 200:
                resp_data = json.loads(res.read().decode("utf-8"))
                return True, resp_data.get("data", {})
    except Exception as e:
        console.print(f"[red]Validation failed: {e}[/red]")
    return False, {}

def get_openrouter_models() -> List[Dict[str, Any]]:
    """Fetch available models from OpenRouter."""
    url = "https://openrouter.ai/api/v1/models"
    try:
        req = urllib.request.Request(url, headers={
            "HTTP-Referer": "https://github.com/nextoken/pas",
            "X-Title": "PAS Toolkit"
        })
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read().decode("utf-8"))
            return data.get("data", [])
    except Exception as e:
        console.print(f"[yellow]Warning: Could not fetch models: {e}[/yellow]")
    return []

def cmd_list(args):
    """List profiles and configurations."""
    config = get_config()
    
    # Profiles Table
    profiles = config.get("profiles", {})
    if profiles:
        table = Table(title="AI Profiles")
        table.add_column("Profile ID", style="cyan")
        table.add_column("Provider", style="green")
        table.add_column("Token (Masked)", style="yellow")
        
        for p_id, p_data in profiles.items():
            token = p_data.get("token", "")
            masked = token[:8] + "..." + token[-4:] if len(token) > 12 else "****"
            table.add_row(p_id, p_data.get("provider", "N/A"), masked)
        console.print(table)
    else:
        console.print("[yellow]No profiles configured.[/yellow]")

    # Configs Table
    configs = config.get("configs", {})
    
    # Get active config for 'pas' as a reference
    pas_config = load_pas_config("pas")
    pas_active_id = pas_config.get("active_ai_config_id")
    
    if configs:
        table = Table(title="AI Configurations")
        table.add_column("Status (pas)", width=12)
        table.add_column("Config ID", style="cyan")
        table.add_column("Profile", style="green")
        table.add_column("Model", style="magenta")
        
        for c_id, c_data in configs.items():
            status = "[bold green]ACTIVE[/bold green]" if c_id == pas_active_id else ""
            table.add_row(status, c_id, c_data.get("profile", "N/A"), c_data.get("model", "N/A"))
        console.print(table)
    else:
        console.print("[yellow]No configurations created.[/yellow]")

def cmd_add_profile(args):
    """Add a new AI profile."""
    config = get_config()
    if "profiles" not in config:
        config["profiles"] = {}

    providers = ["openrouter"] # Currently only openrouter is supported
    provider = questionary.select("Select Provider:", choices=providers).ask()
    if not provider: return

    profile_id = questionary.text("Enter a unique Profile ID (e.g., 'work', 'personal'):").ask()
    if not profile_id: return
    if profile_id in config["profiles"]:
        if not questionary.confirm(f"Profile '{profile_id}' already exists. Overwrite?").ask():
            return

    token = questionary.password(f"Enter {provider} API Token:").ask()
    if not token: return

    console.print("[cyan]Validating token...[/cyan]")
    valid, meta = validate_openrouter_token(token)
    if not valid:
        console.print("[red]Token validation failed. Please check your token.[/red]")
        if not questionary.confirm("Save anyway?").ask():
            return

    config["profiles"][profile_id] = {
        "provider": provider,
        "token": token
    }
    save_config(config)
    console.print(f"[green]Profile '{profile_id}' saved![/green]")

def cmd_add_config(args):
    """Add a new AI configuration."""
    config = get_config()
    profiles = config.get("profiles", {})
    if not profiles:
        console.print("[yellow]No profiles found. Please add a profile first.[/yellow]")
        return

    profile_id = questionary.select("Select Profile:", choices=list(profiles.keys())).ask()
    if not profile_id: return

    provider = profiles[profile_id].get("provider")
    model = None

    if provider == "openrouter":
        console.print("[cyan]Fetching models...[/cyan]")
        all_models = get_openrouter_models()
        if all_models:
            # Sort and filter for a better UI
            choices = []
            for m in all_models[:30]: # Limit to top 30 for now
                m_id = m.get("id")
                choices.append(questionary.Choice(title=f"{m_id}", value=m_id))
            
            choices.append(questionary.Choice(title="[Other/Manual]", value="OTHER"))
            model = questionary.select("Select Model:", choices=choices).ask()
            if model == "OTHER":
                model = questionary.text("Enter Model ID:").ask()
        else:
            model = questionary.text("Enter Model ID (e.g., 'openai/gpt-4o'):").ask()

    if not model: return

    config_id = f"{profile_id}:{model}"
    if "configs" not in config:
        config["configs"] = {}
    
    config["configs"][config_id] = {
        "profile": profile_id,
        "model": model
    }
    
    # Check if 'pas' has an active config, if not, offer to set this one
    pas_config = load_pas_config("pas")
    if not pas_config.get("active_ai_config_id"):
        if questionary.confirm(f"Set '{config_id}' as active for 'pas'?").ask():
            pas_config["active_ai_config_id"] = config_id
            save_pas_config("pas", pas_config)

    save_config(config)
    console.print(f"[green]Configuration '{config_id}' saved![/green]")

def cmd_set_active(args):
    """Set the active configuration for a specific app."""
    config = get_config()
    configs = config.get("configs", {})
    if not configs:
        console.print("[yellow]No configurations found.[/yellow]")
        return

    # For now, we mainly support 'pas'
    app = questionary.select("Select App to configure:", choices=["pas", "Other..."]).ask()
    if not app: return
    if app == "Other...":
        app = questionary.text("Enter App name (config file will be ~/.pas/APP.json):").ask()
    if not app: return

    app_config = load_pas_config(app)
    current_active = app_config.get("active_ai_config_id")

    choices = []
    for c_id in configs:
        is_active = c_id == current_active
        choices.append(questionary.Choice(title=f"{c_id} {'(ACTIVE)' if is_active else ''}", value=c_id))

    selected = questionary.select(f"Select Active Configuration for '{app}':", choices=choices).ask()
    if selected:
        app_config["active_ai_config_id"] = selected
        save_pas_config(app, app_config)
        console.print(f"[green]Active configuration for '{app}' set to '{selected}'.[/green]")

def main():
    parser = argparse.ArgumentParser(description="Manage AI providers and profiles.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", help="List profiles and configs")
    subparsers.add_parser("add-profile", help="Add a new profile")
    subparsers.add_parser("add-config", help="Add a new config")
    subparsers.add_parser("set-active", help="Set active config for an app")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "add-profile":
        cmd_add_profile(args)
    elif args.command == "add-config":
        cmd_add_config(args)
    elif args.command == "set-active":
        cmd_set_active(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
