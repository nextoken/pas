#!/usr/bin/env python3
"""
@pas-executable
Manage multiple Supabase accounts and organizations.
"""

import sys
from pathlib import Path
# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
import sys
from pathlib import Path

# Add the directory containing this script to sys.path to allow imports from nearby files
sys.path.append(str(Path(__file__).resolve().parent))

import os
import argparse
import json
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

import questionary
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helpers.core import (
    load_pas_config, 
    save_pas_config, 
    detect_supabase_binary,
    run_command,
    prompt_toolkit_menu,
    format_menu_choices,
    copy_to_clipboard
)
from helpers.supabase import (
    supabase_api_request,
    get_user_email,
    get_active_token,
    check_local_link,
    get_org_for_project,
    detect_native_login,
    get_native_token,
    get_supabase_env,
    get_api_keys
)

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

# Configuration key in ~/.pas/supabase.json
CONFIG_SERVICE = "supabase"
console = Console()

def render_dashboard(native_status, active_org_display, link_status, org_warning, linked_org_name=None):
    """Render the status dashboard using Rich."""
    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 1), border_style="bright_blue")
    table.add_column("Key", style="cyan", width=20)
    table.add_column("Value", style="white")

    table.add_row("Global CLI Login", native_status)
    table.add_row("Active PAS Org", active_org_display)
    table.add_row("Local Project", link_status)
    
    if linked_org_name:
        table.add_row("Project Org", linked_org_name)

    panel = Panel(
        table,
        title="[bold blue]Supabase Accounts (sb-acs)[/bold blue]",
        expand=False,
        border_style="bright_blue"
    )
    
    console.print("\n")
    console.print(panel)
    
    if org_warning:
        clean_warning = org_warning.strip()
        style = "bold yellow" if "[!]" in clean_warning else "cyan"
        console.print(f"[{style}]{clean_warning}[/{style}]")

def format_size(bytes_num: float) -> str:
    """Format bytes to human readable string."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f}{unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f}PB"

def format_usage(usage_val: float, limit_val: float, unit: str = "bytes") -> str:
    """Format usage against a limit with color coding."""
    if unit == "bytes":
        usage_str = format_size(usage_val)
        limit_str = format_size(limit_val)
    else:
        usage_str = str(usage_val)
        limit_str = str(limit_val)
    
    percent = (usage_val / limit_val) * 100 if limit_val > 0 else 0
    color = "bright_green"
    if percent > 90:
        color = "bold red"
    elif percent > 75:
        color = "bold yellow"
    
    return f"[{color}]{usage_str}/{limit_str} ({percent:.1f}%)[/{color}]"

def install_supabase() -> bool:
    """Prompt to install supabase via Homebrew."""
    print("Supabase CLI binary not found.")
    if not questionary.confirm("Would you like to install Supabase CLI via Homebrew?").ask():
        return False
    
    print("Installing Supabase CLI...")
    try:
        subprocess.run(["brew", "install", "supabase"], check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: Failed to install Supabase CLI. Please install it manually.")
        return False

def list_organizations(config: Dict[str, Any], native_org_ids: List[str] = None):
    """List stored organizations and mark the active and native ones."""
    orgs = config.get("organizations", {})
    if not orgs:
        print("\nNo organizations found. Use 'Add Organization' to get started.")
        return

    updated = False
    tokens_checked = {}
    
    for org_id, data in orgs.items():
        if data.get("email") == "N/A" or not data.get("email") or data.get("email") == "Unknown":
            token = data.get("access_token")
            if token:
                if token not in tokens_checked:
                    print(f"Fetching missing email for {data.get('name')}...")
                    tokens_checked[token] = get_user_email(token) or "N/A"
                
                if tokens_checked[token] != "N/A":
                    data["email"] = tokens_checked[token]
                    updated = True
    
    if updated:
        save_pas_config(CONFIG_SERVICE, config)

    print("\n" + f"{'':<4} {'Organization Name':<30} {'User Email':<30} {'ID'}")
    print("-" * 90)
    active_id = config.get("active_org_id")
    for org_id, data in orgs.items():
        marker = "[*]" if org_id == active_id else "[ ]"
        name = data.get('name', 'Unknown')
        
        if native_org_ids and org_id in native_org_ids:
            name = f"{name} (native)"
            
        email = data.get('email', 'N/A')
        print(f"{marker:<4} {name:<30} {email:<30} {org_id}")

def add_organization(config: Dict[str, Any]):
    """Add a new organization by providing an Access Token."""
    print("\nTo add an organization, you need a Supabase Access Token.")
    print("Create one at: https://supabase.com/dashboard/account/tokens")
    
    token = questionary.password("Enter Supabase Access Token:").ask()
    if not token:
        print("Cancelled.")
        return

    print("Fetching user info and organizations...")
    email = get_user_email(token)
    data = supabase_api_request("organizations", token)
    if not data:
        print("Failed to fetch organizations. Please check your token.")
        return

    if not isinstance(data, list):
        print(f"Unexpected API response format: {data}")
        return

    if "organizations" not in config:
        config["organizations"] = {}

    for org in data:
        org_id = org.get("id")
        org_name = org.get("name")
        config["organizations"][org_id] = {
            "name": org_name,
            "access_token": token,
            "email": email or "Unknown"
        }
        print(f"Added/Updated: {org_name} ({org_id}) - {email or 'No email'}")

    if not config.get("active_org_id") and data:
        config["active_org_id"] = data[0].get("id")
        print(f"Set '{data[0].get('name')}' as active organization.")

    save_pas_config(CONFIG_SERVICE, config)

def switch_organization(config: Dict[str, Any]):
    """Switch the active organization."""
    orgs = config.get("organizations", {})
    if not orgs:
        print("No organizations to switch to.")
        return

    items = list(orgs.items())
    active_id = config.get("active_org_id")
    
    # Prepare org list for format_menu_choices
    org_list = []
    for org_id, data in items:
        label = f"{data.get('name')} <{data.get('email', 'N/A')}> ({org_id})"
        if org_id == active_id:
            label = f"[*] {label}"
        org_list.append({"label": label, "id": org_id})

    choices = format_menu_choices(org_list, title_field="label", value_field="id")
    choices.append(questionary.Choice(title="q. Cancel", value="CANCEL"))

    console.print("\n[bold]Select Organization to Activate:[/bold] (Use arrows or press hotkey)")
    selected_id = prompt_toolkit_menu(choices)

    if selected_id and selected_id not in ("CANCEL", "Cancel"):
        config["active_org_id"] = selected_id
        save_pas_config(CONFIG_SERVICE, config)
        print(f"Switched to organization: {orgs[selected_id].get('name')}")

def import_native_session(config: Dict[str, Any], native_session: Dict[str, Any]):
    """Attempt to import the global CLI session into PAS config."""
    if not native_session or not native_session.get("orgs"):
        print("No global session information to import.")
        return

    print("\nAttempting to retrieve global session token...")
    token = get_native_token()
    
    if not token:
        print("Could not automatically retrieve token (it might be locked or hidden).")
        print("Please provide it manually to enable PAS management.")
        print("Create one at: https://supabase.com/dashboard/account/tokens")
        token = questionary.password("Enter Access Token (or Enter to skip):").ask()
        if not token:
            print("Cancelled.")
            return

    print("Importing metadata and syncing organizations...")
    if "organizations" not in config:
        config["organizations"] = {}

    for org in native_session.get("orgs", []):
        org_id = org.get("id")
        org_name = org.get("name")
        email = get_user_email(token)
        config["organizations"][org_id] = {
            "name": org_name,
            "access_token": token,
            "email": email or "Unknown"
        }
        print(f"Managed in PAS: {org_name} ({org_id}) - {email or 'No email'}")

    if native_session.get("orgs") and not config.get("active_org_id"):
        config["active_org_id"] = native_session["orgs"][0]["id"]

    save_pas_config(CONFIG_SERVICE, config)
    print("\nSession successfully imported to ~/.pas/supabase.json")

def list_projects(token: str, supabase_bin: Path, org_id: str = None):
    """List projects for the active organization."""
    if not token:
        token = get_native_token()

    console.print("[cyan]Fetching projects...[/cyan]")
    
    projects = None
    if token:
        projects = supabase_api_request("projects", token)
    
    if not projects:
        env = get_supabase_env(token) if token else {k: v for k, v in os.environ.items() if k != "SUPABASE_ACCESS_TOKEN"}
        res = subprocess.run(
            [str(supabase_bin), "projects", "list", "--output", "json"],
            capture_output=True,
            text=True,
            check=False,
            env=env
        )
        if res.returncode == 0:
            try:
                projects = json.loads(res.stdout)
            except:
                pass

    if not projects:
        console.print("[red]No projects found or error fetching projects.[/red]")
        return

    # Note: The Supabase Management API doesn't expose resource usage endpoints
    # Resource usage (database size, storage size, egress) is only available
    # through the Supabase Dashboard UI, not via API
    # We'll check if projects themselves contain any usage info, otherwise show N/A

    table = Table(title="Supabase Projects", box=box.ROUNDED, show_lines=True)
    table.add_column("Project Name", style="bold white")
    table.add_column("Ref / Status", style="dim")
    table.add_column("Database Usage (500MB Free)", style="white")
    table.add_column("Storage Usage (1GB Free)", style="white")
    table.add_column("Egress (2GB DB / 5GB ST)", style="white")

    for p in projects:
        name = p.get("name")
        ref = p.get("id")
        status = p.get("status")
        
        db_usage_str = "N/A"
        st_usage_str = "N/A"
        egress_str = "N/A"
        
        # Check if project object itself contains usage info
        # The Supabase Management API doesn't expose resource usage endpoints
        # Resource usage is only available through the Dashboard UI
        project_db_size = p.get("db_size") or p.get("database_size")
        project_storage_size = p.get("storage_size")
        project_db_egress = p.get("db_egress") or p.get("database_egress")
        project_storage_egress = p.get("storage_egress")
        
        if project_db_size is not None or project_storage_size is not None:
            # Project object contains usage info
            db_size = project_db_size if isinstance(project_db_size, (int, float)) else (project_db_size.get("usage", 0) if isinstance(project_db_size, dict) else 0)
            st_size = project_storage_size if isinstance(project_storage_size, (int, float)) else (project_storage_size.get("usage", 0) if isinstance(project_storage_size, dict) else 0)
            db_egress = project_db_egress if isinstance(project_db_egress, (int, float)) else (project_db_egress.get("usage", 0) if isinstance(project_db_egress, dict) else 0)
            st_egress = project_storage_egress if isinstance(project_storage_egress, (int, float)) else (project_storage_egress.get("usage", 0) if isinstance(project_storage_egress, dict) else 0)
            
            db_usage_str = format_usage(db_size, 500 * 1024 * 1024)
            st_usage_str = format_usage(st_size, 1024 * 1024 * 1024)
            db_egress_fmt = format_usage(db_egress, 2 * 1024 * 1024 * 1024)
            st_egress_fmt = format_usage(st_egress, 5 * 1024 * 1024 * 1024)
            egress_str = f"DB: {db_egress_fmt}\nST: {st_egress_fmt}"
        else:
            # Resource usage not available via API - show dashboard link with URL
            # Format: https://supabase.com/dashboard/org/{org_id}/usage?projectRef={project_ref}
            if org_id:
                dashboard_url = f"https://supabase.com/dashboard/org/{org_id}/usage?projectRef={ref}"
            else:
                # Fallback to project dashboard if org_id not available
                dashboard_url = f"https://supabase.com/dashboard/project/{ref}"
            db_usage_str = f"[link={dashboard_url}][cyan]See Dashboard[/cyan][/link]\n[dim]{dashboard_url}[/dim]"
            st_usage_str = f"[link={dashboard_url}][cyan]See Dashboard[/cyan][/link]\n[dim]{dashboard_url}[/dim]"
            egress_str = f"[link={dashboard_url}][cyan]See Dashboard[/cyan][/link]\n[dim]{dashboard_url}[/dim]"
        
        status_color = "green" if status == "ACTIVE_HEALTHY" else "yellow"
        ref_status = f"[white]{ref}[/white]\n[{status_color}]{status}[/{status_color}]"
        
        table.add_row(name, ref_status, db_usage_str, st_usage_str, egress_str)

    console.print("\n")
    console.print(table)
    
    # Add note about resource usage availability
    if any("See Dashboard" in str(row) for row in table.rows):
        console.print("\n[dim]Note: Resource usage (database size, storage size, egress) is not available via the Management API.[/dim]")
        console.print("[dim]Click the 'See Dashboard' links above to view usage details for each project.[/dim]")

def link_project(token: str, supabase_bin: Path):
    """Link a Supabase project."""
    print("Fetching projects to link...")
    
    projects = None
    if token:
        projects = supabase_api_request("projects", token)
    
    if not projects:
        env = get_supabase_env(token) if token else {k: v for k, v in os.environ.items() if k != "SUPABASE_ACCESS_TOKEN"}
        res = subprocess.run(
            [str(supabase_bin), "projects", "list", "--output", "json"],
            capture_output=True,
            text=True,
            check=False,
            env=env
        )
        if res.returncode == 0:
            try:
                projects = json.loads(res.stdout)
            except:
                pass

    if not projects:
        print("No projects found.")
        return

    choices = format_menu_choices(projects, title_field="name", value_field="id")
    choices.append(questionary.Choice(title="q. Cancel", value="CANCEL"))

    console.print("\n[bold]Select Project to Link:[/bold] (Use arrows or press 1-9, q)")
    hotkeys = [str(i) for i in range(1, len(projects) + 1)] + ['q']
    project_ref = prompt_toolkit_menu(choices, hotkeys=hotkeys)

    if project_ref and project_ref not in ("CANCEL", "Cancel"):
        print(f"Linking project {project_ref}...")
        env = get_supabase_env(token) if token else {k: v for k, v in os.environ.items() if k != "SUPABASE_ACCESS_TOKEN"}
        res = run_command([str(supabase_bin), "link", "--project-ref", project_ref], env=env, capture_output=False)
        
        if res.returncode == 0:
            print("Successfully linked project.")
        else:
            print(f"Error linking project: {res.stderr}")

def login_isolated(config: Dict[str, Any], supabase_bin: Path):
    """Run supabase login in an isolated environment to fetch a token into PAS."""
    print(f"\nStarting isolated Supabase login...")
    print("This will open your browser to authenticate.")
    
    print("\nStep 1: Create an Access Token at:")
    print("https://supabase.com/dashboard/account/tokens")
    
    if sys.platform == "darwin":
        subprocess.run(["open", "https://supabase.com/dashboard/account/tokens"])
    
    token = questionary.password("Step 2: Paste your new Access Token here:").ask()
    if not token:
        print("Cancelled.")
        return

    print("\nStep 3: Verifying token and fetching organizations...")
    data = supabase_api_request("organizations", token)
    if data and isinstance(data, list):
        if "organizations" not in config:
            config["organizations"] = {}
        
        email = get_user_email(token)
        for org in data:
            org_id = org.get("id")
            org_name = org.get("name")
            config["organizations"][org_id] = {
                "name": org_name,
                "access_token": token,
                "email": email or "Unknown"
            }
            print(f"Added organization: {org_name} ({org_id})")

        if data and not config.get("active_org_id"):
            config["active_org_id"] = data[0].get("id")
        
        save_pas_config(CONFIG_SERVICE, config)
        print("\nSetup complete! Token saved to ~/.pas/supabase.json")
    else:
        print("Failed to fetch organization details with that token. Please check it.")

def sync_to_global(config: Dict[str, Any], supabase_bin: Path):
    """Sync the active PAS org token to the global CLI login."""
    active_id = config.get("active_org_id")
    if not active_id:
        print("No active PAS organization selected.")
        return
    
    org_data = config.get("organizations", {}).get(active_id)
    if not org_data:
        print("Selected organization data not found.")
        return

    token = org_data.get("access_token")
    if not token:
        print(f"No access token found for organization '{org_data.get('name')}'.")
        return

    print(f"\nSyncing '{org_data.get('name')}' to global CLI...")
    print("Note: This will interact with your system's global Supabase configuration.")
    if sys.platform == "darwin":
        print("      You may see a macOS Keychain prompt. Allow it to save globally.")
    
    if not questionary.confirm("Proceed with global sync?").ask():
        print("Cancelled.")
        return

    cmd = [str(supabase_bin), "login", "--token", token]
    cwd = Path.home() if os.path.isdir(".env") else None

    print(f"\nRunning: {' '.join([cmd[0], cmd[1], cmd[2], '********'])}")
    res = subprocess.run(cmd, capture_output=False, check=False, cwd=cwd)
    
    if res.returncode == 0:
        print(f"\n[✓] Successfully synced '{org_data.get('name')}' to global CLI.")
        print("The standard 'supabase' command will now use this account.")
    else:
        print("\n[!] Global sync failed or was cancelled.")

def copy_api_info(config: Dict[str, Any], token: str):
    """Copy API keys or access tokens to clipboard."""
    choices = [
        questionary.Choice("1. Copy Active Org Access Token", value="ORG_TOKEN"),
        questionary.Choice("2. Copy Project API Keys (Anon/Service Role)", value="PROJECT_KEYS"),
        questionary.Choice("q. Cancel", value="CANCEL")
    ]
    
    console.print("\n[bold]Select what to copy:[/bold]")
    choice = prompt_toolkit_menu(choices, hotkeys=['1', '2', 'q'])
    
    if not choice or choice == "CANCEL":
        return

    if choice == "ORG_TOKEN":
        if token:
            if copy_to_clipboard(token):
                console.print("[green]✅ Access Token copied to clipboard![/green]")
            else:
                console.print("[red]❌ Failed to copy to clipboard.[/red]")
        else:
            console.print("[yellow]⚠️ No active organization token found.[/yellow]")
            
    elif choice == "PROJECT_KEYS":
        console.print("[cyan]Fetching projects...[/cyan]")
        projects = supabase_api_request("projects", token)
        if not projects or not isinstance(projects, list):
            console.print("[red]Could not fetch projects.[/red]")
            return
            
        project_choices = format_menu_choices(projects, title_field="name", value_field=None)
        project_choices.append(questionary.Choice("q. Cancel", value="CANCEL"))
        
        console.print("\n[bold]Select Project:[/bold]")
        hotkeys = [str(i) for i in range(1, len(projects) + 1)] + ['q']
        selected_project = prompt_toolkit_menu(project_choices, hotkeys=hotkeys)
        
        if not selected_project or selected_project == "CANCEL":
            return
            
        project_ref = selected_project['id']
        keys = get_api_keys(project_ref, token)
        
        if not keys:
            console.print("[red]Could not fetch API keys for this project.[/red]")
            return
            
        key_choices = []
        for key_name, key_val in keys.items():
            key_choices.append(questionary.Choice(f"{key_name}", value=key_val))
        key_choices.append(questionary.Choice("q. Cancel", value="CANCEL"))
        
        console.print(f"\n[bold]Select Key to Copy for '{selected_project['name']}':[/bold]")
        selected_key_val = prompt_toolkit_menu(key_choices)
        
        if selected_key_val and selected_key_val != "CANCEL":
            if copy_to_clipboard(selected_key_val):
                console.print("[green]✅ Key copied to clipboard![/green]")
            else:
                console.print("[red]❌ Failed to copy to clipboard.[/red]")

# Tool identity and descriptions (pas list, panel, -h)
TOOL_ID = "sb-acs"
TOOL_TITLE = "Supabase Account Management"
TOOL_SHORT_DESC = "Manage Supabase organizations and CLI sessions (multi-org, CLI injection, global sync)."
TOOL_DESCRIPTION = "Supabase operations tool. Manages multiple organizations and CLI sessions (multi-org, CLI injection, global sync)."

def main_menu():
    parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
    parser.parse_args()

    supabase_bin = detect_supabase_binary()
    if not supabase_bin:
        if not install_supabase():
            print("Supabase CLI is required. Exiting.")
            sys.exit(1)
        supabase_bin = detect_supabase_binary()

    config = load_pas_config(CONFIG_SERVICE)

    local_ref = check_local_link()
    linked_org_id = None
    if local_ref:
        linked_org_id = get_org_for_project(local_ref, config)
        if linked_org_id and not config.get("active_org_id"):
            config["active_org_id"] = linked_org_id
            save_pas_config(CONFIG_SERVICE, config)

    info_text = """
[bold]%s[/bold]

- [cyan]Multi-Org[/cyan]: Switch between different accounts and organizations easily.
- [cyan]CLI Injection[/cyan]: Injects tokens into standard CLI commands (via 'sb').
- [cyan]Global Sync[/cyan]: Synchronizes your PAS active org with 'supabase login'.
- [cyan]Native Login[/cyan]: Detects and imports existing CLI login sessions.
""" % TOOL_DESCRIPTION
    console.print(Panel(info_text.strip(), title=TOOL_TITLE, border_style="blue"))

    while True:
        active_org_id = config.get("active_org_id")
        orgs = config.get("organizations", {})
        active_org_data = orgs.get(active_org_id) if active_org_id else None
        active_org_name = active_org_data.get("name") if active_org_data else "None"
        token = active_org_data.get("access_token") if active_org_data else None
        
        local_ref = check_local_link()
        link_status = f"Linked to {local_ref}" if local_ref else "Not linked"
        
        native_session = detect_native_login(supabase_bin)
        native_status = "Not detected (Run 'supabase login')"
        native_org_ids = []
        if native_session:
            org_names_list = [o['name'] for o in native_session['orgs']]
            native_org_ids = [o['id'] for o in native_session['orgs']]
            org_str = ", ".join(org_names_list) if org_names_list else "Unknown"
            native_status = f"Active [{org_str}] ({native_session['project_count']} Projects)"

        sync_label = ""
        if active_org_id:
            if native_session:
                if active_org_id in native_org_ids:
                    sync_label = " [✓ Synced]"
                else:
                    sync_label = " [! DISCREPANCY: Global CLI is logged into a different account]"
            else:
                sync_label = " [! DISCREPANCY: Global CLI not logged in]"

        active_org_display = f"{active_org_name} ({active_org_id}){sync_label}"
        if active_org_data and active_org_data.get("email"):
            active_org_display = f"{active_org_name} <{active_org_data.get('email')}> ({active_org_id}){sync_label}"

        org_warning = ""
        if local_ref and linked_org_id and active_org_id != linked_org_id:
            linked_org_name_val = orgs.get(linked_org_id, {}).get("name", "Unknown")
            org_warning = f"[!] WARNING: Local project belongs to '{linked_org_name_val}', but active PAS org is '{active_org_name}'."
        elif not active_org_id and native_session:
            org_warning = "[i] INFO: No PAS organization active. CLI commands will use your global 'supabase login' session."

        detected_linked_org_name = None
        if local_ref and linked_org_id:
            detected_linked_org_name = orgs.get(linked_org_id, {}).get("name", "Unknown")

        render_dashboard(
            native_status=native_status,
            active_org_display=active_org_display,
            link_status=link_status,
            org_warning=org_warning,
            linked_org_name=detected_linked_org_name
        )
        
        choices = []
        menu_num = 1
        
        # 1. List Projects in Active Org (if active org exists)
        if active_org_id:
            choices.append(questionary.Choice(title=f"{menu_num}. List Projects in Active Org", value='LIST_PROJECTS'))
            menu_num += 1
            choices.append(questionary.Choice(title=f"{menu_num}. Copy API Keys / Tokens", value='COPY_API'))
            menu_num += 1
        
        # 2. List Organizations
        choices.append(questionary.Choice(title=f"{menu_num}. List Organizations", value='LIST_ORGS'))
        menu_num += 1
        
        # 3. Sync Active PAS Org to Global CLI (if active org exists)
        if active_org_id:
            choices.append(questionary.Choice(title=f"{menu_num}. Sync Active PAS Org to Global CLI ('supabase login')", value='SYNC_GLOBAL'))
            menu_num += 1
        
        # 4. Switch Active Organization
        choices.append(questionary.Choice(title=f"{menu_num}. Switch Active Organization", value='SWITCH_ORG'))
        menu_num += 1
        
        # 5. Add Organization (Manual Access Token)
        choices.append(questionary.Choice(title=f"{menu_num}. Add Organization (Manual Access Token)", value='ADD_ORG'))
        menu_num += 1
        
        # 6. Fetch new Token into PAS (No Keychain prompt)
        choices.append(questionary.Choice(title=f"{menu_num}. Fetch new Token into PAS (No Keychain prompt)", value='FETCH_TOKEN'))
        menu_num += 1
        
        # 7. Use Global CLI Session (if available and no active org)
        if native_session and not active_org_id:
            choices.append(questionary.Choice(title=f"{menu_num}. Use Global CLI Session (Direct Import from Keychain)", value='IMPORT_SESSION'))
            menu_num += 1
        
        # 8. Link Local Project
        choices.append(questionary.Choice(title=f"{menu_num}. Link Local Project", value='LINK_PROJECT'))
        menu_num += 1
        
        # 9-11. CLI commands
        choices.extend([
            questionary.Choice(title=f"{menu_num}. CLI: supabase status", value='CLI_STATUS'),
            questionary.Choice(title=f"{menu_num + 1}. CLI: supabase start", value='CLI_START'),
            questionary.Choice(title=f"{menu_num + 2}. CLI: supabase stop", value='CLI_STOP'),
            questionary.Separator(),
            questionary.Choice(title="q. Quit", value='QUIT'),
        ])

        style = questionary.Style([
            ('selected', 'fg:#cc9900'),
            ('separator', 'fg:#cc9900'),
        ])

        # Generate hotkeys list - include all numbers and special keys
        hotkeys = [str(i) for i in range(1, menu_num + 3)] + ['q']
        choice = prompt_toolkit_menu([c for c in choices if not isinstance(c, questionary.Separator)], style, hotkeys=hotkeys)

        if choice is None or choice == 'QUIT':
            if active_org_id and (not native_session or active_org_id not in native_org_ids):
                print(f"\n[!] Discrepancy detected: Active PAS Org '{active_org_name}' is not synced to global CLI.")
                if questionary.confirm("Sync to global CLI before quitting?").ask():
                    sync_to_global(config, supabase_bin)
            break
        elif choice == 'LIST_ORGS':
            list_organizations(config, native_org_ids)
        elif choice == 'ADD_ORG':
            add_organization(config)
        elif choice == 'FETCH_TOKEN':
            login_isolated(config, supabase_bin)
        elif choice == 'IMPORT_SESSION' and native_session:
            import_native_session(config, native_session)
        elif choice == 'SWITCH_ORG':
            switch_organization(config)
        elif choice == 'SYNC_GLOBAL' and active_org_id:
            sync_to_global(config, supabase_bin)
        elif choice == 'LIST_PROJECTS':
            list_projects(token, supabase_bin, active_org_id)
        elif choice == 'COPY_API':
            copy_api_info(config, token)
        elif choice == 'LINK_PROJECT':
            link_project(token, supabase_bin)
        elif choice.startswith('CLI_'):
            cmd_map = {'CLI_STATUS': 'status', 'CLI_START': 'start', 'CLI_STOP': 'stop'}
            cmd = [str(supabase_bin), cmd_map[choice]]
            
            if os.path.isdir(".env"):
                if os.path.isfile(".env.local"):
                    cmd.extend(["--env-file", ".env.local"])
                else:
                    cmd.extend(["--env-file", "/dev/null"])

            print(f"\nRunning: {' '.join(cmd)}")
            
            env = get_supabase_env(token) if token else {k: v for k, v in os.environ.items() if k != "SUPABASE_ACCESS_TOKEN"}
            run_command(cmd, env=env, capture_output=False)

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
