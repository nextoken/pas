# PPUI TODOs

## Backend Abstraction

- [ ] **IntentUI factory**: Introduce a high-level `IntentUI` API that chooses the appropriate backend (TUI, Web, Voice) for user interactions.
  - Example usage:
    ```python
    from ppui import IntentUI

    ui = IntentUI(backend="tui")  # default backend
    menu = ui.choices("Project Actions", style="bold cyan")
    menu.add_option("Deploy to Staging", deploy_staging)
    menu.add_option("Deploy to Prod", deploy_prod)
    selection = menu.run()
    ```
  - Backend should default to `"tui"` for now, with a clear extension point for `"web"` and `"voice"`.
  - Internally, `IntentUI.choices(...)` should construct a backend-specific `Selection`/`Menu` implementation.

