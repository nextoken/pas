"""
Base abstractions for the PPUI (Python Process User Intent) system.
"""
from abc import ABC, abstractmethod
from typing import Any, List, Optional, Callable, Dict, Union

class Presentable(ABC):
    """Base class for anything that can be presented to a user (TUI, Web, Voice, etc.)"""
    @abstractmethod
    def present(self) -> Any:
        """Present the element to the user and return the result if applicable."""
        pass

class UIElement(Presentable):
    """A generic UI element."""
    def __init__(self, title: str = "", style: str = ""):
        self.title = title
        self.style = style

class Option:
    """A single choice within a Selection."""
    def __init__(self, label: str, value: Any = None, callback: Optional[Callable] = None, description: str = ""):
        self.label = label
        self.value = value if value is not None else label
        self.callback = callback
        self.description = description

class Selection(UIElement):
    """Abstract concept of providing options for a user to choose from."""
    def __init__(self, title: str = "", style: str = ""):
        super().__init__(title, style)
        self.options: List[Union[Option, 'Selection']] = []
    
    @abstractmethod
    def add_option(self, label: str, value_or_callback: Any = None, description: str = ""):
        """Add a single option to the selection."""
        pass

    @abstractmethod
    def add_submenu(self, label: str, submenu: 'Selection', behavior: str = "push"):
        """Add a nested selection (submenu). behavior can be 'push' or 'inline'."""
        pass

    @abstractmethod
    def run(self, loop: bool = True) -> Any:
        """Start the interaction loop."""
        pass
