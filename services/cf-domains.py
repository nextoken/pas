#!/usr/bin/env python3
"""
@pas-executable
Cloudflare Domain and DNS management script.

This script allows you to list your Cloudflare domains (zones) and drill down 
into their DNS records interactively.
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
import questionary
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.console import Console
from rich.panel import Panel

# --- Configuration URLs ---
CF_API_TOKENS_URL = "https://dash.cloudflare.com/profile/api-tokens"
# --------------------------

console = Console()

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helpers.cloudflare import (
    cf_api_request,
    get_zones,
    get_dns_records,
    create_dns_record,
    delete_dns_record
)

from helpers.core import load_pas_config, save_pas_config, prompt_yes_no, format_menu_choices, prompt_toolkit_menu, copy_to_clipboard

def show_dns_records(records: List[Dict[str, Any]]):
    """Display DNS records in a table."""
    if not records:
        print("No DNS records found.")
        return
    
    print(f"\n{'#':<3} {'Type':<8} {'Name':<35} {'Content':<45} {'Updated'}")
    print("-" * 115)
    for idx, rec in enumerate(records, 1):
        updated = rec.get("modified_on", "N/A")
        if updated != "N/A" and "T" in updated:
            updated = updated.replace("T", " ").split(".")[0][:16]
        
        content = rec.get("content", "N/A")
        if len(content) > 42:
            content = content[:39] + "..."
            
        print(f"{idx:<3} {rec['type']:<8} {rec['name']:<35} {content:<45} {updated}")

def interactive_dns_menu(token: str, zones: Optional[List[Dict[str, Any]]] = None, default_domain: Optional[str] = None):
    """Interactive menu to select zones and view DNS records."""
    if zones is None:
        zones = get_zones(token)
        
    if not zones:
        print("No domains found or error fetching domains.")
        return

    config = load_pas_config("cf")
    if default_domain is None:
        default_domain = config.get("DEFAULT_DOMAIN")

    # Find default zone index if provided
    default_idx = None
    if default_domain:
        for i, zone in enumerate(zones, 1):
            if zone['name'] == default_domain:
                default_idx = i
                break

    while True:
        print("\n--- Active Domains ---")
        choices = format_menu_choices(zones, title_field="name", value_field=None)
        
        # Add special options
        choices.append(questionary.Choice("c. Copy Cloudflare API Token", value="COPY_TOKEN"))
        choices.append(questionary.Choice("q. Quit domain drill-down", value="QUIT"))

        console.print("\n[bold]Select a domain to manage:[/bold] (Use arrows or press hotkey)")
        
        selected_choice = prompt_toolkit_menu(choices)
        
        if not selected_choice or selected_choice == "QUIT":
            break
            
        if selected_choice == "COPY_TOKEN":
            if token:
                if copy_to_clipboard(token):
                    console.print("[green]✅ Cloudflare API Token copied to clipboard![/green]")
                else:
                    console.print("[red]❌ Failed to copy to clipboard.[/red]")
            else:
                console.print("[yellow]⚠️ No token found to copy.[/yellow]")
            continue

        selected_zone = selected_choice
        zone_id = selected_zone['id']
        zone_name = selected_zone['name']
            
        # Save as default
        config = load_pas_config("cf")
        config["DEFAULT_DOMAIN"] = zone_name
        save_pas_config("cf", config)
        default_idx = zones.index(selected_zone) + 1
        
        while True:
            print(f"\n--- Managing {zone_name} ---")
            records = get_dns_records(token, zone_id)
            show_dns_records(records)
            
            action_choices = [
                {"title": "Add DNS Record", "value": "add"},
                {"title": "Delete DNS Record", "value": "delete"},
                {"title": "Refresh records", "value": "refresh"},
                {"title": "[Back]", "value": "back"},
                {"title": "[Quit]", "value": "quit"}
            ]
            
            formatted_actions = format_menu_choices(action_choices, title_field="title", value_field="value")
            console.print(f"\n[bold]Select an action for {zone_name}:[/bold]")
            action = prompt_toolkit_menu(formatted_actions, hotkeys=["1", "2", "r", "b", "q"])
            
            if action == 'back' or not action:
                break
            elif action == 'quit':
                print("Goodbye!")
                sys.exit(0)
            elif action == 'refresh':
                continue
            elif action == 'add':
                # Add DNS Record flow
                rec_type = input("Record Type [CNAME]: ").strip().upper() or "CNAME"
                name = input(f"Name (e.g. 'www' for www.{zone_name}): ").strip()
                if not name:
                    print("Error: Name is required.")
                    continue
                
                # Existing contents for selection
                contents = sorted(list(set(r['content'] for r in records if 'content' in r)))
                content_menu = [{"title": c, "value": c} for c in contents]
                content_menu.append({"title": "[Manual input]", "value": "__manual__"})
                content_menu.append({"title": "[Back]", "value": "__back__"})
                
                formatted_contents = format_menu_choices(content_menu, title_field="title", value_field="value")
                console.print("\n[bold]Select content or enter manually:[/bold]")
                content = prompt_toolkit_menu(formatted_contents)
                
                if content == "__back__" or not content:
                    continue
                if content == "__manual__":
                    content = input("Enter content: ").strip()
                
                if not content:
                    print("Error: Content is required.")
                    continue
                
                proxied = prompt_yes_no("Proxied (Cloudflare speed/security)?", default=True)
                res = create_dns_record(token, zone_id, rec_type, name, content, proxied)
                if res and res.get("success"):
                    print(f"\nSuccessfully added {rec_type} record: {name} -> {content}")
                else:
                    print(f"\nFailed to add record: {res.get('errors')}")

            elif action == 'delete':
                # Delete DNS Record flow
                record_items = []
                for r in records:
                    record_items.append({
                        "title": f"{r['type']:<5} {r['name']:<30} -> {r['content'][:30]}",
                        "value": r
                    })
                record_items.append({"title": "[Back]", "value": "__back__"})
                
                formatted_records = format_menu_choices(record_items, title_field="title", value_field="value")
                console.print("\n[bold]Select record to delete:[/bold]")
                target = prompt_toolkit_menu(formatted_records)
                
                if target == "__back__" or not target:
                    continue
                    
                if prompt_yes_no(f"Are you sure you want to delete {target['type']} record '{target['name']}'?", default=False):
                    res = delete_dns_record(token, zone_id, target['id'])
                    if res and res.get("success"):
                        print(f"\nSuccessfully deleted DNS record: {target['name']}")
                    else:
                        print(f"\nFailed to delete record: {res.get('errors')}")
            else:
                print("Invalid action.")
        else:
            print("Invalid selection. Please enter a number or a domain name.")

def main():
    parser = argparse.ArgumentParser(description=__doc__.replace("@pas-executable", "").strip(), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("domains", nargs="*", help="Optional list of domain names to drill down into directly")
    args = parser.parse_args()

    info_text = """
[bold]Cloudflare Domain Management[/bold]

This script allows you to:
- [cyan]List all domains[/cyan] (zones) in your Cloudflare account.
- [cyan]Manage DNS records[/cyan] for a selected domain.
- [cyan]Add/Delete CNAME and A records[/cyan] interactively.
- [cyan]Cache your preferred domain[/cyan] for faster future access.
"""
    console.print(Panel(info_text.strip(), title="cf-domains", border_style="blue"))
    console.print("\n")

    config = load_pas_config("cf")
    token = config.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
    
    # Simple check for token validity if present
    if token:
        test_req = cf_api_request("user/tokens/verify", token)
        if not test_req or not test_req.get("success"):
            print("Current Cloudflare token is invalid or expired.")
            token = None

    if not token:
        print("\n" + "!" * 60)
        print("Cloudflare API Token not found or invalid.")
        print("\nTo set up your API Token:")
        print(f"1. Go to: {CF_API_TOKENS_URL}")
        print("2. Click 'Create Token' -> Use 'Read all resources' template (or custom with Zone:Read, DNS:Read)")
        print("3. Enter it below to save it to ~/.pas/cf.json")
        print("!" * 60 + "\n")
        token = input("Enter Cloudflare API Token (press Enter to skip): ").strip()
        if token:
            config["CLOUDFLARE_API_TOKEN"] = token
            save_pas_config("cf", config)

    if not token:
        print("\nNo valid token provided. Cannot check domains.")
        sys.exit(1)

    # Fetch zones early to discover account ID if not already cached
    all_zones = get_zones(token)
    if all_zones and not config.get("CLOUDFLARE_ACCOUNT_ID"):
        account_id = all_zones[0].get("account", {}).get("id")
        if account_id:
            config["CLOUDFLARE_ACCOUNT_ID"] = account_id
            save_pas_config("cf", config)
            print(f"Discovered and cached Cloudflare Account ID: {account_id}")

    default_domain = config.get("DEFAULT_DOMAIN")

    if args.domains:
        selected_zones = [z for z in all_zones if z["name"] in args.domains]
        if not selected_zones:
            print(f"\nError: None of the provided domains {args.domains} found in your account.")
            sys.exit(1)
        interactive_dns_menu(token, zones=selected_zones, default_domain=default_domain)
    else:
        interactive_dns_menu(token, zones=all_zones, default_domain=default_domain)

if __name__ == "__main__":
    main()

