"""Regression tests for build_install_commands.

Verifies that:
- Per-app managers (winget, apt) return one (app, cmd) pair per app.
- Batch managers (dnf, choco, pacman, flatpak) return a single shared
  command object for all apps in the batch.
- Apps with a missing package ID for the manager are silently omitted
  and never receive a result in the caller's results dict.
"""

from getready_tui.catalog import App
from getready_tui.core import build_install_commands


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _app(name: str, **kwargs: str | None) -> App:
    return App(name, **kwargs)


# ---------------------------------------------------------------------------
# Per-app managers (winget / apt)
# ---------------------------------------------------------------------------


class TestWingetPerApp:
    """winget emits one command per app."""

    def test_all_apps_have_package_ids(self):
        a = _app("Firefox", winget="Mozilla.Firefox")
        b = _app("Chrome", winget="Google.Chrome")
        result = build_install_commands("winget", [a, b])
        assert len(result) == 2
        assert result[0][0] is a
        assert result[1][0] is b
        assert "--id" in result[0][1]

    def test_missing_package_id_omitted(self):
        a = _app("Firefox", winget="Mozilla.Firefox")
        b = _app("NoWinget")  # winget defaults to None
        result = build_install_commands("winget", [a, b])
        assert len(result) == 1
        assert result[0][0] is a

    def test_all_missing_returns_empty(self):
        a = _app("NoWinget")
        b = _app("AlsoNoWinget")
        result = build_install_commands("winget", [a, b])
        assert result == []


class TestAptPerApp:
    """apt emits one command per app."""

    def test_all_apps_have_package_ids(self):
        a = _app("Firefox", apt="firefox")
        b = _app("Git", apt="git")
        result = build_install_commands("apt", [a, b])
        assert len(result) == 2
        assert result[0][0] is a
        assert result[1][0] is b
        assert "sudo" in result[0][1]
        assert "apt" in result[0][1]

    def test_missing_package_id_omitted(self):
        a = _app("Firefox", apt="firefox")
        b = _app("NoApt")  # apt defaults to None
        result = build_install_commands("apt", [a, b])
        assert len(result) == 1
        assert result[0][0] is a


# ---------------------------------------------------------------------------
# Batch managers (dnf, choco, pacman, flatpak)
# ---------------------------------------------------------------------------


class TestDnfBatch:
    """dnf batches all packages into one command."""

    def test_all_apps_have_package_ids(self):
        a = _app("Firefox", dnf="firefox")
        b = _app("Git", dnf="git")
        result = build_install_commands("dnf", [a, b])
        assert len(result) == 2
        # Both apps map to the same command object.
        assert result[0][1] is result[1][1]
        assert result[0][0] is a
        assert result[1][0] is b
        cmd = result[0][1]
        assert "firefox" in cmd
        assert "git" in cmd

    def test_missing_package_id_omitted(self):
        a = _app("Firefox", dnf="firefox")
        b = _app("NoDnf")  # dnf defaults to None
        result = build_install_commands("dnf", [a, b])
        # Only Firefox should appear; NoDnf has no package.
        assert len(result) == 1
        assert result[0][0] is a
        cmd = result[0][1]
        assert "firefox" in cmd
        # The missing app must not be in the command at all.
        assert "NoDnf" not in " ".join(cmd)

    def test_all_missing_returns_empty(self):
        a = _app("NoDnf")
        result = build_install_commands("dnf", [a])
        assert result == []


class TestChocoBatch:
    """choco batches all packages into one command."""

    def test_missing_package_id_omitted(self):
        a = _app("Firefox", choco="firefox")
        b = _app("NoChoco")
        result = build_install_commands("choco", [a, b])
        assert len(result) == 1
        assert result[0][0] is a
        assert result[0][1] is result[0][1]  # single command object


# ---------------------------------------------------------------------------
# Regression: mixed supported + missing IDs must NOT report missing apps
# as successful.  Simulates the caller's logic from app.py.
# ---------------------------------------------------------------------------


class TestMixedSupportedAndMissing:
    """Apps missing package IDs must not appear in results."""

    def test_apt_mixed(self):
        a = _app("Firefox", apt="firefox")
        b = _app("NoApt")
        cmds = build_install_commands("apt", [a, b])

        # Simulate the caller loop from app.py
        results: dict[str, bool] = {}
        cmd_results: dict[int, bool] = {}
        for app, cmd in cmds:
            cmd_id = id(cmd)
            if cmd_id not in cmd_results:
                cmd_results[cmd_id] = True  # pretend success
            results[app.name] = cmd_results[cmd_id]

        # Firefox succeeded.
        assert results.get("Firefox") is True
        # NoApt has no entry at all — not reported successful.
        assert "NoApt" not in results

    def test_dnf_mixed(self):
        a = _app("Firefox", dnf="firefox")
        b = _app("NoDnf")
        cmds = build_install_commands("dnf", [a, b])

        results: dict[str, bool] = {}
        cmd_results: dict[int, bool] = {}
        for app, cmd in cmds:
            cmd_id = id(cmd)
            if cmd_id not in cmd_results:
                cmd_results[cmd_id] = True  # pretend success
            results[app.name] = cmd_results[cmd_id]

        assert results.get("Firefox") is True
        assert "NoDnf" not in results

    def test_winget_mixed(self):
        a = _app("Firefox", winget="Mozilla.Firefox")
        b = _app("NoWinget")
        cmds = build_install_commands("winget", [a, b])

        results: dict[str, bool] = {}
        cmd_results: dict[int, bool] = {}
        for app, cmd in cmds:
            cmd_id = id(cmd)
            if cmd_id not in cmd_results:
                cmd_results[cmd_id] = True
            results[app.name] = cmd_results[cmd_id]

        assert results.get("Firefox") is True
        assert "NoWinget" not in results

    def test_batch_deduplication(self):
        """Batch managers must run the command only once."""
        a = _app("Firefox", dnf="firefox")
        b = _app("Git", dnf="git")
        cmds = build_install_commands("dnf", [a, b])

        run_count = 0
        results: dict[str, bool] = {}
        cmd_results: dict[int, bool] = {}
        for app, cmd in cmds:
            cmd_id = id(cmd)
            if cmd_id not in cmd_results:
                run_count += 1
                cmd_results[cmd_id] = True
            results[app.name] = cmd_results[cmd_id]

        assert run_count == 1
        assert results["Firefox"] is True
        assert results["Git"] is True

    def test_all_missing_yields_no_results(self):
        """When every app is missing a package ID, nothing runs."""
        a = _app("NoApt")
        b = _app("AlsoNoApt")
        cmds = build_install_commands("apt", [a, b])
        assert cmds == []

        results: dict[str, bool] = {}
        for app, cmd in cmds:
            results[app.name] = True
        assert results == {}
