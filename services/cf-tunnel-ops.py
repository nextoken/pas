#!/usr/bin/env python3
"""
@pas-executable
Create a Cloudflare tunnel via API.

Follow the instructions at:
{CF_TUNNEL_DOCS_URL}
TODO:
- Store TUNNEL_ID in .env.local
- Automate 3a in the above developer instructions
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
import shutil
import subprocess
import urllib.request
import urllib.error
import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from rich.console import Console
from rich.panel import Panel

# --- Configuration URLs ---
CF_DASHBOARD_URL = "https://dash.cloudflare.com/"
CF_API_TOKENS_URL = "https://dash.cloudflare.com/profile/api-tokens"
CF_TUNNEL_DOCS_URL = "https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel-api/"
CF_ZERO_TRUST_DASH_URL = "https://one.dash.cloudflare.com/"
CF_TUNNEL_EDIT_URL_TEMPLATE = f"{CF_ZERO_TRUST_DASH_URL}{{account_id}}/networks/connectors/cloudflare-tunnels/cfd_tunnel/{{tunnel_id}}/edit?tab=overview"
# --------------------------

# --- Service Configuration ---
SSH_PORT = 22
VNC_PORT = 5900
SSH_SERVICE_PROTOCOL = "ssh://"
VNC_SERVICE_PROTOCOL = "tcp://"
DEFAULT_INGRESS_FALLBACK = "http_status:404"
# --------------------------

# --- Hostname Patterns ---
SSH_HOSTNAME_SUFFIX = "-ssh"
VNC_HOSTNAME_SUFFIX = "-vnc"
# --------------------------

# --- macOS Power Management ---
# Settings for remote macOS servers to keep system awake while allowing display to sleep
MACOS_PMSET_SLEEP = 0  # System never sleeps (keeps tunnel running)
MACOS_PMSET_DISKSLEEP = 0  # Disk never sleeps
MACOS_PMSET_DISPLAYSLEEP = 5  # Display sleeps after 5 minutes
MACOS_PMSET_WOMP = 1  # Wake on network access enabled
MACOS_PMSET_POWERNAP = 0  # Power nap disabled

# Consolidated pmset command arguments (for use in subprocess calls)
MACOS_PMSET_ARGS = [
    "-a",
    "sleep", str(MACOS_PMSET_SLEEP),
    "disksleep", str(MACOS_PMSET_DISKSLEEP),
    "displaysleep", str(MACOS_PMSET_DISPLAYSLEEP),
    "womp", str(MACOS_PMSET_WOMP),
    "powernap", str(MACOS_PMSET_POWERNAP)
]
# --------------------------

# --- Service Paths ---
MACOS_LAUNCHDAEMON_PATH = "/Library/LaunchDaemons/com.cloudflare.cloudflared.plist"
LINUX_SYSTEMD_SERVICE = "cloudflared"
# --------------------------

# --- DNS Configuration ---
CF_TUNNEL_TARGET_TEMPLATE = "{tunnel_id}.cfargotunnel.com"
DNS_PROXIED = True  # Default for CNAME records
# --------------------------

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helpers.cloudflare import (
    create_tunnel,
    get_tunnel,
    get_zones,
    create_dns_record,
    update_tunnel_configuration,
    get_tunnel_configuration,
    list_tunnels,
    get_tunnel_token,
    delete_tunnel,
    get_user_details,
    create_access_app,
    create_access_policy,
    list_access_apps,
    update_access_app,
    update_dns_record,
    get_dns_records,
    delete_access_app,
    update_tunnel,
    verify_token_permissions,
    get_token_info
)

from helpers.core import (
    load_pas_config,
    save_pas_config,
    prompt_yes_no,
    detect_cloudflared_binary,
    format_menu_choices,
    prompt_toolkit_menu,
    run_command
)

console = Console()

def setup_remote_tunnel(ssh_host: str, token_val: str, tunnel_name: str):
    """Setup cloudflared on a remote server via SSH."""
    console.print(f"\n[bold blue]Setting up remote tunnel on {ssh_host}...[/bold blue]")
    
    # 1. Check if cloudflared is installed on remote (using full path search if simple check fails)
    check_cmd = ["ssh", ssh_host, "which cloudflared || find /opt/homebrew/bin /usr/local/bin -name cloudflared -type f 2>/dev/null"]
    res = run_command(check_cmd)
    
    if res.returncode != 0 or not res.stdout.strip():
        console.print("[yellow]cloudflared not found on remote. Attempting to install via Homebrew...[/yellow]")
        
        # Detect brew path on remote more robustly
        detect_brew = ["ssh", ssh_host, "for p in /opt/homebrew/bin/brew /usr/local/bin/brew /home/linuxbrew/.linuxbrew/bin/brew; do if [ -x \"$p\" ]; then echo \"$p\"; exit 0; fi; done; which brew"]
        brew_res = run_command(detect_brew)
        brew_path = brew_res.stdout.strip().split('\n')[0] if brew_res.stdout.strip() else "brew"

        console.print(f"Using brew path: {brew_path}")
        remote_install = ["ssh", ssh_host, f"{brew_path} install cloudflared"]
        res = run_command(remote_install, capture_output=False)
        if res.returncode != 0:
            console.print(f"[red]Failed to install cloudflared on remote server via {brew_path}.[/red]")
            return False
        console.print("[green]cloudflared installed on remote.[/green]")

    # Re-verify cloudflared path after potential install
    get_cf_path = ["ssh", ssh_host, "for p in /opt/homebrew/bin/cloudflared /usr/local/bin/cloudflared /home/linuxbrew/.linuxbrew/bin/cloudflared; do if [ -x \"$p\" ]; then echo \"$p\"; exit 0; fi; done; which cloudflared"]
    cf_res = run_command(get_cf_path)
    cf_path = cf_res.stdout.strip().split('\n')[0] if cf_res.stdout.strip() else "cloudflared"

    # 2. Install as service
    console.print(f"Installing/Updating tunnel service for '{tunnel_name}' on remote...")
    # Use -t to allocate a TTY for sudo password prompt if needed
    # We allow this to "fail" because it fails if already installed, but we want to continue to persistence
    service_cmd = ["ssh", "-t", ssh_host, f"sudo {cf_path} service install {token_val}"]
    run_command(service_cmd, capture_output=False)
    
    # 3. Ensure service is enabled and started persistently
    console.print("Ensuring service is persistent and active...")
    setup_cmds = [
        "if command -v systemctl >/dev/null; then ",
        "sudo systemctl daemon-reload && ",
        f"sudo systemctl enable {LINUX_SYSTEMD_SERVICE} && ",
        f"sudo systemctl restart {LINUX_SYSTEMD_SERVICE}; ",
        "elif [ \"$(uname)\" = \"Darwin\" ]; then ",
        f"sudo launchctl load -w {MACOS_LAUNCHDAEMON_PATH} 2>/dev/null; ",
        "sudo launchctl kickstart -k system/com.cloudflare.cloudflared; ",
        "echo 'Optimizing macOS power settings for 24/7 tunnel access...'; ",
        f"sudo pmset {' '.join(MACOS_PMSET_ARGS)}; ",
        "else ",
        "echo 'Unknown OS, attempting generic start...'; ",
        "sudo cloudflared service start; ",
        "fi"
    ]
    res = run_command(["ssh", "-t", ssh_host, "".join(setup_cmds)], capture_output=False)
    
    if res.returncode == 0:
        console.print("[green]Remote tunnel persistence configured successfully.[/green]")
        
        # 4. Check status robustly
        console.print("Checking remote service status...")
        status_check = [
            "ssh", "-t", ssh_host,
            f"if command -v systemctl >/dev/null; then systemctl status {LINUX_SYSTEMD_SERVICE}; "
            "elif [ \"$(uname)\" = \"Darwin\" ]; then sudo launchctl print system/com.cloudflare.cloudflared | grep state; "
            "else echo 'Could not determine service status'; fi"
        ]
        run_command(status_check, capture_output=False)
        return True
    else:
        console.print(f"[red]Failed to configure remote tunnel persistence. (Exit code: {res.returncode})[/red]")
        return False

def bootstrap_local_macos(token: str, account_id: str):
    """Bootstrap tunnel on local macOS machine (self-bootstrap mode)."""
    if sys.platform != "darwin":
        console.print("[red]Local bootstrap is only supported on macOS.[/red]")
        return False
    
    import socket
    hostname = socket.gethostname().split('.')[0]  # Get short hostname
    tunnel_name = f"{hostname}-tunnel"
    
    console.print(f"\n[bold blue]Local macOS Bootstrap Mode[/bold blue]")
    console.print(f"Hostname: {hostname}")
    console.print(f"Tunnel name: {tunnel_name}\n")
    
    # Check if tunnel already exists
    existing_tunnels = list_tunnels(token, account_id)
    existing = next((t for t in existing_tunnels if t.get("name") == tunnel_name), None)
    
    if existing:
        console.print(f"[yellow]Tunnel '{tunnel_name}' already exists (ID: {existing.get('id')}).[/yellow]")
        if not prompt_yes_no("Reuse existing tunnel?", default=True):
            return False
        tunnel_id = existing.get("id")
        token_val = get_tunnel_token(token, account_id, tunnel_id)
    else:
        # Create new tunnel
        console.print(f"Creating tunnel '{tunnel_name}'...")
        tunnel_res = create_tunnel(token, account_id, tunnel_name)
        if not tunnel_res or not tunnel_res.get("success"):
            console.print(f"[red]Failed to create tunnel: {tunnel_res.get('errors') if tunnel_res else 'No response'}[/red]")
            return False
        tunnel_id = tunnel_res.get("result", {}).get("id")
        token_val = tunnel_res.get("result", {}).get("tunnel_token") or tunnel_res.get("result", {}).get("token")
    
    if not token_val:
        console.print("[red]Failed to get connector token.[/red]")
        return False
    
    # Install cloudflared if needed
    cloudflared_bin = detect_cloudflared_binary()
    if not cloudflared_bin:
        console.print("[yellow]cloudflared not found.[/yellow]")
        if prompt_yes_no("Install cloudflared via Homebrew?", default=True):
            try:
                subprocess.run(["brew", "install", "cloudflared"], check=True)
                cloudflared_bin = detect_cloudflared_binary()
            except (subprocess.CalledProcessError, FileNotFoundError):
                console.print("[red]Failed to install cloudflared. Please install manually.[/red]")
                return False
        else:
            return False
    
    # Install service
    console.print(f"\nInstalling cloudflared service via {cloudflared_bin}...")
    try:
        subprocess.run(["sudo", str(cloudflared_bin), "service", "install", token_val], check=True)
        console.print("[green][✓] Service installed.[/green]")
    except subprocess.CalledProcessError as e:
        # May fail if already installed, which is okay
        console.print("[yellow]Service install returned non-zero (may already be installed).[/yellow]")
    
    # Configure power settings
    console.print("Configuring macOS power settings for 24/7 tunnel access...")
    try:
        subprocess.run(["sudo", "pmset"] + MACOS_PMSET_ARGS, check=True)
        console.print("[green][✓] Power settings configured.[/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Warning: Failed to configure power settings: {e}[/yellow]")
    
    # Start service
    console.print("Starting cloudflared service...")
    try:
        subprocess.run(["sudo", "launchctl", "load", "-w", MACOS_LAUNCHDAEMON_PATH], check=False)
        subprocess.run(["sudo", "launchctl", "kickstart", "-k", "system/com.cloudflare.cloudflared"], check=True)
        console.print("[green][✓] Service started.[/green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[yellow]Warning: Service start issue: {e}[/yellow]")
    
    # Save tunnel info to config
    config = load_pas_config("cf")
    config["TUNNEL_TOKEN"] = token_val
    if "local_tunnels" not in config:
        config["local_tunnels"] = {}
    config["local_tunnels"][tunnel_id] = {
        "name": tunnel_name,
        "hostname": hostname,
        "created_at": datetime.datetime.now().isoformat()
    }
    save_pas_config("cf", config)
    
    console.print(f"\n[bold green]Local bootstrap complete![/bold green]")
    console.print(f"Tunnel ID: {tunnel_id}")
    console.print(f"Tunnel name: {tunnel_name}")
    
    # Offer DNS setup
    if prompt_yes_no("\nWould you like to set up DNS records now?", default=True):
        zones = get_zones(token)
        if zones:
            zone_choices = [{"title": z["name"], "value": z} for z in zones]
            zone_choices.append({"title": "[Skip DNS Setup]", "value": "skip"})
            formatted_zones = format_menu_choices(zone_choices, title_field="title", value_field="value")
            console.print("\n[bold]Select a zone for DNS records:[/bold]")
            selected_zone = prompt_toolkit_menu(formatted_zones)
            
            if selected_zone and selected_zone != "skip":
                zone_name = selected_zone['name']
                target = CF_TUNNEL_TARGET_TEMPLATE.format(tunnel_id=tunnel_id)
                
                # Create SSH DNS record
                ssh_hostname = f"{hostname}{SSH_HOSTNAME_SUFFIX}.{zone_name}"
                if prompt_yes_no(f"Create DNS record: {ssh_hostname} -> {target}?", default=True):
                    dns_res = create_dns_record(token, selected_zone['id'], "CNAME", f"{hostname}{SSH_HOSTNAME_SUFFIX}", target, proxied=DNS_PROXIED)
                    if dns_res and dns_res.get("success"):
                        console.print(f"[green][✓] DNS record created: {ssh_hostname}[/green]")
                
                # Create VNC DNS record
                vnc_hostname = f"{hostname}{VNC_HOSTNAME_SUFFIX}.{zone_name}"
                if prompt_yes_no(f"Create DNS record: {vnc_hostname} -> {target}?", default=True):
                    dns_res = create_dns_record(token, selected_zone['id'], "CNAME", f"{hostname}{VNC_HOSTNAME_SUFFIX}", target, proxied=DNS_PROXIED)
                    if dns_res and dns_res.get("success"):
                        console.print(f"[green][✓] DNS record created: {vnc_hostname}[/green]")
                
                # Configure ingress rules
                console.print("Configuring ingress rules...")
                ingress_rules = [
                    {"hostname": ssh_hostname, "service": f"{SSH_SERVICE_PROTOCOL}localhost:{SSH_PORT}"},
                    {"hostname": vnc_hostname, "service": f"{VNC_SERVICE_PROTOCOL}localhost:{VNC_PORT}"},
                    {"service": DEFAULT_INGRESS_FALLBACK}
                ]
                ingress_config = {"config": {"ingress": ingress_rules}}
                config_res = update_tunnel_configuration(token, account_id, tunnel_id, ingress_config)
                
                if config_res and config_res.get("success"):
                    console.print(f"[green][✓] Ingress configuration updated.[/green]")
                    console.print(f"\nConnection commands:")
                    console.print(f"  ssh-cf {ssh_hostname}")
                    console.print(f"  vnc-cf {vnc_hostname}")
                else:
                    console.print(f"[yellow]Warning: Failed to update ingress: {config_res.get('errors') if config_res else 'No response'}[/yellow]")
    
    return True

def main():
    parser = argparse.ArgumentParser(description=__doc__.replace("@pas-executable", "").strip(), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--debug-token", action="store_true", help="Enable debug output for token name retrieval")
    parser.add_argument("--local-bootstrap", action="store_true", help="Bootstrap tunnel on local macOS machine (self-bootstrap mode)")
    args = parser.parse_args()
    
    # Handle local bootstrap mode
    if args.local_bootstrap:
        config = load_pas_config("cf")
        token = config.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
        account_id = config.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        
        if not token:
            console.print("[red]CLOUDFLARE_API_TOKEN not found.[/red]")
            console.print(f"Set it with: export CLOUDFLARE_API_TOKEN='your-token'")
            console.print(f"Or run cf-tunnel-ops normally first to configure credentials.")
            sys.exit(1)
        
        if not account_id:
            console.print("[red]CLOUDFLARE_ACCOUNT_ID not found.[/red]")
            console.print(f"Set it with: export CLOUDFLARE_ACCOUNT_ID='your-account-id'")
            console.print(f"Or run cf-tunnel-ops normally first to configure credentials.")
            sys.exit(1)
        
        bootstrap_local_macos(token, account_id)
        return

    info_text = """
[bold]Create Cloudflare Tunnel[/bold]

Automates the manual steps of tunnel creation via API:
- [cyan]Remote Creation[/cyan]: Creates the tunnel in your Cloudflare account.
- [cyan]Token Generation[/cyan]: Fetches the required connector token.
- [cyan]Service Install[/cyan]: Optional automatic installation as a system service.
- [cyan]Persistence[/cyan]: Saves the tunnel token to your PAS configuration.
"""
    console.print(Panel(info_text.strip(), title="cf-tunnel-ops", border_style="blue"))
    console.print("\n")

    config = load_pas_config("cf")
    
    account_id = config.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = config.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN")
    
    if account_id:
        print(f"Current Cloudflare Account ID: {account_id}")
        print(f"Find Account ID at: {CF_DASHBOARD_URL}")
        new_id = input("Enter to proceed or input a different Account ID: ").strip()
        if new_id:
            account_id = new_id
            config["CLOUDFLARE_ACCOUNT_ID"] = account_id
            save_pas_config("cf", config)
    else:
        print("\nCloudflare Account ID not found.")
        print(f"Find it on your Dashboard sidebar or in the URL: {CF_DASHBOARD_URL}")
        account_id = input("Enter Cloudflare Account ID: ").strip()
        if account_id:
            config["CLOUDFLARE_ACCOUNT_ID"] = account_id
            save_pas_config("cf", config)
    
    if not account_id:
        print("Error: Account ID is required.")
        sys.exit(1)
    
    # If token exists, verify permissions BEFORE asking if user wants to change it
    if token:
        # Pre-flight Permission Check (we'll show token name after verification)
        console.print("\n[bold blue]Verifying API Token permissions...[/bold blue]")
        perms = verify_token_permissions(token, account_id)
        
        # Separate required vs optional permissions
        required_perms = {k: v for k, v in perms.items() if k != "API Tokens (Read)"}
        optional_perms = {k: v for k, v in perms.items() if k == "API Tokens (Read)"}
        
        all_ok = all(required_perms.values())
        for perm, status in required_perms.items():
            icon = "[green]✓[/green]" if status else "[red]✗[/red]"
            console.print(f"  {icon} {perm}")
        
        # Show optional permission with note
        for perm, status in optional_perms.items():
            icon = "[green]✓[/green]" if status else "[yellow]○[/yellow]"
            note = " (optional: for token name display)" if not status else ""
            console.print(f"  {icon} {perm}{note}")

        if not all_ok:
            console.print("\n[bold red]⚠️ Missing Required Permissions![/bold red]")
            console.print("Your Cloudflare API Token needs the following 'Edit' permissions:")
            console.print("1. [bold]Account | Cloudflare Tunnel[/bold]")
            console.print("2. [bold]Account | Access: Apps and Policies[/bold]")
            console.print("3. [bold]Zone | DNS[/bold]")
            console.print("4. [bold]Zone | Zone (Read)[/bold]")
            console.print("\n[cyan]Direct Link to manage API tokens:[/cyan]")
            console.print(CF_API_TOKENS_URL)
            console.print("\n[cyan]Where to find Account ID:[/cyan]")
            console.print(f"Log in to {CF_DASHBOARD_URL} and check the URL or the sidebar.")
            
            if not perms["Access (Edit)"]:
                console.print("\n[yellow]Note: You can still manage tunnels and DNS, but 'Access Protection' will fail.[/yellow]")
        
        # Display token status with name and type
        # Try to retrieve token name/label (requires "API Tokens Read" permission)
        token_info = get_token_info(token, account_id, debug=args.debug_token)
        token_name = None
        token_type = None
        if token_info:
            # Try various possible field names for token name
            token_name = (token_info.get("name") or 
                         token_info.get("label") or 
                         token_info.get("token_name") or
                         token_info.get("description"))
            token_type = token_info.get("token_type")
            if args.debug_token and not token_name:
                print(f"\nDEBUG: Token info retrieved but no name field found.")
                print(f"DEBUG: Available keys: {list(token_info.keys())}")
                print(f"DEBUG: Full token info: {token_info}")
        elif args.debug_token:
            print("\nDEBUG: get_token_info returned None or token detail fetch failed")
            print("DEBUG: Error 9109 'Unauthorized' means the token lacks permission to read its own details")
            print("DEBUG: For USER-OWNED tokens: Add 'User -> API Tokens -> Read' permission")
            print("DEBUG: For ACCOUNT-OWNED tokens: Add 'Account -> API Tokens -> Read' permission")
        
        # Display token status with name and type inline if available
        token_parts = []
        if token_name:
            token_parts.append(f"name: {token_name}")
        if token_type:
            token_parts.append(f"type: {token_type.capitalize()}")
        
        if token_parts:
            print(f"\nCloudflare API Token is already set. ({', '.join(token_parts)})")
        else:
            print("\nCloudflare API Token is already set.")
        
        print(f"Manage tokens at: {CF_API_TOKENS_URL}")
        new_token = input("Enter to proceed or input a different API Token: ").strip()
        if new_token:
            token = new_token
            config["CLOUDFLARE_API_TOKEN"] = token
            save_pas_config("cf", config)
            # Re-verify permissions for the new token
            if token:
                console.print("\n[bold blue]Verifying new API Token permissions...[/bold blue]")
                perms = verify_token_permissions(token, account_id)
                # Separate required vs optional permissions
                required_perms = {k: v for k, v in perms.items() if k != "API Tokens (Read)"}
                optional_perms = {k: v for k, v in perms.items() if k == "API Tokens (Read)"}
                
                all_ok = all(required_perms.values())
                for perm, status in required_perms.items():
                    icon = "[green]✓[/green]" if status else "[red]✗[/red]"
                    console.print(f"  {icon} {perm}")
                
                # Show optional permission with note
                for perm, status in optional_perms.items():
                    icon = "[green]✓[/green]" if status else "[yellow]○[/yellow]"
                    note = " (optional: for token name display)" if not status else ""
                    console.print(f"  {icon} {perm}{note}")
                if not all_ok:
                    console.print("\n[yellow]⚠️ New token also has missing permissions. Some features may not work.[/yellow]")

    if not token:
        print("\nCLOUDFLARE_API_TOKEN not found.")
        print("Create a 'Custom Token' with needed permissions at:")
        print(CF_API_TOKENS_URL)
        token = input("Enter Cloudflare API Token: ").strip()
        if token:
            config["CLOUDFLARE_API_TOKEN"] = token
            save_pas_config("cf", config)
            # Verify permissions for the new token
            if account_id:
                console.print("\n[bold blue]Verifying API Token permissions...[/bold blue]")
                perms = verify_token_permissions(token, account_id)
                # Separate required vs optional permissions
                required_perms = {k: v for k, v in perms.items() if k != "API Tokens (Read)"}
                optional_perms = {k: v for k, v in perms.items() if k == "API Tokens (Read)"}
                
                all_ok = all(required_perms.values())
                for perm, status in required_perms.items():
                    icon = "[green]✓[/green]" if status else "[red]✗[/red]"
                    console.print(f"  {icon} {perm}")
                
                # Show optional permission with note
                for perm, status in optional_perms.items():
                    icon = "[green]✓[/green]" if status else "[yellow]○[/yellow]"
                    note = " (optional: for token name display)" if not status else ""
                    console.print(f"  {icon} {perm}{note}")
                if not all_ok:
                    console.print("\n[yellow]⚠️ Token has missing permissions. Some features may not work.[/yellow]")
        
    if not token:
        print("Error: API Token is required.")
        sys.exit(1)

    # Main menu for tunnel operations
    ops_choices = [
        {"title": "Create Local Tunnel (Install on this machine)", "value": "local"},
        {"title": "Create Remote Tunnel (Install on another server via direct SSH)", "value": "remote"},
        {"title": "Update Existing Tunnel", "value": "update"},
        {"title": "Manage/Remove Access Protection", "value": "access_manage"},
        {"title": "[Quit]", "value": "quit"}
    ]
    formatted_ops = format_menu_choices(ops_choices, title_field="title", value_field="value")
    console.print("\n[bold]Select tunnel operation mode:[/bold]")
    mode = prompt_toolkit_menu(formatted_ops)

    if not mode or mode == "quit":
        return

    if mode == "access_manage":
        print("Fetching Access Applications...")
        apps = list_access_apps(token, account_id)
        if not apps:
            print("No Access Applications found.")
            return
        
        app_choices = [{"title": f"{a.get('name')} ({a.get('domain')})", "value": a} for a in apps]
        app_choices.append({"title": "[Back]", "value": "back"})
        formatted_apps = format_menu_choices(app_choices, title_field="title", value_field="value")
        console.print("\n[bold]Select an Access Application to remove protection:[/bold]")
        selected_app = prompt_toolkit_menu(formatted_apps)
        
        if not selected_app or selected_app == "back":
            return
            
        if prompt_yes_no(f"Are you sure you want to REMOVE Zero Trust protection for {selected_app['domain']}?", default=False):
            res = delete_access_app(token, account_id, selected_app['id'])
            if res and res.get("success"):
                console.print(f"[green][✓] Access protection removed for {selected_app['domain']}.[/green]")
                console.print("[yellow]Note: SSH/VNC access will now rely solely on your server's local authentication.[/yellow]")
            else:
                console.print(f"[red][ ] Failed to remove protection: {res.get('errors') if res else 'No response'}[/red]")
        return

    tunnel_id = None
    token_val = None
    name = None

    if mode == "update":
        print("Fetching existing tunnels...")
        existing_tunnels = list_tunnels(token, account_id)
        if not existing_tunnels:
            print("No existing tunnels found.")
            return
        
        tunnel_choices = [{"title": f"{t.get('name')} ({t.get('id')})", "value": t} for t in existing_tunnels]
        tunnel_choices.append({"title": "[Back]", "value": "back"})
        formatted_tunnels = format_menu_choices(tunnel_choices, title_field="title", value_field="value")
        console.print("\n[bold]Select a tunnel to update:[/bold]")
        selected_tunnel = prompt_toolkit_menu(formatted_tunnels)
        
        if not selected_tunnel or selected_tunnel == "back":
            return
            
        name = selected_tunnel.get("name")
        tunnel_id = selected_tunnel.get("id")
        
        print(f"Fetching token for tunnel '{name}'...")
        token_val = get_tunnel_token(token, account_id, tunnel_id)
        if not token_val:
            console.print("[red]Failed to fetch token for tunnel. Cannot proceed.[/red]")
            return
            
        # For updates, we still need to know if it's local or remote for the service setup part
        update_ops = [
            {"title": "Update on Local Machine", "value": "local"},
            {"title": "Update on Remote Server (via direct SSH)", "value": "remote"},
            {"title": "Rename this Tunnel", "value": "rename"},
            {"title": "Delete this Tunnel", "value": "delete"},
            {"title": "[Back]", "value": "back"}
        ]
        formatted_update_ops = format_menu_choices(update_ops, title_field="title", value_field="value")
        console.print(f"\n[bold]What would you like to do with tunnel '{name}'?[/bold]")
        mode = prompt_toolkit_menu(formatted_update_ops)
        if not mode or mode == "back":
            return
            
        if mode == "delete":
            if prompt_yes_no(f"Are you sure you want to DELETE tunnel '{name}' ({tunnel_id})?", default=False):
                print(f"Deleting tunnel '{tunnel_id}'...")
                del_res = delete_tunnel(token, account_id, tunnel_id)
                if del_res and del_res.get("success"):
                    console.print(f"[green][✓] Tunnel '{name}' deleted successfully.[/green]")
                    # Clean up cached SSH host
                    if "tunnel_ssh_hosts" in config and tunnel_id in config["tunnel_ssh_hosts"]:
                        del config["tunnel_ssh_hosts"][tunnel_id]
                        save_pas_config("cf", config)
                    # Also suggest cleaning up DNS if it was the last use of the tunnel
                    print("Note: You may still have DNS records pointing to this tunnel ID.")
                else:
                    console.print(f"[red][ ] Failed to delete tunnel: {del_res.get('errors') if del_res else 'No response'}[/red]")
            return

        if mode == "rename":
            new_name = input(f"Enter new name for tunnel '{name}': ").strip()
            if new_name and new_name != name:
                print(f"Renaming tunnel to '{new_name}'...")
                rename_res = update_tunnel(token, account_id, tunnel_id, new_name)
                if rename_res and rename_res.get("success"):
                    console.print(f"[green][✓] Tunnel renamed successfully to '{new_name}'.[/green]")
                    name = new_name # Update local variable for next steps
                else:
                    console.print(f"[red][ ] Failed to rename tunnel: {rename_res.get('errors') if rename_res else 'No response'}[/red]")
            return

    if not name:
        name = input("\nEnter name for the new tunnel (default: api-tunnel): ").strip() or "api-tunnel"
    
    # Check if tunnel already exists (only for new creation flows)
    if not tunnel_id:
        print(f"Checking for existing tunnels named '{name}'...")
        existing_tunnels = list_tunnels(token, account_id)
        match = next((t for t in existing_tunnels if t.get("name") == name), None)
        
        if match:
            tunnel_id = match.get("id")
            console.print(f"\n[yellow]⚠️ Tunnel with name '{name}' already exists (ID: {tunnel_id}).[/yellow]")
            
            exist_choices = [
                {"title": f"Reuse/Update existing tunnel '{name}'", "value": "reuse"},
                {"title": f"Delete and recreate tunnel '{name}'", "value": "recreate"},
                {"title": "[Cancel / Choose different name]", "value": "cancel"}
            ]
            formatted_exist = format_menu_choices(exist_choices, title_field="title", value_field="value")
            console.print("[bold]How would you like to proceed?[/bold]")
            exist_action = prompt_toolkit_menu(formatted_exist)
            
            if exist_action == "cancel" or not exist_action:
                return
            
            if exist_action == "reuse":
                print(f"Fetching token for existing tunnel '{name}'...")
                token_val = get_tunnel_token(token, account_id, tunnel_id)
                if not token_val:
                    console.print("[red]Failed to fetch token for existing tunnel. Cannot proceed with setup.[/red]")
                    return
            else: # recreate
                if prompt_yes_no(f"Are you sure you want to DELETE tunnel '{name}' and create a new one?", default=False):
                    print(f"Deleting tunnel '{tunnel_id}'...")
                    del_res = delete_tunnel(token, account_id, tunnel_id)
                    if not del_res or not del_res.get("success"):
                        console.print(f"[red]Failed to delete tunnel: {del_res.get('errors') if del_res else 'No response'}[/red]")
                        return
                    # Clean up cached SSH host for the old ID
                    if "tunnel_ssh_hosts" in config and tunnel_id in config["tunnel_ssh_hosts"]:
                        del config["tunnel_ssh_hosts"][tunnel_id]
                        save_pas_config("cf", config)
                    print("Tunnel deleted. Creating new one...")
                    tunnel_id = None # Reset so it creates a new one
                else:
                    return

    if not tunnel_id:
        print(f"\nCreating new tunnel '{name}'...")
        full_response = create_tunnel(token, account_id, name)
        if full_response and full_response.get("success"):
            result = full_response.get("result")
            tunnel_id = result.get("id")
            token_val = result.get("tunnel_token") or result.get("token")
        else:
            if full_response:
                print(f"\nAPI Error: {full_response.get('errors')}")
            else:
                print("\nFailed to create tunnel (no response from API).")
            return

    # If we have both tunnel_id and token_val, proceed
    if tunnel_id and token_val:
        print("\n" + "=" * 40)
        print(f"Tunnel Ready: {name}")
        print(f"ID:    {tunnel_id}")
        print(f"Token: {token_val}")
        print(f"URL:   {CF_TUNNEL_EDIT_URL_TEMPLATE.format(account_id=account_id, tunnel_id=tunnel_id)}")
        print("=" * 40)

        # Verification step
        print(f"\nVerifying tunnel '{tunnel_id}' via API...")
        v_data = get_tunnel(token, account_id, tunnel_id)
        if v_data and v_data.get("success"):
            print(f"  [✓] Tunnel verified. Status: {v_data.get('result', {}).get('status', 'N/A')}")
        else:
            print(f"  [ ] Verification failed: {v_data.get('errors') if v_data else 'No response'}")
        
        # Save TUNNEL_TOKEN to cf.json
        if token_val:
            config["TUNNEL_TOKEN"] = token_val
            save_pas_config("cf", config)
            print(f"Tunnel token saved to ~/.pas/cf.json as TUNNEL_TOKEN")

        # Installation logic based on mode
        installed_successfully = False
        if mode == "local":
            if token_val and prompt_yes_no("\nWould you like to install this tunnel as a system service on THIS machine?"):
                cloudflared_bin = detect_cloudflared_binary()
                if not cloudflared_bin:
                    print("Error: cloudflared binary not found; cannot install service.")
                else:
                    print(f"Installing tunnel service via {cloudflared_bin}...")
                    try:
                        import subprocess
                        subprocess.run(["sudo", str(cloudflared_bin), "service", "install", token_val], check=True)
                        print("\n[✓] Tunnel service installed successfully.")
                        installed_successfully = True
                    except subprocess.CalledProcessError as e:
                        print(f"\n[ ] Failed to install tunnel service: {e}")
        elif mode == "remote":
            # Check for cached SSH host for this tunnel
            ssh_hosts_cache = config.get("tunnel_ssh_hosts", {})
            default_ssh_host = ssh_hosts_cache.get(tunnel_id, "")
            
            prompt_msg = f"\nEnter remote SSH host (e.g. user@host or alias) [{default_ssh_host}]: " if default_ssh_host else "\nEnter remote SSH host (e.g. user@host or alias): "
            ssh_host = input(prompt_msg).strip() or default_ssh_host
            
            if ssh_host:
                if setup_remote_tunnel(ssh_host, token_val, name):
                    installed_successfully = True
                    # Cache the successful SSH host
                    if "tunnel_ssh_hosts" not in config:
                        config["tunnel_ssh_hosts"] = {}
                    config["tunnel_ssh_hosts"][tunnel_id] = ssh_host
                    save_pas_config("cf", config)
                    print(f"Remote SSH host '{ssh_host}' cached for tunnel '{name}'")

        # DNS Setup
        if installed_successfully or prompt_yes_no("\nWould you like to setup DNS records for this tunnel now?"):
            # Fetch existing configuration to find current hostnames
            current_config = get_tunnel_configuration(token, account_id, tunnel_id)
            existing_hostnames = []
            if current_config and current_config.get("success"):
                ingress = current_config.get("result", {}).get("config", {}).get("ingress", [])
                raw_hostnames = sorted(list(set(rule.get("hostname") for rule in ingress if rule.get("hostname"))))
                # Clean up any malformed hostnames (like double vnc-)
                existing_hostnames = []
                for h in raw_hostnames:
                    parts = h.split('.')
                    if len(parts) > 1:
                        prefix = parts[0]
                        while prefix.startswith("vnc-vnc-"):
                            prefix = prefix[4:] # strip one "vnc-"
                        # Also handle if someone manually put vnc-vnc- as a string
                        if prefix.startswith("vnc-vnc-"):
                             prefix = prefix.replace("vnc-vnc-", "vnc-")
                        new_h = ".".join([prefix] + parts[1:])
                        if new_h not in existing_hostnames:
                            existing_hostnames.append(new_h)
                existing_hostnames.sort()

            zones = get_zones(token)
            if zones:
                zone_choices = [{"title": z["name"], "value": z} for z in zones]
                zone_choices.append({"title": "[Skip DNS Setup]", "value": "skip"})
                formatted_zones = format_menu_choices(zone_choices, title_field="title", value_field="value")
                console.print("\n[bold]Select a zone for DNS record:[/bold]")
                selected_zone = prompt_toolkit_menu(formatted_zones)

                if selected_zone and selected_zone != "skip":
                    # Offer existing hostnames for this zone if any
                    zone_name = selected_zone['name']
                    relevant_hostnames = [h for h in existing_hostnames if h.endswith(f".{zone_name}")]
                    
                    # Define standard hostnames based on tunnel name
                    standard_ssh = f"{name}{SSH_HOSTNAME_SUFFIX}.{zone_name}"
                    standard_vnc = f"{name}{VNC_HOSTNAME_SUFFIX}.{zone_name}"
                    is_standard_present = standard_ssh in relevant_hostnames
                    
                    selected_full_hostname = None
                    if relevant_hostnames or not is_standard_present:
                        host_choices = []
                        if len(relevant_hostnames) >= 1:
                            host_choices.append({"title": f"Accept all current hostnames for {zone_name} ({', '.join(relevant_hostnames)})", "value": "__all_current__"})
                        
                        if not is_standard_present:
                            host_choices.append({"title": f"Migrate to new standard: {standard_ssh}, {standard_vnc}", "value": "__migrate_standard__"})
                        
                        host_choices.extend([{"title": f"Keep/Update existing: {h}", "value": h} for h in relevant_hostnames])
                        host_choices.append({"title": "[Create new hostname]", "value": "__new__"})
                        formatted_hosts = format_menu_choices(host_choices, title_field="title", value_field="value")
                        console.print(f"\n[bold]DNS Setup for {zone_name}:[/bold]")
                        selected_full_hostname = prompt_toolkit_menu(formatted_hosts)
                    
                    # Handle the selections
                    hostnames_to_process = []
                    if selected_full_hostname == "__all_current__":
                        hostnames_to_process = relevant_hostnames
                    elif selected_full_hostname == "__migrate_standard__":
                        hostnames_to_process = [standard_ssh, standard_vnc]
                    elif selected_full_hostname and selected_full_hostname != "__new__":
                        hostnames_to_process = [selected_full_hostname]
                    elif not selected_full_hostname or selected_full_hostname == "__new__":
                        # Default to tunnel name with SSH suffix
                        default_hostname = f"{name}{SSH_HOSTNAME_SUFFIX}"
                        hostname_part = input(f"Enter hostname part (e.g. 'myservice' for myservice.{zone_name}) [{default_hostname}]: ").strip() or default_hostname
                        if hostname_part:
                            hostnames_to_process = [f"{hostname_part}.{zone_name}"]

                    actual_hostnames = []
                    if not hostnames_to_process:
                        print("No hostnames selected. Skipping DNS setup.")
                    else:
                        target = CF_TUNNEL_TARGET_TEMPLATE.format(tunnel_id=tunnel_id)
                        # Process each hostname
                        for full_hostname in hostnames_to_process:
                            hostname_part = full_hostname[:-(len(zone_name) + 1)]
                            
                            # Check if DNS record exists
                            existing_records = get_dns_records(token, selected_zone['id'])
                            dns_record = next((r for r in existing_records if r.get("name") == full_hostname and r.get("type") == "CNAME"), None)
                            
                            dns_res = None
                            if dns_record:
                                if prompt_yes_no(f"Update DNS record: {full_hostname} -> {target}?", default=True):
                                    dns_res = update_dns_record(token, selected_zone['id'], dns_record['id'], "CNAME", hostname_part, target, proxied=DNS_PROXIED)
                                else:
                                    print(f"Using existing DNS record for {full_hostname}")
                                    actual_hostnames.append(full_hostname)
                                    continue
                            else:
                                if prompt_yes_no(f"Create DNS record: {full_hostname} -> {target}?", default=True):
                                    dns_res = create_dns_record(token, selected_zone['id'], "CNAME", hostname_part, target, proxied=DNS_PROXIED)
                                else:
                                    print(f"Skipping creation for {full_hostname}")
                                    continue
                                
                            if dns_res and dns_res.get("success"):
                                actual_hostnames.append(full_hostname)
                                console.print(f"[green][✓] DNS record configured: {full_hostname}[/green]")
                            else:
                                console.print(f"[red][ ] Failed to configure DNS record: {dns_res.get('errors') if dns_res else 'No response'}[/red]")

                    # Determine hostnames for Access protection logic
                    primary_hostname = None
                    vnc_full_hostname = None
                    
                    # Heuristic: try to find a pair or just use what we processed
                    for h in actual_hostnames:
                        h_part = h[:-(len(zone_name) + 1)]
                        # Check for and clean up double prefixes/suffixes in the processed list
                        if h_part.startswith("vnc-vnc-"):
                            while h_part.startswith("vnc-vnc-"):
                                h_part = h_part[4:]
                            h = f"{h_part}.{zone_name}"

                        if h_part.endswith(VNC_HOSTNAME_SUFFIX) or h_part.startswith("vnc-"):
                            vnc_full_hostname = h
                        elif h_part.endswith(SSH_HOSTNAME_SUFFIX) or not h_part.endswith(VNC_HOSTNAME_SUFFIX):
                            primary_hostname = h
                    
                    # If we only have VNC, try to find/infer the primary
                    if vnc_full_hostname and not primary_hostname:
                        vnc_part = vnc_full_hostname[:-(len(zone_name) + 1)]
                        primary_part = None
                        if vnc_part.endswith(VNC_HOSTNAME_SUFFIX):
                            primary_part = f"{vnc_part[:-len(VNC_HOSTNAME_SUFFIX)]}{SSH_HOSTNAME_SUFFIX}"
                        elif vnc_part.startswith("vnc-"):
                            primary_part = vnc_part[4:]
                            
                        if primary_part:
                            primary_hostname = f"{primary_part}.{zone_name}"
                            console.print(f"[yellow]Inferring primary hostname '{primary_hostname}' from VNC hostname.[/yellow]")

                    # If we only have primary, ensure VNC is also offered for update
                    if primary_hostname and not vnc_full_hostname:
                        primary_part = primary_hostname[:-(len(zone_name) + 1)]
                        if primary_part.endswith(SSH_HOSTNAME_SUFFIX):
                            vnc_hostname_part = f"{primary_part[:-len(SSH_HOSTNAME_SUFFIX)]}{VNC_HOSTNAME_SUFFIX}"
                        else:
                            vnc_hostname_part = f"vnc-{primary_part}"
                            
                        vnc_full_hostname = f"{vnc_hostname_part}.{zone_name}"
                        target = CF_TUNNEL_TARGET_TEMPLATE.format(tunnel_id=tunnel_id)
                        
                        # Ensure VNC record exists too
                        vnc_dns_record = next((r for r in existing_records if r.get("name") == vnc_full_hostname and r.get("type") == "CNAME"), None)
                        vnc_res = None
                        if vnc_dns_record:
                            if prompt_yes_no(f"Update VNC DNS record: {vnc_full_hostname} -> {target}?", default=True):
                                vnc_res = update_dns_record(token, selected_zone['id'], vnc_dns_record['id'], "CNAME", vnc_hostname_part, target, proxied=DNS_PROXIED)
                            else:
                                vnc_res = {"success": True}
                        else:
                            if prompt_yes_no(f"Create VNC DNS record: {vnc_full_hostname} -> {target}?", default=True):
                                vnc_res = create_dns_record(token, selected_zone['id'], "CNAME", vnc_hostname_part, target, proxied=DNS_PROXIED)
                        
                        if vnc_res and vnc_res.get("success"):
                            console.print(f"[green][✓] VNC DNS record ensured: {vnc_full_hostname}[/green]")
                        else:
                            vnc_full_hostname = None # Reset if not actually created/updated
                            if vnc_res:
                                 console.print(f"[red][ ] Failed to ensure VNC DNS record: {vnc_res.get('errors')}[/red]")

                    # Automatically configure ingress rules for all hostnames we found/ensured
                    ingress_hosts = []
                    if primary_hostname: ingress_hosts.append({"hostname": primary_hostname, "service": f"{SSH_SERVICE_PROTOCOL}localhost:{SSH_PORT}"})
                    if vnc_full_hostname: ingress_hosts.append({"hostname": vnc_full_hostname, "service": f"{VNC_SERVICE_PROTOCOL}localhost:{VNC_PORT}"})
                    
                    if ingress_hosts:
                        console.print(f"Configuring ingress rules...")
                        ingress_rules = [ { "hostname": ih["hostname"], "service": ih["service"] } for ih in ingress_hosts ]
                        ingress_rules.append({"service": "http_status:404"})
                        
                        ingress_config = { "config": { "ingress": ingress_rules } }
                        config_res = update_tunnel_configuration(token, account_id, tunnel_id, ingress_config)
                        
                        if config_res and config_res.get("success"):
                            console.print(f"[green][✓] Ingress configuration updated successfully.[/green]")
                            
                            # Protect all relevant hostnames
                            hosts_to_protect = [primary_hostname, vnc_full_hostname] if primary_hostname and vnc_full_hostname else actual_hostnames
                            for target_host in [h for h in hosts_to_protect if h]:
                                if prompt_yes_no(f"\nWould you like to add Zero Trust Access protection for {target_host}?\n(Note: This requires interactive browser login for every new session)", default=False):
                                    console.print(f"Ensuring Access protection for {target_host}...")
                                    
                                    # Fetch user details if needed
                                    user_info = get_user_details(token)
                                    user_email = user_info.get("result", {}).get("email") if user_info and user_info.get("success") else None
                                    
                                    # Check if Access App already exists
                                    existing_apps = list_access_apps(token, account_id)
                                    access_app = next((a for a in existing_apps if a.get("domain") == target_host), None)
                                    
                                    app_name = f"Access: {target_host}"
                                    while True:
                                        if access_app:
                                            app_name = access_app.get("name", app_name)
                                            console.print(f"\n[yellow]Existing Access Application found:[/yellow] {app_name} ({target_host})")
                                            new_app_name = input(f"Review Application Name [{app_name}] (Enter to keep, or type new): ").strip()
                                            if new_app_name:
                                                app_name = new_app_name
                                            
                                            console.print(f"Updating Access Application for {target_host}...")
                                            app_res = update_access_app(token, account_id, access_app['id'], app_name, target_host)
                                        else:
                                            if not user_email:
                                                user_email = input(f"Could not fetch account email. Enter email for the 'Allow' policy for {target_host}: ").strip()
                                            else:
                                                console.print(f"Using default email for policy: {user_email}")

                                            console.print(f"Creating Access Application for {target_host}...")
                                            app_res = create_access_app(token, account_id, app_name, target_host)
                                            
                                        if app_res and app_res.get("success"):
                                            app_id = app_res.get("result", {}).get("id") if not access_app else access_app['id']
                                            console.print(f"[green][✓] Access Application configured for {target_host}[/green]")
                                            
                                            if not access_app:
                                                console.print(f"Creating 'Allow' policy for {user_email}...")
                                                pol_res = create_access_policy(token, account_id, app_id, "Allow Owner", user_email)
                                                if pol_res and pol_res.get("success"):
                                                    console.print(f"[green][✓] Access Policy created successfully.[/green]")
                                                else:
                                                    console.print(f"[red][ ] Failed to create Access Policy: {pol_res.get('errors')}[/red]")
                                            break 
                                        else:
                                            errors = app_res.get('errors', [])
                                            if any(e.get('code') == 10000 for e in errors):
                                                console.print(f"\n[bold red][!] Access Authentication Error (10000)[/bold red]")
                                                console.print("Your API Token is missing 'Access' permissions. Please add Edit permissions for Applications and Policies.")
                                                if not prompt_yes_no("\nRetry after updating permissions?"): break
                                            else:
                                                console.print(f"[red][ ] Failed to configure Access Application: {errors}[/red]")
                                                break

                            console.print(f"\n[bold green]Success![/bold green] Everything is configured.")
                            console.print(f"[yellow]Note: It may take 60 seconds for Cloudflare Access policies to propagate globally.[/yellow]")
                            console.print(f"Connection commands:")
                            if primary_hostname:
                                console.print(f"  ssh-cf {primary_hostname}")
                                if vnc_full_hostname:
                                    console.print(f"  vnc-cf {vnc_full_hostname}")
                        else:
                            console.print(f"[red][ ] Failed to update ingress configuration: {config_res.get('errors') if config_res else 'No response'}[/red]")
                else:
                    console.print(f"[red][ ] Failed to configure DNS record: {dns_res.get('errors') if dns_res else 'No response'}[/red]")
            else:
                print("No Cloudflare zones found to setup DNS.")

        print("\nSetup complete.")
    else:
        if full_response:
            print(f"\nAPI Error: {full_response.get('errors')}")
        else:
            print("\nFailed to create tunnel (no response from API).")

if __name__ == "__main__":
    main()
