"""
GetReady TUI
============
A terminal UI for selecting and installing a batch of apps via apt,
dnf, pacman, flatpak, winget, or chocolatey — built on Textual.
Multiple package managers can be used simultaneously; each app is
automatically resolved to the best available manager.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import Optional

from textual import on
from textual.app import App as TextualApp, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    SelectionList,
    Static,
    Switch,
)
from textual.widgets.selection_list import Selection

from .catalog import CATALOG
from .catalog import App as CatalogApp
from .core import (
    MANAGER_NAMES,
    build_install_commands,
    build_install_plan,
    detect_available_managers,
    get_update_command,
    load_selection,
    best_manager_for,
    resolve_multi,
    save_selection,
)


# --------------------------------------------------------------------------
# Modal: add a custom app
# --------------------------------------------------------------------------


MANAGER_SHORT: dict[str, str] = {
    "apt": "APT",
    "dnf": "DNF",
    "pacman": "pacman",
    "flatpak": "Flatpak",
    "winget": "winget",
    "choco": "Choco",
}


class AddCustomAppScreen(ModalScreen[Optional[CatalogApp]]):
    """Prompts for a display name + package id for the active manager."""

    DEFAULT_CSS = """
    AddCustomAppScreen {
        align: center middle;
    }
    #dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #dialog Label {
        margin-top: 1;
    }
    #dialog-buttons {
        margin-top: 1;
        height: auto;
        align: right middle;
    }
    #dialog-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, managers: list[str]) -> None:
        super().__init__()
        self.managers = managers

    def compose(self) -> ComposeResult:
        mgr_label = (
            MANAGER_NAMES[self.managers[0]] if self.managers else "a package manager"
        )
        with Vertical(id="dialog"):
            yield Label("[b]Add a custom app[/b]")
            yield Label("Display name")
            yield Input(placeholder="e.g. My Cool App", id="name-input")
            yield Label(f"Package id / namespace for {mgr_label}")
            yield Input(
                placeholder="e.g. Publisher.AppName or package-name", id="pkg-input"
            )
            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="dialog-cancel")
                yield Button("Add", id="dialog-add", variant="success")

    @on(Button.Pressed, "#dialog-add")
    def add(self) -> None:
        name = self.query_one("#name-input", Input).value.strip()
        pkg = self.query_one("#pkg-input", Input).value.strip()
        if not name or not pkg or not self.managers:
            self.app.bell()
            return
        kwargs = {"name": name, "custom": True, self.managers[0]: pkg}
        self.dismiss(CatalogApp(**kwargs))

    @on(Button.Pressed, "#dialog-cancel")
    def cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------
# Modal: prompt for a file path (used by both export and import)
# --------------------------------------------------------------------------


class PathPromptScreen(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    PathPromptScreen {
        align: center middle;
    }
    #dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #dialog Label {
        margin-top: 1;
    }
    #dialog-buttons {
        margin-top: 1;
        height: auto;
        align: right middle;
    }
    #dialog-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, title: str, default_path: str, action_label: str) -> None:
        super().__init__()
        self.title_text = title
        self.default_path = default_path
        self.action_label = action_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"[b]{self.title_text}[/b]")
            yield Label("File path")
            yield Input(value=self.default_path, id="path-input")
            with Horizontal(id="dialog-buttons"):
                yield Button("Cancel", id="dialog-cancel")
                yield Button(self.action_label, id="dialog-ok", variant="success")

    @on(Button.Pressed, "#dialog-ok")
    def confirm(self) -> None:
        path = self.query_one("#path-input", Input).value.strip()
        if not path:
            self.app.bell()
            return
        self.dismiss(path)

    @on(Button.Pressed, "#dialog-cancel")
    def cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------
# Modal: confirm before installing
# --------------------------------------------------------------------------


class ConfirmInstallScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmInstallScreen {
        align: center middle;
    }
    #confirm-dialog {
        width: 70;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    #confirm-buttons {
        margin-top: 1;
        height: auto;
        align: right middle;
    }
    #confirm-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        plan: dict[str, list[CatalogApp]],
        unsupported: list[CatalogApp],
    ) -> None:
        super().__init__()
        self.plan = plan
        self.unsupported = unsupported

    def compose(self) -> ComposeResult:
        total = sum(len(apps) for apps in self.plan.values())
        with VerticalScroll(id="confirm-dialog"):
            yield Label(
                f"[b]Install {total} app(s) via {len(self.plan)} manager(s)?[/b]"
            )
            for mgr, apps in self.plan.items():
                yield Label(f"\n[b]{MANAGER_NAMES[mgr]}[/b]")
                for app in apps:
                    pkg = app.package_for(mgr)
                    yield Label(f"  [green]+[/green] {app.name}  [dim]({pkg})[/dim]")
            if self.unsupported:
                yield Label(
                    "\n[yellow]Skipping (no package for any enabled manager):[/yellow]"
                )
                for app in self.unsupported:
                    yield Label(f"  [dim]- {app.name}[/dim]")
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", id="confirm-no")
                yield Button("Install", id="confirm-yes", variant="success")

    @on(Button.Pressed, "#confirm-yes")
    def yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-no")
    def no(self) -> None:
        self.dismiss(False)


# --------------------------------------------------------------------------
# Main app
# --------------------------------------------------------------------------


class GetReadyApp(TextualApp):
    """GetReady TUI — pick apps, pick managers, install."""

    TITLE = "GetReady TUI"
    SUB_TITLE = "Batch app installer"

    CSS = """
    #status {
        padding: 0 1;
        color: $text-muted;
    }
    #search-row {
        height: auto;
        padding: 0 1;
        margin: 0 1;
    }
    #search-row Input {
        width: 100%;
    }
    #manager-select {
        height: auto;
        padding: 0 1;
        border: round $accent;
        margin: 0 1;
    }
    #manager-select Label {
        width: auto;
    }
    #manager-select Switch {
        width: auto;
        margin: 0 1 0 0;
    }
    #app-list {
        height: 1fr;
        margin: 0 1;
        border: round $primary;
    }
    #button-row {
        height: auto;
        padding: 1;
        align: left middle;
    }
    #button-row Button {
        margin-right: 1;
    }
    #log {
        height: 12;
        margin: 0 1 1 1;
        border: round $secondary;
        display: none;
    }
    #log.-visible {
        display: block;
    }
    """

    BINDINGS = [
        ("q", "request_quit", "Quit"),
        ("a", "select_all", "Select all"),
        ("x", "clear_all", "Clear all"),
        ("n", "add_custom", "Add custom app"),
        ("e", "export_selection", "Export"),
        ("o", "import_selection", "Import"),
        ("i", "install", "Install selected"),
        ("s", "focus_search", "Search"),
        ("escape", "clear_search", "Clear search"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.available_managers = detect_available_managers()
        self.enabled_managers: list[str] = list(self.available_managers)
        self.apps_by_value: dict[str, CatalogApp] = {}
        self._extra_apps: dict[str, CatalogApp] = {}
        self._custom_counter = 0
        self._search_term: str = ""
        self._selected_count: int = 0

    # -- composition ---------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(self._status_text(), id="status")
        with Horizontal(id="search-row"):
            yield Input(
                placeholder="Type to filter apps...  (s to focus, Esc to clear)",
                id="search-input",
            )
        if len(self.available_managers) > 1:
            with Horizontal(id="manager-select"):
                for m in self.available_managers:
                    yield Switch(
                        MANAGER_NAMES[m],
                        value=True,
                        id=f"mgr-{m}",
                    )
        yield SelectionList[str](
            *self._build_selections(self.enabled_managers), id="app-list"
        )
        with Horizontal(id="button-row"):
            yield Button("Add Custom App (n)", id="add-custom", variant="primary")
            yield Button("Select All (a)", id="select-all")
            yield Button("Clear All (x)", id="clear-all")
            yield Button("Export (e)", id="export-selection")
            yield Button("Import (o)", id="import-selection")
            yield Button("Install Selected (i)", id="install", variant="success")
            yield Button("Quit (q)", id="quit", variant="error")
        yield RichLog(id="log", highlight=True, markup=True, wrap=True)
        yield Footer()

    def _status_text(self) -> str:
        if not self.enabled_managers:
            return (
                f"OS: {platform.system()}  |  "
                "No package managers enabled. Toggle one above."
            )
        names = ", ".join(MANAGER_SHORT[m] for m in self.enabled_managers)
        return f"OS: {platform.system()}  |  Managers: {names}  |  {self._selected_count} selected"

    def _build_selections(self, managers: list[str]) -> list[Selection]:
        selections: list[Selection] = []
        for category, apps in CATALOG.items():
            selections.append(
                Selection(
                    f"── {category} ──", f"__header__{category}", False, disabled=True
                )
            )
            for app in apps:
                self.apps_by_value[app.name] = app
                mgr = best_manager_for(app, managers)
                if mgr:
                    label = f"{app.name}  [dim]({MANAGER_SHORT[mgr]})[/dim]"
                else:
                    label = f"{app.name}  [dim](no package)[/dim]"
                selections.append(Selection(label, app.name, False))
        if self._extra_apps:
            selections.append(
                Selection("── Custom ──", "__header__Custom", False, disabled=True)
            )
            for name, app in self._extra_apps.items():
                self.apps_by_value[name] = app
                selections.append(
                    Selection(f"{app.name}  [dim](custom)[/dim]", name, False)
                )
        return selections

    def _build_filtered_selections(self) -> list[Selection]:
        """Build selections filtered by the current search term."""
        all_sels = self._build_selections(self.enabled_managers)
        if not self._search_term:
            return all_sels
        term = self._search_term.lower()
        filtered: list[Selection] = []
        current_category: str | None = None
        category_apps: list[Selection] = []
        for sel in all_sels:
            if sel.value.startswith("__header__"):
                # Flush previous category
                if category_apps and current_category:
                    header = Selection(
                        f"── {current_category} ──",
                        f"__header__{current_category}",
                        False,
                        disabled=True,
                    )
                    filtered.append(header)
                    filtered.extend(category_apps)
                current_category = sel.value[len("__header__") :]
                category_apps = []
            else:
                # Match against app name (case-insensitive)
                app_name = sel.value if isinstance(sel.value, str) else ""
                if app_name and term in app_name.lower():
                    category_apps.append(sel)
        # Flush last category
        if category_apps and current_category:
            header = Selection(
                f"── {current_category} ──",
                f"__header__{current_category}",
                False,
                disabled=True,
            )
            filtered.append(header)
            filtered.extend(category_apps)
        return filtered

    def _rebuild_selection_list(self) -> None:
        """Rebuild the selection list, preserving current selections."""
        selection_list = self.query_one("#app-list", SelectionList)
        previously_selected = set(selection_list.selected)
        selection_list.clear_options()
        for sel in self._build_filtered_selections():
            selection_list.add_option(sel)
        for value in previously_selected:
            if value in self.apps_by_value:
                selection_list.select(value)
        self._selected_count = len(selection_list.selected)
        self.query_one("#status", Static).update(self._status_text())

    # -- helpers ---------------------------------------------------------

    def log_line(self, text: str) -> None:
        log = self.query_one("#log", RichLog)
        log.add_class("-visible")
        log.write(text)

    # -- manager selection -------------------------------------------------

    @on(Switch.Changed, "[id^='mgr-']")
    def manager_toggled(self, event: Switch.Changed) -> None:
        # Extract manager id from the switch id ("mgr-apt" -> "apt")
        switch_id = event.switch.id
        if switch_id and switch_id.startswith("mgr-"):
            mgr = switch_id[4:]
            if event.switch.value:
                if mgr not in self.enabled_managers:
                    self.enabled_managers.append(mgr)
            else:
                if mgr in self.enabled_managers:
                    self.enabled_managers.remove(mgr)
        self._rebuild_selection_list()

    @on(Input.Changed, "#search-input")
    def search_changed(self, event: Input.Changed) -> None:
        self._search_term = event.value.strip()
        self._rebuild_selection_list()

    @on(SelectionList.SelectionToggled)
    def selection_toggled(self) -> None:
        self._selected_count = len(self.query_one("#app-list", SelectionList).selected)
        self.query_one("#status", Static).update(self._status_text())

    # -- button / binding actions -----------------------------------------

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_clear_search(self) -> None:
        search = self.query_one("#search-input", Input)
        if search.value:
            search.value = ""
            self._search_term = ""
            self._rebuild_selection_list()
        else:
            search.blur()

    @on(Button.Pressed, "#add-custom")
    def action_add_custom(self) -> None:
        if not self.enabled_managers:
            self.notify(
                "No package managers enabled. Toggle one above.", severity="error"
            )
            return

        def handle_result(result: Optional[CatalogApp]) -> None:
            if result is None:
                return
            value = result.name
            if value in self.apps_by_value:
                self._custom_counter += 1
                value = f"{result.name} (custom {self._custom_counter})"
            result.name = value
            self._extra_apps[value] = result
            self._rebuild_selection_list()
            self.notify(f"Added custom app: {result.name}")

        self.push_screen(AddCustomAppScreen(self.enabled_managers), handle_result)

    @on(Button.Pressed, "#select-all")
    def action_select_all(self) -> None:
        sl = self.query_one("#app-list", SelectionList)
        sl.select_all()
        self._selected_count = len(sl.selected)
        self.query_one("#status", Static).update(self._status_text())

    @on(Button.Pressed, "#clear-all")
    def action_clear_all(self) -> None:
        sl = self.query_one("#app-list", SelectionList)
        sl.deselect_all()
        self._selected_count = 0
        self.query_one("#status", Static).update(self._status_text())

    @on(Button.Pressed, "#export-selection")
    def action_export_selection(self) -> None:
        selection_list = self.query_one("#app-list", SelectionList)
        selected = [v for v in selection_list.selected if v in self.apps_by_value]
        if not selected:
            self.notify("Nothing selected to export.", severity="warning")
            return
        apps = [self.apps_by_value[v] for v in selected]

        def handle_path(path_str: Optional[str]) -> None:
            if not path_str:
                return
            try:
                save_selection(Path(path_str).expanduser(), apps, self.enabled_managers)
            except OSError as e:
                self.notify(f"Couldn't write file: {e}", severity="error")
                return
            self.notify(f"Exported {len(apps)} app(s) to {path_str}")

        self.push_screen(
            PathPromptScreen("Export selection", "getready-selection.json", "Save"),
            handle_path,
        )

    @on(Button.Pressed, "#import-selection")
    def action_import_selection(self) -> None:
        def handle_path(path_str: Optional[str]) -> None:
            if not path_str:
                return

            # load_selection is defensive end-to-end: bad JSON, a wrong
            # shape, or entries that no longer match the catalog all
            # come back as (apps, warnings) rather than an exception —
            # except for a file that isn't a GetReady export at all,
            # which raises ValueError with a human-readable reason.
            try:
                resolved, warnings = load_selection(Path(path_str).expanduser())
            except ValueError as e:
                self.notify(str(e), severity="error")
                return
            except Exception as e:  # never let a bad file take the TUI down
                self.notify(f"Import failed: {e}", severity="error")
                return

            added = 0
            catalog_names = {app.name for apps in CATALOG.values() for app in apps}
            for app in resolved:
                if app.name not in catalog_names and app.name not in self._extra_apps:
                    self._extra_apps[app.name] = app
                    added += 1

            self._rebuild_selection_list()

            selection_list = self.query_one("#app-list", SelectionList)
            for app in resolved:
                selection_list.select(app.name)

            for w in warnings:
                self.log_line(f"[yellow]![/yellow] {w}")

            msg = f"Imported {len(resolved)} app(s) ({added} new)."
            if warnings:
                msg += f" {len(warnings)} note(s) - see log panel."
            self.notify(msg, severity="warning" if warnings else "information")

        self.push_screen(
            PathPromptScreen("Import selection", "getready-selection.json", "Load"),
            handle_path,
        )

    @on(Button.Pressed, "#quit")
    def action_request_quit(self) -> None:
        self.exit()

    @on(Button.Pressed, "#install")
    def action_install(self) -> None:
        if not self.enabled_managers:
            self.notify(
                "No package managers enabled. Toggle one above.", severity="error"
            )
            return

        selection_list = self.query_one("#app-list", SelectionList)
        selected_values = list(selection_list.selected)
        apps = [
            self.apps_by_value[v] for v in selected_values if v in self.apps_by_value
        ]

        if not apps:
            self.notify("No apps selected.", severity="warning")
            return

        installable, unsupported = resolve_multi(apps, self.enabled_managers)
        plan = build_install_plan(installable)

        def handle_confirm(confirmed: bool) -> None:
            if confirmed:
                self.run_install(plan)

        self.push_screen(
            ConfirmInstallScreen(plan, unsupported),
            handle_confirm,
        )

    # -- install ------------------------------------------------------------

    def run_install(self, plan: dict[str, list[CatalogApp]]) -> None:
        if not plan:
            self.notify("Nothing installable was selected.", severity="warning")
            return

        results: dict[str, bool] = {}

        # Suspend the TUI so package managers (which may prompt for a
        # sudo password, or print progress bars) get a real terminal.
        with self.suspend():
            print("\n" + "=" * 60)
            print("GetReady TUI - installing packages")
            print("=" * 60)

            for mgr, apps in plan.items():
                print(f"\n--- {MANAGER_NAMES[mgr]} ({len(apps)} app(s)) ---")

                update_cmd = get_update_command(mgr)
                if update_cmd:
                    print(f"\n$ {' '.join(update_cmd)}")
                    subprocess.run(update_cmd)

                cmds = build_install_commands(mgr, apps)

                if mgr == "winget":
                    for app, cmd in zip(apps, cmds):
                        print(f"\n-- {app.name} --")
                        print(f"$ {' '.join(cmd)}")
                        proc = subprocess.run(cmd)
                        results[app.name] = proc.returncode == 0
                else:
                    overall_ok = True
                    for cmd in cmds:
                        print(f"\n$ {' '.join(cmd)}")
                        proc = subprocess.run(cmd)
                        if proc.returncode != 0:
                            overall_ok = False
                    for app in apps:
                        results[app.name] = overall_ok

            print("\nDone. Press Enter to return to GetReady TUI...")
            input()

        self.show_install_summary(plan, results)

    def show_install_summary(
        self, plan: dict[str, list[CatalogApp]], results: dict[str, bool]
    ) -> None:
        all_apps = [a for apps in plan.values() for a in apps]
        succeeded = [a.name for a in all_apps if results.get(a.name)]
        failed = [a.name for a in all_apps if not results.get(a.name)]

        self.log_line("[b]Install finished[/b]")
        for mgr, apps in plan.items():
            self.log_line(f"  [b]{MANAGER_NAMES[mgr]}[/b]")
            for app in apps:
                if results.get(app.name):
                    self.log_line(f"    [green]\u2713[/green] {app.name}")
                else:
                    self.log_line(f"    [red]\u2717[/red] {app.name}")

        if failed:
            self.notify(
                f"{len(succeeded)} succeeded, {len(failed)} failed.",
                severity="warning",
            )
        else:
            self.notify(
                f"All {len(succeeded)} app(s) installed successfully.",
                severity="information",
            )


def run() -> None:
    GetReadyApp().run()


if __name__ == "__main__":
    run()
