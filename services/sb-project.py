#!/usr/bin/env python3
"""
@pas-executable
Manage local Supabase project configuration and connectivity.
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
import shutil
import subprocess
import json
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.console import Console
from rich.panel import Panel

console = Console()

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helpers.core import (
    prompt_yes_no, 
    detect_supabase_binary,
    prompt_toolkit_menu,
    format_menu_choices
)
from helpers.supabase import (
    get_projects,
    get_api_keys,
    get_project_pooling_config,
    test_connection,
    test_api,
    detect_pooler_prefix
)

def find_project_root(start_path: Path) -> Optional[Path]:
    """Search upwards for the Supabase project root (containing supabase/config.toml)."""
    current = start_path.resolve()
    for parent in [current] + list(current.parents):
        if (parent / "supabase" / "config.toml").exists():
            return parent
    return None

def is_logged_in(supabase_bin: Path) -> bool:
    """Check if the user is logged into Supabase."""
    if os.environ.get("SUPABASE_ACCESS_TOKEN"):
        return True
        
    try:
        cmd = [str(supabase_bin), "projects", "list"]
        if os.path.isdir(".env"):
            if os.path.isfile(".env.local"):
                cmd.extend(["--env-file", ".env.local"])
            else:
                cmd.extend(["--env-file", "/dev/null"])
                
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False

def login(supabase_bin: Path) -> bool:
    """Prompt the user to login."""
    print("\nSupabase login required.")
    if prompt_yes_no("Would you like to run 'supabase login' now?"):
        try:
            subprocess.run([str(supabase_bin), "login"], check=True)
            return True
        except subprocess.CalledProcessError:
            print("Login failed.")
            return False
    return False

def get_current_project_ref(project_root: Optional[Path]) -> Optional[str]:
    """Get the currently linked project-ref from the project root."""
    if not project_root:
        return None
    paths = [
        project_root / "supabase" / ".temp" / "project-ref",
        project_root / ".supabase" / "project-ref",
    ]
    for p in paths:
        if p.exists():
            return p.read_text().strip()
    return None

def update_gitignore(project_root: Path):
    """Ensure .env.local and its backups are in .gitignore."""
    gitignore_path = project_root / ".gitignore"
    patterns = [".env.local", ".env.local.*"]
    
    if not gitignore_path.exists():
        gitignore_path.write_text("\n".join(patterns) + "\n")
        print(f"Created {gitignore_path.name} and added env patterns.")
        return

    content = gitignore_path.read_text()
    lines = content.splitlines()
    added = []
    for p in patterns:
        if p not in lines:
            lines.append(p)
            added.append(p)
    
    if added:
        gitignore_path.write_text("\n".join(lines) + "\n")
        print(f"Updated {gitignore_path.name} to ignore: {', '.join(added)}")

def update_env_file(supabase_bin: Path, project_root: Path, project_ref: str, region: Optional[str] = None):
    """Test all 3 modes and update DB_* variables in .env.local."""
    env_path = project_root / ".env.local"
    update_gitignore(project_root)

    example_candidates = [project_root / ".env.local.example", project_root / "env.local.example"]
    example_path = next((c for c in example_candidates if c.exists()), None)

    if not env_path.exists():
        if example_path:
            if prompt_yes_no(f"\nFound template {example_path.name}. Create {env_path.name} from it?"):
                shutil.copy(example_path, env_path)
        else:
            if prompt_yes_no(f"\nNo template found. Create a new {env_path.name}?"):
                env_path.write_text("# Supabase Configuration\nDB_POSTGRESDB_HOST=\nDB_POSTGRESDB_PORT=\nDB_POSTGRESDB_DATABASE=\nDB_POSTGRESDB_USER=\nDB_POSTGRESDB_PASSWORD=\nDB_POSTGRESDB_MODE=\nNEXT_PUBLIC_SUPABASE_URL=\nNEXT_PUBLIC_SUPABASE_ANON_KEY=\n")

    db_password = None
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "DB_POSTGRESDB_PASSWORD=" in line or "DB_PASSWORD=" in line:
                parts = line.split("=", 1)
                if len(parts) > 1:
                    db_password = parts[1].strip().strip('"').strip("'")
                    if db_password: break

    if not db_password:
        print(f"\nNo password found. Reset it here: https://supabase.com/dashboard/project/{project_ref}/settings/database")
        if prompt_yes_no("Enter DB password now?"):
            db_password = input("Password: ").strip()

    token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    api_keys = get_api_keys(project_ref, token) if token else {}
    anon_key = api_keys.get("anon")
    api_success = False
    if anon_key:
        api_success = test_api(project_ref, anon_key)
    else:
        print("\n⚠️  Could not fetch API keys. Skipping API test.")

    reg = region if region else "us-east-1"
    
    # Try to get pooler host from API first
    pooler_host = None
    if token:
        print(f"Fetching database pooling config from API...")
        pool_config = get_project_pooling_config(project_ref, token)
        if pool_config and isinstance(pool_config, dict):
            conn_string = pool_config.get("connection_string")
            if conn_string:
                try:
                    pooler_host = conn_string.split("@")[-1].split("/")[0].split(":")[0]
                    print(f"  ✅ Retrieved pooler host from API: {pooler_host}")
                except:
                    pass

    if not pooler_host:
        prefix = detect_pooler_prefix(reg, project_ref, db_password) if db_password else "aws-0"
        pooler_host = f"{prefix}-{reg}.pooler.supabase.com"
    
    modes = [
        {"name": "Direct (IPv6)", "host": f"db.{project_ref}.supabase.co", "port": "5432", "user": "postgres"},
        {"name": "Pooler (Transaction)", "host": pooler_host, "port": "6543", "user": f"postgres.{project_ref}"},
        {"name": "Pooler (Session)", "host": pooler_host, "port": "5432", "user": f"postgres.{project_ref}"}
    ]

    print("\n" + "="*60 + "\nRUNNING DATABASE TESTS\n" + "="*60)
    results = []
    for mode in modes:
        success = test_connection(mode["host"], mode["port"], mode["user"], "postgres", db_password)
        results.append({**mode, "success": success})

    print("\n" + "#"*60 + "\nSUPABASE CONNECTION SUMMARY\n" + "#"*60)
    print(f"{'MODE':<20} | {'HOST':<45} | {'PORT':<5} | {'RESULT'}")
    print("-" * 85)
    for r in results:
        status = "✅ PASS" if r["success"] else "❌ FAIL"
        print(f"{r['name']:<20} | {r['host']:<45} | {r['port']:<5} | {status}")
    
    api_status = "✅ PASS" if api_success else "❌ FAIL"
    print(f"{'REST API':<20} | {f'https://{project_ref}.supabase.co':<45} | {'443':<5} | {api_status}")
    print("#"*60)

    print("\n" + "-"*60)
    print("Which mode would you like to save to .env.local? (Default: 3. Pooler Session)")
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['name']} (Port: {r['port']})")
    print("0. Skip update")
    
    choice = input("\nSelection (0-3, default: 3): ").strip()
    if choice == "": choice = "3"
    
    if choice in ["1", "2", "3"]:
        selected = results[int(choice)-1]
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        backup = env_path.parent / f"{env_path.name}.{timestamp}"
        shutil.copy(env_path, backup)
        
        updates = {
            "DB_POSTGRESDB_HOST": selected["host"],
            "DB_POSTGRESDB_PORT": selected["port"],
            "DB_POSTGRESDB_DATABASE": "postgres",
            "DB_POSTGRESDB_USER": selected["user"],
            "DB_POSTGRESDB_MODE": selected["name"].lower().replace(" (", "-").replace(")", "").replace(" ", "-"),
            "DB_HOST": selected["host"],
            "DB_PORT": selected["port"],
            "DB_NAME": "postgres",
            "DB_USER": selected["user"],
            "NEXT_PUBLIC_SUPABASE_URL": f"https://{project_ref}.supabase.co",
        }
        if db_password:
            updates["DB_POSTGRESDB_PASSWORD"] = db_password
            updates["DB_PASSWORD"] = db_password
        if anon_key:
            updates["NEXT_PUBLIC_SUPABASE_ANON_KEY"] = anon_key

        lines = env_path.read_text().splitlines()
        new_lines = []
        for line in lines:
            matched = False
            for k, v in updates.items():
                if line.strip().startswith(f"{k}=") or line.strip().startswith(f"export {k}="):
                    prefix_str = "export " if "export " in line else ""
                    new_lines.append(f"{prefix_str}{k}={v}")
                    matched = True
                    break
            if not matched: new_lines.append(line)
        
        env_path.write_text("\n".join(new_lines) + "\n")
        print(f"\n✅ {env_path.name} updated.")

def list_and_link_projects_with_data(supabase_bin: Path, projects: List[Dict[str, Any]], project_root: Path, current_ref: Optional[str]):
    """List Supabase projects and offer to link one."""
    import questionary
    if not projects:
        token = os.environ.get("SUPABASE_ACCESS_TOKEN")
        if token: projects = get_projects(token)
    
    choices = []
    if projects:
        choices = format_menu_choices(projects, title_field="name", value_field="id")
    
    choices.append(questionary.Choice(title="q. Skip / Keep Current", value="SKIP"))

    console.print("\n[bold]Select Supabase Project to Link:[/bold] (Use arrows or press hotkey)")
    choice = prompt_toolkit_menu(choices)
    
    def get_region(ref: str):
        for p in projects: 
            if p.get("id") == ref: return p.get("region")
        return None

    if not choice or choice == "SKIP":
        if current_ref: update_env_file(supabase_bin, project_root, current_ref, get_region(current_ref))
        return

    ref = choice
    print(f"Selected project: {ref}")
    
    try:
        cmd = [str(supabase_bin), "link", "--project-ref", ref]
        subprocess.run(cmd, check=True)
        update_env_file(supabase_bin, project_root, ref, get_region(ref))
    except subprocess.CalledProcessError:
        print("Failed to link project.")

def main():
    parser = argparse.ArgumentParser(description=__doc__.replace("@pas-executable", "").strip())
    parser.parse_args()

    info_text = """
[bold]Supabase Project Connectivity[/bold]

This tool manages local project links and tests database connectivity:
- [cyan]Link Project[/cyan]: Connects your local folder to a Supabase project ref.
- [cyan]Connection Test[/cyan]: Verifies Direct, Pooler (Transaction), and Pooler (Session) modes.
- [cyan]API Test[/cyan]: Confirms REST API and anon key functionality.
- [cyan]Environment Sync[/cyan]: Automatically updates .env.local with tested credentials.
"""
    console.print(Panel(info_text.strip(), title="sb-project", border_style="blue"))
    console.print("\n")

    bin = detect_supabase_binary()
    if not bin:
        print("Error: 'supabase' CLI not found.")
        return

    root = find_project_root(Path.cwd())
    if not root:
        if prompt_yes_no("\nNo project detected. Init here?"):
            subprocess.run([str(bin), "init"], check=True)
            root = Path.cwd()
        else: return

    ref = get_current_project_ref(root)
    token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    projs = get_projects(token) if token else []

    if ref:
        name = next((p.get("name") for p in projs if p.get("id") == ref), None)
        print(f"Currently linked: {ref}{f' ({name})' if name else ''}")

    os.chdir(root)
    if not projs and not is_logged_in(bin):
        if not login(bin): return
        token = os.environ.get("SUPABASE_ACCESS_TOKEN")
        projs = get_projects(token) if token else []
        
    list_and_link_projects_with_data(bin, projs, root, ref)

if __name__ == "__main__":
    main()
