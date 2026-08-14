"""Tests for the pre-flight diagnostics tool.

The point of the tool is that it works when the environment is broken, so the
tests exercise the pure decision logic directly rather than any happy path.
"""
from __future__ import annotations

import json
import re
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import diagnose_fifa14_local_fut as diag  # noqa: E402


# --------------------------------------------------------------------------
# Redaction — CONTRIBUTING.md and SECURITY.md require it before posting
# --------------------------------------------------------------------------

def test_redact_removes_the_home_directory() -> None:
    sample = str(Path.home() / "Games" / "FIFA 14")
    redacted = diag.redact(sample)
    assert str(Path.home()) not in redacted
    assert "<home>" in redacted


def test_redact_removes_the_account_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USERNAME", "SomeoneReal")
    assert "SomeoneReal" not in diag.redact(r"D:\Users\SomeoneReal\FIFA 14")


def test_redact_ignores_very_short_account_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 1-2 character name would corrupt unrelated text if substituted."""
    monkeypatch.setenv("USERNAME", "ab")
    assert diag.redact("a stable path") == "a stable path"


def test_redact_handles_empty_input() -> None:
    assert diag.redact("") == ""


# --------------------------------------------------------------------------
# config.local.psd1 parsing
# --------------------------------------------------------------------------

def test_parse_psd1_reads_both_keys() -> None:
    values = diag.parse_psd1_paths(
        "@{\n"
        "    # Folder containing fifa14.exe\n"
        "    GameRoot = 'D:\\Games\\FIFA 14\\Game'\n"
        "    GameExe  = 'D:\\Games\\FIFA 14\\Game\\fifa14.exe'\n"
        "}\n"
    )
    assert values["gameroot"] == r"D:\Games\FIFA 14\Game"
    assert values["gameexe"] == r"D:\Games\FIFA 14\Game\fifa14.exe"


def test_parse_psd1_skips_commented_lines() -> None:
    values = diag.parse_psd1_paths("@{\n#   GameRoot = 'D:\\Nope'\n}\n")
    assert values == {}


def test_parse_psd1_accepts_double_quotes() -> None:
    values = diag.parse_psd1_paths('@{\n    GameRoot = "E:\\FIFA 14\\Game"\n}\n')
    assert values["gameroot"] == r"E:\FIFA 14\Game"


def test_parse_psd1_tolerates_garbage() -> None:
    assert diag.parse_psd1_paths("not a psd1 at all") == {}


# --------------------------------------------------------------------------
# Status aggregation
# --------------------------------------------------------------------------

def _check(status: str, name: str = "x", hint: str = "") -> diag.Check:
    return diag.Check(name, status, "detail", hint)


def test_worst_status_prefers_failure() -> None:
    assert diag.worst_status([_check(diag.OK), _check(diag.WARN), _check(diag.FAIL)]) == diag.FAIL


def test_worst_status_reports_warning_when_nothing_failed() -> None:
    assert diag.worst_status([_check(diag.OK), _check(diag.WARN)]) == diag.WARN


def test_worst_status_is_ok_when_all_ok() -> None:
    assert diag.worst_status([_check(diag.OK), _check(diag.OK)]) == diag.OK


def test_summarise_counts_each_status() -> None:
    counts = diag.summarise([_check(diag.OK), _check(diag.OK), _check(diag.FAIL)])
    assert counts == {diag.OK: 2, diag.WARN: 0, diag.FAIL: 1}


# --------------------------------------------------------------------------
# Report rendering
# --------------------------------------------------------------------------

def test_render_includes_hints_for_problems() -> None:
    text = diag.render([_check(diag.FAIL, "Port 8099", "close the other program")])
    assert "Port 8099" in text
    assert "close the other program" in text
    assert "What to do" in text


def test_render_omits_the_advice_block_when_clean() -> None:
    text = diag.render([_check(diag.OK, "Python version")])
    assert "What to do" not in text
    assert "No blocking problems found" in text


def test_render_does_not_hide_a_failure_count() -> None:
    text = diag.render([_check(diag.OK), _check(diag.FAIL)])
    assert "1 failure(s)" in text


# --------------------------------------------------------------------------
# Port probing
# --------------------------------------------------------------------------

def test_port_is_free_detects_a_bound_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        assert diag.port_is_free(port) is False
    # The socket is closed again once the block exits.


def test_port_is_free_on_an_unused_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert diag.port_is_free(port) is True


# --------------------------------------------------------------------------
# Game root resolution order (mirrors tools/common.ps1)
# --------------------------------------------------------------------------

def test_explicit_game_root_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIFA14_GAME_ROOT", r"D:\from-env")
    root, source = diag.resolve_game_root(r"D:\from-cli")
    assert source == "command line"
    assert str(root) == r"D:\from-cli"


def test_explicit_game_root_is_unquoted() -> None:
    root, _ = diag.resolve_game_root('"D:\\quoted path\\Game"')
    assert str(root) == r"D:\quoted path\Game"


# --------------------------------------------------------------------------
# Catalogue integrity, against the real bundled files
# --------------------------------------------------------------------------

def test_bundled_catalogues_all_pass() -> None:
    failures = [c for c in diag.check_catalogues() if c.status == diag.FAIL]
    assert not failures, f"bundled catalogues should be valid: {[c.name for c in failures]}"


def test_every_checked_catalogue_is_still_referenced_by_the_server() -> None:
    """Stop the checklist drifting away from what the server actually loads.

    The catalogues are spread across modules: local_identity.py loads the card
    catalogues, probe.py loads the icebreaker fixture.
    """
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "server").glob("*.py"))
    )
    for name in diag.REQUIRED_CATALOGUES:
        assert name in sources, f"{name} is checked but no longer loaded by the server"


# --------------------------------------------------------------------------
# Staying in sync with the launcher
#
# The failure mode this whole group guards against: the diagnostic drifts away
# from what the launcher really does, and then confidently reports the wrong
# thing -- a false all-clear, or a problem for a port nothing uses.
# --------------------------------------------------------------------------

def test_required_ports_match_the_launcher() -> None:
    source = (TOOLS / "trace_fifa14_fut_hub_store.ps1").read_text(encoding="utf-8")
    match = re.search(r"\$ports\s*=\s*@\(([^)]*)\)", source)
    assert match, "could not find the launcher's $ports list"
    launcher_ports = {int(value) for value in re.findall(r"\d+", match.group(1))}
    assert set(diag.REQUIRED_PORTS) == launcher_ports, (
        "the checked ports drifted from tools/trace_fifa14_fut_hub_store.ps1"
    )


def test_auto_detect_candidates_match_common_ps1() -> None:
    source = (TOOLS / "common.ps1").read_text(encoding="utf-8")
    block = re.search(
        r"function Get-Fifa14AutoDetectCandidates.*?^}", source, re.S | re.M
    )
    assert block, "could not find Get-Fifa14AutoDetectCandidates"
    quoted = set(re.findall(r'"([^"]*FIFA 14\\Game)"', block.group(0)))
    relative = {path for path in quoted if not re.match(r"^[A-Za-z]:\\", path)}
    assert relative == set(diag.AUTODETECT_RELATIVE), (
        "the per-drive auto-detect paths drifted from tools/common.ps1"
    )


def test_openssl_lookup_covers_the_same_roots_as_probe() -> None:
    """probe.py also probes a per-user Git install; missing it sends people
    towards a pointless reinstall."""
    source = (ROOT / "server" / "probe.py").read_text(encoding="utf-8")
    block = re.search(r"def find_openssl\(.*?\n\n\n", source, re.S)
    assert block, "could not find probe.find_openssl"
    for marker in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        assert marker in block.group(0)
        assert marker in (TOOLS / "diagnose_fifa14_local_fut.py").read_text(encoding="utf-8"), (
            f"probe.py looks for OpenSSL under {marker} but the diagnostic does not"
        )


def test_archives_are_found_directly_in_the_game_root(tmp_path: Path) -> None:
    """The patchers read the archives straight out of the Game folder.

    Probing a data/ subdirectory reports a perfectly good installation as
    missing its archives.
    """
    patcher = (TOOLS / "patch_fifa14_fut_dynamic_route.py").read_text(encoding="utf-8")
    assert 'game_root / "data1.bh"' in patcher, "patcher layout changed; re-check this"

    fake_root = tmp_path / "Game"
    fake_root.mkdir()
    (fake_root / "fifa14.exe").write_bytes(b"MZ")
    for archive in diag.EXPECTED_ARCHIVES:
        (fake_root / archive).write_bytes(b"ViV4")

    by_name = {c.name: c for c in diag.check_game(str(fake_root))}
    for archive in diag.EXPECTED_ARCHIVES:
        check = by_name[f"Archive: {archive}"]
        assert check.status == diag.OK, (
            f"{archive} sits in the Game root but was reported as {check.status}"
        )


def test_expected_archives_are_named_by_the_patchers() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(TOOLS.glob("*.py")) + sorted(TOOLS.glob("*.ps1"))
    )
    for archive in diag.EXPECTED_ARCHIVES:
        assert archive in sources, f"{archive} is checked but no tool references it"


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------

def test_run_all_returns_checks() -> None:
    checks = diag.run_all(game_root=None)
    assert checks
    assert all(c.status in (diag.OK, diag.WARN, diag.FAIL) for c in checks)


def test_main_writes_json_and_reports_failure(tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    code = diag.main(["--game-root", str(tmp_path / "definitely-not-fifa"), "--json", str(target)])

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["status"] == diag.FAIL
    assert payload["summary"][diag.FAIL] >= 1
    assert code == 1, "a missing game installation must be reported as a failure"

    names = [c["name"] for c in payload["checks"]]
    assert "FIFA 14 installation" in names
