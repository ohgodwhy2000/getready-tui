"""
Platform detection and install-command construction, shared by any
front end (TUI today, could back a CLI/script tomorrow).
"""

from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path

from .catalog import CATALOG, App

FORMAT_VERSION = 1

MANAGER_NAMES: dict[str, str] = {
    "apt": "APT (Debian / Ubuntu)",
    "dnf": "DNF (Fedora / RHEL)",
    "pacman": "Pacman (Arch Linux)",
    "flatpak": "Flatpak / Flathub (Linux)",
    "winget": "winget (Windows)",
    "choco": "Chocolatey (Windows)",
}

UPDATE_COMMANDS: dict[str, list[str]] = {
    "apt": ["sudo", "apt", "update"],
    "dnf": ["sudo", "dnf", "check-update"],
    "pacman": ["sudo", "pacman", "-Syu", "--noconfirm"],
}

# Native managers are preferred over flatpak; listed in a sensible
# priority order so that best_manager_for() can pick the best one.
MANAGER_PRIORITY: list[str] = ["apt", "dnf", "pacman", "winget", "choco", "flatpak"]


def detect_available_managers() -> list[str]:
    """Return package managers that are both relevant to this OS and
    actually installed/on PATH."""
    system = platform.system()
    if system == "Linux":
        candidates = ["apt", "dnf", "pacman", "flatpak"]
    else:
        candidates = ["winget", "choco"]
    return [m for m in candidates if shutil.which(m) is not None]


def best_manager_for(app: App, available: list[str]) -> str | None:
    """Pick the best available manager for a single app.  Native
    managers are always preferred over flatpak."""
    for m in MANAGER_PRIORITY:
        if m in available and app.package_for(m):
            return m
    return None


def resolve_multi(
    apps: list[App], available: list[str]
) -> tuple[list[tuple[App, str]], list[App]]:
    """Resolve every app to its best available manager.

    Returns (installable, unsupported) where installable is a list of
    (app, manager) tuples and unsupported lists apps that have no
    package for any of the available managers.
    """
    installable: list[tuple[App, str]] = []
    unsupported: list[App] = []
    for app in apps:
        mgr = best_manager_for(app, available)
        if mgr is not None:
            installable.append((app, mgr))
        else:
            unsupported.append(app)
    return installable, unsupported


def get_update_command(manager: str) -> list[str] | None:
    return UPDATE_COMMANDS.get(manager)


def build_install_commands(
    manager: str, installable: list[App]
) -> list[tuple[App, list[str]]]:
    """Return ``(app, command)`` pairs to run, in order.

    *apt* and *winget* install one app at a time so that a missing
    package does not block the rest; the others batch every package
    into a single invocation (all apps in the batch share the same
    command object).  Apps without a package ID for *manager* are
    silently omitted — callers must not attribute results to them.
    """
    pkg_map = [
        (app, app.package_for(manager))
        for app in installable
        if app.package_for(manager)
    ]

    if manager == "winget":
        return [
            (
                app,
                [
                    "winget",
                    "install",
                    "--id",
                    pkg,
                    "-e",
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                ],
            )
            for app, pkg in pkg_map
        ]
    if manager == "apt":
        return [(app, ["sudo", "apt", "install", "-y", pkg]) for app, pkg in pkg_map]

    # Batch managers — single command for every qualifying package.
    pkg_ids = [pkg for _, pkg in pkg_map]
    apps = [app for app, _ in pkg_map]
    if not pkg_ids:
        return []

    if manager == "choco":
        cmd = ["choco", "install", "-y", *pkg_ids]
    elif manager == "dnf":
        cmd = ["sudo", "dnf", "install", "-y", *pkg_ids]
    elif manager == "pacman":
        cmd = ["sudo", "pacman", "-S", "--noconfirm", *pkg_ids]
    elif manager == "flatpak":
        cmd = ["flatpak", "install", "-y", "flathub", *pkg_ids]
    else:
        return []

    return [(app, cmd) for app in apps]


def build_install_plan(
    resolved: list[tuple[App, str]],
) -> dict[str, list[App]]:
    """Group resolved (app, manager) pairs into a plan keyed by manager.
    Each group is a list of apps to install via that manager."""
    plan: dict[str, list[App]] = {}
    for app, mgr in resolved:
        plan.setdefault(mgr, []).append(app)
    return plan


# --------------------------------------------------------------------------
# Import / export
#
# Selections are saved as plain JSON: a list of fully self-contained app
# records (name + every manager's package id, plus whether the app was
# custom). Import always re-resolves against the *current* catalog rather
# than trusting old data blindly, and falls back to the record's own
# package ids for anything the catalog no longer recognizes — so editing,
# renaming, or trimming CATALOG later can never make an old export file
# crash or silently vanish an app.
# --------------------------------------------------------------------------


def catalog_index() -> dict[str, App]:
    """Fresh name -> App lookup built from the live catalog."""
    return {app.name: app for apps in CATALOG.values() for app in apps}


def serialize_app(app: App) -> dict:
    return {
        "name": app.name,
        "custom": app.custom,
        "apt": app.apt,
        "dnf": app.dnf,
        "pacman": app.pacman,
        "flatpak": app.flatpak,
        "winget": app.winget,
        "choco": app.choco,
    }


def build_export(apps: list[App], managers: list[str] | None) -> dict:
    return {
        "format_version": FORMAT_VERSION,
        "managers": managers or [],
        "apps": [serialize_app(a) for a in apps],
    }


def _clean_str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def parse_import(data: object) -> tuple[list[App], list[str]]:
    """Turn parsed JSON into a list of Apps, tolerating malformed or
    stale data. Never raises for bad *entries* — only for a file that
    isn't shaped like a GetReady export at all. Returns (apps, warnings)."""
    warnings: list[str] = []

    if not isinstance(data, dict):
        raise ValueError(
            "That file isn't a GetReady selection (expected a JSON object)."
        )

    if "format_version" not in data:
        warnings.append(
            "File has no format_version - importing on a best-effort basis."
        )

    apps_data = data.get("apps")
    if not isinstance(apps_data, list):
        raise ValueError("That file has no 'apps' list to import.")

    live_catalog = catalog_index()
    resolved: list[App] = []
    seen: set[str] = set()

    for i, entry in enumerate(apps_data):
        if not isinstance(entry, dict):
            warnings.append(f"Entry {i + 1}: skipped (not a valid app record).")
            continue

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            warnings.append(f"Entry {i + 1}: skipped (missing a name).")
            continue
        name = name.strip()

        if name in seen:
            continue
        seen.add(name)

        catalog_app = live_catalog.get(name)
        was_custom = bool(entry.get("custom", False))

        if catalog_app is not None and not was_custom:
            # Trust the live catalog's package ids over whatever was
            # saved, in case they've since been updated.
            resolved.append(catalog_app)
            continue

        restored = App(
            name=name,
            apt=_clean_str(entry.get("apt")),
            dnf=_clean_str(entry.get("dnf")),
            pacman=_clean_str(entry.get("pacman")),
            flatpak=_clean_str(entry.get("flatpak")),
            winget=_clean_str(entry.get("winget")),
            choco=_clean_str(entry.get("choco")),
            custom=True,
        )

        if not any(
            (
                restored.apt,
                restored.dnf,
                restored.pacman,
                restored.flatpak,
                restored.winget,
                restored.choco,
            )
        ):
            warnings.append(f"'{name}': skipped (no package data in file or catalog).")
            continue

        if catalog_app is None and not was_custom:
            # This used to be a catalog app but the catalog no longer
            # recognizes it (renamed/removed) — restored straight from
            # the file's own saved package ids.
            warnings.append(
                f"'{name}': not in the current catalog - restored from file."
            )

        resolved.append(restored)

    return resolved, warnings


def save_selection(path: Path, apps: list[App], managers: list[str] | None) -> None:
    data = build_export(apps, managers)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_selection(path: Path) -> tuple[list[App], list[str]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Couldn't read '{path}': {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"'{path}' isn't valid JSON: {e}") from e
    return parse_import(data)
