import shutil
import subprocess
import sys
import uuid
from typing import Optional, Dict, Any, List, Callable, Union

from rich.console import Console
from rich.panel import Panel
from .base import Selection, Option, UIElement, Presentable

# Shared console instance for all Rich output
console = Console()

class Menu(Selection):
    """
    TUI implementation of the abstract Selection concept.
    Encapsulates state management, boilerplate loops, and UI logic.
    """
    def __init__(self, title: str, style: str = "bold blue"):
        super().__init__(title, style)
        self.callbacks: Dict[str, Callable] = {}
        self._items: List[Dict[str, Any]] = []

    def add_option(self, label: str, value_or_callback: Any = None, description: str = ""):
        """
        Adds a menu option. 
        If a callable is passed, it's stored as a callback and executed automatically in run().
        If a string/value is passed, run() returns it upon selection.
        """
        if callable(value_or_callback):
            action_id = f"cb_{uuid.uuid4().hex[:8]}"
            self.callbacks[action_id] = value_or_callback
            self._items.append({"title": label, "value": action_id, "description": description})
        else:
            self._items.append({"title": label, "value": value_or_callback, "description": description})

    def add_item(self, label: str, action_or_callback: Any):
        """Backward compatibility alias for add_option."""
        self.add_option(label, action_or_callback)

    def add_submenu(self, label: str, submenu: Selection, behavior: str = "push"):
        """
        Adds a nested selection. 
        'push': Standard drill-down (opens a new menu).
        'inline': Flattens submenu options into the current menu with indentation.
        """
        if behavior == "push":
            self.add_option(label, lambda: submenu.run())
        elif behavior == "inline":
            # For inline, we store a reference to the submenu object
            # and handle it during formatting in the run loop.
            self._items.append({
                "title": label, 
                "value": submenu, 
                "behavior": "inline", 
                "is_submenu": True,
                "expanded": False
            })

    def add_back_item(self, label: str = "[Back]"):
        """Convenience method to add a standard Back option."""
        self.add_option(label, "back")

    def add_quit_item(self, label: str = "[Quit]"):
        """
        Convenience method to add a standard Quit option.
        
        Args:
            label: Custom text for the quit option. Defaults to "[Quit]".
                  Custom labels are fully supported. If the label exactly matches
                  a special keyword (e.g., "quit", "[quit]") when lowercased,
                  it will automatically receive the 'q' hotkey via format_menu_choices.
                  Otherwise, the item will still function correctly but may not
                  have the automatic hotkey.
        """
        self.add_option(label, "quit")

    def _get_flattened_items(self) -> List[Dict[str, Any]]:
        """Flatten inline submenus if they are expanded."""
        flat_list = []
        for item in self._items:
            flat_list.append(item)
            if item.get("is_submenu") and item.get("behavior") == "inline" and item.get("expanded"):
                submenu = item["value"]
                # Recursively get items from submenu
                sub_items = submenu._get_flattened_items() if isinstance(submenu, Menu) else []
                for sub_item in sub_items:
                    indented_item = sub_item.copy()
                    indented_item["title"] = f"  {sub_item['title']}"
                    flat_list.append(indented_item)
        return flat_list

    def _get_callback(self, action_id: str) -> Optional[Callable]:
        """Resolve a callback ID, searching through inline submenus if necessary."""
        if action_id in self.callbacks:
            return self.callbacks[action_id]
        
        # Search in inline submenus
        for item in self._items:
            if item.get("is_submenu") and item.get("behavior") == "inline":
                submenu = item["value"]
                if isinstance(submenu, Menu):
                    cb = submenu._get_callback(action_id)
                    if cb:
                        return cb
        return None

    def run(self, loop: bool = True) -> Any:
        """
        Starts the interactive menu interaction.
        Returns the selected value (or 'quit'/'back' for standard items).
        """
        while True:
            console.print(Panel(self.title, style=self.style))
            
            # Flatten items for display (handles inline expansion)
            display_items = self._get_flattened_items()
            
            choices = format_menu_choices(display_items, title_field="title", value_field="value")
            selection = prompt_toolkit_menu(choices)
            
            if selection is None: # Esc or Ctrl-C
                return "quit"

            # Handle inline submenu toggle
            if isinstance(selection, Menu):
                for item in self._items:
                    if item.get("value") == selection:
                        item["expanded"] = not item.get("expanded")
                        break
                continue

            # Execute callback if it exists (resolving via hierarchy)
            callback = self._get_callback(str(selection))
            if callback:
                res = callback()
                if not loop:
                    return selection # Return the ID that triggered the callback
                continue
            
            # Standard navigation handling
            if selection in ["quit", "exit"]:
                return "quit"
            if selection == "back":
                return "back"
                
            if not loop:
                return selection

    def present(self) -> Any:
        """Presentable implementation."""
        return self.run()


class PanelSection(UIElement):
    """
    High-level presentable section rendered as a Rich Panel in the TUI implementation.
    """
    def __init__(self, title: str, body: str, style: str = "bold"):
        super().__init__(title=title, style=style)
        self.body = body

    def present(self) -> Any:
        console.print(Panel(self.body, title=self.title, style=self.style))


class CodeBlock(UIElement):
    """
    High-level code snippet presentable.
    In the TUI implementation, this uses `rich.syntax.Syntax` (optionally wrapped in a Panel).
    """
    def __init__(self, code: str, language: str = "text", title: str = "", style: str = ""):
        super().__init__(title=title, style=style)
        self.code = code
        self.language = language

    def present(self) -> Any:
        try:
            from rich.syntax import Syntax
        except ImportError:
            # Fallback: plain text if Syntax is unavailable
            console.print(self.code)
            return

        syntax = Syntax(self.code, self.language, theme="monokai", line_numbers=False)
        if self.title or self.style:
            console.print(Panel(syntax, title=self.title or None, style=self.style or ""))
        else:
            console.print(syntax)


class DataTable(UIElement):
    """
    High-level table presentable.
    In the TUI implementation, this uses `rich.table.Table` (optionally wrapped in a Panel).
    """
    def __init__(self, columns: List[str], rows: List[List[Any]], title: str = "", style: str = ""):
        super().__init__(title=title, style=style)
        self.columns = columns
        self.rows = rows

    def present(self) -> Any:
        try:
            from rich.table import Table
        except ImportError:
            # Fallback: simple text rendering if Table is unavailable
            header = " | ".join(self.columns)
            console.print(header)
            console.print("-" * len(header))
            for row in self.rows:
                console.print(" | ".join(str(c) for c in row))
            return

        table = Table(title=self.title or None)
        for col in self.columns:
            table.add_column(str(col))
        for row in self.rows:
            table.add_row(*[str(c) for c in row])

        if self.style:
            console.print(Panel(table, style=self.style))
        else:
            console.print(table)

def prompt_yes_no(message: str, default: bool = False) -> bool:
    """Standardize yes/no confirmation prompts."""
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        choice = input(f"{message} {suffix}: ").strip().lower()
        if choice == "":
            return default
        if choice in {"y", "yes"}:
            return True
        if choice in {"n", "no"}:
            return False
        console.print("[yellow]Please enter y or n.[/yellow]")

def copy_to_clipboard(text: str) -> bool:
    """Copy text to the system clipboard across different platforms."""
    try:
        if shutil.which("pbcopy"):
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
            return True
        elif shutil.which("xclip"):
            subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, check=True)
            return True
        elif shutil.which("xsel"):
            subprocess.run(["xsel", "--clipboard", "--input"], input=text, text=True, check=True)
            return True
    except Exception:
        pass
    return False

def prompt_toolkit_menu(choices, style=None, hotkeys=None, default_idx=0):
    """Interactive selection menu supporting arrow keys and immediate hotkeys."""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.application import Application
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window, HSplit
    from prompt_toolkit.layout.controls import FormattedTextControl
    
    idx = max(0, min(default_idx, len(choices) - 1)) if choices else 0
    kb = KeyBindings()

    @kb.add('up')
    def _(event):
        nonlocal idx
        idx = (idx - 1) % len(choices)

    @kb.add('down')
    def _(event):
        nonlocal idx
        idx = (idx + 1) % len(choices)

    @kb.add('enter')
    def _(event):
        event.app.exit(result=choices[idx].value)

    @kb.add('escape')
    @kb.add('c-c')
    def _(event):
        event.app.exit(result=None)

    def make_hotkey_handler(h_val):
        keys = list(str(h_val))
        @kb.add(*keys)
        def _(event):
            for choice in choices:
                clean_title = choice.title.strip().lower()
                title_prefix = clean_title.split('.')[0].strip().lstrip('0')
                h_prefix = str(h_val).lstrip('0')
                
                if clean_title.startswith(f"{h_val}."):
                    event.app.exit(result=choice.value)
                    return
                if title_prefix == h_prefix and title_prefix != "":
                    event.app.exit(result=choice.value)
                    return
                if str(choice.value) == str(h_val):
                    event.app.exit(result=choice.value)
                    return
        return _

    if not hotkeys:
        hotkeys = []
        for choice in choices:
            title = choice.title.strip()
            if '.' in title:
                prefix = title.split('.')[0].strip().lower()
                if prefix:
                    hotkeys.append(prefix)
                    if prefix.isdigit() and prefix.startswith('0') and prefix != '0':
                        hotkeys.append(prefix.lstrip('0'))
        if not hotkeys:
            hotkeys = [str(i) for i in range(1, 10)] + ['q', 'b']
    
    seen = set()
    unique_hotkeys = []
    for h in hotkeys:
        if h not in seen:
            unique_hotkeys.append(h)
            seen.add(h)

    for h in unique_hotkeys:
        make_hotkey_handler(h)

    def get_text():
        result = []
        for i, choice in enumerate(choices):
            if i == idx:
                result.append(('class:selected', f" » {choice.title}\n"))
            else:
                result.append(('', f"   {choice.title}\n"))
        return result

    layout = Layout(HSplit([
        Window(content=FormattedTextControl(get_text)),
    ]))

    from prompt_toolkit.styles import Style
    if not style:
        style = Style([('selected', 'fg:#cc9900')])

    app = Application(layout=layout, key_bindings=kb, style=style, full_screen=False)
    return app.run()

def format_menu_choices(items: List[Any], title_field: Optional[str] = None, value_field: Optional[str] = None) -> List[Any]:
    """Prepare a list of items for `prompt_toolkit_menu` by adding index numbers and hotkeys."""
    import questionary
    
    special_keywords = {
        "quit": "q", "[quit]": "q", "(q) [quit]": "q", "q": "q",
        "back": "b", "[back]": "b", "(b) [back]": "b", "b": "b",
        "menu": "m", "[menu]": "m", "return to menu": "m"
    }
    
    regular_items = []
    special_items = []
    
    for item in items:
        title = ""
        if isinstance(item, dict):
            if title_field: title = str(item.get(title_field))
            elif "title" in item: title = str(item["title"])
            else: title = str(item)
        else:
            title = str(item)
            
        lower_title = title.strip().lower()
        if lower_title in special_keywords:
            special_items.append((item, special_keywords[lower_title]))
        else:
            regular_items.append(item)
            
    pad = len(str(len(regular_items)))
    choices = []
    
    for i, item in enumerate(regular_items, 1):
        idx_str = str(i).zfill(pad)
        if isinstance(item, dict):
            title = item.get(title_field) if title_field else item.get("title", str(item))
            value = item.get(value_field) if value_field else item.get("value", item)
        else:
            title = str(item)
            value = item
        choices.append(questionary.Choice(f"{idx_str}. {title}", value=value))
        
    for item, key in special_items:
        if isinstance(item, dict):
            title = item.get(title_field) if title_field else item.get("title", str(item))
            value = item.get(value_field) if value_field else item.get("value", item)
        else:
            title = str(item)
            value = item
        choices.append(questionary.Choice(f"{' ' * (pad - 1)}{key}. {title}", value=value))
        
    return choices
