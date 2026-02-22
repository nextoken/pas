#!/usr/bin/env python3
"""
@pas-executable
Check active Cloudflare services, tunnels, and domains.
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
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.console import Console
from rich.panel import Panel

# --- Configuration URLs ---
CF_API_TOKENS_URL = "https://dash.cloudflare.com/profile/api-tokens"
CF_ZERO_TRUST_DASH_URL = "https://one.dash.cloudflare.com/"
CF_TUNNEL_EDIT_URL_TEMPLATE = f"{CF_ZERO_TRUST_DASH_URL}{{account_id}}/networks/connectors/cloudflare-tunnels/cfd_tunnel/{{tunnel_id}}/edit?tab=overview"
# --------------------------

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helpers.core import load_pas_config, save_pas_config, prompt_yes_no, detect_cloudflared_binary
from cf_domains import cf_api_request, get_zones, get_dns_records, interactive_dns_menu

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

def list_tunnels(cloudflared_bin: Path) -> Optional[List[Dict[str, Any]]]:
    """List active Cloudflare tunnels."""
    print("\n--- Active Tunnels ---")
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

def find_hostnames_for_tunnel(tunnel_id: str, all_dns_records: List[Dict[str, Any]]) -> List[str]:
    """Find DNS hostnames that point to a specific tunnel ID."""
    hostnames = []
    target = f"{tunnel_id}.cfargotunnel.com"
    for record in all_dns_records:
        # Hostnames point to <tunnel-id>.cfargotunnel.com
        if record.get("type") == "CNAME" and record.get("content") == target:
            hostnames.append(record.get("name", "N/A"))
    return hostnames

def interactive_tunnel_menu(cloudflared_bin: Path, tunnels: List[Dict[str, Any]], all_dns_records: List[Dict[str, Any]] = None, account_id: str = None):
    """Interactive menu to view tunnel details."""
    if not tunnels:
        return

    while True:
        print("\n--- Tunnel Drill-down ---")
        for idx, t in enumerate(tunnels, 1):
            name = t.get("name", "N/A")
            id_str = t.get("id", "N/A")
            hostnames = find_hostnames_for_tunnel(id_str, all_dns_records) if all_dns_records else []
            host_str = f" -> {', '.join(hostnames)}" if hostnames else ""
            print(f"{idx}. {name} ({id_str}){host_str}")
            if account_id:
                print(f"   URL: {CF_TUNNEL_EDIT_URL_TEMPLATE.format(account_id=account_id, tunnel_id=id_str)}")
        print("q. Quit tunnel drill-down")

        choice = input("\nSelect a tunnel to view details (or 'q' to go back): ").strip().lower()
        if choice == 'q':
            break
        
        try:
            selection = int(choice)
            if 1 <= selection <= len(tunnels):
                selected = tunnels[selection - 1]
                name_or_id = selected.get("name") or selected.get("id")
                print(f"\n--- Details for Tunnel: {name_or_id} ---")
                for cmd_type in ["info", "route ip show"]:
                    print(f"\n> cloudflared tunnel {cmd_type} {name_or_id}")
                    cmd = [str(cloudflared_bin), "tunnel"] + cmd_type.split() + [name_or_id]
                    subprocess.run(cmd)
            else:
                print("Invalid selection.")
        except ValueError:
            print("Invalid input. Please enter a number or 'q'.")

def main():
    parser = argparse.ArgumentParser(description=__doc__.replace("@pas-executable", "").strip(), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()

    info_text = """
[bold]Cloudflare Comprehensive Check[/bold]

This tool performs a complete audit of your Cloudflare setup:
- [cyan]Tunnel Monitoring[/cyan]: Lists active tunnels and routes via cloudflared.
- [cyan]Domain & DNS[/cyan]: Interactive drill-down into all your zones and records.
- [cyan]Token Validation[/cyan]: Ensures your API token has necessary permissions.
"""
    console.print(Panel(info_text.strip(), title="check-cloudflare", border_style="blue"))
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
        print("\nTo set up your API Token:")
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
        zones = get_zones(token)
        if zones:
            account_id = zones[0].get("account", {}).get("id")
            
            # Cache account_id if found and not already set
            if account_id and not config.get("CLOUDFLARE_ACCOUNT_ID"):
                config["CLOUDFLARE_ACCOUNT_ID"] = account_id
                save_pas_config("cf", config)
                print(f"Discovered and cached Cloudflare Account ID: {account_id}")
                
        for zone in zones:
            all_dns_records.extend(get_dns_records(token, zone["id"]))

    if cloudflared_bin:
        print(f"Using cloudflared binary: {cloudflared_bin}")
        tunnel_output = list_tunnels(cloudflared_bin)
        if tunnel_output:
            interactive_tunnel_menu(cloudflared_bin, tunnel_output, all_dns_records, account_id)
    else:
        print("Proceeding without cloudflared binary features.")
    
    if token:
        interactive_dns_menu(token)
    else:
        print("\nSkipping domain and DNS check (no token provided).")

if __name__ == "__main__":
    main()

