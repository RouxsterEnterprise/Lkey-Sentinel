# Lkey Sentinel

A small, free, open-source Windows tray tool that watches your PC's vitals while
you game — CPU/GPU temperature, system RAM, and VRAM — and warns you **before** a
freeze, so you have time to save and act. It can also politely free browser
memory, take a screenshot from the tray, and (optionally) send alerts to your
phone via Telegram.

No accounts. No ads. Nothing leaves your machine unless you explicitly
configure Telegram.

## What it actually does

- **Early warnings.** Polls temperatures, RAM, and VRAM against thresholds and
  raises a Windows notification when one is crossed. Sustained conditions
  (long gaming sessions) warn once — not every few seconds.
- **Free memory (safe).** Closes *spare background* browser renderer
  processes to reclaim RAM. It never force-kills your browser, your active
  tabs, or your game. Gains are deliberately modest — safety first.
- **Screenshot from the tray.** Captures all screens, saves to
  `Pictures/Lkey_Screenshots` as `screenshot_YYYYMMDD_HHMMSS.png`, and opens
  File Explorer with the new file already selected. If Telegram is
  configured, the photo is also sent to your chat.
- **Open captures folder.** One click to where your screenshots live.
- **Safety note.** Your first capture drops `_SAFETY_NOTE.txt` into the
  folder: screenshots capture *everything* on screen — passwords, keys,
  private messages — so review before sharing, crop what's sensitive, and
  treat any leaked secret as exposed.
- **Crash fix helper.** Recognizes some known crash patterns (OneDrive,
  Copilot) and writes **reviewable** fix scripts. It never runs them — you
  read, then you run, and Windows' own UAC prompt gates anything elevated.
- **Black box.** Recent samples and events are kept in
  `app/data/sentinel_blackbox.log`; plain-English crash notes in
  `app/data/sentinel_crashes.log`.
- **Self-updater.** Pull-based and version-gated: "Check for updates"
  downloads the newer version to a temp file, verifies it compiles, backs up
  your current copy, and **stages the update for the next start** — quit and
  relaunch to apply. It never swaps the running file mid-run and never
  downgrades.

## Quick start

1. Install **Python 3.12 (64-bit)** from python.org. On a fresh Windows,
   first disable the Microsoft Store "python" aliases (Settings → Apps →
   Advanced app settings → App execution aliases) — `INSTALL.bat` checks for
   this decoy and will tell you.
2. Download or clone this folder and run `INSTALL.bat` once — it installs
   the few required packages.
3. Run `START_SENTINEL.bat`. A tray icon appears near your clock.

## Tray menu

Status line (latest event) · Check for updates · 📸 Screenshot ·
Open captures folder · Free memory (safe) · Quit

## Optional Telegram (`.env`)

Create a file named `.env` next to the script. Entirely optional — the tool
works normally without it.

```ini
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_numeric_chat_id
SENTINEL_MACHINE_NAME=GamingRig   # optional label so alerts say which PC
```

With both keys set, warnings text your phone and tray screenshots arrive as
photos. Without them, everything stays local — silently, by design.

## Thresholds (`app/data/sentinel.json`)

Created on first run with these defaults; edit the file and restart to tune:

```json
{
  "poll_seconds": 5,
  "gpu_temp_warn": 83,
  "cpu_temp_warn": 90,
  "ram_percent_warn": 92,
  "vram_percent_warn": 94,
  "history_ring": 12
}
```

## Honest limitations

- **Not an overclocking or fan-control tool.** It monitors and warns; it
  cannot change fan speeds, undervolt, or stop thermal throttling.
- **Sensor compatibility varies.** Some boards and GPUs need vendor drivers
  or admin rights before Windows exposes accurate temperatures.
- **No guarantee against crashes.** Warnings buy you time to save and act;
  they cannot prevent driver timeouts, software bugs, or hardware faults.

## License

MIT. Free to inspect, modify, and build on.
