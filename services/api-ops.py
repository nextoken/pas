#!/usr/bin/env python3
"""
@pas-executable
Manage OpenAPI/Swagger API endpoints and their authentication.
"""

import sys
from pathlib import Path
# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
import sys
import os
import json
import requests
import argparse
import yaml
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Optional, Dict, Any, List

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.syntax import Syntax
from rich.tree import Tree

# Add the directory containing this script to sys.path to allow imports from nearby files
sys.path.append(str(Path(__file__).resolve().parent))

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helpers.core import (
    load_pas_config,
    save_pas_config,
    prompt_toolkit_menu,
    copy_to_clipboard,
    format_menu_choices
)

console = Console()

# Tool identity and descriptions (pas list, panel, -h)
TOOL_ID = "api-ops"
TOOL_TITLE = "API-Ops"
TOOL_SHORT_DESC = "Manage OpenAPI/Swagger API endpoints and authentication."
TOOL_DESCRIPTION = "API-Ops - OpenAPI/Swagger Service Manager"

def fetch_openapi(url: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Fetch and parse OpenAPI spec (JSON or YAML)."""
    headers = {"User-Agent": "PAS-Toolkit/1.0"}
    
    # Clean URL
    url = url.strip()
    
    def try_parse(content: str):
        try:
            return json.loads(content)
        except:
            try:
                return yaml.safe_load(content)
            except:
                return None

    # 1. Try the URL directly
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            spec = try_parse(r.text)
            if spec and isinstance(spec, dict) and (spec.get("openapi") or spec.get("swagger")):
                return spec, url
    except Exception as e:
        console.print(f"[yellow]Direct fetch failed: {e}[/yellow]")

    # 2. Try common locations if it looks like a docs page
    parsed = urlparse(url)
    base_path = parsed.path
    if not base_path.endswith("/"):
        base_path += "/"
    
    # Heuristics for common doc paths
    candidates = [
        urljoin(url, "openapi.json"),
        urljoin(url, "../openapi.json"),
        urljoin(url, "/openapi.json"),
        urljoin(url, "swagger.json"),
        urljoin(url, "/swagger.json"),
        urljoin(url, "api-docs"),
        urljoin(url, "api-json"),
        urljoin(url, "../api-json"),
        urljoin(url, "/api-json"),
        urljoin(url, "v1/openapi.json"),
    ]
    
    # Remove duplicates and the original URL
    candidates = list(dict.fromkeys([c for c in candidates if c != url]))
    
    for cand in candidates:
        try:
            console.print(f"Trying spec at: [dim]{cand}[/dim]")
            r = requests.get(cand, headers=headers, timeout=5)
            if r.status_code == 200:
                spec = try_parse(r.text)
                if spec and isinstance(spec, dict) and (spec.get("openapi") or spec.get("swagger")):
                    return spec, cand
        except:
            continue
            
    return None, None

def extract_base_url(docs_url: str, spec: Dict[str, Any]) -> str:
    """Determine the base URL for the API."""
    # 1. Check spec for 'servers' (OpenAPI 3)
    servers = spec.get("servers", [])
    if servers and isinstance(servers, list) and servers[0].get("url"):
        server_url = servers[0]["url"]
        if server_url.startswith("http"):
            return server_url.rstrip("/") + "/"
        else:
            # Relative URL in spec, join with docs_url
            return urljoin(docs_url, server_url).rstrip("/") + "/"
            
    # 2. Check 'host' and 'basePath' (Swagger 2)
    host = spec.get("host")
    base_path = spec.get("basePath", "/")
    if host:
        schemes = spec.get("schemes", ["https"])
        scheme = schemes[0] if schemes else "https"
        return f"{scheme}://{host}{base_path}".rstrip("/") + "/"

    # 3. Fallback: use docs_url minus the last segment (docs, docs/, etc.)
    parsed = urlparse(docs_url)
    path_parts = parsed.path.rstrip("/").split("/")
    if path_parts and path_parts[-1] in ["docs", "swagger", "redoc", "api-docs"]:
        new_path = "/".join(path_parts[:-1])
        return f"{parsed.scheme}://{parsed.netloc}{new_path}".rstrip("/") + "/"

    return docs_url.rstrip("/") + "/"

def add_new_api_from_url(docs_url: str, apis_config: Dict[str, Any]):
    """Internal helper to add an API directly from a URL (used by CLI args)."""
    spec = None
    spec_url = None

    with console.status(f"[bold green]Attempting to auto-fetch API spec for {docs_url}..."):
        spec, spec_url = fetch_openapi(docs_url)

    if not spec:
        console.print("[yellow]Could not automatically find OpenAPI spec. Please use the interactive menu to provide it manually.[/yellow]")
        return

    base_url = extract_base_url(docs_url, spec)
    
    # We still need to confirm the Base URL even in non-interactive mode for safety
    base_url = questionary.text("Verify API Base URL:", default=base_url).ask()
    if not base_url:
        return
    if not base_url.endswith("/"):
        base_url += "/"

    title = spec.get("info", {}).get("title", "Untitled API")
    version = spec.get("info", {}).get("version", "1.0.0")
    
    console.print(f"\n[bold green]Success![/bold green] Found: [bold]{title} (v{version})[/bold]")
    
    # Store in config
    # Robust check: look for entry without trailing slash too
    normalized_base = base_url.rstrip("/")
    existing_key = None
    if base_url in apis_config:
        existing_key = base_url
    elif normalized_base in apis_config:
        existing_key = normalized_base
    elif normalized_base + "/" in apis_config:
        existing_key = normalized_base + "/"

    if not existing_key:
        apis_config[base_url] = {
            "title": title,
            "version": version,
            "spec_url": spec_url,
            "docs_url": docs_url,
            "endpoints": {},
            "auth": {}
        }
    else:
        # Update existing entry
        apis_config[existing_key].update({
            "title": title,
            "version": version,
            "spec_url": spec_url,
            "docs_url": docs_url
        })
        # If the key name itself changed (e.g. added slash), rename it
        if existing_key != base_url:
            apis_config[base_url] = apis_config.pop(existing_key)
    
    # Parse endpoints
    paths = spec.get("paths", {})
    endpoint_count = 0
    apis_config[base_url]["endpoints"] = {}
    for path, methods in paths.items():
        if not isinstance(methods, dict): continue
        for method, details in methods.items():
            if method.lower() not in ["get", "post", "put", "delete", "patch", "options", "head"]:
                continue
            
            endpoint_count += 1
            op_id = details.get("operationId", f"{method}_{path.replace('/', '_')}")
            summary = details.get("summary", details.get("description", ""))
            
            apis_config[base_url]["endpoints"][f"{method.upper()} {path}"] = {
                "operationId": op_id,
                "summary": (summary or "")[:100] + ("..." if summary and len(summary) > 100 else "")
            }
            
    console.print(f"Indexed [bold]{endpoint_count}[/bold] endpoints.")
    save_pas_config("apis", apis_config)

def add_new_api(apis_config: Dict[str, Any]):
    """Prompt user for a URL and add to config."""
    docs_url = questionary.text("Enter OpenAPI/Swagger Doc URL (e.g. https://.../api/v1/docs/):").ask()
    if not docs_url:
        return

    spec = None
    spec_url = None

    with console.status("[bold green]Attempting to auto-fetch API spec..."):
        spec, spec_url = fetch_openapi(docs_url)

    if not spec:
        console.print("[yellow]Could not automatically find OpenAPI spec at that URL.[/yellow]")
        
        menu_items = [
            {"title": "Provide Direct URL to Spec (.json/.yaml)", "value": "direct_url"},
            {"title": "Upload Local Spec File", "value": "local_file"},
            {"title": "[Back]", "value": "cancel"}
        ]
        choices = format_menu_choices(menu_items, title_field="title", value_field="value")
        choice = prompt_toolkit_menu(choices)

        if choice == "direct_url":
            spec_url = questionary.text("Enter direct URL to spec:").ask()
            if spec_url:
                try:
                    with console.status("[bold green]Downloading spec..."):
                        r = requests.get(spec_url, timeout=10)
                        spec = yaml.safe_load(r.text) # Handles both JSON and YAML
                except Exception as e:
                    console.print(f"[bold red]Error downloading spec:[/bold red] {e}")
                    return
        elif choice == "local_file":
            path_str = questionary.text("Enter local path to spec file:").ask()
            if path_str:
                # Strip quotes and whitespace
                path_str = path_str.strip().strip("'").strip('"')
                path = Path(path_str).expanduser().resolve()
                if path.exists() and path.is_file():
                    try:
                        content = path.read_text()
                        spec = json.loads(content) if path.suffix == ".json" else yaml.safe_load(content)
                        
                        # Store copy in ~/.pas/specs/
                        specs_dir = Path.home() / ".pas" / "specs"
                        specs_dir.mkdir(parents=True, exist_ok=True)
                        stored_path = specs_dir / path.name
                        stored_path.write_text(content)
                        spec_url = str(stored_path)
                    except Exception as e:
                        console.print(f"[bold red]Error parsing local file:[/bold red] {e}")
                        return
                else:
                    # Detailed debug info
                    console.print(f"[bold red]File not found:[/bold red] {path}")
                    if not path.exists():
                        console.print(f"  [dim](Path does not exist according to OS)[/dim]")
                    elif not path.is_file():
                        console.print(f"  [dim](Path is not a file)[/dim]")
                    return
        else:
            return

    if not spec:
        console.print("[bold red]Error:[/bold red] No valid OpenAPI spec provided.")
        return

    base_url = extract_base_url(docs_url, spec)
    
    # Verify/Edit Base URL
    base_url = questionary.text("Verify API Base URL:", default=base_url).ask()
    if not base_url:
        return
    if not base_url.endswith("/"):
        base_url += "/"

    title = spec.get("info", {}).get("title", "Untitled API")
    version = spec.get("info", {}).get("version", "1.0.0")
    
    console.print(f"\n[bold green]Success![/bold green] Found: [bold]{title} (v{version})[/bold]")
    
    # Store in config
    # Robust check: look for entry without trailing slash too
    normalized_base = base_url.rstrip("/")
    existing_key = None
    if base_url in apis_config:
        existing_key = base_url
    elif normalized_base in apis_config:
        existing_key = normalized_base
    elif normalized_base + "/" in apis_config:
        existing_key = normalized_base + "/"

    if not existing_key:
        apis_config[base_url] = {
            "title": title,
            "version": version,
            "spec_url": spec_url,
            "docs_url": docs_url,
            "endpoints": {},
            "auth": {}
        }
    else:
        # Update existing entry
        apis_config[existing_key].update({
            "title": title,
            "version": version,
            "spec_url": spec_url,
            "docs_url": docs_url
        })
        # If the key name itself changed (e.g. added slash), rename it
        if existing_key != base_url:
            apis_config[base_url] = apis_config.pop(existing_key)
    
    # Parse endpoints
    paths = spec.get("paths", {})
    endpoint_count = 0
    apis_config[base_url]["endpoints"] = {}
    for path, methods in paths.items():
        if not isinstance(methods, dict): continue
        for method, details in methods.items():
            if method.lower() not in ["get", "post", "put", "delete", "patch", "options", "head"]:
                continue
            
            endpoint_count += 1
            op_id = details.get("operationId", f"{method}_{path.replace('/', '_')}")
            summary = details.get("summary", details.get("description", ""))
            
            apis_config[base_url]["endpoints"][f"{method.upper()} {path}"] = {
                "operationId": op_id,
                "summary": (summary or "")[:100] + ("..." if summary and len(summary) > 100 else "")
            }
            
    console.print(f"Indexed [bold]{endpoint_count}[/bold] endpoints.")
    
    if questionary.confirm("Would you like to set an API key/token for this API now?").ask():
        update_auth(base_url, apis_config)
    
    save_pas_config("apis", apis_config)

def update_auth(base_url: str, apis_config: Dict[str, Any]):
    """Update auth settings for an API."""
    api = apis_config[base_url]
    auth = api.get("auth", {})
    
    menu_items = [
        {"title": "Bearer Token", "value": "bearer"},
        {"title": "API Key (Header)", "value": "api_key_header"},
        {"title": "None", "value": "none"},
        {"title": "[Back]", "value": "back"}
    ]
    choices = format_menu_choices(menu_items, title_field="title", value_field="value")
    auth_type = prompt_toolkit_menu(choices)
    
    if not auth_type or auth_type == "back":
        return
    
    if auth_type == "none":
        api["auth"] = {}
        console.print("[yellow]Auth removed.[/yellow]")
    elif auth_type == "bearer":
        token = questionary.password("Enter Bearer Token:").ask()
        if token:
            api["auth"] = {"type": "bearer", "token": token}
            console.print("[green]Bearer token saved (securely).[/green]")
    elif auth_type == "api_key_header":
        header = questionary.text("Header Name (e.g. X-API-Key):", default=auth.get("header", "X-API-Key")).ask()
        key = questionary.password(f"Enter {header}:").ask()
        if header and key:
            api["auth"] = {"type": "api_key_header", "header": header, "api_key": key}
            console.print(f"[green]API Key for {header} saved (securely).[/green]")

def copy_auth_info(base_url: str, apis_config: Dict[str, Any]):
    """Copy stored API auth info to clipboard."""
    api = apis_config[base_url]
    auth = api.get("auth", {})
    
    if not auth:
        console.print("[yellow]No auth info stored for this API.[/yellow]")
        return

    # Check if the data is still secretized (retrieval failed)
    def _check_secret(val):
        if isinstance(val, str) and val.startswith("SEC:"):
            console.print(f"[bold red]Error:[/bold red] Could not retrieve secret from Keychain.")
            console.print(f"Reference: [dim]{val}[/dim]")
            console.print("[yellow]Try running 'Update Auth' to re-save the credential.[/yellow]")
            return None
        return val

    if auth.get("type") == "bearer":
        token = _check_secret(auth.get("token"))
        if token and copy_to_clipboard(token):
            console.print("[green]✅ Bearer token copied to clipboard![/green]")
        elif token:
            console.print("[red]❌ Failed to copy bearer token.[/red]")
    elif auth.get("type") == "api_key_header":
        key = _check_secret(auth.get("api_key"))
        header = auth.get("header", "API Key")
        if key and copy_to_clipboard(key):
            console.print(f"[green]✅ {header} copied to clipboard![/green]")
        elif key:
            console.print(f"[red]❌ Failed to copy {header}.[/red]")

def list_apis(apis_config: Dict[str, Any]):
    """List and interact with stored APIs."""
    if not apis_config:
        console.print("[yellow]No APIs stored yet. Use 'Add New API' first.[/yellow]")
        return

    while True:
        # Sort by title
        items = sorted(
            [{"url": url, **data} for url, data in apis_config.items()],
            key=lambda x: x.get("title", "").lower()
        )
        
        choices = format_menu_choices(items, title_field="title", value_field="url")
        choices.append(questionary.Choice("b. Back", value="BACK"))
        choices.append(questionary.Choice("q. Quit", value="QUIT"))
        
        console.print("\n[bold]Stored APIs:[/bold]")
        choice = prompt_toolkit_menu(choices)
        
        if choice == "QUIT":
            sys.exit(0)
        if not choice or choice == "BACK":
            break
            
        api_menu(choice, apis_config)

def api_menu(base_url: str, apis_config: Dict[str, Any]):
    """Menu for a specific API."""
    while True:
        api = apis_config[base_url]
        console.print(f"\n[bold cyan]API: {api['title']}[/bold cyan]")
        console.print(f"Base URL: [dim]{base_url}[/dim]")
        
        auth_status = "[green]Configured[/green]" if api.get("auth") else "[yellow]None[/yellow]"
        console.print(f"Auth: {auth_status}")

        menu_items = [
            {"title": "List Endpoints", "value": "LIST"},
            {"title": "Update Auth", "value": "AUTH"},
            {"title": "Copy Auth Info", "value": "COPY_AUTH"},
            {"title": "Refresh Spec", "value": "REFRESH"},
            {"title": "Remove API", "value": "REMOVE"},
            {"title": "[Back]", "value": "BACK"},
            {"title": "[Quit]", "value": "QUIT"}
        ]
        choices = format_menu_choices(menu_items, title_field="title", value_field="value")
        choice = prompt_toolkit_menu(choices)
        
        if choice == "QUIT":
            sys.exit(0)
        if not choice or choice == "BACK":
            break
        elif choice == "LIST":
            list_endpoints(base_url, apis_config)
        elif choice == "AUTH":
            update_auth(base_url, apis_config)
            save_pas_config("apis", apis_config)
        elif choice == "COPY_AUTH":
            copy_auth_info(base_url, apis_config)
        elif choice == "REFRESH":
            # Re-fetch or re-read spec
            spec = None
            spec_url = api.get("spec_url")
            docs_url = api.get("docs_url")

            with console.status("[bold green]Refreshing API spec..."):
                if spec_url and spec_url.startswith("http"):
                    try:
                        r = requests.get(spec_url, timeout=10)
                        spec = yaml.safe_load(r.text)
                    except:
                        # Fallback to auto-fetch from docs_url if direct spec_url fails
                        spec, spec_url = fetch_openapi(docs_url)
                elif spec_url:
                    # Local path
                    path = Path(spec_url)
                    if path.exists():
                        try:
                            content = path.read_text()
                            spec = json.loads(content) if path.suffix == ".json" else yaml.safe_load(content)
                        except: pass

            if spec:
                api["title"] = spec.get("info", {}).get("title", api["title"])
                api["version"] = spec.get("info", {}).get("version", api["version"])
                api["spec_url"] = spec_url
                # Update endpoints
                paths = spec.get("paths", {})
                api["endpoints"] = {}
                for path, methods in paths.items():
                    if not isinstance(methods, dict): continue
                    for method, details in methods.items():
                        if method.lower() in ["get", "post", "put", "delete", "patch"]:
                            api["endpoints"][f"{method.upper()} {path}"] = {
                                "operationId": details.get("operationId", f"{method}_{path}"),
                                "summary": (details.get("summary") or "")[:100]
                            }
                save_pas_config("apis", apis_config)
                console.print("[green]Refreshed successfully![/green]")
            else:
                console.print("[red]Failed to refresh spec.[/red]")
        elif choice == "REMOVE":
            if questionary.confirm(f"Are you sure you want to remove {api['title']}?").ask():
                del apis_config[base_url]
                save_pas_config("apis", apis_config)
                break

def list_endpoints(base_url: str, apis_config: Dict[str, Any]):
    """Explore endpoints of an API."""
    api = apis_config[base_url]
    endpoints = api.get("endpoints", {})
    
    if not endpoints:
        console.print("[yellow]No endpoints found for this API.[/yellow]")
        return

    while True:
        # Sort endpoints by path
        sorted_keys = sorted(endpoints.keys(), key=lambda x: x.split(" ", 1)[1])
        
        # Find max method length for padding
        max_method_len = 0
        for key in sorted_keys:
            method = key.split(" ", 1)[0]
            max_method_len = max(max_method_len, len(method))

        # Prepare items for format_menu_choices
        items = []
        for key in sorted_keys:
            method, path = key.split(" ", 1)
            summary = endpoints[key].get("summary", "")
            padded_key = f"{method:<{max_method_len}} {path}"
            title = f"{padded_key} - {summary}" if summary else padded_key
            items.append({"title": title, "key": key})

        choices = format_menu_choices(items, title_field="title", value_field="key")
        choices.append(questionary.Choice("b. Back", value="BACK"))
        choices.append(questionary.Choice("q. Quit", value="QUIT"))
        
        console.print(f"\n[bold]Endpoints for {api['title']}:[/bold]")
        choice = prompt_toolkit_menu(choices)
        
        if choice == "QUIT":
            sys.exit(0)
        if not choice or choice == "BACK":
            break
            
        view_endpoint_details(base_url, choice, apis_config)

def view_endpoint_details(base_url: str, endpoint_key: str, apis_config: Dict[str, Any]):
    """Fetch full spec and show details for one endpoint."""
    api = apis_config[base_url]
    spec_url = api.get("spec_url")
    
    with console.status("[bold green]Fetching full details..."):
        try:
            if spec_url.startswith("http"):
                r = requests.get(spec_url, timeout=10)
                spec = r.json() if spec_url.endswith(".json") or "json" in r.headers.get("Content-Type", "") else yaml.safe_load(r.text)
            else:
                # Local path
                path = Path(spec_url)
                content = path.read_text()
                spec = json.loads(content) if path.suffix == ".json" else yaml.safe_load(content)
        except Exception as e:
            console.print(f"[red]Error fetching spec: {e}[/red]")
            return

    method, path = endpoint_key.split(" ", 1)
    method = method.lower()
    
    endpoint_data = spec.get("paths", {}).get(path, {}).get(method)
    if not endpoint_data:
        console.print("[red]Endpoint details not found in spec.[/red]")
        return

    console.print(Panel(f"[bold magenta]{method.upper()}[/bold magenta] [bold]{path}[/bold]", title="Endpoint Details", border_style="blue"))
    
    if endpoint_data.get("summary"):
        console.print(f"\n[bold]Summary:[/bold] {endpoint_data['summary']}")
    if endpoint_data.get("description"):
        console.print(f"\n[bold]Description:[/bold]\n{endpoint_data['description']}")

    # Parameters
    params = endpoint_data.get("parameters", [])
    # Also check if it's OpenAPI 3 and has requestBody
    request_body = endpoint_data.get("requestBody")

    if params:
        table = Table(title="Parameters", box=None, header_style="bold green")
        table.add_column("In", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Type")
        table.add_column("Required")
        table.add_column("Description")
        
        for p in params:
            schema = p.get("schema", {})
            p_type = schema.get("type", "string")
            table.add_row(
                p.get("in", "query"),
                p.get("name", ""),
                p_type,
                "Yes" if p.get("required") else "No",
                p.get("description", "")
            )
        console.print(table)

    if request_body:
        console.print("\n[bold]Request Body:[/bold]")
        content = request_body.get("content", {})
        for mime, details in content.items():
            console.print(f"  [cyan]{mime}[/cyan]")
            schema = details.get("schema")
            if schema:
                # Pretty print schema or example
                example = details.get("example") or schema.get("example")
                if example:
                    console.print(Syntax(json.dumps(example, indent=2), "json", theme="monokai"))
                else:
                    console.print("  (Schema available, use Swagger UI for full interactive explore)")

    # Responses
    responses = endpoint_data.get("responses", {})
    if responses:
        table = Table(title="Responses", box=None, header_style="bold yellow")
        table.add_column("Code", style="bold")
        table.add_column("Description")
        for code, r_details in responses.items():
            table.add_row(code, r_details.get("description", ""))
        console.print(table)

    # Offer to copy curl command or call endpoint
    menu_items = [
        {"title": "Generate and copy cURL example", "value": "curl"},
        {"title": "Call endpoint (Request)", "value": "call"},
        {"title": "[Back]", "value": "back"},
        {"title": "[Quit]", "value": "QUIT"}
    ]
    choices = format_menu_choices(menu_items, title_field="title", value_field="value")
    action = prompt_toolkit_menu(choices)

    if action == "QUIT":
        sys.exit(0)
    if action == "curl":
        curl = generate_curl(base_url, method, path, endpoint_data, api.get("auth"))
        if copy_to_clipboard(curl):
            console.print("[green]✅ cURL command copied to clipboard![/green]")
    elif action == "call":
        call_endpoint(base_url, method, path, endpoint_data, api.get("auth"))

def call_endpoint(base_url: str, method: str, path: str, endpoint_data: Dict[str, Any], auth: Optional[Dict[str, Any]]):
    """Interactively collect parameters and call the endpoint."""
    url = urljoin(base_url, path.lstrip("/"))
    params = {}
    headers = {}
    json_data = None

    # Collect Parameters
    api_params = endpoint_data.get("parameters", [])
    for p in api_params:
        p_name = p.get("name")
        p_in = p.get("in", "query")
        p_req = p.get("required", False)
        
        prompt = f"Param ({p_in}) '{p_name}'"
        if p.get("description"):
            prompt += f" [{p['description']}]"
        
        val = questionary.text(f"{prompt} {'(required)' if p_req else '(optional)'}:").ask()
        
        if val:
            if p_in == "query":
                params[p_name] = val
            elif p_in == "header":
                headers[p_name] = val
            elif p_in == "path":
                url = url.replace(f"{{{p_name}}}", val)

    # Collect Request Body if needed
    request_body = endpoint_data.get("requestBody")
    if request_body:
        content = request_body.get("content", {})
        if "application/json" in content:
            console.print("\n[bold]JSON Request Body Required[/bold]")
            example = content["application/json"].get("example")
            if example:
                console.print("Example:")
                console.print(Syntax(json.dumps(example, indent=2), "json", theme="monokai"))
            
            body_str = questionary.text("Enter JSON Body (leave empty for empty dict {}):").ask()
            try:
                json_data = json.loads(body_str) if body_str else {}
            except Exception as e:
                console.print(f"[red]Invalid JSON: {e}. Using empty dict.[/red]")
                json_data = {}

    # Apply Auth
    if auth:
        if auth.get("type") == "bearer":
            headers["Authorization"] = f"Bearer {auth.get('token')}"
        elif auth.get("type") == "api_key_header":
            headers[auth.get("header")] = auth.get("api_key")

    # Execute Request
    console.print(f"\n[bold green]Calling:[/bold green] [bold]{method.upper()}[/bold] {url}")
    if params: console.print(f"Query Params: {params}")
    if json_data: console.print(f"Body: {json_data}")

    try:
        with console.status("[bold blue]Request in progress..."):
            r = requests.request(
                method=method.upper(),
                url=url,
                params=params,
                headers=headers,
                json=json_data,
                timeout=30
            )
        
        # Display Response
        status_color = "green" if 200 <= r.status_code < 300 else "red"
        console.print(Panel(f"Status: [{status_color}]{r.status_code} {r.reason}[/{status_color}]", title="Response", border_style="blue"))
        
        # Pretty print response content
        content_type = r.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                console.print(Syntax(json.dumps(r.json(), indent=2), "json", theme="monokai"))
            except:
                console.print(r.text)
        elif "text/" in content_type:
            console.print(r.text)
        else:
            console.print(f"[dim]Received {len(r.content)} bytes of type {content_type}[/dim]")

    except Exception as e:
        console.print(f"[bold red]Request failed:[/bold red] {e}")

    questionary.text("\nPress Enter to continue...").ask()

def generate_curl(base_url: str, method: str, path: str, endpoint_data: Dict[str, Any], auth: Optional[Dict[str, Any]]) -> str:
    """Generate a placeholder cURL command."""
    url = urljoin(base_url, path.lstrip("/"))
    curl_parts = [f"curl -X {method.upper()} \"{url}\""]
    
    # Auth headers
    if auth:
        if auth.get("type") == "bearer":
            token = auth.get("token", "YOUR_TOKEN")
            curl_parts.append(f"-H \"Authorization: Bearer {token}\"")
        elif auth.get("type") == "api_key_header":
            header = auth.get("header", "X-API-Key")
            key = auth.get("api_key", "YOUR_KEY")
            curl_parts.append(f"-H \"{header}: {key}\"")
            
    # Content-type if it has requestBody
    if endpoint_data.get("requestBody"):
        curl_parts.append("-H \"Content-Type: application/json\"")
        curl_parts.append("-d '{\n  \"example\": \"data\"\n}'")
        
    return " \\\n  ".join(curl_parts)

def main():
    parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
    parser.add_argument("--docs", help="Add a new API by providing its documentation URL directly")
    args = parser.parse_args()

    console.print(Panel("[bold blue]%s[/bold blue]" % TOOL_DESCRIPTION, border_style="blue"))
    
    apis_config = load_pas_config("apis")

    if args.docs:
        # Skip main menu and go straight to adding the API
        add_new_api_from_url(args.docs, apis_config)
        save_pas_config("apis", apis_config)
    
    while True:
        menu_items = [
            {"title": "List Stored APIs", "value": "LIST"},
            {"title": "Add New API", "value": "ADD"},
            {"title": "[Quit]", "value": "QUIT"}
        ]
        choices = format_menu_choices(menu_items, title_field="title", value_field="value")
        
        console.print("\n[bold]Main Menu:[/bold]")
        choice = prompt_toolkit_menu(choices)
        
        if choice == "QUIT" or not choice:
            break
        elif choice == "LIST":
            list_apis(apis_config)
        elif choice == "ADD":
            add_new_api(apis_config)
            save_pas_config("apis", apis_config)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
