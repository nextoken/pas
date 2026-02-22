#!/usr/bin/env python3
"""
@pas-executable
Interactively select a Python library in the workspace and guide through building, installing, or publishing it.
"""

import sys
import os
import shutil
import argparse
import json
import urllib.request
from pathlib import Path
from typing import List, Optional, Dict, Any

# Try to import tomllib (Python 3.11+) or fallback
try:
    import tomllib
except ImportError:
    tomllib = None

# For robust version comparison
try:
    from packaging.version import parse as parse_version
except ImportError:
    parse_version = None

# --- Configuration URLs ---
PYPI_DASHBOARD_URL = "https://pypi.org/manage/projects/"
TEST_PYPI_DASHBOARD_URL = "https://test.pypi.org/manage/projects/"
PYPI_TOKEN_URL = "https://pypi.org/manage/account/token/"
TEST_PYPI_TOKEN_URL = "https://test.pypi.org/manage/account/token/"
PACKAGING_GUIDE_URL = "https://packaging.python.org/en/latest/tutorials/packaging-projects/"
TWINE_DOCS_URL = "https://twine.readthedocs.io/en/latest/"
# --------------------------

# --- Service Configuration ---
# Folders to ignore when searching for libraries
IGNORE_DIRS = {".git", "node_modules", "venv", ".venv", "build", "dist", "__pycache__"}
# --------------------------

# Ensure PAS helpers and libraries are accessible if running within the toolkit
pas_root = Path(__file__).resolve().parent.parent
if str(pas_root) not in sys.path:
    sys.path.append(str(pas_root))

# Inject PPUI library path for zero-setup execution
ppui_path = pas_root / "libs" / "ppui" / "src"
if ppui_path.exists() and str(ppui_path) not in sys.path:
    sys.path.append(str(ppui_path))

try:
    from ppui import (
        console,
        prompt_yes_no,
        Menu
    )
    from rich.panel import Panel
    from helpers.core import (
        run_command,
        load_pas_config,
        save_pas_config
    )
except ImportError as e:
    # Minimal fallback if used outside PAS toolkit and helpers are missing
    print(f"Error: This script requires PAS Toolkit helpers and PPUI library. ({e})")
    print("Please run it from within the PAS environment or run 'make setup'.")
    sys.exit(1)

def get_libraries(root_dir: Path) -> List[Path]:
    """Find all directories containing a pyproject.toml file."""
    libs = []
    # Search up to 3 levels deep recursively
    try:
        for path in root_dir.rglob("pyproject.toml"):
            # Skip ignored directories
            if any(ignored in path.parts for ignored in IGNORE_DIRS):
                continue
            libs.append(path.parent)
    except Exception as e:
        console.print(f"[red]Error searching for libraries: {e}[/red]")
    return sorted(list(set(libs)))

def show_summary():
    """Display the tool summary."""
    summary = """
[bold]Python Library Publisher Assistant[/bold]

A generic tool to manage, build, and publish Python libraries.
It helps automate the tedious parts of the packaging workflow.

[bold]Capabilities:[/bold]
1. [cyan]Discovery[/cyan]: Finds Python packages (with pyproject.toml) in a directory.
2. [cyan]Build[/cyan]: Packages your library into source and wheel distributions.
3. [cyan]Install[/cyan]: Installs the library locally in editable mode for testing.
4. [cyan]Publish[/cyan]: Guides you through uploading to TestPyPI or PyPI using twine.

[bold]Requirements:[/bold]
- [yellow]build[/yellow] (`pip install build`)
- [yellow]twine[/yellow] (`pip install twine`)
    """
    console.print(Panel(summary, title="Python Publisher", expand=False))

def build_library(lib_path: Path):
    """Build the library using the 'build' module."""
    # Check if build is installed
    res = run_command(["python3", "-m", "build", "--version"])
    if res.returncode != 0:
        console.print("[yellow]The 'build' package is required but not found.[/yellow]")
        if prompt_yes_no("Would you like to install the 'build' package now?", default=True):
            console.print("Installing 'build'...")
            install_res = run_command(["python3", "-m", "pip", "install", "build"], capture_output=False)
            if install_res.returncode != 0:
                console.print("[red]Failed to install 'build'. Please install it manually with: pip install build[/red]")
                return False
        else:
            return False

    console.print(f"\n[bold blue]Building library at {lib_path}...[/bold blue]")
    
    # Remove old build/dist folders to be clean
    dist_path = lib_path / "dist"
    if dist_path.exists():
        shutil.rmtree(dist_path)
    
    res = run_command(["python3", "-m", "build"], cwd=lib_path, capture_output=False)
    if res.returncode == 0:
        console.print(f"[green]Successfully built distributions in {dist_path}[/green]")
        return True
    else:
        console.print("[red]Build failed.[/red]")
        return False

def install_local(lib_path: Path):
    """Install the library locally in editable mode."""
    console.print(f"\n[bold blue]Installing library locally (editable)...[/bold blue]")
    res = run_command(["python3", "-m", "pip", "install", "-e", "."], cwd=lib_path, capture_output=False)
    if res.returncode == 0:
        console.print("[green]Successfully installed library locally.[/green]")
    else:
        console.print("[red]Local installation failed.[/red]")

def publish_library(lib_path: Path, repository: str = "pypi"):
    """Guide the user through publishing the library."""
    dist_path = lib_path / "dist"
    if not dist_path.exists() or not list(dist_path.glob("*")):
        console.print("[yellow]No distribution files found in dist/.[/yellow]")
        if prompt_yes_no("Build them now?", default=True):
            if not build_library(lib_path):
                return
        else:
            return

    repo_url = TEST_PYPI_DASHBOARD_URL if repository == "testpypi" else PYPI_DASHBOARD_URL
    token_url = TEST_PYPI_TOKEN_URL if repository == "testpypi" else PYPI_TOKEN_URL
    repo_name = "TestPyPI" if repository == "testpypi" else "PyPI"
    config_key = "testpypi_token" if repository == "testpypi" else "pypi_token"

    console.print(f"\n[bold cyan]Publishing to {repo_name}[/bold cyan]")

    # Check if dist files match current version
    metadata = get_package_metadata(lib_path)
    current_version = metadata.get("version")
    if current_version and dist_path.exists():
        dist_files = list(dist_path.glob("*"))
        version_match = any(current_version in f.name for f in dist_files)
        
        if not version_match and dist_files:
            console.print(f"[yellow]Warning: Files in dist/ do not seem to match current version {current_version}.[/yellow]")
            if prompt_yes_no("Would you like to REBUILD fresh distributions before uploading?", default=True):
                if not build_library(lib_path):
                    return
        elif version_match:
            if prompt_yes_no(f"Distributions for version {current_version} found. Rebuild anyway for a clean upload?", default=False):
                if not build_library(lib_path):
                    return
    
    # Load existing token if available
    config = load_pas_config("py-publish")
    token = config.get(config_key)

    if token:
        console.print(f"[green]Using saved API token for {repo_name} (from macOS Keychain).[/green]")
    else:
        console.print(f"Manage projects: [link={repo_url}]{repo_url}[/link]")
        console.print(f"Get API token:   [link={token_url}]{token_url}[/link]")
        
        token = input(f"\nEnter your {repo_name} API token (starts with pypi-): ").strip()
        if not token:
            console.print("[red]Token required for publishing.[/red]")
            return
            
        if prompt_yes_no("Save this token securely in macOS Keychain for future use?", default=True):
            config[config_key] = token
            save_pas_config("py-publish", config)
            console.print("[green]Token saved securely.[/green]")

    if not prompt_yes_no(f"Ready to upload to {repo_name}?", default=True):
        return

    # Check if twine is installed
    res = run_command(["twine", "--version"])
    if res.returncode != 0:
        console.print("[yellow]'twine' package not found.[/yellow]")
        if prompt_yes_no("Would you like to install the 'twine' package now?", default=True):
            console.print("Installing 'twine'...")
            install_res = run_command(["python3", "-m", "pip", "install", "twine"], capture_output=False)
            if install_res.returncode != 0:
                console.print("[red]Failed to install 'twine'. Please install it manually with: pip install twine[/red]")
                return
        else:
            return

    # Prepare environment with credentials
    env = os.environ.copy()
    env["TWINE_USERNAME"] = "__token__"
    env["TWINE_PASSWORD"] = token

    cmd = ["twine", "upload"]
    if repository == "testpypi":
        cmd.extend(["--repository", "testpypi"])
    cmd.append("dist/*")

    console.print(f"[yellow]Executing: {' '.join(cmd)}[/yellow]")
    # Run twine upload using our provided token
    result = subprocess.run(cmd, cwd=lib_path, env=env)

    if result.returncode == 0:
        # Get metadata to show the final URL
        metadata = get_package_metadata(lib_path)
        name = metadata.get("name")
        if name:
            project_url = f"https://pypi.org/project/{name}/" if repository == "pypi" else f"https://test.pypi.org/project/{name}/"
            console.print(f"\n[bold green]Success![/bold green] Library published to {repo_name}.")
            console.print(f"View your project at: [link={project_url}]{project_url}[/link]")
        else:
            console.print(f"\n[bold green]Success![/bold green] Library published to {repo_name}.")
    else:
        console.print(f"\n[bold red]Error:[/bold red] Upload to {repo_name} failed.")

def show_guide():
    """Display a guide for publishing."""
    guide = f"""
[bold]Publishing Guide[/bold]

To publish a Python library for public access:

1. [bold]Prepare pyproject.toml[/bold]:
   Ensure you have metadata like `name`, `version`, `authors`, and `dependencies` correctly set.

2. [bold]Build[/bold]:
   Run `python3 -m build` to create `.tar.gz` and `.whl` files in the `dist/` directory.

3. [bold]Test Upload[/bold]:
   Upload to TestPyPI first: `twine upload --repository testpypi dist/*`
   Check it at: [link={TEST_PYPI_DASHBOARD_URL}]{TEST_PYPI_DASHBOARD_URL}[/link]

4. [bold]Official Upload[/bold]:
   Upload to PyPI: `twine upload dist/*`
   Check it at: [link={PYPI_DASHBOARD_URL}]{PYPI_DASHBOARD_URL}[/link]

5. [bold]Security[/bold]:
   Use API tokens instead of passwords. Store them in `~/.pypirc`.

For more details, see:
- [link={PACKAGING_GUIDE_URL}]{PACKAGING_GUIDE_URL}[/link]
- [link={TWINE_DOCS_URL}]{TWINE_DOCS_URL}[/link]
    """
    console.print(Panel(guide, title="PAS Publishing Guide", expand=False))

def get_package_metadata(lib_path: Path) -> Dict[str, str]:
    """Extract name and version from pyproject.toml."""
    pyproject_path = lib_path / "pyproject.toml"
    if not pyproject_path.exists():
        return {}
    
    try:
        if tomllib:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
                project = data.get("project", {})
                return {
                    "name": project.get("name"),
                    "version": project.get("version")
                }
        else:
            # Simple regex fallback if tomllib is missing
            import re
            content = pyproject_path.read_text()
            name_match = re.search(r'^name\s*=\s*"(.*?)"', content, re.MULTILINE)
            version_match = re.search(r'^version\s*=\s*"(.*?)"', content, re.MULTILINE)
            return {
                "name": name_match.group(1) if name_match else None,
                "version": version_match.group(1) if version_match else None
            }
    except Exception as e:
        console.print(f"[red]Error parsing pyproject.toml: {e}[/red]")
    return {}

def check_pypi_version(package_name: str) -> Optional[str]:
    """Fetch the latest version of the package from PyPI."""
    if not package_name:
        return None
    url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode())
            return data.get("info", {}).get("version")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "not_found"
    except Exception:
        pass
    return None

def manage_library(lib_path: Path):
    """Sub-menu for managing a specific library."""
    # Get local metadata
    metadata = get_package_metadata(lib_path)
    name = metadata.get("name", lib_path.name)
    local_version = metadata.get("version", "unknown")

    def _show_status():
        # Check PyPI status
        console.print(f"\n[bold cyan]Managing: {name}[/bold cyan] ({lib_path})")
        console.print(f"Local Version:  [bold]{local_version}[/bold]")
        
        remote_version = check_pypi_version(name)
        if remote_version == "not_found":
            console.print("PyPI Status:    [yellow]Not yet published[/yellow]")
        elif remote_version:
            # Normalize and compare versions
            is_match = False
            if parse_version:
                try:
                    is_match = parse_version(local_version) == parse_version(remote_version)
                except Exception:
                    is_match = local_version == remote_version
            else:
                # Fallback to string comparison
                is_match = local_version == remote_version

            if is_match:
                console.print(f"PyPI Status:    [green]Up to date (v{remote_version})[/green]")
            else:
                console.print(f"PyPI Status:    [bold yellow]Update available (v{remote_version} on PyPI)[/bold yellow]")
        else:
            console.print("PyPI Status:    [dim]Could not check (offline?)[/dim]")

    # We use a custom run loop because we want to refresh status before each menu display
    while True:
        _show_status()
        
        menu = Menu(f"Actions for {name}", style="bold cyan")
        
        # Dogfooding: Using an inline submenu for publishing targets
        pub_menu = Menu("Publishing Targets")
        pub_menu.add_option("Publish to PyPI", lambda: publish_library(lib_path, repository="pypi"))
        pub_menu.add_option("Publish to TestPyPI", lambda: publish_library(lib_path, repository="testpypi"))
        menu.add_submenu("Publishing Options...", pub_menu, behavior="inline")

        menu.add_item("Build Library", lambda: build_library(lib_path))
        menu.add_item("Install Locally (Editable)", lambda: install_local(lib_path))
        menu.add_item("View Publishing Guide", show_guide)
        menu.add_back_item()
        menu.add_quit_item()
        
        # run(loop=False) because we handle the outer loop to refresh _show_status
        selection = menu.run(loop=False)
        if selection in ["back", "quit"]:
            if selection == "quit":
                sys.exit(0)
            break

import subprocess

def main():
    parser = argparse.ArgumentParser(description="Build and publish Python libraries.")
    parser.add_argument("path", nargs="?", default=".", help="Directory to search for libraries (default: current directory)")
    args = parser.parse_args()

    show_summary()
    
    search_root = Path(args.path).resolve()
    libraries = get_libraries(search_root)
    
    while True:
        menu = Menu(f"Search Root: {search_root}", style="bold cyan")
        
        for lib in libraries:
            try:
                rel_path = lib.relative_to(search_root)
                display_path = "." if str(rel_path) == "." else str(rel_path)
            except ValueError:
                display_path = str(lib)
            
            # Use a closure to capture the correct 'lib' for the callback
            menu.add_item(f"{lib.name} ({display_path})", lambda l=lib: manage_library(l))
        
        menu.add_item("[Enter Path Manually]", "manual")
        menu.add_quit_item()
        
        selected = menu.run(loop=False)
        
        if selected == "quit":
            break
        
        if selected == "manual":
            path_str = input("Enter the path to the Python library (containing pyproject.toml): ").strip()
            if not path_str:
                continue
            manual_path = Path(path_str).expanduser().resolve()
            if not (manual_path / "pyproject.toml").exists():
                console.print(f"[red]Error: {manual_path}/pyproject.toml not found.[/red]")
                continue
            manage_library(manual_path)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user.[/yellow]")
        sys.exit(0)
