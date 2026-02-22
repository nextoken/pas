#!/usr/bin/env python3
"""
@pas-executable
Smart SCP wrapper that automatically detects if a Cloudflare Tunnel is needed.
Usage: xscp [scp_args...] source target
"""

import sys
import subprocess
from pathlib import Path

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from helpers.core import (
    console,
    detect_cloudflared_binary,
    is_cloudflare_host,
    load_pas_config,
)
from rich.panel import Panel

# --- Configuration ---
CONNECT_TIMEOUT = 15
SSHS_CONFIG_SERVICE = "sshs"
# ---------------------

def show_summary():
    """Display a brief summary of the tool's capabilities."""
    summary = (
        "[bold cyan]xscp[/bold cyan] (Extended SCP) is a smart wrapper around [bold]scp[/bold].\n\n"
        "[bold]Capabilities:[/bold]\n"
        "• [bold]Smart Detection:[/bold] Automatically checks if source or target is a Cloudflare Tunnel.\n"
        "• [bold]Zero-Config:[/bold] Uses `cloudflared access ssh` ProxyCommand only when needed.\n"
        "• [bold]Fallback:[/bold] Transparently falls back to standard `scp` for non-tunnel hosts.\n"
        "• [bold]Transparency:[/bold] Passes all additional arguments directly to the underlying `scp` command."
    )
    console.print(Panel(summary, title="Smart SCP Wrapper", expand=False))

def main():
    if len(sys.argv) < 3:
        show_summary()
        console.print("\n[bold yellow]Usage:[/bold yellow] xscp [scp_args...] source target [--no-profile]")
        sys.exit(1)

    args = list(sys.argv[1:])

    # Handle --no-profile
    no_profile = "--no-profile" in args
    if no_profile:
        args.remove("--no-profile")

    # Extract -i from args before building cmd (SCP requires -i before source/target)
    identity_opts = []
    if "-i" in args:
        idx = args.index("-i")
        if idx + 1 < len(args):
            identity_opts = ["-i", args[idx + 1]]
            args = [a for i, a in enumerate(args) if i != idx and i != idx + 1]

    # Identify potential remote hosts in source and target (last two args)
    potential_remotes = []
    for arg in args[-2:]:
        if ":" in arg:
            host_part = arg.split(":")[0]
            potential_remotes.append(host_part)

    # Load profile identity if not provided on CLI
    if not no_profile and not identity_opts and potential_remotes:
        config = load_pas_config(SSHS_CONFIG_SERVICE)
        profiles = config.get("profiles", {})
        for remote in potential_remotes:
            if remote in profiles:
                saved_key = profiles[remote].get("identity_file")
                if saved_key and Path(saved_key).expanduser().exists():
                    identity_opts = ["-i", saved_key]
                    break
            else:
                # Fallback: match host part (e.g. profile "user@host" when remote is "host")
                for profile_name, data in profiles.items():
                    if "@" in profile_name:
                        _, p_host = profile_name.split("@", 1)
                        if p_host == remote:
                            saved_key = data.get("identity_file")
                            if saved_key and Path(saved_key).expanduser().exists():
                                identity_opts = ["-i", saved_key]
                                break
                if identity_opts:
                    break

    # Smart detection
    use_cf = any(is_cloudflare_host(host) for host in potential_remotes)

    if use_cf:
        # Find local cloudflared binary
        cloudflared_path = detect_cloudflared_binary()
        if not cloudflared_path:
            console.print("[bold red]Error:[/bold red] 'cloudflared' binary not found locally, but target appears to be a tunnel.")
            console.print("Falling back to standard SCP...")
            cmd = ["scp"]
        else:
            proxy_cmd = f"{cloudflared_path} access ssh --hostname %h"
            cmd = [
                "scp",
                "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
                "-o", f"ProxyCommand={proxy_cmd}",
            ]
    else:
        cmd = ["scp"]

    # Identity options MUST come before source/target
    if identity_opts:
        cmd.extend(identity_opts)
    cmd.extend(args)

    try:
        # Use subprocess.run to hand over control to the SCP process
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
