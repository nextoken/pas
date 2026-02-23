"""
PPUI TUI System Helpers (Proxy for ppui subpackage)
"""

import sys
from pathlib import Path

try:
    from ppui import (
        choice,
        console,
        prompt_yes_no,
        prompt_toolkit_menu,
        format_menu_choices,
        copy_to_clipboard,
        Menu,
    )
except ImportError:
    # Fallback: If ppui isn't installed in the environment,
    # inject its source path into sys.path to ensure PAS remains "zero-setup".
    lib_path = Path(__file__).resolve().parent.parent / "libs" / "ppui" / "src"
    if lib_path.exists() and str(lib_path) not in sys.path:
        sys.path.append(str(lib_path))

    from ppui import (
        choice,
        console,
        prompt_yes_no,
        prompt_toolkit_menu,
        format_menu_choices,
        copy_to_clipboard,
        Menu,
    )
