# GetReady TUI

A terminal UI for picking a batch of apps and installing them via `apt`, `dnf`, `pacman`, `flatpak`, `winget`, or `chocolatey` — whichever your OS has. Multiple package managers can be used simultaneously; each app is automatically resolved to the best available manager.

## Install

```bash
pip install -e .
```

Or, for the dev/live-reload tooling too:

```bash
pip install -e ".[dev]"
```

## Run

```bash
getready
```

## Using it

- The app list is grouped by category. Use arrow keys / space (or click) to check the apps you want.
- **a** / "Select All" — select everything in the catalog.
- **x** / "Clear All" — deselect everything.
- **n** / "Add Custom App" — add an app that isn't in the catalog by giving it a display name and the package id/namespace for your active package manager (e.g. `Publisher.AppName` for winget, `package-name` for apt/dnf/pacman/choco, or `app.id` for flatpak).
- **Toggle managers** — if multiple package managers are installed (e.g. apt + flatpak on Debian, or pacman + flatpak on Arch), toggle switches appear near the top. Enable any combination; each app automatically resolves to the best available manager (native packages are preferred over flatpak).
- **i** / "Install Selected" — shows a confirmation screen grouping apps by their resolved manager, then runs installs per-manager (update first, then batched install commands). Apps that can't be installed via any enabled manager are skipped.
- **e** / "Export" — saves your current checkbox selection to a JSON file (default `getready-selection.json`). Each entry stores the app's name plus its package id for every manager, so the file is fully self-contained.
- **o** / "Import" — loads a selection file back in and re-checks those apps. Import always resolves names against the _current_ catalog first (so package ids stay up to date); if an app in the file no longer exists in the catalog (renamed, removed, or you're opening the file on a machine with an older/newer catalog), it's transparently restored using the package ids saved in the file instead of failing. Malformed or hand-edited files are handled the same way — bad entries are skipped with a note in the log panel rather than crashing the import.

### How manager resolution works

When you enable multiple managers (e.g. `apt` and `flatpak`), each app is resolved to the best one automatically:

1. Native managers (`apt`, `dnf`, `pacman`) are always preferred over `flatpak`.
2. If an app has a package in an enabled native manager, that's what gets used.
3. If no native manager has the app but `flatpak` does, the Flatpak ID from Flathub is used.
4. If no enabled manager has a package for the app, it shows as "(no package)" and is skipped during install.

For example, with `apt` + `flatpak` enabled on a Debian system: Firefox installs via `apt`, Google Chrome installs via `flatpak` (since it's not in the standard apt repos), and apps without any package in either manager are skipped.

### Selection file format

```json
{
  "format_version": 1,
  "managers": ["apt", "flatpak"],
  "apps": [
    {
      "name": "Mozilla Firefox",
      "custom": false,
      "apt": "firefox",
      "dnf": "firefox",
      "pacman": "firefox",
      "flatpak": "org.mozilla.firefox",
      "winget": "Mozilla.Firefox",
      "choco": "firefox"
    }
  ]
}
```

Installing **suspends the TUI** and hands control back to your real terminal, since `apt`/`dnf`/`pacman` may prompt for your sudo password, `winget`/`choco` print their own progress output, and `flatpak` installs from Flathub. Each manager's installs run sequentially; press Enter when it's done to return to the TUI, where you'll see a pass/fail summary per-manager.
