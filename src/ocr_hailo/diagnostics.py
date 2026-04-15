from __future__ import annotations

from dataclasses import dataclass
import platform
from shutil import which
import subprocess
import sys


@dataclass(slots=True)
class CheckResult:
    name: str
    ok: bool
    details: str


def _command_details(command: str) -> str:
    if which(command) is None:
        return "absent"

    candidates = ([command, "--version"], [command, "-v"])
    last_output = ""

    for args in candidates:
        try:
            completed = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
        except Exception:
            continue

        output = (completed.stdout or completed.stderr or "").strip().splitlines()
        if completed.returncode == 0 and output:
            return output[0]
        if completed.returncode == 0:
            return "présent"
        if output:
            last_output = output[0]

    return last_output or "présent mais version non remontée"


def _hailo_device_details() -> CheckResult:
    if which("hailortcli") is None:
        return CheckResult("Hailo device", False, "runtime absent")

    try:
        completed = subprocess.run(
            ["hailortcli", "scan"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception as exc:
        return CheckResult("Hailo device", False, f"scan impossible: {exc}")

    output = (completed.stdout or completed.stderr or "").strip().splitlines()
    devices = [line.strip() for line in output if "Device:" in line]
    if devices:
        return CheckResult("Hailo device", True, f"{len(devices)} device(s) détecté(s)")

    details = output[0] if output else "aucun périphérique remonté"
    return CheckResult("Hailo device", completed.returncode == 0, details)


def run_checks() -> list[CheckResult]:
    python_details = f"Python {sys.version.split()[0]} sur {platform.platform()}"

    checks = [
        CheckResult("Python", True, python_details),
        CheckResult("Hailo runtime CLI", which("hailortcli") is not None, _command_details("hailortcli")),
        _hailo_device_details(),
        CheckResult("Tesseract OCR", which("tesseract") is not None, _command_details("tesseract")),
        CheckResult("Poppler / pdftoppm", which("pdftoppm") is not None, _command_details("pdftoppm")),
    ]
    return checks
