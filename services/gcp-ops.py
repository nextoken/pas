#!/usr/bin/env python3
"""
@pas-executable
Google Cloud Platform Operations Tool for project setup, APIs, and service accounts.
"""

import os
import sys
from pathlib import Path
# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
import sys
import argparse
import json
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Add services directory to path if needed for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helpers.core import (
    load_pas_config, 
    save_pas_config, 
    prompt_yes_no, 
    run_command, 
    copy_to_clipboard,
    get_pas_config_dir,
    prompt_toolkit_menu,
    check_gcloud_installed,
    check_gcloud_auth,
    get_gcp_projects,
    select_gcp_project,
    ensure_gcp_apis_enabled,
    check_gcp_billing
)

console = Console()

# Tool identity and descriptions (pas list, panel, -h)
TOOL_ID = "gcp-ops"
TOOL_TITLE = "GCP Operations"
TOOL_SHORT_DESC = "GCP setup: auth, projects, API enablement, service accounts (e.g. for n8n)."
TOOL_DESCRIPTION = "GCP Operations Tool. Auth check, project management, API enablement, service accounts, integration ready (e.g. n8n)."

def show_intro():
    info_text = """
[bold]%s[/bold]

- [cyan]Auth Check[/cyan]: Ensures gcloud CLI is installed and authenticated.
- [cyan]Project Management[/cyan]: List, select, or create GCP projects.
- [cyan]API Enablement[/cyan]: Automatically enables required APIs (IAM, Resource Manager, etc.).
- [cyan]Service Accounts[/cyan]: Creates service accounts and generates JSON keys.
- [cyan]Integration Ready[/cyan]: Provides formatted output for easy copy-pasting into other tools.
""" % TOOL_DESCRIPTION
    console.print(Panel(info_text.strip(), title=TOOL_TITLE, border_style="green"))
    console.print("\n")

def enable_apis(project_id: str):
    """Enable required and optional APIs."""
    required_apis = [
        "iam.googleapis.com",
        "iamcredentials.googleapis.com",
        "cloudresourcemanager.googleapis.com"
    ]
    
    productivity_apis = {
        "gmail.googleapis.com": "Gmail API",
        "drive.googleapis.com": "Google Drive API",
        "sheets.googleapis.com": "Google Sheets API",
        "docs.googleapis.com": "Google Docs API",
        "slides.googleapis.com": "Google Slides API"
    }
    
    ai_apis = {
        "aiplatform.googleapis.com": "Vertex AI API"
    }

    console.print("\n[bold]Checking required APIs...[/bold]")
    ensure_gcp_apis_enabled(project_id, required_apis)

    if prompt_yes_no("\nWould you like to enable Productivity APIs (Gmail, Drive, Sheets, etc.)?"):
        ensure_gcp_apis_enabled(project_id, list(productivity_apis.keys()))

    if prompt_yes_no("\nWould you like to enable AI APIs (Vertex AI)?"):
        ensure_gcp_apis_enabled(project_id, list(ai_apis.keys()))

def setup_service_account(project_id: str) -> Optional[Dict[str, Any]]:
    """Create a service account, grant roles, and generate a key."""
    default_name = "pas-service-account"
    sa_name = input(f"\nEnter Service Account ID (default: {default_name}): ").strip() or default_name
    sa_display_name = f"PAS Service Account for {project_id}"
    
    # Create SA
    console.print(f"Creating service account: {sa_name}...")
    res = run_command([
        "gcloud", "iam", "service-accounts", "create", sa_name,
        "--display-name", sa_display_name,
        "--project", project_id
    ])
    
    sa_email = f"{sa_name}@{project_id}.iam.gserviceaccount.com"
    
    # Grant roles
    roles = ["roles/editor"]
    console.print(f"Granting roles to {sa_email}...")
    for role in roles:
        run_command([
            "gcloud", "projects", "add-iam-policy-binding", project_id,
            "--member", f"serviceAccount:{sa_email}",
            "--role", role
        ])
    
    # Generate Key
    key_dir = get_pas_config_dir() / "gcp-keys"
    key_dir.mkdir(parents=True, exist_ok=True)
    key_file = key_dir / f"{sa_name}-{project_id}.json"
    
    console.print(f"Generating JSON key file: {key_file}...")
    res = run_command([
        "gcloud", "iam", "service-accounts", "keys", "create", str(key_file),
        "--iam-account", sa_email,
        "--project", project_id
    ])
    
    if res.returncode == 0:
        try:
            return json.loads(key_file.read_text())
        except Exception as e:
            console.print(f"[red]Error reading key file: {e}[/red]")
            return None
    else:
        console.print(f"[red]Failed to generate key: {res.stderr}[/red]")
        return None

def display_integration_info(key_data: Dict[str, Any]):
    """Display information for n8n or other integrations."""
    client_email = key_data.get("client_email")
    private_key = key_data.get("private_key")
    
    console.print("\n" + "="*60)
    console.print("[bold green]Setup Complete! Use these details for your integration:[/bold green]")
    console.print("="*60)
    
    console.print(f"\n[bold]Service Account Email:[/bold]")
    console.print(f"{client_email}")
    
    console.print(f"\n[bold]Private Key:[/bold]")
    truncated_key = (private_key[:50] + "..." + private_key[-50:]) if private_key else "N/A"
    console.print(f"{truncated_key}")
    
    console.print("\n" + "-"*60)
    
    while True:
        menu_options = [
            {"title": "Copy Service Account Email", "value": "email"},
            {"title": "Copy Private Key (full)", "value": "key"},
            {"title": "Copy entire JSON Key content", "value": "json"},
            {"title": "[Quit]", "value": "quit"}
        ]
        
        choices = format_menu_choices(menu_options, title_field="title", value_field="value")
        console.print("\n[bold cyan]Post-Setup Actions:[/bold cyan]")
        choice = prompt_toolkit_menu(choices)
        
        if not choice or choice == "quit":
            break
        elif choice == "email":
            if copy_to_clipboard(client_email):
                console.print("[green]Email copied to clipboard![/green]")
        elif choice == "key":
            if copy_to_clipboard(private_key):
                console.print("[green]Private Key copied to clipboard![/green]")
        elif choice == "json":
            if copy_to_clipboard(json.dumps(key_data, indent=2)):
                console.print("[green]Entire JSON content copied to clipboard![/green]")

def main():
    parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
    parser.add_argument("--project", help="GCP Project ID to use")
    args = parser.parse_args()

    show_intro()
    
    if not check_gcloud_installed():
        sys.exit(1)
        
    auth_status = check_gcloud_auth()
    if auth_status is None:
        sys.exit(0)
    if not auth_status:
        console.print("[red]Authentication required to proceed.[/red]")
        sys.exit(1)

    project_id = select_gcp_project(args.project)
    if not project_id:
        console.print("[red]No project selected. Exiting.[/red]")
        sys.exit(1)

    # Set active project
    run_command(["gcloud", "config", "set", "project", project_id])
    
    check_gcp_billing(project_id)
    enable_apis(project_id)

    key_data = setup_service_account(project_id)
    if key_data:
        display_integration_info(key_data)
        console.print("\n[bold green]Success![/bold green] Your GCP environment is ready.")
    else:
        console.print("[red]Service Account setup failed.[/red]")
        sys.exit(1)

if __name__ == "__main__":
    main()

