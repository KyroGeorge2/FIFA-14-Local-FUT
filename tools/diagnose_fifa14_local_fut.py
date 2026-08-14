#!/usr/bin/env python3
"""Pre-flight environment check for FIFA 14 Local FUT.

Most "the server does not start" and "FUT servers unavailable" reports come
down to a handful of environment problems that the launcher only discovers
half-way through startup, by which point the failure is buried in output a
non-technical user cannot summarise.

This script checks those conditions up front and prints one report that is
safe to paste into a GitHub issue. Personal paths and the Windows user name
are redacted, per CONTRIBUTING.md and SECURITY.md.

Usage:
    python tools/diagnose_fifa14_local_fut.py
    python tools/diagnose_fifa14_local_fut.py --game-root "D:\\Games\\FIFA 14\\Game"
    python tools/diagnose_fifa14_local_fut.py --json report.json

Exit code is 0 when nothing failed, 1 when at least one check failed.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import re
import socket
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"

OK = "ok"
WARN = "warn"
FAIL = "fail"

_STATUS_MARK = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]"}

MINIMUM_PYTHON = (3, 10)

# The set the documented launcher actually binds, from the $ports list and the
# --*-port arguments in tools/trace_fifa14_fut_hub_store.ps1. These are not the
# argparse defaults in server/probe.py: the launcher passes --blaze-port 42129
# and --dynamic-http-port 8306, so checking probe.py's defaults would both miss
# a port that must be free and flag one that is never used.
REQUIRED_PORTS = {
    42129: "Blaze redirector",
    42128: "Blaze main",
    8080: "HTTP / client config",
    8099: "FUT HTTP API",
    8306: "FUT dynamic HTTP",
    44125: "GOSCA (TLS)",
}
OPTIONAL_PORTS = {
    42127: "Blaze redirector (probe.py default, only when launched by hand)",
    3216: "Origin Core / LSX probe (only with --enable-lsx-probe)",
}

REQUIRED_GAME_FILES = ("fifa14.exe",)
EXPECTED_GAME_FILES = ("CardsDLLzf.dll", "powdllzf.dll")
# The patchers read these straight out of the Game folder -- see
# tools/patch_fifa14_fut_dynamic_route.py (game_root / "data1.bh") and
# tools/patch_fifa14_fcc_login1_popup.py (game_root / "cards0.bh"). There is no
# data/ subdirectory in the layout they expect.
EXPECTED_ARCHIVES = ("data1.big", "data1.bh", "cards0.big", "cards0.bh")
OPTIONAL_ARCHIVES = ("patch.big", "patch.bh", "data0.big", "data0.bh")

# Catalogues server/local_identity.py loads at import time. A truncated or
# half-downloaded release breaks these before any useful error is printed.
REQUIRED_CATALOGUES = {
    "pack-catalog.v237.json": "packs",
    "pack-weights.v237.json": None,
    "fifa14-player-catalog.v237.json": "players",
    "manager-catalog.v237.json": None,
    "fifa14-special-catalog.v240.json": "players",
    "fifa14-legend-catalog.v24013.json": "players",
    "fifa14-consumable-catalog.v2412.json": "items",
    "icebreakerpacklist.v27.json": None,
}

AUTODETECT_CANDIDATES = (
    r"C:\Program Files\EA Games\FIFA 14\Game",
    r"C:\Program Files (x86)\Origin Games\FIFA 14\Game",
    r"C:\Program Files\Origin Games\FIFA 14\Game",
)
# Must stay identical to Get-Fifa14AutoDetectCandidates in tools/common.ps1,
# otherwise this report can say "not found" for an install the launcher starts
# from happily. test_diagnostics.py asserts they match.
AUTODETECT_RELATIVE = (
    r"EA Games\FIFA 14\Game",
    r"Games\FIFA 14\Game",
    r"Origin Games\FIFA 14\Game",
    r"SteamLibrary\steamapps\common\FIFA 14\Game",
    r"Program Files\EA Games\FIFA 14\Game",
    r"Program Files (x86)\Origin Games\FIFA 14\Game",
)


@dataclass
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""
    data: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# Pure helpers (unit tested)
# --------------------------------------------------------------------------

def redact(text: str) -> str:
    """Strip the user's home directory and account name from a string."""
    if not text:
        return text
    result = str(text)
    home = str(Path.home())
    if home:
        result = re.sub(re.escape(home), "<home>", result, flags=re.IGNORECASE)
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if user and len(user) > 2:
        result = re.sub(re.escape(user), "<user>", result, flags=re.IGNORECASE)
    return result


def parse_psd1_paths(text: str) -> dict[str, str]:
    """Read GameRoot / GameExe out of a config.local.psd1 without PowerShell.

    Only the two keys the launcher uses are extracted; everything else in the
    file is ignored. Commented-out lines are skipped.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^(GameRoot|GameExe)\s*=\s*['\"](.+?)['\"]\s*$", line, re.IGNORECASE)
        if match:
            values[match.group(1).lower()] = match.group(2)
    return values


def worst_status(checks: list[Check]) -> str:
    if any(check.status == FAIL for check in checks):
        return FAIL
    if any(check.status == WARN for check in checks):
        return WARN
    return OK


def summarise(checks: list[Check]) -> dict[str, int]:
    return {
        OK: sum(1 for c in checks if c.status == OK),
        WARN: sum(1 for c in checks if c.status == WARN),
        FAIL: sum(1 for c in checks if c.status == FAIL),
    }


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------

def check_python() -> Check:
    version = sys.version_info
    detail = f"{version.major}.{version.minor}.{version.micro} ({platform.python_implementation()})"
    if (version.major, version.minor) < MINIMUM_PYTHON:
        return Check(
            "Python version", FAIL, detail,
            f"The server uses syntax that needs Python "
            f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or newer. "
            f"Run INSTALL_PREREQUISITES.cmd, or install a current Python.",
        )
    return Check("Python version", OK, detail)


def check_platform() -> Check:
    detail = f"{platform.system()} {platform.release()} ({platform.machine()})"
    if platform.system() != "Windows":
        return Check(
            "Operating system", WARN, detail,
            "This project targets Windows. The launcher, the archive patchers "
            "and Frida injection are Windows-only.",
        )
    return Check("Operating system", OK, detail)


def check_admin() -> Check:
    if platform.system() != "Windows":
        return Check("Administrator rights", WARN, "not checked on this platform")
    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception as exc:  # pragma: no cover - defensive
        return Check("Administrator rights", WARN, f"could not determine ({exc})")
    if not is_admin:
        return Check(
            "Administrator rights", FAIL, "not elevated",
            "Frida has to inject into fifa14.exe and the patchers write to the "
            "game folder. Right-click RUN_FIFA14_LOCAL_BETA.cmd and choose "
            "'Run as administrator'.",
        )
    return Check("Administrator rights", OK, "elevated")


def check_dependencies() -> list[Check]:
    checks: list[Check] = []
    for module, purpose in (
        ("cryptography", "certificate generation"),
        ("frida", "redirecting the game's network calls"),
    ):
        try:
            imported = __import__(module)
            version = getattr(imported, "__version__", "unknown")
            checks.append(Check(f"Dependency: {module}", OK, str(version)))
        except Exception as exc:
            checks.append(Check(
                f"Dependency: {module}", FAIL, f"not importable ({exc.__class__.__name__})",
                f"Needed for {purpose}. Run INSTALL_PREREQUISITES.cmd, or "
                f"'python -m pip install -r requirements.txt'.",
            ))
    return checks


def find_openssl() -> Path | None:
    """Mirror the lookup in server/probe.py so the report matches reality."""
    from shutil import which

    found = which("openssl")
    if found:
        return Path(found)
    local_programs = (
        str(Path(os.environ["LOCALAPPDATA"]) / "Programs")
        if os.environ.get("LOCALAPPDATA") else None
    )
    candidates: list[str] = []
    for base in filter(None, (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        # Git for Windows installed per-user, which is the layout a non-admin
        # install produces. probe.py and install_prerequisites.ps1 both use it.
        local_programs,
    )):
        candidates.extend([
            str(Path(base) / "Git" / "mingw64" / "bin" / "openssl.exe"),
            str(Path(base) / "Git" / "usr" / "bin" / "openssl.exe"),
        ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def check_openssl() -> Check:
    found = find_openssl()
    if found is None:
        return Check(
            "OpenSSL", FAIL, "not found",
            "FIFA 14's old ProtoSSL needs legacy certificates that modern "
            "'cryptography' refuses to sign, so the server shells out to "
            "OpenSSL. Run INSTALL_PREREQUISITES.cmd; it installs Git for "
            "Windows, which ships OpenSSL.",
        )
    return Check("OpenSSL", OK, redact(str(found)))


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def check_ports() -> list[Check]:
    checks: list[Check] = []
    for port, purpose in REQUIRED_PORTS.items():
        if port_is_free(port):
            checks.append(Check(f"Port {port}", OK, f"free ({purpose})"))
        else:
            checks.append(Check(
                f"Port {port}", FAIL, f"already in use ({purpose})",
                "Another program is holding this port, so the local server "
                "cannot bind it and the game will report that the FUT servers "
                "are unavailable. A previous run that did not shut down cleanly "
                "is the usual cause: close it, or reboot, then try again.",
                {"port": port},
            ))
    for port, purpose in OPTIONAL_PORTS.items():
        if not port_is_free(port):
            checks.append(Check(f"Port {port}", WARN, f"in use ({purpose})"))
    return checks


DRIVE_TYPE_CDROM = 5


def ready_drives() -> list[str]:
    """Mounted drive roots, skipping optical drives.

    The equivalent of [IO.DriveInfo]::GetDrives() with an IsReady filter in
    tools/common.ps1. Probing an empty optical drive can block, so it is left
    out rather than walked.
    """
    if platform.system() != "Windows":
        return []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        get_type = ctypes.windll.kernel32.GetDriveTypeW
    except Exception:  # pragma: no cover - defensive
        return []

    roots: list[str] = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        root = f"{chr(ord('A') + index)}:\\"
        try:
            if get_type(ctypes.c_wchar_p(root)) == DRIVE_TYPE_CDROM:
                continue
        except Exception:  # pragma: no cover - defensive
            continue
        roots.append(root)
    return roots


def resolve_game_root(explicit: str | None) -> tuple[Path | None, str]:
    """Resolve the game folder the same way tools/common.ps1 does."""
    if explicit:
        return Path(explicit.strip().strip('"')), "command line"

    config = ROOT / "config.local.psd1"
    if config.is_file():
        try:
            values = parse_psd1_paths(config.read_text(encoding="utf-8-sig"))
        except OSError:
            values = {}
        if values.get("gameroot"):
            return Path(values["gameroot"]), "config.local.psd1"
        if values.get("gameexe"):
            return Path(values["gameexe"]).parent, "config.local.psd1"

    env_root = os.environ.get("FIFA14_GAME_ROOT")
    if env_root:
        return Path(env_root), "FIFA14_GAME_ROOT"

    candidates = list(AUTODETECT_CANDIDATES)
    for drive in ready_drives():
        candidates.extend(str(Path(drive) / relative) for relative in AUTODETECT_RELATIVE)
    for candidate in candidates:
        if (Path(candidate) / "fifa14.exe").is_file():
            return Path(candidate), "auto-detected"
    return None, "not found"


def check_game(explicit: str | None) -> list[Check]:
    root, source = resolve_game_root(explicit)
    if root is None:
        return [Check(
            "FIFA 14 installation", FAIL, "not found",
            "Copy config.local.psd1.example to config.local.psd1 and set "
            "GameRoot to the folder that contains fifa14.exe, or set the "
            "FIFA14_GAME_ROOT environment variable.",
        )]

    checks = [Check("FIFA 14 installation", OK, f"{redact(str(root))} (via {source})")]
    if not root.is_dir():
        checks[0] = Check(
            "FIFA 14 installation", FAIL, f"{redact(str(root))} does not exist (via {source})",
            "The configured path is stale. Update config.local.psd1.",
        )
        return checks

    for name in REQUIRED_GAME_FILES:
        target = root / name
        if target.is_file():
            checks.append(Check(f"Game file: {name}", OK, f"{target.stat().st_size} bytes"))
        else:
            checks.append(Check(
                f"Game file: {name}", FAIL, "missing",
                "This is not a FIFA 14 Game folder. Point GameRoot at the "
                "folder that directly contains fifa14.exe.",
            ))

    for name in EXPECTED_GAME_FILES:
        matches = list(root.rglob(name))
        if matches:
            checks.append(Check(f"Game file: {name}", OK, f"found ({len(matches)})"))
        else:
            checks.append(Check(
                f"Game file: {name}", WARN, "not found",
                "The launcher looks for this during startup. A missing file "
                "usually means an incomplete or non-standard installation.",
            ))

    for name in EXPECTED_ARCHIVES:
        target = root / name
        checks.append(
            Check(f"Archive: {name}", OK, "present") if target.is_file()
            else Check(
                f"Archive: {name}", WARN, "missing",
                "The FUT patchers read this archive. Without it they fall back "
                "to compatibility mode and some fixes are skipped.",
            )
        )
    present_optional = [n for n in OPTIONAL_ARCHIVES if (root / n).is_file()]
    checks.append(Check(
        "Archive: optional patch/cards", OK,
        ", ".join(present_optional) if present_optional else "none present",
    ))
    return checks


def check_catalogues() -> list[Check]:
    checks: list[Check] = []
    for name, list_key in REQUIRED_CATALOGUES.items():
        path = SERVER / name
        if not path.is_file():
            checks.append(Check(
                f"Catalogue: {name}", FAIL, "missing",
                "The server cannot start without it. Re-extract the release "
                "ZIP; a partial extraction is the usual cause.",
            ))
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            checks.append(Check(
                f"Catalogue: {name}", FAIL, f"unreadable ({exc.__class__.__name__})",
                "The file is corrupt or truncated. Re-download the release.",
            ))
            continue
        if list_key:
            rows = document.get(list_key) if isinstance(document, dict) else None
            if not isinstance(rows, list) or not rows:
                checks.append(Check(
                    f"Catalogue: {name}", FAIL, f"no '{list_key}' entries",
                    "The file parsed but is empty. Re-download the release.",
                ))
                continue
            checks.append(Check(f"Catalogue: {name}", OK, f"{len(rows)} entries"))
        else:
            checks.append(Check(f"Catalogue: {name}", OK, "parsed"))
    return checks


def check_server_imports() -> Check:
    if str(SERVER) not in sys.path:
        sys.path.insert(0, str(SERVER))
    try:
        import local_identity  # noqa: F401
        import beta_identity  # noqa: F401
    except Exception as exc:
        return Check(
            "Server modules import", FAIL, f"{exc.__class__.__name__}: {redact(str(exc))}",
            "The server cannot be imported, so it will never reach the READY "
            "line. If the catalogue checks above passed, please include this "
            "message in your issue.",
        )
    return Check("Server modules import", OK, "local_identity + beta_identity")


def check_writable() -> Check:
    probe = ROOT / ".diagnose-write-test"
    try:
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
    except OSError as exc:
        return Check(
            "Project folder writable", FAIL, f"{exc.__class__.__name__}",
            "The launcher writes artifacts/, certs/ and state/ next to the "
            "scripts. Move the folder somewhere writable, such as your "
            "Documents folder, and do not run it from inside Program Files.",
        )
    return Check("Project folder writable", OK, redact(str(ROOT)))


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def run_all(game_root: str | None = None) -> list[Check]:
    checks: list[Check] = [check_python(), check_platform(), check_admin()]
    checks.extend(check_dependencies())
    checks.append(check_openssl())
    checks.extend(check_ports())
    checks.append(check_writable())
    checks.extend(check_catalogues())
    checks.append(check_server_imports())
    checks.extend(check_game(game_root))
    return checks


def render(checks: list[Check]) -> str:
    counts = summarise(checks)
    lines = [
        "FIFA 14 Local FUT - environment report",
        "=" * 52,
        "",
    ]
    for check in checks:
        lines.append(f"{_STATUS_MARK[check.status]} {check.name}: {check.detail}")
    lines.extend(["", "-" * 52,
                  f"{counts[OK]} ok, {counts[WARN]} warning(s), {counts[FAIL]} failure(s)"])

    problems = [c for c in checks if c.status in (FAIL, WARN) and c.hint]
    if problems:
        lines.extend(["", "What to do", "-" * 52])
        for check in problems:
            lines.extend([f"* {check.name}: {check.hint}", ""])
    if counts[FAIL] == 0:
        lines.append("No blocking problems found. If FUT still fails, open an "
                     "issue and attach this report.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game-root", default=None, help="FIFA 14 Game folder to check")
    parser.add_argument("--json", dest="json_path", default=None, help="also write the report as JSON")
    arguments = parser.parse_args(argv)

    checks = run_all(arguments.game_root)
    print(render(checks))

    if arguments.json_path:
        payload = {
            "summary": summarise(checks),
            "status": worst_status(checks),
            "checks": [asdict(check) for check in checks],
        }
        Path(arguments.json_path).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nJSON report written to {arguments.json_path}")

    return 1 if summarise(checks)[FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
