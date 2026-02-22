#!/usr/bin/env python3
"""
@pas-executable
Check active Cloudflare tunnels and system service status.
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
import shutil
import subprocess
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

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helpers.cloudflare import (
    cf_api_request,
    get_zones,
    get_dns_records,
    list_tunnels as list_cf_tunnels_api
)

from helpers.core import (
    load_pas_config, 
    save_pas_config, 
    prompt_yes_no, 
    detect_cloudflared_binary,
    format_menu_choices,
    prompt_toolkit_menu,
    copy_to_clipboard
)

console = Console()

def install_cloudflared() -> bool:
    """Prompt to install cloudflared via Homebrew."""
    print("cloudflared binary not found.")
    if not prompt_yes_no("Would you like to install cloudflared via Homebrew?"):
        return False
    
    print("Installing cloudflared...")
    try:
        subprocess.run(["brew", "install", "cloudflared"], check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: Failed to install cloudflared. Please install it manually.")
        return False

def check_system_service() -> None:
    """Check the status of the cloudflared launchd service on macOS."""
    print("\n--- macOS System Service Status ---")
    try:
        # Check if the service is loaded in launchctl
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        cf_services = [line for line in result.stdout.splitlines() if "cloudflare" in line.lower()]
        
        if cf_services:
            for svc in cf_services:
                print(f"  [✓] Found service: {svc}")
        else:
            print("  [ ] No cloudflared service found in launchctl list.")
            
        # Check for the plist file
        plist_path = Path("/Library/LaunchDaemons/com.cloudflare.cloudflared.plist")
        if plist_path.exists():
            print(f"  [✓] Plist exists at: {plist_path}")
        else:
            print("  [ ] Plist file not found in /Library/LaunchDaemons/")
            
    except Exception as e:
        print(f"Error checking system service: {e}")

def list_tunnels(cloudflared_bin: Path) -> Optional[List[Dict[str, Any]]]:
    """List Cloudflare tunnels and their status."""
    print("\n--- Cloudflare Tunnels (CLI) ---")
    try:
        # Get machine-readable JSON output
        result = subprocess.run(
            [str(cloudflared_bin), "tunnel", "list", "--output", "json"], 
            capture_output=True, text=True, check=True
        )
        tunnels = json.loads(result.stdout)
        
        # Still show the human-readable output for the user
        subprocess.run([str(cloudflared_bin), "tunnel", "list"], check=True)
        
        return tunnels
    except subprocess.CalledProcessError as e:
        print(f"Error fetching tunnels: {e.stderr}")
        return None
    except json.JSONDecodeError:
        print("Error: Could not parse cloudflared JSON output.")
        return None

def get_account_id_and_dns_records(token: str) -> tuple[Optional[str], List[Dict[str, Any]]]:
    """Fetch account ID and all DNS records for all zones."""
    records = []
    account_id = None
    zones = get_zones(token)
    if zones:
        account_id = zones[0].get("account", {}).get("id")
        for zone in zones:
            records.extend(get_dns_records(token, zone['id']))
    return account_id, records

def find_hostnames_for_tunnel(tunnel_id: str, all_dns_records: List[Dict[str, Any]]) -> List[str]:
    """Find DNS hostnames that point to a specific tunnel ID."""
    hostnames = []
    target = f"{tunnel_id}.cfargotunnel.com"
    for record in all_dns_records:
        if record.get("type") == "CNAME" and record.get("content") == target:
            hostnames.append(record.get("name", "N/A"))
    return hostnames

def interactive_tunnel_menu(cloudflared_bin: Path, tunnels: List[Dict[str, Any]], all_dns_records: List[Dict[str, Any]] = None, account_id: str = None, token: str = None):
    """Interactive menu to view tunnel details."""
    while True:
        choices = []
        if tunnels:
            print("\n--- Tunnel Drill-down ---")
            tunnel_items = []
            for t in tunnels:
                name = t.get("name", "N/A")
                id_str = t.get("id", "N/A")
                status = t.get("status", "N/A")
                hostnames = find_hostnames_for_tunnel(id_str, all_dns_records) if all_dns_records else []
                host_str = f" -> {', '.join(hostnames)}" if hostnames else ""
                status_symbol = "✓" if status == "healthy" else "!"
                label = f"[{status_symbol}] {name} ({id_str}){host_str}"
                tunnel_items.append({"label": label, "tunnel": t})
            
            choices = format_menu_choices(tunnel_items, title_field="label", value_field="tunnel")
        else:
            print("\n(No active tunnels detected)")
        
        # Add special options
        choices.append(questionary.Choice("c. Copy Cloudflare API Token", value="COPY_TOKEN"))
        choices.append(questionary.Choice("s. Restart Cloudflared System Service (sudo)", value="RESTART_SERVICE"))
        choices.append(questionary.Choice("q. Quit", value="QUIT"))

        console.print("\n[bold]Select an action or tunnel:[/bold] (Use arrows or press hotkey)")
        
        choice = prompt_toolkit_menu(choices)
        
        if not choice or choice == "QUIT":
            break
            
        if choice == "COPY_TOKEN":
            if token:
                if copy_to_clipboard(token):
                    console.print("[green]✅ Cloudflare API Token copied to clipboard![/green]")
                else:
                    console.print("[red]❌ Failed to copy to clipboard.[/red]")
            else:
                console.print("[yellow]⚠️ No token found to copy.[/yellow]")
            continue
            
        if choice == "RESTART_SERVICE":
            print("\nAttempting to restart cloudflared service...")
            try:
                plist = "/Library/LaunchDaemons/com.cloudflare.cloudflared.plist"
                if os.path.exists(plist):
                    # Newer macOS way
                    subprocess.run(["sudo", "launchctl", "kickstart", "-k", "system/com.cloudflare.cloudflared"], check=True)
                else:
                    # Fallback or older way
                    subprocess.run(["sudo", "launchctl", "stop", "com.cloudflare.cloudflared"], check=False)
                    subprocess.run(["sudo", "launchctl", "start", "com.cloudflare.cloudflared"], check=True)
                print("Restart command sent successfully.")
            except subprocess.CalledProcessError as e:
                print(f"Error restarting service: {e}")
            continue
        
        # If it's a tunnel object
        selected = choice
        name_or_id = selected.get("name") or selected.get("id")
        print(f"\n--- Details for Tunnel: {name_or_id} ---")
        
        # Show info, route
        for cmd_type in ["info", "route ip show"]:
            print(f"\n> cloudflared tunnel {cmd_type} {name_or_id}")
            cmd = [str(cloudflared_bin), "tunnel"] + cmd_type.split() + [name_or_id]
            subprocess.run(cmd)

def main():
    parser = argparse.ArgumentParser(description=__doc__.replace("@pas-executable", "").strip(), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()

    info_text = """
[bold]Cloudflare Tunnel Health Check[/bold]

This script monitors your tunnels:
- [cyan]Binary Check[/cyan]: Verifies cloudflared is installed.
- [cyan]System Service[/cyan]: Checks if the tunnel service is running on macOS.
- [cyan]Tunnel Status[/cyan]: Lists active tunnels and their health.
- [cyan]DNS Cross-ref[/cyan]: Matches tunnels to your public hostnames.
- [cyan]Drill-down[/cyan]: View detailed logs and routing for specific tunnels.
"""
    console.print(Panel(info_text.strip(), title="cf-tunnels", border_style="blue"))
    console.print("\n")

    config = load_pas_config("cf")
    
    cloudflared_bin = detect_cloudflared_binary()
    if not cloudflared_bin:
        if install_cloudflared():
            cloudflared_bin = detect_cloudflared_binary()
    
    token = config.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
    if token:
        print(f"Cloudflare API Token is already set.")
        new_token = input("Enter to proceed or input a different API Token: ").strip()
        if new_token:
            token = new_token
            config["CLOUDFLARE_API_TOKEN"] = token
            save_pas_config("cf", config)

    if not token:
        print("\n" + "!" * 60)
        print("Cloudflare API Token not found.")
        print("\nTo set up your API Token (required for DNS mapping and Dashboard links):")
        print(f"1. Go to: {CF_API_TOKENS_URL}")
        print("2. Click 'Create Token' -> Use 'Read all resources' template (or custom with Zone:Read, DNS:Read)")
        print("3. Enter it below to save it to ~/.pas/cf.json")
        print("!" * 60 + "\n")
        token = input("Enter Cloudflare API Token (press Enter to skip): ").strip()
        if token:
            config["CLOUDFLARE_API_TOKEN"] = token
            save_pas_config("cf", config)
    
    all_dns_records = []
    account_id = None
    if token:
        print("Fetching Cloudflare DNS records for cross-referencing...")
        account_id, all_dns_records = get_account_id_and_dns_records(token)
        
        # Cache account_id if found and not already set
        if account_id and not config.get("CLOUDFLARE_ACCOUNT_ID"):
            config["CLOUDFLARE_ACCOUNT_ID"] = account_id
            save_pas_config("cf", config)
            print(f"Discovered and cached Cloudflare Account ID: {account_id}")

    if cloudflared_bin:
        print(f"Binary: {cloudflared_bin}")
        check_system_service()
        output = list_tunnels(cloudflared_bin)
        # Always enter the menu if the binary is found, even if no tunnels are listed
        interactive_tunnel_menu(cloudflared_bin, output or [], all_dns_records, account_id, token)
    else:
        print("Error: cloudflared is required for tunnel checks.")
        sys.exit(1)

if __name__ == "__main__":
    main()
