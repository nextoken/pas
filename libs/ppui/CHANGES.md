# Changelog

All notable changes to the `ppui` library will be documented in this file.

## [2026.02.23] - 2026-02-23

### Documentation
- **Shortcut-indexed vs numeric-indexed menus**: Documented when to use `format_menu_choices()` (numeric 01., 02., …) vs passing `questionary.Choice()` directly to `prompt_toolkit_menu()` for shortcut-only menus (q., p., 0–9, b.). Updated docstrings in `format_menu_choices` and `prompt_toolkit_menu`, and README "Notes for AI Coding Agents".

## [2026.01.20.2] - 2026-01-20

### Added
- **Rebranded as PPUI**: Python Process User Intent.
- **Abstract Base Classes**: `UIElement`, `Selection`, `Presentable`, and `Option` in `ppui.base`.
- **Submenu Support**: `add_submenu` to `Menu` with `push` and `inline` behaviors.
- **Migration**: Full feature parity with the former TUI layer.
