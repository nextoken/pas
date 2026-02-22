#!/usr/bin/env python3
"""
@pas-executable
Transparent wrapper for 'supabase' CLI that injects the PAS-active account token.
"""

import os
import sys
import json
import subprocess
from pathlib import Path

from rich.console import Console

console = Console()

def main():
    config_path = Path.home() / ".pas" / "supabase.json"
    token = None

    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            active_id = config.get("active_org_id")
            if active_id:
                token = config.get("organizations", {}).get(active_id, {}).get("access_token")
                org_name = config.get("organizations", {}).get(active_id, {}).get("name")
                console.print(f"[bold blue]Supabase CLI Wrapper[/bold blue] (active org: [cyan]{org_name}[/cyan])")
        except Exception:
            pass

    # Find the real supabase binary
    supabase_bin = None
    candidates = ["/opt/homebrew/bin/supabase", "/usr/local/bin/supabase"]
    for c in candidates:
        if os.path.exists(c):
            supabase_bin = c
            break
    
    if not supabase_bin:
        # Fallback to which
        try:
            supabase_bin = subprocess.check_output(["which", "supabase"], text=True).strip()
        except:
            print("Error: 'supabase' CLI not found in PATH.")
            sys.exit(1)

    # Prepare environment
    env = os.environ.copy()
    if token:
        env["SUPABASE_ACCESS_TOKEN"] = token
    
    # Prepare arguments
    args = sys.argv[1:]
    command = args[0] if args else ""
    
    # Check for a common Supabase CLI issue: a directory named '.env'
    # which causes the CLI to crash. 
    # CRITICAL: only add --env-file for commands that support it.
    # 'login' and 'logout' do NOT support it.
    supports_env_file = command not in {"login", "logout", "help", "--help", "-h"}
    
    if os.path.isdir(".env") and "--env-file" not in args:
        if supports_env_file:
            if os.path.isfile(".env.local"):
                args = ["--env-file", ".env.local"] + args
            else:
                args = ["--env-file", "/dev/null"] + args
        else:
            # For commands that don't support --env-file, we move to home dir
            # if we are in a directory with a .env folder.
            os.chdir(Path.home())

    # Execute the real supabase command with all arguments
    try:
        # We use execvpe to replace the current process completely
        os.execvpe(supabase_bin, [supabase_bin] + args, env)
    except Exception as e:
        print(f"Error executing supabase: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

