#!/usr/bin/env python3
"""🩹 SENTINEL FIXES — known-crash guidance + reviewable fix-script generator.
NEVER executes anything — points to safe fixes + writes REVIEWABLE scripts the
operator reads then runs. Elevated steps trigger the real UAC prompt = the gate."""
from pathlib import Path

KNOWN_FIXES = {
    "onedrive": {
        "label": "OneDrive sync crash",
        "match_any": ["onedrive.exe", "filesync"],
        "summary": "OneDrive's sync engine crashed. Safe fix: reset OneDrive "
                   "(re-syncs, does NOT delete files), then optionally repair "
                   "system files. Fully reversible.",
        "script_name": "onedrive_fix_helper.bat",
    },
    "copilot": {
        "label": "Copilot proxy crash",
        "match_any": ["mscopilot", "copilot"],
        "summary": "Windows Copilot's proxy crashed. If you don't use Copilot, "
                   "disable it via Settings (reversible) — no registry edits, "
                   "no update-blocking. Standard, safe.",
        "script_name": None,
    },
}

def identify_fix(crash_text):
    t = (crash_text or "").lower()
    for key, fix in KNOWN_FIXES.items():
        if any(m in t for m in fix["match_any"]):
            return fix
    return None

def fix_hint(crash_text):
    fix = identify_fix(crash_text)
    if not fix:
        return ""
    tail = f" A reviewable fix script can be prepared ({fix['script_name']})." if fix.get("script_name") else ""
    return f"\n🩹 Known fix: {fix['summary']}{tail}"

def write_review_script(crash_text, out_dir):
    fix = identify_fix(crash_text)
    if not fix or not fix.get("script_name"):
        return None
    out = Path(out_dir) / fix["script_name"]
    if fix["script_name"] == "onedrive_fix_helper.bat":
        out.write_text(_onedrive_script(), encoding="utf-8")
        return out
    return None

def _onedrive_script():
    return (
        "@echo off\r\n"
        "REM Lkey OneDrive Fix Helper - REVIEW before running.\r\n"
        "REM Safe + reversible. YOU run this. Admin step triggers UAC = the gate.\r\n"
        "echo Lkey OneDrive Fix Helper\r\n"
        "echo   1. Reset OneDrive (files safe)  2. Optional admin SFC\r\n"
        "pause\r\n"
        "%localappdata%\\Microsoft\\OneDrive\\onedrive.exe /reset\r\n"
        "timeout /t 5 >nul\r\n"
        "start \"\" %localappdata%\\Microsoft\\OneDrive\\onedrive.exe\r\n"
        "set /p runsfc=\"Run sfc /scannow (admin)? (y/n): \"\r\n"
        "if /i \"%runsfc%\"==\"y\" powershell -Command \"Start-Process cmd -ArgumentList '/c sfc /scannow ^& pause' -Verb RunAs\"\r\n"
        "echo Done.\r\n"
        "pause\r\n"
    )

if __name__ == "__main__":
    for sample in ["OneDrive.exe crashed FileSyncFALWB.dll",
                   "mscopilot_proxy.exe crashed",
                   "somerandomapp.exe crashed"]:
        fix = identify_fix(sample)
        print(f"{sample[:40]:42} -> {fix['label'] if fix else 'no known fix'}")
        print(f"   hint:{fix_hint(sample) or ' (none)'}")
