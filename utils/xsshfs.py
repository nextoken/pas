#!/usr/bin/env python3
"""
@pas-executable
Smart SSHFS wrapper that automatically detects if a Cloudflare Tunnel is needed.
Usage: xsshfs [user@]hostname:/path mountpoint [sshfs_args...]
"""

import sys
import subprocess
from pathlib import Path

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import console, detect_cloudflared_binary, is_cloudflare_host
from rich.panel import Panel

# --- Configuration ---
CONNECT_TIMEOUT = 15
# ---------------------

def show_summary():
    """Display a brief summary of the tool's capabilities."""
    summary = (
        "[bold cyan]xsshfs[/bold cyan] (Extended SSHFS) is a smart wrapper around [bold]sshfs[/bold].\n\n"
        "[bold]Capabilities:[/bold]\n"
        "• [bold]Smart Detection:[/bold] Automatically checks if the host is a Cloudflare Tunnel.\n"
        "• [bold]Zero-Config:[/bold] Uses `cloudflared access ssh` ProxyCommand only when needed.\n"
        "• [bold]Fallback:[/bold] Transparently falls back to standard `sshfs` for non-tunnel hosts.\n"
        "• [bold]Transparency:[/bold] Passes all additional arguments directly to the underlying `sshfs` command."
    )
    console.print(Panel(summary, title="Smart SSHFS Wrapper", expand=False))

def main():
    if len(sys.argv) < 3:
        show_summary()
        console.print("\n[bold yellow]Usage:[/bold yellow] xsshfs [user@]hostname:/path mountpoint [sshfs_args...]")
        sys.exit(1)

    args = sys.argv[1:]
    target_spec = args[0]
    
    # Identify potential remote host
    use_cf = False
    if ":" in target_spec:
        host_part = target_spec.split(":")[0]
        use_cf = is_cloudflare_host(host_part)

    if use_cf:
        # Find local cloudflared binary
        cloudflared_path = detect_cloudflared_binary()
        if not cloudflared_path:
            console.print("[bold red]Error:[/bold red] 'cloudflared' binary not found locally, but target appears to be a tunnel.")
            console.print("Falling back to standard SSHFS...")
            cmd = ["sshfs"] + args
        else:
            proxy_cmd = f"{cloudflared_path} access ssh --hostname %h"
            cmd = [
                "sshfs",
                "-o", f"ProxyCommand={proxy_cmd}",
                "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
            ] + args
    else:
        # Standard SSHFS
        cmd = ["sshfs"] + args

    try:
        # Use subprocess.run to hand over control to the sshfs process
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
