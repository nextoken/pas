# PPUI: Python Process User Intent 🎭

A high-level abstract UI system for Python scripts. 

PPUI (pronounced "pui") is the Python implementation of the **PUI** (**Process User Intent**) philosophy.

The goal isn't just to "draw a menu"—it's to capture what the user wants to do next. Whether that intent is captured via a terminal hotkey, a web click, or a voice command, the script's logic remains the same. 

*PPUI: Because your scripts should care about what you want, not just how you say it.*

## Features
- **High-level Abstractions**: Use concepts like `Selection` and `Input` without caring about the rendering engine.
- **TUI Implementation**: Professional, hotkey-driven terminal menus.
- **Submenu Support**: Nested selections with `push` (drill-down) or `inline` (expandable) behaviors.
- **Rich Integration**: Full support for Rich-formatted output and panels.
- **Presentable Blocks**: High-level `CodeBlock`, `DataTable`, and `PanelSection` primitives for showing code, tables, and sections.

## Installation

```bash
pip install ppui
```

## Usage

### High-Level Menu Class

The `Menu` class provides a declarative way to build interactive loops with callback support.

```python
from ppui import Menu

def run_setup():
    print("Running setup...")

def main():
    menu = Menu("Project Manager", style="bold green")
    menu.add_option("Setup Project", run_setup)
    menu.add_option("View Dashboard", "view_dash")
    
    # Submenu support
    advanced = Menu("Advanced Settings")
    advanced.add_option("DNS Config", lambda: print("Configuring DNS..."))
    menu.add_submenu("Advanced...", advanced, behavior="push")
    
    menu.add_back_item() 
    menu.add_quit_item()
    
    selection = menu.run()
```

## Notes for AI Coding Agents

- **Think in intents, not widgets**
  - Use **PPUI** to capture **user intent** (selections, confirmations, inputs), not to directly manipulate low-level UI widgets.
  - Application code should express *what the user is choosing or providing*, not *how it is rendered*.

- **Allowed imports in application code (e.g. `services/*`, `media/*`, `utils/*`):**
  - From `ppui`:
    - `Menu`, `UIElement`, `Selection`, `Option`, `Presentable`
    - `CodeBlock`, `DataTable`, `PanelSection` for high-level code/table/section rendering
    - `prompt_yes_no`, `console`, `prompt_toolkit_menu`, `format_menu_choices`, `copy_to_clipboard`
  - From `helpers.core`:
    - `run_command`, `load_pas_config`, `save_pas_config`, and other non-UI helpers.

- **Forbidden in application code:**
  - Direct imports from low-level UI libraries such as:
    - `from rich.panel import Panel`
    - `from rich.syntax import Syntax`
    - `from rich.table import Table`
  - Direct `prompt_toolkit` or `questionary` usage.
  - These are **implementation details** inside PPUI, not part of the public surface for tools.

- **Menu pattern to use (example):**

```python
from ppui import Menu, prompt_yes_no, console

def manage_project(path):
    while True:
        console.print(f"Managing: {path}")
        menu = Menu("Project Actions", style="bold cyan")
        menu.add_option("Deploy to Staging", deploy_staging)
        menu.add_option("Deploy to Prod", deploy_prod)
        menu.add_back_item()
        menu.add_quit_item()
        selection = menu.run(loop=False)
        if selection in ["back", "quit"]:
            break
```

- **Extending PPUI**
  - If you need richer concepts (forms, layouts, dashboards, etc.), **add new abstractions to PPUI** (e.g. `Form`, `Layout`) and implement them here.
  - Do **not** introduce new `rich`/`prompt_toolkit` imports in application code; route them through PPUI so the same intent can later be rendered as TUI, web, or voice.

