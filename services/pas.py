#!/usr/bin/env python3
"""
@pas-executable
Central administrative tool for PAS. Controls updates, listing tools, and AI help.
Subcommands:
- list: Scans the repository for tools and shows their descriptions.
- ask: Query an AI assistant for help or finding tools.
- upgrade: Pulls latest code and refreshes system environment/symlinks.
- up: Alias for upgrade.
- repo: Opens the official GitHub repository in your browser.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path

# Add project root to sys.path so we can find 'helpers'
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from rich.console import Console
from rich.panel import Panel

import questionary

from helpers.core import load_pas_config, save_pas_config, format_menu_choices, prompt_toolkit_menu, Menu

console = Console()

def check_platform():
    """Check if the current platform is macOS and warn if not."""
    if sys.platform != "darwin":
        console.print(Panel(
            "[bold yellow]Warning:[/bold yellow] PAS Toolkit is primarily designed for [bold]macOS[/bold].\n"
            f"You are running on [bold cyan]{sys.platform}[/bold cyan]. Some features (Keychain, VNC, etc.) may not work as expected.",
            title="Platform Compatibility",
            border_style="yellow"
        ))

def get_pas_root() -> Path:
    """Find the root of the PAS installation."""
    # Assume the script is in [PAS_ROOT]/services/
    return Path(__file__).resolve().parent.parent

def run_command(cmd: list[str], cwd: Path) -> bool:
    """Run a shell command and print output."""
    print(f"\n> {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=cwd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error: Command failed with exit code {e.returncode}")
        return False

def get_pas_version() -> str:
    """Read the version from the VERSION file."""
    version_path = get_pas_root() / "VERSION"
    if version_path.exists():
        return version_path.read_text().strip()
    return "unknown"

def cmd_upgrade(args):
    """Update PAS from the remote repository and refresh setup."""
    root = get_pas_root()
    old_version = get_pas_version()
    print(f"Upgrading PAS at {root} (Current version: {old_version})...")
    
    # 1. git pull
    if not run_command(["git", "pull"], cwd=root):
        sys.exit(1)
        
    # 2. make setup
    if not run_command(["make", "setup"], cwd=root):
        sys.exit(1)
        
    new_version = get_pas_version()
    console.print(f"\n[bold green]PAS upgrade complete![/bold green]")
    if old_version != new_version:
        console.print(f"Updated from [yellow]{old_version}[/yellow] to [bold cyan]{new_version}[/bold cyan]")
    else:
        console.print(f"Already at latest version: [bold cyan]{new_version}[/bold cyan]")
    console.print("\n[bold yellow]Note:[/bold yellow] If new tools are not appearing in tab-completion, run: [cyan]rehash[/cyan]")

def cmd_repo(args):
    """Open the official PAS GitHub repository."""
    url = "https://github.com/nextoken/pas"
    console.print(f"\nOfficial Repository: [bold cyan]{url}[/bold cyan]")
    console.print("Opening in default browser...\n")
    webbrowser.open(url)

def migrate_llm_config(config):
    """Migrate legacy LLM config to the new format."""
    if config is None:
        config = {}
    
    # If it's already in the new format, return as is
    if "active_config_id" in config and "configs" in config and "providers" in config:
        return config, False

    new_config = {
        "active_config_id": config.get("active_config_id"),
        "configs": config.get("configs", {}),
        "providers": config.get("providers", {})
    }
    
    # Check if we have legacy provider data to migrate
    legacy_providers = config.get("providers", {})
    active_provider = config.get("active_provider")
    
    # If it's truly empty, don't say we migrated anything
    if not legacy_providers and not active_provider and not new_config["configs"]:
        return new_config, False

    # ... (rest of migration logic)

    # Move tokens to top-level providers map and create config entries
    for p_id, p_data in legacy_providers.items():
        if not isinstance(p_data, dict):
            continue
            
        token = p_data.get("token")
        model = p_data.get("model")
        
        new_config["providers"][p_id] = {"token": token}
        
        if model:
            config_id = f"{p_id}:{model}"
            new_config["configs"][config_id] = {
                "provider": p_id,
                "model": model
            }
            if p_id == active_provider:
                new_config["active_config_id"] = config_id

    # Fallback if no active config could be determined
    if not new_config["active_config_id"] and new_config["configs"]:
        new_config["active_config_id"] = list(new_config["configs"].keys())[0]
    
    # If it was empty or couldn't be migrated, keep it as is but with new structure
    if not new_config["active_config_id"] and active_provider:
        # We have a provider but no model was set yet or migration failed
        pass

    return new_config, True

def cmd_ask(args):
    """Ask an LLM about available tools."""
    config = load_pas_config("llms")
    if config is None:
        config = {}
    
    # Migrate if necessary
    config, migrated = migrate_llm_config(config)
    if migrated:
        save_pas_config("llms", config)
    
    if not config or not config.get("active_config_id"):
        setup_llm(config)
        config = load_pas_config("llms") or {} # Reload after setup
    
    active_config_id = config.get("active_config_id")
    active_config = config.get("configs", {}).get(active_config_id)
    
    if not active_config:
        setup_llm(config)
        config = load_pas_config("llms") or {}
        active_config_id = config.get("active_config_id")
        active_config = config.get("configs", {}).get(active_config_id)

    if not active_config:
        console.print("[red]Error: Could not determine active LLM configuration.[/red]")
        return

    provider = active_config.get("provider")
    model = active_config.get("model")
    provider_token = config.get("providers", {}).get(provider, {}).get("token")
    
    # Prepare provider_config for existing call_llm / validate_llm_token
    # We might want to refactor those too later
    provider_config = {
        "token": provider_token,
        "model": model
    }
    
    # Validate token and get metadata
    is_valid, token_meta = validate_llm_token(provider, provider_token)
    if not provider_token or not is_valid:
        if provider_token:
            console.print(f"[bold red]Error:[/bold red] {provider} API token is invalid or expired.")
        setup_llm(config)
        config = load_pas_config("llms") or {} # Reload after setup
        active_config_id = config.get("active_config_id")
        active_config = config.get("configs", {}).get(active_config_id)
        provider = active_config.get("provider")
        model = active_config.get("model")
        provider_token = config.get("providers", {}).get(provider, {}).get("token")
        provider_config = {"token": provider_token, "model": model}
        is_valid, token_meta = validate_llm_token(provider, provider_token)

    initial_query = " ".join(args.query).strip() if args.query else None
    is_interactive = not initial_query
    
    while True:
        query = initial_query
        if is_interactive:
            # ... (rest of interactive logic)
            # Refresh config in case it was changed in the loop
            config = load_pas_config("llms")
            active_config_id = config.get("active_config_id", "Not configured")
            active_config = config.get("configs", {}).get(active_config_id, {})
            provider = active_config.get("provider", "N/A")
            model = active_config.get("model", "N/A")
            provider_token = config.get("providers", {}).get(provider, {}).get("token")
            provider_config = {"token": provider_token, "model": model}
            
            # Interactive mode
            menu_choices = [
                {"title": "Ask a question", "value": "ASK"},
                {"title": f"Switch/Setup LLM Config (Current: {active_config_id})", "value": "SETUP"},
                {"title": "[Quit]", "value": "QUIT"}
            ]
            formatted_choices = format_menu_choices(menu_choices, title_field="title", value_field="value")
            action = prompt_toolkit_menu(formatted_choices)
            
            if action == "QUIT" or not action:
                return
            elif action == "SETUP":
                setup_llm(config)
                continue
            
            query = questionary.text("Enter your question:").ask()
            if not query:
                continue

        # Re-validate token and get metadata for current provider
        _, token_meta = validate_llm_token(provider, provider_token)

        # Prepare tools info
        tools = get_tools_info()
        tools_context = "\n".join([f"- {name}: {desc}" for name, desc in tools])
        
        # Get token age for display
        from helpers.core import get_secret_age, SECRET_ROTATION_DAYS
        token_age = get_secret_age("llms", f"providers.{provider}.token")
        
        # Try to get actual expiration from OpenRouter metadata
        actual_expiry_days = None
        if token_meta and "expires_at" in token_meta and token_meta["expires_at"]:
            try:
                # OpenRouter returns ISO 8601, e.g., "2026-03-16T14:59:18.822Z"
                # We strip the 'Z' and parse
                expiry_str = token_meta["expires_at"].replace("Z", "+00:00")
                expiry_date = datetime.datetime.fromisoformat(expiry_str)
                now = datetime.datetime.now(datetime.timezone.utc)
                actual_expiry_days = (expiry_date - now).days
            except Exception:
                pass

        if actual_expiry_days is not None:
            expiry_str = f", Actual Expiry in: {actual_expiry_days} days" if actual_expiry_days > 0 else ", [bold red]EXPIRED[/bold red]"
            age_str = f" (Token age: {token_age} days{expiry_str})"
        elif token_age is not None:
            expires_in = SECRET_ROTATION_DAYS - token_age
            expiry_str = f", Policy Expiry in: {expires_in} days" if expires_in > 0 else ", [bold red]EXPIRED[/bold red]"
            age_str = f" (Token age: {token_age} days{expiry_str})"
        else:
            age_str = ""

        root = get_pas_root()
        readme_content = ""
        readme_path = root / "README.md"
        if readme_path.exists():
            try:
                readme_content = readme_path.read_text(errors='ignore')
            except Exception:
                pass

        dev_guide_content = ""
        dev_guide_path = root / "dev-guide.md"
        if dev_guide_path.exists():
            try:
                dev_guide_content = dev_guide_path.read_text(errors='ignore')
            except Exception:
                pass

        project_url = "https://github.com/nextoken/pas"
        author = "Nextoken (https://github.com/nextoken)"
        
        system_prompt = f"""You are an assistant for the PAS (Process Automation Setups) toolkit. 
Your goal is to help users find the right tool for their task and answer questions about the toolkit itself.

[Project Info]
- Project Name: PAS (Process Automation Setups)
- Official Repository: {project_url}
- Author/Organization: {author}
- Description: A collection of automation tools for developers and power users.

[README Overview]
{readme_content}

[Developer Guide]
{dev_guide_content}

[Available Tools]
Below is a list of available tools and their detailed descriptions (extracted from their help/docstrings):
{tools_context}

[Instructions]
1. When asked about a task, be concise and recommend specific tool names from the list.
2. Use the [Available Tools] section to explain what a tool does and what subcommands or features it has.
3. If asked about the author or project URL, use the [Project Info] provided above.
4. Use the [README Overview] to explain the toolkit's philosophy, installation, or configuration.
5. Use the [Developer Guide] to explain how to create new tools, register them, or use helper functions.
6. If no tool seems relevant, say so and suggest looking at the official repository."""

        console.print(f"\n[bold blue]Asking LLM ({provider}:{model}){age_str}...[/bold blue]")
        
        try:
            response = call_llm(provider, provider_config, system_prompt, query)
            if not response or not response.strip():
                console.print(Panel("[yellow]The AI returned an empty response. This can happen with some models or providers. Please try rephrasing your question or try again.[/yellow]", title="PAS AI Assistant", border_style="yellow"))
            else:
                console.print(Panel(response, title="PAS AI Assistant", border_style="green"))
        except Exception as e:
            error_msg = str(e)
            console.print(f"[bold red]Error:[/bold red] {error_msg}")
            if "403" in error_msg or "401" in error_msg:
                if questionary.confirm("The API token seems invalid. Would you like to re-configure it?").ask():
                    setup_llm(config)
                    if not is_interactive:
                        # Re-run the call if not interactive
                        config = load_pas_config("llms")
                        active_config_id = config.get("active_config_id")
                        active_config = config.get("configs", {}).get(active_config_id, {})
                        provider = active_config.get("provider")
                        model = active_config.get("model")
                        provider_token = config.get("providers", {}).get(provider, {}).get("token")
                        provider_config = {"token": provider_token, "model": model}
                        try:
                            console.print(f"\n[bold blue]Retrying LLM ({provider}:{model})...[/bold blue]")
                            response = call_llm(provider, provider_config, system_prompt, query)
                            if not response or not response.strip():
                                console.print(Panel("[yellow]The AI returned an empty response on retry. Please try again later or check your API status.[/yellow]", title="PAS AI Assistant", border_style="yellow"))
                            else:
                                console.print(Panel(response, title="PAS AI Assistant", border_style="green"))
                        except Exception as retry_e:
                            console.print(f"[bold red]Retry failed:[/bold red] {str(retry_e)}")

        if not is_interactive:
            break

def get_openrouter_models():
    """Fetch and filter popular models from OpenRouter API."""
    url = "https://openrouter.ai/api/v1/models"
    try:
        req = urllib.request.Request(url, headers={
            "HTTP-Referer": "https://github.com/nextoken/pas",
            "X-Title": "PAS Toolkit"
        })
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read().decode("utf-8"))
            if "data" in data:
                # OpenRouter doesn't have a direct "popularity" field in the models API,
                # but we can prioritize common providers and non-free models that are generally popular.
                # For now, we'll fetch them all and select a subset of well-known ones, 
                # or just use the first 15 if they seem relevant.
                all_models = data["data"]
                
                # Sort by a heuristic: prefer non-free, prefer specific providers
                def model_priority(m):
                    pid = m.get("id", "")
                    # Prioritize these providers
                    p_score = 0
                    for p in ["openai/", "anthropic/", "google/", "deepseek/", "meta-llama/"]:
                        if pid.startswith(p):
                            p_score = 10
                            break
                    
                    # Penalize free/exp models slightly so they don't dominate the top
                    if ":free" in pid or "-exp" in pid:
                        p_score -= 5
                        
                    return p_score

                sorted_models = sorted(all_models, key=model_priority, reverse=True)
                return sorted_models
    except Exception as e:
        console.print(f"[yellow]Warning: Could not fetch models from OpenRouter: {e}[/yellow]")
    return []

def setup_llm(config):
    if "providers" not in config:
        config["providers"] = {}
    if "configs" not in config:
        config["configs"] = {}

    # Menu for existing configs or new one
    choices = []
    for cfg_id in config.get("configs", {}).keys():
        is_active = cfg_id == config.get("active_config_id")
        title = f"{cfg_id} {'(ACTIVE)' if is_active else ''}"
        choices.append({"title": title, "value": cfg_id})
    
    choices.append({"title": "[Add new configuration]", "value": "NEW"})
    choices.append({"title": "[Back]", "value": "BACK"})
    
    formatted_choices = format_menu_choices(choices, title_field="title", value_field="value")
    console.print("\n[bold cyan]LLM Configuration Management:[/bold cyan]")
    selected = prompt_toolkit_menu(formatted_choices)
    
    if not selected or selected == "BACK":
        return

    if selected == "NEW":
        # Select Provider
        providers_list = [{"name": "openrouter", "id": "openrouter"}]
        provider_choices = format_menu_choices(providers_list, title_field="name", value_field="id")
        console.print("\n[bold cyan]Select LLM Provider:[/bold cyan]")
        provider = prompt_toolkit_menu(provider_choices)
        if not provider: return

        # Handle Token
        current_token = config.get("providers", {}).get(provider, {}).get("token")
        token = None
        
        if current_token:
            masked_token = current_token[:8] + "*" * (len(current_token) - 12) + current_token[-4:] if len(current_token) > 12 else "****"
            if questionary.confirm(f"Reuse existing token for {provider} ({masked_token})?").ask():
                token = current_token
        
        if not token:
            if provider == "openrouter":
                console.print(f"You can create an API key at: [bold cyan]https://openrouter.ai/settings/keys[/bold cyan]")
            token = questionary.password(f"Enter {provider} API Token:").ask()
            if not token:
                console.print("[yellow]Token cannot be empty.[/yellow]")
                return

        # Select Model
        model = "openai/gpt-4o-mini"
        if provider == "openrouter":
            console.print("[cyan]Fetching available models from OpenRouter...[/cyan]")
            dynamic_models = get_openrouter_models()
            
            # Get already configured models to show them first
            existing_models = []
            for cfg in config.get("configs", {}).values():
                if cfg.get("provider") == provider and cfg.get("model"):
                    if cfg["model"] not in existing_models:
                        existing_models.append(cfg["model"])

            # Use dynamic models if available, otherwise fallback
            all_available_models = get_openrouter_models()
            source_models = all_available_models if all_available_models else [
                {"id": "openai/gpt-4o-mini"},
                {"id": "anthropic/claude-3.5-sonnet"},
                {"id": "google/gemini-2.0-flash-001"},
                {"id": "deepseek/deepseek-chat"},
            ]
            
            # Use ppui Menu for model selection
            menu = Menu("Select Model")
            
            def format_model_title(m_obj):
                mid = m_obj.get("id")
                pricing = m_obj.get("pricing", {})
                if not pricing:
                    # Try to find the object in source_models if it exists to get pricing
                    # This handles the case where m_obj was a fallback {"id": m_id}
                    m_obj = next((obj for obj in source_models if obj.get("id") == mid), m_obj)
                    pricing = m_obj.get("pricing", {})

                if pricing:
                    prompt = float(pricing.get("prompt", 0)) * 1000000
                    completion = float(pricing.get("completion", 0)) * 1000000
                    # Format as $0.00 or $0.0000 depending on value
                    p_str = f"${prompt:g}" if prompt >= 0.01 else f"${prompt:.4f}"
                    c_str = f"${completion:g}" if completion >= 0.01 else f"${completion:.4f}"
                    return f"{mid:<40} | Cost/1M: {p_str} in, {c_str} out"
                return f"{mid:<40}"

            # Add existing models first
            for m_id in existing_models:
                # Find the object in source_models if it exists to get pricing
                m_obj = next((obj for obj in source_models if obj.get("id") == m_id), {"id": m_id})
                menu.add_option(format_model_title(m_obj), m_id)
            
            # Add top dynamic models
            display_limit = 15
            displayed_count = 0
            for m_obj in source_models:
                m_id = m_obj.get("id")
                if m_id not in existing_models:
                    menu.add_option(format_model_title(m_obj), m_id)
                    displayed_count += 1
                    if displayed_count >= display_limit:
                        break
            
            OTHER_VALUE = "__other__"
            menu.add_option("Other (see https://openrouter.ai/models for IDs)", OTHER_VALUE)
            menu.add_back_item()
            menu.add_quit_item()
                
            model_choice = menu.run(loop=False)
            
            if not model_choice or model_choice == "quit":
                sys.exit(0)
            elif model_choice == "back":
                return setup_llm(config) # Recursive call to go back
            elif model_choice == OTHER_VALUE:
                model = questionary.text("Enter model ID:", default="openai/gpt-4o-mini").ask() or "openai/gpt-4o-mini"
            else:
                model = model_choice

        # Save configuration
        config_id = f"{provider}:{model}"
        config["providers"][provider] = {"token": token}
        config["configs"][config_id] = {"provider": provider, "model": model}
        config["active_config_id"] = config_id
        
        save_pas_config("llms", config)
        
        if validate_llm_token(provider, token)[0]:
            console.print(f"[green]Configuration '{config_id}' saved and verified.[/green]")
        else:
            console.print(f"[bold yellow]Warning:[/bold yellow] Saved, but verification failed for {provider}. Check your token.")
    else:
        # Switch to existing
        config["active_config_id"] = selected
        
        # Check if we should re-configure the token for this provider
        active_config = config.get("configs", {}).get(selected, {})
        provider = active_config.get("provider")
        current_token = config.get("providers", {}).get(provider, {}).get("token")
        
        if current_token:
            masked_token = current_token[:8] + "*" * (len(current_token) - 12) + current_token[-4:] if len(current_token) > 12 else "****"
            console.print(f"\nConfiguration '{selected}' uses provider '{provider}' with token: {masked_token}")
            if questionary.confirm(f"Would you like to update the API token for '{provider}'?").ask():
                if provider == "openrouter":
                    console.print(f"You can create an API key at: [bold cyan]https://openrouter.ai/settings/keys[/bold cyan]")
                new_token = questionary.password(f"Enter new {provider} API Token:").ask()
                if new_token:
                    config["providers"][provider]["token"] = new_token
                    console.print(f"[green]Token updated for {provider}.[/green]")
        
        save_pas_config("llms", config)
        console.print(f"[green]Switched to configuration '{selected}'.[/green]")

def validate_llm_token(provider, token):
    """Check if the provided token is valid for the given provider. Returns (is_valid, metadata)."""
    if provider == "openrouter":
        url = "https://openrouter.ai/api/v1/auth/key"
        headers = {
            "Authorization": f"Bearer {token or ''}",
            "HTTP-Referer": "https://github.com/nextoken/pas",
            "X-Title": "PAS Toolkit",
            "User-Agent": "PAS-Toolkit/1.0"
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as res:
                if res.getcode() == 200:
                    resp_data = json.loads(res.read().decode("utf-8"))
                    return True, resp_data.get("data", {})
                return False, {}
        except Exception:
            return False, {}
    return True, {}

def call_llm(provider, provider_config, system_prompt, query):
    """Call the LLM API."""
    if provider == "openrouter":
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider_config['token']}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nextoken/pas",
            "X-Title": "PAS Toolkit",
            "User-Agent": "PAS-Toolkit/1.0"
        }
        data = {
            "model": provider_config["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ]
        }
        
        req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req) as res:
                resp_data = json.loads(res.read().decode("utf-8"))
                if "choices" in resp_data:
                    return resp_data["choices"][0]["message"]["content"]
                else:
                    raise Exception(f"Unexpected response: {resp_data}")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            try:
                error_json = json.loads(error_body)
                if "error" in error_json:
                    msg = error_json["error"].get("message", error_body)
                    raise Exception(f"OpenRouter Error ({e.code}): {msg}")
            except:
                pass
            raise Exception(f"HTTP Error {e.code}: {e.reason}\n{error_body}")
    else:
        raise Exception(f"Unsupported provider: {provider}")

def _parse_tool_constants(content: str) -> tuple[str | None, str | None]:
    """Extract TOOL_ID and TOOL_SHORT_DESC from file content (single-line string values). Returns (tool_id, short_desc)."""
    tool_id = None
    short_desc = None
    # TOOL_ID = "user-ops" or TOOL_ID = 'user-ops'
    m = re.search(r'TOOL_ID\s*=\s*["\']([^"\']+)["\']', content)
    if m:
        tool_id = m.group(1).strip()
    m = re.search(r'TOOL_SHORT_DESC\s*=\s*["\']((?:[^"\'\\]|\\.)*)["\']', content)
    if m:
        short_desc = m.group(1).strip().replace('\\n', '\n')
    return (tool_id, short_desc)

def get_tools_info() -> list[tuple[str, str]]:
    """Scans the repository and returns a list of (tool_name, description)."""
    root = get_pas_root()
    marker = "@pas-executable"
    tools = []

    for path, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != 'bin']
        for file in files:
            file_path = Path(path) / file
            # Only check files that are likely scripts
            if file_path.suffix not in ['.py', '', '.sh', '.bb'] or file_path.name.startswith("legacy-"):
                continue
            
            try:
                with open(file_path, 'r', errors='ignore') as f:
                    content = f.read(8192)
                    lines = content.splitlines()[:80]
                    
                    # Prefer TOOL_ID and TOOL_SHORT_DESC when defined in the script
                    tool_id, short_desc = _parse_tool_constants(content)
                    name = tool_id if tool_id else file_path.stem
                    desc = short_desc

                    # Fall back to docstring/comment description if no TOOL_SHORT_DESC
                    if not desc:
                        found_marker_at = -1
                        for i, line in enumerate(lines):
                            clean = line.strip().strip(' "\'#*;')
                            if clean == marker:
                                found_marker_at = i
                                break
                        
                        if found_marker_at != -1:
                            desc_lines = []
                            start_index = found_marker_at + 1
                            while start_index < len(lines) and not lines[start_index].strip():
                                start_index += 1
                            
                            if start_index < len(lines):
                                first_line = lines[start_index].strip()
                                if first_line.startswith('"""') or first_line.startswith("'''"):
                                    q = '"""' if first_line.startswith('"""') else "'''"
                                    if first_line.count(q) >= 2:
                                        desc_lines.append(first_line.replace(q, "").strip())
                                    else:
                                        desc_lines.append(first_line.replace(q, "").strip())
                                        for line in lines[start_index + 1:]:
                                            clean = line.strip()
                                            if q in clean:
                                                content_part = clean.replace(q, "").strip()
                                                if content_part: desc_lines.append(content_part)
                                                break
                                            desc_lines.append(clean)
                                elif first_line.startswith('#'):
                                    for line in lines[start_index:]:
                                        clean = line.strip()
                                        if clean.startswith('#'):
                                            content_part = clean.lstrip('# ').strip()
                                            if content_part: desc_lines.append(content_part)
                                            elif desc_lines: break
                                        elif clean: break
                                        else: break
                                else:
                                    desc_lines.append(first_line)

                            desc = "\n  ".join([d for d in desc_lines if d]) if desc_lines else "No description available"
                    if not desc:
                        desc = "No description available"
                    tools.append((name, desc))
            except Exception:
                continue
    return sorted(tools)

def cmd_list(args):
    """List all available PAS tools and their descriptions."""
    tools = get_tools_info()
    num_tools = len(tools)
    
    # Determine padding based on total number of tools
    if num_tools < 10:
        padding = 1
    elif num_tools < 100:
        padding = 2
    else:
        padding = 3

    print(f"\n{'No.':<{padding + 2}} {'Tool':<20} Description")
    print("-" * (padding + 2 + 20 + 40))
    for i, (name, desc) in enumerate(tools, 1):
        num_str = str(i).zfill(padding)
        print(f"{num_str:<{padding + 2}} {name:<20} {desc}")
    print(f"\nTotal: {num_tools} tools found.\n")

def main():
    check_platform()
    version = get_pas_version()
    parser = argparse.ArgumentParser(
        description=__doc__.replace("@pas-executable", "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-v", "--version", action="version", version=f"PAS Toolkit {version}")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    info_text = f"""
[bold]PAS Toolkit Manager (v{version})[/bold]

The central command-line utility for managing this toolkit:
- [cyan]list[/cyan]: Scans the repository and displays all available self-documenting tools.
- [cyan]ask[/cyan]: Query an AI assistant about which tool to use for your task.
- [cyan]upgrade[/cyan]: Automatically pulls latest changes and refreshes system symlinks.
- [cyan]up[/cyan]: Alias for [cyan]upgrade[/cyan].
- [cyan]repo[/cyan]: Opens the official GitHub repository in your browser.
"""
    # Only print if no arguments
    if len(sys.argv) == 1:
        console.print(Panel(info_text.strip(), title="pas", border_style="blue"))
        console.print("\n")

    # Upgrade command
    upgrade_parser = subparsers.add_parser("upgrade", help="Pull latest changes and run setup")
    upgrade_parser.set_defaults(func=cmd_upgrade)

    # "up" alias for upgrade
    up_parser = subparsers.add_parser("up", help="Alias for 'upgrade'")
    up_parser.set_defaults(func=cmd_upgrade)

    # List command
    list_parser = subparsers.add_parser("list", help="List all available PAS tools")
    list_parser.set_defaults(func=cmd_list)

    # Repo command
    repo_parser = subparsers.add_parser("repo", help="Open official GitHub repository")
    repo_parser.set_defaults(func=cmd_repo)

    # Ask command — everything after "ask" is the question (no quotes needed)
    ask_parser = subparsers.add_parser("ask", help="Ask an LLM about PAS tools")
    ask_parser.add_argument("query", nargs="*", help="The question to ask (all words form one sentence)")
    ask_parser.set_defaults(func=cmd_ask)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        if len(sys.argv) > 1:
            parser.print_help()
        sys.exit(0)
    args.func(args)

if __name__ == "__main__":
    main()

