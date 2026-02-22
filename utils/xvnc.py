#!/usr/bin/env python3
"""
@pas-executable
Smart VNC wrapper that automatically detects if a Cloudflare Tunnel is needed.
Usage: xvnc hostname [local_port]
"""

import sys
import subprocess
import time
import socket
import json
import argparse
import re
from pathlib import Path

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    console, 
    detect_cloudflared_binary, 
    load_pas_config, 
    save_pas_config,
    is_cloudflare_host
)
from rich.panel import Panel

# --- Configuration ---
VNC_PORT = 5900
# ---------------------

def show_summary():
    """Display a brief summary of the tool's capabilities."""
    summary = (
        "[bold cyan]xvnc[/bold cyan] (Extended VNC) is a smart wrapper for macOS [bold]Screen Sharing[/bold].\n\n"
        "[bold]Capabilities:[/bold]\n"
        "• [bold]Smart Detection:[/bold] Automatically checks if the host is a Cloudflare Tunnel.\n"
        "• [bold]Zero-Config Bridge:[/bold] Maps unique loopback IPs (127.0.0.x) for tunnel hosts.\n"
        "• [bold]Fallback:[/bold] Directly opens `vnc://hostname` for non-tunnel hosts.\n"
        "• [bold]Persistence:[/bold] Remembers loopback mappings to preserve saved passwords in Screen Sharing."
    )
    console.print(Panel(summary, title="Smart VNC Wrapper", expand=False))

def load_vnc_ips() -> dict:
    """Load hostname to loopback IP mappings from PAS config."""
    return load_pas_config("vnc-mappings")

def save_vnc_ips(mappings: dict):
    """Save hostname to loopback IP mappings to PAS config."""
    save_pas_config("vnc-mappings", mappings)

def is_addr_in_use(ip, port):
    """Check if an address is already listening."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex((ip, port)) == 0
    except Exception:
        return False

def ensure_ip_aliased(ip):
    """Ensure the unique loopback IP is aliased on the lo0 interface (macOS)."""
    if sys.platform != "darwin":
        return True
    
    # Check if the IP alias already exists by looking for "inet <ip>" in the lo0 output
    res = subprocess.run(["ifconfig", "lo0"], capture_output=True, text=True)
    # More robust check: look for "inet 127.0.0.x" pattern (not just substring match)
    if re.search(rf'\binet\s+{re.escape(ip)}\b', res.stdout):
        return True
    
    console.print(f"\n[bold yellow][!][/bold yellow] Unique IP {ip} is not configured. Adding alias (requires sudo)...")
    console.print("[dim]Note: IP aliases are not persistent across reboots. This is normal.[/dim]")
    try:
        # Use -n flag to avoid password prompt if sudo is configured with NOPASSWD
        result = subprocess.run(
            ["sudo", "-n", "ifconfig", "lo0", "alias", ip],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            return True
        # If -n failed (needs password), prompt interactively
        subprocess.run(["sudo", "ifconfig", "lo0", "alias", ip], check=True)
        return True
    except subprocess.CalledProcessError:
        console.print(f"[bold red]✗[/bold red] Failed to add alias for {ip}.")
        return False

def find_available_tunnel_address(hostname):
    """Find a persistent available 127.0.0.x address for a hostname."""
    ips = load_vnc_ips()
    if hostname in ips:
        ip = ips[hostname]
        return ip, VNC_PORT
    
    used_ips = set(ips.values())
    for i in range(2, 255):
        ip = f"127.0.0.{i}"
        if ip not in used_ips and not is_addr_in_use(ip, VNC_PORT):
            ips[hostname] = ip
            save_vnc_ips(ips)
            console.print(f"[dim]Saved IP mapping: {hostname} -> {ip} (in ~/.pas/vnc-mappings.json)[/dim]")
            return ip, VNC_PORT
    return "127.0.0.1", 5901

def main():
    parser = argparse.ArgumentParser(
        description="Smart VNC wrapper for macOS Screen Sharing.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("hostname", help="Target hostname")
    parser.add_argument("local_port", nargs="?", type=int, default=VNC_PORT, help="Local port for the bridge")
    parser.add_argument("--cf", action="store_true", help="Force Cloudflare Tunnel mode")
    parser.add_argument("--no-cf", action="store_true", help="Force standard VNC mode")
    
    # Check if we are being run without argparse (backwards compatibility for raw sys.argv)
    if len(sys.argv) < 2:
        show_summary()
        console.print("\n[bold yellow]Usage:[/bold yellow] xvnc hostname [local_port] [--cf] [--no-cf]")
        sys.exit(1)

    args = parser.parse_args()
    hostname = args.hostname
    
    # Smart detection with overrides
    use_cf = False
    if args.cf:
        use_cf = True
    elif args.no_cf:
        use_cf = False
    else:
        use_cf = is_cloudflare_host(hostname)
    
    if use_cf:
        target_ip, local_port = find_available_tunnel_address(hostname)
        
        if not ensure_ip_aliased(target_ip):
            sys.exit(1)

        cloudflared_path = detect_cloudflared_binary()
        if not cloudflared_path:
            console.print("[bold red]Error:[/bold red] 'cloudflared' binary not found locally, but target appears to be a tunnel.")
            console.print("Attempting direct connection...")
            vnc_url = f"vnc://{hostname}"
            subprocess.run(["open", "-a", "Screen Sharing", vnc_url])
            return

        # VNC requires a background listener, so 'access tcp' is correct here.
        cf_cmd = [str(cloudflared_path), "access", "tcp", "--hostname", hostname, "--url", f"{target_ip}:{local_port}"]

        console.print(f"Starting tunnel: [bold cyan]{target_ip}:{local_port}[/bold cyan] -> [bold cyan]{hostname}[/bold cyan]")
        proxy_proc = subprocess.Popen(cf_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        try:
            time.sleep(2.0)
            if proxy_proc.poll() is not None:
                _, stderr = proxy_proc.communicate()
                console.print(f"[bold red]Error:[/bold red] Cloudflared failed to start:\n{stderr}")
                sys.exit(1)

            vnc_url = f"vnc://{target_ip}:{local_port}"
            console.print(f"Opening Screen Sharing: [bold blue]{vnc_url}[/bold blue]")
            subprocess.run(["open", "-a", "Screen Sharing", vnc_url])

            console.print("\n" + "="*60)
            console.print(f"CONNECTED TO: [bold green]{hostname}[/bold green]")
            console.print("Keep this terminal open. Press [bold]Ctrl+C[/bold] to exit.")
            console.print("="*60)
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            console.print("\nClosing tunnel...")
        finally:
            proxy_proc.terminate()
            console.print("Done.")
    else:
        # Standard VNC
        vnc_url = f"vnc://{hostname}"
        console.print(f"Opening standard Screen Sharing: [bold blue]{vnc_url}[/bold blue]")
        subprocess.run(["open", "-a", "Screen Sharing", vnc_url])

if __name__ == "__main__":
    main()
