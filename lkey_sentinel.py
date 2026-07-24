#!/usr/bin/env python3
# ==========================================
# 🛡️ LKEY SENTINEL v1.0 — the featherweight Beta
# ==========================================
# PURPOSE: keep a powerful-but-unstable gaming rig ALIVE longer by watching
# for the danger signs that precede a crash, warning BEFORE it happens, and
# recording a black-box log of the machine's state at the moment of trouble.
#
# It is deliberately tiny: no GUI, no AI model in RAM, no background git.
# It sips telemetry on a slow timer and otherwise sleeps. Footprint is a
# few MB — it will not compete with her games for resources.
#
#   python lkey_sentinel.py              -> watch, warn, log (console)
#   python lkey_sentinel.py --tray       -> run quietly in the system tray
#   python lkey_sentinel.py --once       -> single snapshot + exit
#
# Thresholds are conservative and tunable via app/data/sentinel.json.
# Everything is READ-ONLY toward the system: it observes, it never throttles
# or kills anything. It cannot fix hardware faults (power/thermal/RAM) —
# it gives you EARLY WARNING and a BLACK BOX so the real cause is visible.
# ==========================================

import json
import os
try:
    from sentinel_fixes import fix_hint
except Exception:
    def fix_hint(x): return ""  # graceful if module missing
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = ROOT / "app" / "data" / "sentinel.json"
BLACKBOX = ROOT / "app" / "data" / "sentinel_blackbox.log"
CRASHLOG = ROOT / "app" / "data" / "sentinel_crashes.log"           # plain English
CRASHDEBUG = ROOT / "app" / "data" / "sentinel_crash_debug.log"     # long-form for debugging

# Telegram alerting: reads .env (same keys the Alpha uses) OR sentinel.json.
# Optional — absent keys simply disable remote alerts, local watch continues.
def _load_telegram():
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    envf = ROOT / ".env"
    if (not tok or not chat) and envf.exists():
        try:
            for line in envf.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k == "TELEGRAM_BOT_TOKEN" and not tok: tok = v
                    if k == "TELEGRAM_CHAT_ID" and not chat: chat = v
        except OSError:
            pass
    return tok, chat


def machine_label():
    """Friendly machine name for alerts: SENTINEL_MACHINE_NAME from .env if set,
    else the computer's hostname. Lets you tell WHICH machine an alert is from."""
    import os, socket
    name = os.environ.get("SENTINEL_MACHINE_NAME", "").strip()
    if not name:
        # also check the .env file directly (same pattern the token uses)
        try:
            envf = ROOT / ".env"
            if envf.exists():
                for line in envf.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("SENTINEL_MACHINE_NAME"):
                        name = line.split("=", 1)[1].strip()
                        break
        except Exception:
            pass
    return name or socket.gethostname()


def send_telegram(text):
    tok, chat = _load_telegram()
    if not tok or not chat:
        return False
    try:
        import urllib.request, urllib.parse
        url = f"https://api.telegram.org/bot{tok}/sendMessage"
        data = urllib.parse.urlencode(
            {"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=8)
        return True
    except Exception:
        return False

_SAFETY_TEXT = """📸 ABOUT THIS FOLDER — read once, stay safe

Screenshots capture EVERYTHING visible on screen — including passwords,
API keys, private messages, and banking details if they were open.

Before sharing any capture (Telegram, Discord, email, forums):
  1. Look at it first. Zoom in. Check every corner.
  2. Crop or redact anything sensitive.
  3. Delete captures you no longer need — this folder is not a vault.

If a secret does leak in a shared image, treat it as exposed:
rotate or replace that key/password immediately.

— Lkey keeps you safer than anything else. That includes from ourselves.
"""

def _drop_safety_note(d):
    """[SNAP-SAFETY] Self-documenting folder: one educational note, written once."""
    try:
        n = Path(d) / "_SAFETY_NOTE.txt"
        if not n.exists():
            n.write_text(_SAFETY_TEXT, encoding="utf-8")
    except Exception:
        pass


def send_telegram_photo(path, caption=""):
    """[EYES-V2] Send an image to Telegram. Same philosophy as send_telegram:
    stdlib-only multipart, silent skip when unconfigured, never raises."""
    tok, chat = _load_telegram()
    if not tok or not chat:
        return False
    try:
        import urllib.request, uuid
        data = Path(path).read_bytes()
        b = uuid.uuid4().hex
        body = b"".join([
            f"--{b}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat}\r\n".encode(),
            f"--{b}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption[:1000]}\r\n".encode(),
            f"--{b}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{Path(path).name}\"\r\n"
            f"Content-Type: image/png\r\n\r\n".encode(), data, b"\r\n".encode(),
            f"--{b}--\r\n".encode()])
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendPhoto", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        urllib.request.urlopen(req, timeout=20)
        return True
    except Exception:
        return False


DEFAULTS = {
    "poll_seconds": 5,          # slow + gentle; raise to 10 for even less load
    "gpu_temp_warn": 83,        # °C — 5090 throttles ~83-88; warn before that
    "cpu_temp_warn": 90,        # °C
    "ram_percent_warn": 92,     # % — approaching exhaustion
    "vram_percent_warn": 94,    # % — VRAM pressure precedes many game crashes
    "sustain_samples": 3,       # consecutive readings a condition must hold before an alert may speak
    "history_ring": 12,         # keep last N samples in the black box on alert
    "beacon_animate": True,    # tray orb breathes; False = static colour, no blink EVER
    "event_flash_seconds": 6,   # how long the blue "event logged" flash holds
    "panel_mode": "dark",       # Panel skin: dark | glass (Glass Engine) — click the Panel orb to cycle
}


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CFG.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        CFG.parent.mkdir(parents=True, exist_ok=True)
        try:
            CFG.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
        except OSError:
            pass
    return cfg


def sample():
    """One lightweight reading. Degrades gracefully if a sensor is absent."""
    s = {"t": datetime.now().strftime("%H:%M:%S")}
    try:
        import psutil
        s["cpu"] = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        s["ram"] = vm.percent
        s["ram_used_gb"] = round(vm.used / 1e9, 1)
        temps = getattr(psutil, "sensors_temperatures", lambda: {})() or {}
        cpu_t = None
        for key in ("coretemp", "k10temp", "acpitz"):
            if key in temps and temps[key]:
                cpu_t = max(x.current for x in temps[key])
                break
        s["cpu_temp"] = cpu_t
    except Exception as e:
        s["err_psutil"] = str(e)
    # GPU via NVML if available (nvidia-ml-py / pynvml) — 5090 friendly
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        s["gpu_temp"] = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        s["vram"] = round(mem.used / mem.total * 100, 1)
        s["vram_used_gb"] = round(mem.used / 1e9, 1)
        s["vram_total_gb"] = round(mem.total / 1e9, 1)
        util = pynvml.nvmlDeviceGetUtilizationRates(h)
        s["gpu_load"] = util.gpu
        pynvml.nvmlShutdown()
    except Exception:
        pass  # no NVML -> GPU fields simply absent; CPU/RAM watch still works
    _flight_record(s)
    return s


FLIGHT_LOG = ROOT / "app" / "data" / "sentinel_readings.csv"
_FLIGHT_KEYS = ["t", "cpu", "cpu_temp", "ram", "ram_used_gb",
                "gpu_temp", "gpu_load", "vram", "vram_used_gb"]


def _flight_record(s):
    """Continuous flight recorder — EVERY sample lands here, alert or not.
    The next 'it lied' gets settled by history, not theories. ~5MB rotation."""
    try:
        FLIGHT_LOG.parent.mkdir(parents=True, exist_ok=True)
        if FLIGHT_LOG.exists() and FLIGHT_LOG.stat().st_size > 5_000_000:
            FLIGHT_LOG.replace(FLIGHT_LOG.with_suffix(".csv.1"))
        fresh = not FLIGHT_LOG.exists()
        with open(FLIGHT_LOG, "a", encoding="utf-8", newline="") as f:
            if fresh:
                f.write("date," + ",".join(_FLIGHT_KEYS) + "\n")
            f.write(datetime.now().strftime("%Y-%m-%d") + "," +
                    ",".join(str(s.get(k, "")) for k in _FLIGHT_KEYS) + "\n")
    except OSError:
        pass


_BREACH_STREAK = {}


# ---------------------------------------------------------------
# [BEACON] Shared status truth for the tray orb + panel.
# The beacon NEVER senses anything itself: it is the existing
# Honest Alert machinery made visible.
#   GREEN  calm
#   YELLOW breach building — the hysteresis counting 1..2 before
#          an alert may speak, made visible
#   RED    sustained danger (an alert is live)
#   BLUE   an event of interest was just written to disk
#          (crash record / blackbox) — flashes, then returns to truth
# ---------------------------------------------------------------
_BEACON = {"state": "green", "event_until": 0.0, "flash_secs": 6.0,
           "sample": {}, "last": ""}


def beacon_set(state):
    """Set the underlying truth colour (green/yellow/red)."""
    if state in ("green", "yellow", "red"):
        _BEACON["state"] = state


def beacon_event():
    """A moment of interest just landed on disk (crash record or
    blackbox). The orb flashes BLUE briefly, then returns to truth."""
    try:
        secs = float(_BEACON.get("flash_secs", 6.0))
    except (TypeError, ValueError):
        secs = 6.0
    _BEACON["event_until"] = time.time() + max(1.0, secs)


def beacon_state():
    """What the orb should show RIGHT NOW (blue overrides briefly)."""
    if time.time() < _BEACON["event_until"]:
        return "blue"
    return _BEACON["state"]


def evaluate(s, cfg):
    """Return list of (level, message) warnings for this sample.

    HONEST ALERT: a condition must hold for cfg['sustain_samples']
    consecutive readings before it may speak. Single-sample blips are
    recorded in the flight log, never shouted. Alerts carry evidence
    (GB + held-duration), so a warning is always checkable."""
    alerts = []
    need = int(cfg.get("sustain_samples", 3))
    poll = int(cfg.get("poll_seconds", 5))

    def chk(key, limit, label, unit="°C", extra=""):
        v = s.get(key)
        breach = isinstance(v, (int, float)) and v >= limit
        streak = (_BREACH_STREAK.get(key, 0) + 1) if breach else 0
        _BREACH_STREAK[key] = streak
        if breach and streak >= need:
            alerts.append(("DANGER",
                           f"{label} {v}{unit} ≥ {limit}{unit}{extra} — held {streak * poll}s"))

    vram_extra = ""
    if "vram_used_gb" in s and "vram_total_gb" in s:
        vram_extra = f" ({s['vram_used_gb']}/{s['vram_total_gb']} GB)"
    ram_extra = f" ({s.get('ram_used_gb', '?')} GB used)" if "ram_used_gb" in s else ""

    chk("gpu_temp", cfg["gpu_temp_warn"], "GPU temp")
    chk("cpu_temp", cfg["cpu_temp_warn"], "CPU temp")
    chk("ram", cfg["ram_percent_warn"], "RAM", "%", ram_extra)
    chk("vram", cfg["vram_percent_warn"], "VRAM", "%", vram_extra)
    return alerts


def write_blackbox(ring, alerts):
    """On alert, dump the recent sample ring so the pre-crash state survives."""
    beacon_event()   # [BEACON] blue flash: a blackbox just hit the disk
    try:
        BLACKBOX.parent.mkdir(parents=True, exist_ok=True)
        with open(BLACKBOX, "a", encoding="utf-8") as f:
            f.write(f"\n===== ALERT {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            for lvl, msg in alerts:
                f.write(f"  ⚠️ {lvl}: {msg}\n")
            f.write("  --- last samples before this alert ---\n")
            for r in ring:
                f.write(f"  {json.dumps(r)}\n")
    except OSError:
        pass


def fmt(s):
    bits = [f"CPU {s.get('cpu','--')}%"]
    if s.get("cpu_temp") is not None:
        bits.append(f"{s['cpu_temp']}°C")
    bits.append(f"RAM {s.get('ram','--')}%")
    if "gpu_temp" in s:
        bits.append(f"GPU {s.get('gpu_load','--')}% {s['gpu_temp']}°C")
    if "vram" in s:
        vb = f"VRAM {s['vram']}%"
        if "vram_used_gb" in s and "vram_total_gb" in s:
            vb += f" ({s['vram_used_gb']}/{s['vram_total_gb']}G)"
        bits.append(vb)
    return " | ".join(bits)


def _running_procs():
    """Set of (pid, name) for currently running processes. Gracefully empty
    if psutil is missing."""
    out = {}
    try:
        import psutil
        for p in psutil.process_iter(attrs=["pid", "name"]):
            try:
                out[p.info["pid"]] = (p.info["name"] or "?")
            except Exception:
                continue
    except Exception:
        pass
    return out


def _recent_crash_events(within_seconds=15):
    """Read the Windows Application event log for 'Application Error' (1000)
    and .NET/app-hang crashes in the last N seconds. Returns list of dicts:
    {app, faulting_module, detail}. Windows-only; returns [] elsewhere or if
    the read fails (so the process-diff path still works on its own)."""
    events = []
    try:
        import win32evtlog  # pywin32
        import time as _t
        server = None
        htype = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
        h = win32evtlog.OpenEventLog(server, "Application")
        now = _t.time()
        recs = win32evtlog.ReadEventLog(h, htype, 0)
        for ev in recs or []:
            try:
                # EventID 1000 = Application Error; 1002 = App Hang
                eid = ev.EventID & 0xFFFF
                if eid not in (1000, 1002):
                    continue
                gen = ev.TimeGenerated
                # TimeGenerated is a pywintypes time; convert to epoch
                secs_ago = now - int(gen.timestamp()) if hasattr(gen, "timestamp") else 0
                if secs_ago > within_seconds:
                    break  # log is newest-first; older than window = stop
                ins = list(ev.StringInserts or [])
                app = ins[0] if ins else "?"
                faulting = ins[3] if len(ins) > 3 else "?"
                events.append({"app": app, "faulting_module": faulting,
                               "detail": " | ".join(ins[:8])})
            except Exception:
                continue
        win32evtlog.CloseEventLog(h)
    except Exception:
        pass  # not Windows, or pywin32 absent -> event confirmation simply off
    return events


def _log_crash(name, faulting, detail, vitals_ring, confirmed):
    """Write BOTH logs: a human line + a long-form debug block with the
    machine's vitals at the moment of death (the 'why' + the 'state')."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tag = "CRASH" if confirmed else "vanished"
    try:
        CRASHLOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CRASHLOG, "a", encoding="utf-8") as f:
            if confirmed:
                f.write(f"[{stamp}] 💥 {name} CRASHED — faulting module: {faulting}\n")
            else:
                f.write(f"[{stamp}] ⚪ {name} closed (no crash record — likely normal exit)\n")
    except OSError:
        pass
    # long-form debug only for confirmed crashes (keeps the debug log signal-rich)
    if confirmed:
        try:
            with open(CRASHDEBUG, "a", encoding="utf-8") as f:
                f.write(f"\n===== CRASH {stamp} =====\n")
                f.write(f"  Application : {name}\n")
                f.write(f"  Faulting module: {faulting}\n")
                f.write(f"  Event detail: {detail}\n")
                f.write("  --- machine vitals in the seconds before the crash ---\n")
                for r in vitals_ring:
                    f.write(f"    {json.dumps(r)}\n")
                f.write("  (share this block with Lkey/Claude to diagnose the cause)\n")
        except OSError:
            pass


def detect_crashes(prev_procs, now_procs, vitals_ring, say, notify_telegram=True, machine=None):
    """Compare process snapshots; for anything that vanished, confirm via the
    Windows event log, log both files, and ping Telegram on a real crash.
    Returns the current process set to become 'prev' for the next poll."""
    prev_names = set(prev_procs.values())
    now_names = set(now_procs.values())
    vanished = prev_names - now_names
    # ignore our own churn + transient helpers we never care about
    ignore = {"?", "conhost.exe", "backgroundTaskHost.exe", "python.exe", "pythonw.exe"}
    vanished = {v for v in vanished if v and v not in ignore}
    if not vanished:
        return
    # ⏱️ TIMING FIX: a crashed process vanishes INSTANTLY, but Windows/WerFault
    # takes a few seconds to WRITE the crash event to the log. Checking once,
    # immediately, misses it (logs 'normal exit' for a real crash). So we retry
    # the event-log read a few times with short waits, giving WerFault time.
    crash_events = _recent_crash_events()
    _still_unmatched = {v for v in vanished
                        if not any(v.lower() in e["app"].lower()
                                   or e["app"].lower() == v.lower()
                                   for e in crash_events)}
    _retries = 0
    while _still_unmatched and _retries < 4:
        time.sleep(2)  # let WerFault finish writing the crash event
        _retries += 1
        fresh = _recent_crash_events(within_seconds=30)
        # merge any newly-appeared events
        for e in fresh:
            if e not in crash_events:
                crash_events.append(e)
        _still_unmatched = {v for v in _still_unmatched
                            if not any(v.lower() in e["app"].lower()
                                       or e["app"].lower() == v.lower()
                                       for e in crash_events)}
    for name in vanished:
        match = next((e for e in crash_events
                      if e["app"].lower() == name.lower()
                      or name.lower() in e["app"].lower()), None)
        if match:
            say(f"💥 {name} CRASHED — faulting module: {match['faulting_module']}")
            _log_crash(name, match["faulting_module"], match["detail"], vitals_ring, True)
            beacon_event()   # [BEACON] blue flash: crash record written
            if notify_telegram:
                _mach = machine or machine_label()
                _crash_sig = f"{name} {match['faulting_module']}"
                _hint = fix_hint(_crash_sig)
                send_telegram(f"💥 LKEY SENTINEL — CRASH DETECTED\n"
                              f"Machine: {_mach}\n"
                              f"Program: {name}\n"
                              f"Faulting module: {match['faulting_module']}\n"
                              f"Vitals + full debug saved to the crash log.{_hint}")
        else:
            # vanished but no crash record — log quietly, no ping (probably normal)
            _log_crash(name, "?", "", vitals_ring, False)


def watch(cfg, notify=None, stop=lambda: False):
    import socket
    ring = []
    machine_name = machine_label()  # friendly name from .env, else hostname
    say = notify or (lambda m: print(m))
    say(f"🛡️ Sentinel online ({machine_name}) — gentle watch every {cfg['poll_seconds']}s. "
        "Warns before trouble; pings Telegram on danger; logs a black box.")
    if send_telegram(f"🛡️ Sentinel is now watching {machine_name}. "
                     "You'll get a message here if anything gets dangerous."):
        say("   📡 Telegram link confirmed — startup ping sent to you.")
    last_alert = 0
    last_alert_sig = ""
    prev_procs = _running_procs()   # baseline for crash detection
    while not stop():
        s = sample()
        ring.append(s)
        ring[:] = ring[-cfg["history_ring"]:]
        # 💥 crash detection: did anything die since last poll?
        now_procs = _running_procs()
        try:
            detect_crashes(prev_procs, now_procs, ring, say, machine=machine_name)
        except Exception:
            pass
        prev_procs = now_procs
        alerts = evaluate(s, cfg)
        # [BEACON] turn the streak machinery into colour truth
        _BEACON["sample"] = s
        _BEACON["flash_secs"] = float(cfg.get("event_flash_seconds", 6))
        _need_b = int(cfg.get("sustain_samples", 3))
        if alerts:
            beacon_set("red")
        elif any(0 < v < _need_b for v in _BREACH_STREAK.values()):
            beacon_set("yellow")
        else:
            beacon_set("green")
        if alerts:
            now = time.time()
            # signature of WHICH conditions are firing (e.g. "VRAM;RAM")
            sig = ";".join(sorted(lvl_msg[1].split()[0] for lvl_msg in alerts))
            # only alert if: it's a NEW/changed condition, OR the cooldown passed
            # (sustained conditions like gaming warn ONCE, not every 20s)
            cooldown = cfg.get("alert_cooldown_seconds", 600)  # 10 min default
            new_condition = (sig != last_alert_sig)
            if new_condition or (now - last_alert > cooldown):
                for lvl, msg in alerts:
                    say(f"⚠️ {msg} — save your game / expect instability")
                write_blackbox(ring, alerts)
                summary = "; ".join(m for _, m in alerts)
                try:
                    hogs = memory_hogs(top=5)
                except Exception:
                    hogs = []
                hog_line = ("\nTop memory: " + ", ".join(hogs)) if hogs else ""
                if send_telegram(f"🛡️ LKEY SENTINEL — {machine_name}\n"
                                 f"⚠️ {summary}\nState: {fmt(s)}{hog_line}"):
                    say("   📡 Telegram alert sent (with memory report).")
                last_alert = now
                last_alert_sig = sig
        else:
            last_alert_sig = ""   # conditions cleared — reset so it can warn again
        time.sleep(max(2, int(cfg["poll_seconds"])))


NEVER_TOUCH = [
    # games / launchers / platforms
    "steam", "epicgames", "riotclient", "leagueclient", "valorant",
    "xbox", "gamebar", "battlenet", "origin", "eaapp", "ubisoft",
    "gog", "rockstar", "minecraft",
    # voice / social while gaming
    "discord", "teamspeak", "mumble", "obs", "streamlabs",
    # anti-cheat — killing these = instant ban or crash
    "easyanticheat", "eac", "battleye", "vanguard", "vgtray", "vgc",
    # the OS itself
    "explorer", "dwm", "csrss", "winlogon", "services", "svchost",
    "system", "registry", "lsass", "wininit", "smss", "ntoskrnl",
    # our own watcher
    "sentinel", "python", "pythonw",
]

# The ONLY family we will offer to close: extra browser helper processes.
# (The main browser window stays; only surplus background renderers go —
#  and never if the browser is the focused window.)
SAFE_TO_SUGGEST = ["chrome", "msedge", "firefox", "brave", "opera"]


def _foreground_pid():
    """PID of the window she's actually using RIGHT NOW. Always spared."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    except Exception:
        return None


def _foreground_family_pids():
    """The focused window's PID plus its parent and children — i.e. the whole
    game+launcher+anticheat lineage she's actively using. ALL sacred.
    Works for ANY game, even one this code has never seen, because it never
    needs the game's name — only that it's what she's using right now."""
    sacred = set()
    fg = _foreground_pid()
    if fg is None:
        return sacred
    sacred.add(fg)
    try:
        import psutil
        try:
            proc = psutil.Process(fg)
            # walk UP to parents (launcher) and DOWN to children (helpers)
            par = proc.parent()
            hops = 0
            while par and hops < 4:
                sacred.add(par.pid)
                par = par.parent()
                hops += 1
            for child in proc.children(recursive=True):
                sacred.add(child.pid)
            # also spare siblings sharing the immediate parent (game + its
            # sibling helper processes launched by the same launcher)
            if proc.parent():
                for sib in proc.parent().children(recursive=True):
                    sacred.add(sib.pid)
        except Exception:
            pass
    except Exception:
        pass
    return sacred


def memory_hogs(top=6):
    """REPORT ONLY: the biggest RAM users right now, for the Telegram alert.
    Pure observation — closes nothing."""
    out = []
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(attrs=["pid", "name", "memory_info"]):
            try:
                mb = p.info["memory_info"].rss / 1_048_576
                procs.append((mb, p.info["name"] or "?", p.info["pid"]))
            except Exception:
                continue
        for mb, name, pid in sorted(procs, reverse=True)[:top]:
            out.append(f"{name} {mb:.0f}MB")
    except Exception:
        pass
    return out


def free_browser_tabs(dry_run=True, notify=print):
    """MANUAL, OPT-IN. The ONLY things it will ever close: surplus background
    processes of plain WEB BROWSERS. It spares, unconditionally:
      - anything on NEVER_TOUCH
      - the entire foreground family (her game + launcher + anti-cheat +
        helpers), identified by what she's USING, not by name
      - each browser's main window (largest process of that browser)
    Anything that is not a known web browser is never even a candidate, so
    a brand-new game/engine/launcher she's never run is safe automatically."""
    import psutil
    sacred = _foreground_family_pids()

    by_name = {}
    for p in psutil.process_iter(attrs=["pid", "name", "memory_info"]):
        try:
            nm = (p.info["name"] or "").lower()
        except Exception:
            continue
        # allowlist: MUST be a known browser, and not on the never list
        if any(b in nm for b in SAFE_TO_SUGGEST) and not any(x in nm for x in NEVER_TOUCH):
            by_name.setdefault(nm, []).append(p)

    victims = []
    for nm, plist in by_name.items():
        if len(plist) <= 1:
            continue  # lone process = main window; never touch
        plist.sort(key=lambda p: p.info["memory_info"].rss, reverse=True)
        for p in plist[1:]:                       # helpers only, main spared
            if p.info["pid"] in sacred:
                continue                          # focused browser + family spared
            victims.append(p)

    if not victims:
        notify("🧹 Nothing safe to free — only essential/foreground apps running. "
               "(Her game and everything she's using are always protected.)")
        return 0

    if dry_run:
        preview = ", ".join(f"{p.info['name']}({p.info['pid']})" for p in victims[:8])
        notify(f"🧹 Would free {len(victims)} background browser tab(s): {preview}")
        return len(victims)

    freed = 0
    for p in victims:
        try:
            p.terminate()
            freed += 1
        except Exception:
            pass
    try:
        psutil.wait_procs(victims, timeout=3)
    except Exception:
        pass
    notify(f"🧹 Freed {freed} background browser tab(s). Everything she was "
           "using — game, launcher, voice, anti-cheat — left untouched.")
    return freed


# ---------------------------------------------------------------
# [BEACON] The orb, its frames, the Panel, and the tray runner.
# Research-verified (Grok + Gemini, independently): the Windows tray
# can only ever display a static icon, so all animation is swapping
# pre-rendered frames. We render every frame ONCE at startup —
# negligible CPU, no per-tick drawing, no GDI leak risk (pystray
# manages the handles).
# ---------------------------------------------------------------
_BEACON_RGB = {
    "green":  (72, 199, 116),
    "yellow": (232, 176, 46),
    "red":    (226, 61, 55),
    "blue":   (66, 158, 235),
}
_BEACON_WORD = {
    "green": "calm",
    "yellow": "watching a rise",
    "red": "DANGER sustained",
    "blue": "event logged",
}


def _beacon_frames(animate=True, size=64, steps=20):
    """Pre-render every orb frame at startup. animate=False builds a
    single calm frame per colour — the photosensitivity law: with the
    breath off there is NO periodic redraw at all, only a swap when
    the state itself changes."""
    from PIL import Image, ImageDraw
    import math
    frames = {}
    for name, (r, g, b) in _BEACON_RGB.items():
        seq = []
        n = steps if animate else 1
        for i in range(n):
            phase = math.sin(2 * math.pi * i / n) if animate else 0.0
            rad = 20 + 3 * phase                       # gentle breath, +/-3px
            glow = int(60 + 35 * (phase + 1) / 2)      # halo follows softly
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            c = size / 2
            d.ellipse([c - rad - 5, c - rad - 5, c + rad + 5, c + rad + 5],
                      fill=(r, g, b, glow))
            d.ellipse([c - rad, c - rad, c + rad, c + rad],
                      fill=(r, g, b, 255))
            seq.append(img)
        frames[name] = seq
    return frames


def _open_panel(state, cfg, actions):
    """[PANEL v1.2] Optional always-on-top mini window. PURE STDLIB tkinter
    so the public tool stays dependency-light for gamers. Sanctuary-dark
    (or Glass Engine) live bars + flight-recorder sparkline + the tools
    as buttons. ONE thread owns the Panel for its whole life; clicking
    the header orb to cycle dark<->glass REBUILDS the window inside that
    same thread — never a second Tk, never a race. If a previous panel
    thread died badly, the alive-check below self-heals the state."""
    th = state.get("panel_thread")
    if th is not None and th.is_alive() and state.get("panel_alive"):
        state["panel_lift"] = True       # already open -> tick() lifts it
        return
    state["panel_alive"] = False         # heal any wedged flag
    import threading

    def _run():
        try:
            import tkinter as tk
        except Exception:
            _BEACON["last"] = "Panel needs tkinter (python.org builds include it)"
            return
        state["panel_alive"] = True
        try:
            while True:                  # one lap per skin; same thread rebuilds
                mode = str(cfg.get("panel_mode", "dark")).lower()
                if mode == "glass":   # [GLASS ENGINE] the Sanctuary sensory mode
                    BG, FG, DIM, ACC = "#050505", "#00EE66", "#008833", "#00FF88"
                    BAR_BG, BTN_BG, BTN_ABG = "#0A0A0A", "#031503", "#052505"
                    _alpha = 0.85
                else:                 # Sanctuary dark (default)
                    BG, FG, DIM, ACC = "#121417", "#d7dde2", "#8b949e", "#8FBC8F"
                    BAR_BG, BTN_BG, BTN_ABG = "#232a31", "#1b2026", "#232a31"
                    _alpha = 1.0
                COLS = {"green": "#48c774", "yellow": "#e8b02e",
                        "red": "#e23d37", "blue": "#429eeb"}
                root = tk.Tk()
                root.title("Sentinel")
                root.configure(bg=BG)
                root.attributes("-topmost", True)
                root.resizable(False, False)
                try:
                    root.attributes("-alpha", _alpha)   # [GLASS ENGINE] translucency
                except Exception:
                    pass

                head = tk.Canvas(root, width=304, height=32, bg=BG, highlightthickness=0)
                head.pack(padx=12, pady=(10, 0))
                bars = tk.Canvas(root, width=304, height=118, bg=BG, highlightthickness=0)
                bars.pack(padx=12)
                spark = tk.Canvas(root, width=304, height=64, bg=BG, highlightthickness=0)
                spark.pack(padx=12, pady=(6, 2))
                last = tk.Label(root, bg=BG, fg=DIM, font=("Segoe UI", 8),
                                wraplength=294, justify="left", anchor="w")
                last.pack(padx=12, fill="x")
                btnrow = tk.Frame(root, bg=BG)
                btnrow.pack(padx=12, pady=(4, 10), fill="x")
                for i in range(2):
                    btnrow.columnconfigure(i, weight=1)
                for idx, (label, fn) in enumerate(actions.items()):
                    tk.Button(btnrow, text=label, command=fn, bg=BTN_BG, fg=FG,
                              activebackground=BTN_ABG, activeforeground=FG,
                              relief="flat", font=("Segoe UI", 8), padx=6, pady=3
                              ).grid(row=idx // 2, column=idx % 2,
                                     padx=3, pady=2, sticky="ew")

                def bar(y, label, val, text):
                    bars.create_text(4, y, anchor="w", fill=FG,
                                     font=("Segoe UI", 8), text=label)
                    x0, x1 = 56, 300
                    bars.create_rectangle(x0, y - 5, x1, y + 5,
                                          outline=BAR_BG, width=1)
                    if isinstance(val, (int, float)):
                        frac = max(0.0, min(1.0, float(val) / 100.0))
                        bars.create_rectangle(x0, y - 5, x0 + frac * (x1 - x0), y + 5,
                                              fill=ACC, width=0)
                    bars.create_text(x1, y - 12, anchor="e", fill=DIM,
                                     font=("Segoe UI", 7), text=text)

                def _spark_points():
                    try:
                        lines = FLIGHT_LOG.read_text(encoding="utf-8").strip().splitlines()
                        if len(lines) < 3:
                            return [], ""
                        hdr = lines[0].split(",")
                        col = "gpu_temp" if "gpu_temp" in hdr else "cpu"
                        ci = hdr.index(col)
                        pts = []
                        for ln in lines[-120:]:
                            parts = ln.split(",")
                            if parts and parts[0] != "date" and len(parts) > ci:
                                try:
                                    pts.append(float(parts[ci]))
                                except ValueError:
                                    pass
                        return pts, col
                    except Exception:
                        return [], ""

                def tick():
                    try:
                        if not state.get("panel_alive"):
                            return
                        if state.pop("panel_lift", False):
                            root.deiconify()
                            root.lift()
                            root.attributes("-topmost", True)
                        s = dict(_BEACON.get("sample") or {})
                        st = beacon_state()
                        head.delete("all")
                        head.create_oval(4, 7, 22, 25, fill=COLS.get(st, ACC), width=0)
                        head.create_text(30, 16, anchor="w", fill=FG,
                                         font=("Segoe UI", 10, "bold"),
                                         text=f"{_BEACON_WORD[st]} · {machine_label()}")
                        bars.delete("all")
                        cpu_txt = f"{s.get('cpu', '--')}%"
                        if s.get("cpu_temp") is not None:
                            cpu_txt += f" · {s['cpu_temp']}°C"
                        bar(16, "CPU", s.get("cpu"), cpu_txt)
                        ram_txt = f"{s.get('ram', '--')}%"
                        if "ram_used_gb" in s:
                            ram_txt += f" · {s['ram_used_gb']} GB"
                        bar(44, "RAM", s.get("ram"), ram_txt)
                        if "gpu_load" in s or "gpu_temp" in s:
                            gpu_txt = f"{s.get('gpu_load', '--')}%"
                            if "gpu_temp" in s:
                                gpu_txt += f" · {s['gpu_temp']}°C"
                        else:
                            gpu_txt = "no NVML"
                        bar(72, "GPU", s.get("gpu_load"), gpu_txt)
                        if "vram" in s:
                            vr_txt = f"{s.get('vram', '--')}%"
                            if "vram_total_gb" in s:
                                vr_txt += (f" · {s.get('vram_used_gb', '?')}"
                                           f"/{s.get('vram_total_gb', '?')} GB")
                        else:
                            vr_txt = "no NVML"
                        bar(100, "VRAM", s.get("vram"), vr_txt)
                        spark.delete("all")
                        pts, col = _spark_points()
                        spark.create_text(4, 8, anchor="w", fill=DIM, font=("Segoe UI", 7),
                                          text=f"flight recorder · {col or 'no data yet'}")
                        if len(pts) >= 2:
                            lo, hi = min(pts), max(pts)
                            span = (hi - lo) or 1.0
                            w, h = 304, 64
                            step = (w - 8) / (len(pts) - 1)
                            coords = []
                            for i2, v in enumerate(pts):
                                coords += [4 + i2 * step,
                                           h - 6 - (v - lo) / span * (h - 22)]
                            spark.create_line(*coords, fill=ACC, width=1)
                            spark.create_text(300, 8, anchor="e", fill=DIM,
                                              font=("Segoe UI", 7), text=f"{lo:.0f}-{hi:.0f}")
                        if _BEACON.get("last"):
                            last.config(text=_BEACON["last"][:160])
                        root.after(2000, tick)
                    except Exception:
                        return            # window died mid-draw: stop quietly

                def _cycle_mode(_e=None):
                    # [GLASS ENGINE v1.2] persist the new skin, then let THIS
                    # thread's loop rebuild — never a second window, no race
                    new_mode = "glass" if mode != "glass" else "dark"
                    cfg["panel_mode"] = new_mode
                    try:
                        _cur = {}
                        try:
                            _cur = json.loads(CFG.read_text(encoding="utf-8"))
                        except Exception:
                            pass
                        _cur["panel_mode"] = new_mode
                        CFG.write_text(json.dumps(_cur, indent=2), encoding="utf-8")
                    except OSError:
                        pass
                    state["panel_restyle"] = True
                    try:
                        root.destroy()
                    except Exception:
                        pass
                head.bind("<Button-1>", _cycle_mode)

                def on_close():
                    try:
                        root.destroy()
                    except Exception:
                        pass
                root.protocol("WM_DELETE_WINDOW", on_close)
                tick()
                root.mainloop()
                if state.pop("panel_restyle", False):
                    continue             # same thread, new skin
                break
        finally:
            state["panel_alive"] = False

    t = threading.Thread(target=_run, daemon=True)
    state["panel_thread"] = t
    t.start()


def _self_icon(here):
    """[SELF-HEAL] The Sentinel's own face. Prefers a shipped
    LKEY_SENTINEL.ico; otherwise draws one from the same orb it wears in
    the tray, written in classic BMP frames — the only ICO encoding every
    Windows shell path accepts (PNG-framed icons silently fall back to
    generic on .bat/.vbs shortcuts). No download, no new dependency."""
    ico = here / "LKEY_SENTINEL.ico"
    try:
        if ico.exists() and ico.stat().st_size > 2000:
            return ico
    except Exception:
        pass
    try:
        import struct
        from PIL import Image, ImageDraw
        sizes = [16, 24, 32, 48, 64, 128, 256]
        frames = []
        for sz in sizes:
            img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            c = sz / 2.0
            r = sz * 0.33
            glow = sz * 0.10
            d.ellipse([c - r - glow, c - r - glow, c + r + glow, c + r + glow],
                      fill=(72, 199, 116, 80))
            d.ellipse([c - r, c - r, c + r, c + r], fill=(72, 199, 116, 255))
            d.ellipse([c - r * 0.55, c - r * 0.62, c + r * 0.30, c + r * 0.15],
                      fill=(168, 245, 195, 170))
            px = img.load()
            w = h = sz
            hdr = struct.pack('<IiiHHIIiiII', 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
            xor = bytearray()
            for y in range(h - 1, -1, -1):
                for x in range(w):
                    _r, _g, _b, _a = px[x, y]
                    xor += bytes((_b, _g, _r, _a))
            row_bytes = ((w + 31) // 32) * 4
            frames.append((sz, hdr + bytes(xor) + bytes(bytearray(row_bytes * h))))
        head = struct.pack('<HHH', 0, 1, len(frames))
        offset = 6 + 16 * len(frames)
        entries, blobs = b'', b''
        for sz, data in frames:
            b = sz if sz < 256 else 0
            entries += struct.pack('<BBBBHHII', b, b, 0, 0, 1, 32, len(data), offset)
            blobs += data
            offset += len(data)
        ico.write_bytes(head + entries + blobs)
        return ico
    except Exception:
        return None


def _heal_shortcut():
    """[SELF-HEAL] Make the desktop shortcut exist and wear the orb.
    The updater ships CODE, never Windows shortcut objects — so the app
    repairs its own on every start. Idempotent, silent, non-Windows safe."""
    import os as _os
    if _os.name != "nt":
        return
    try:
        import subprocess as _sp
        here = Path(__file__).resolve().parent
        ico = _self_icon(here)
        if ico is None:
            return
        # [PIN v2] Windows will not hold a taskbar pin on a shortcut whose
        # target is a .bat, so pointing at START_SENTINEL.bat meant the pin
        # vanished the moment the app closed. sys.executable is the
        # interpreter actually running us — a real .exe, correct on every
        # machine, no hardcoded path. Prefer pythonw so nothing flashes.
        _py = sys.executable
        if _py.lower().endswith("python.exe"):
            _pw = _py[:-len("python.exe")] + "pythonw.exe"
            if Path(_pw).exists():
                _py = _pw
        target = _py
        _args = '"' + str(Path(__file__).resolve()) + '" --tray'
        ps = (
            "$sh=New-Object -ComObject WScript.Shell;"
            "$p=[Environment]::GetFolderPath('Desktop')+'\\Lkey Sentinel.lnk';"
            "$s=$sh.CreateShortcut($p);"
            "$s.TargetPath='" + target + "';"
            "$s.Arguments='" + _args + "';"   # [PIN v2]
            "$s.WorkingDirectory='" + str(here) + "';"
            "$s.IconLocation='" + str(ico) + ",0';"
            "$s.Save()"
        )
        _sp.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                capture_output=True, timeout=25)
    except Exception:
        pass


# ---------------------------------------------------------------
# [PERF DOCTOR] every rival shows numbers; this one says what's wrong
# ---------------------------------------------------------------
_DOCTOR_HEAVY = {
    "msmpeng.exe": ("Windows Defender is scanning in the background",
                    "Add your game and project folders to Defender's exclusion "
                    "list (Windows Security > Virus & threat protection > "
                    "Manage settings > Exclusions)."),
    "searchindexer.exe": ("Windows Search is rebuilding its index",
                          "It settles on its own. If it never does, rebuild the "
                          "index in Indexing Options > Advanced."),
    "vmmem": ("WSL / a virtual machine is holding a large block of memory",
              "Cap it in C:\\Users\\<you>\\.wslconfig with memory=8GB and "
              "autoMemoryReclaim=gradual, then run: wsl --shutdown"),
    "vmmemwsl": ("WSL is holding a large block of memory",
                 "Cap it in .wslconfig (memory=8GB, autoMemoryReclaim=gradual), "
                 "then: wsl --shutdown"),
    "ollama.exe": ("A local AI model is resident in memory",
                   "That is normal while you use it. Free it with: ollama stop <model>"),
    "tiworker.exe": ("Windows Update is installing in the background",
                     "Let it finish, or pause updates while you play."),
    "compattelrunner.exe": ("Windows telemetry is collecting data",
                            "Safe to end. It reschedules itself; harmless either way."),
    "onedrive.exe": ("OneDrive is syncing files",
                     "Pause syncing while you work: tray icon > Pause sync."),
}


def _doctor_procs(top=8):
    """Heaviest processes right now. Read-only."""
    out = []
    try:
        import psutil
        procs = []
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                p.cpu_percent(None)
                procs.append(p)
            except Exception:
                continue
        time.sleep(0.4)                     # one short window for real CPU %
        rows = []
        for p in procs:
            try:
                rows.append((p.info.get("name") or "?",
                             p.cpu_percent(None),
                             (p.info.get("memory_info").rss if p.info.get("memory_info") else 0) / 1e9))
            except Exception:
                continue
        rows.sort(key=lambda r: (r[1], r[2]), reverse=True)
        out = rows[:top]
    except Exception:
        pass
    return out


def _diagnose(cfg=None):
    """Return (headline, [lines]) — what is wrong, in plain words, with a
    cure attached to every finding. Observes only; never acts."""
    cfg = cfg or {}
    findings, notes = [], []
    s = {}
    try:
        s = sample()
    except Exception:
        pass

    # --- the vitals, judged against honest thresholds ---
    ram = s.get("ram")
    if isinstance(ram, (int, float)) and ram >= 90:
        findings.append((
            f"Memory is nearly full ({ram:.0f}%)",
            "Windows starts swapping to disk, which is what makes everything "
            "feel sticky. Close what you are not using, or see the heavy "
            "processes below."))
    vram = s.get("vram")
    if isinstance(vram, (int, float)) and vram >= 90:
        findings.append((
            f"Video memory is nearly full ({vram:.0f}%"
            + (f", {s.get('vram_used_gb')}/{s.get('vram_total_gb')} GB" if s.get("vram_total_gb") else "")
            + ")",
            "Games and AI models fall back to system RAM when VRAM runs out, "
            "which causes stutter. Lower texture quality, or unload a resident "
            "AI model (ollama stop <model>)."))
    gt = s.get("gpu_temp")
    if isinstance(gt, (int, float)) and gt >= 83:
        findings.append((
            f"The graphics card is hot ({gt:.0f}\u00b0C)",
            "It will throttle itself to protect the hardware, and that shows up "
            "as sudden frame drops. Check case airflow and dust in the GPU fans."))
    ct = s.get("cpu_temp")
    if isinstance(ct, (int, float)) and ct >= 90:
        findings.append((
            f"The processor is hot ({ct:.0f}\u00b0C)",
            "It will slow itself down to stay safe. Usually dust in the cooler "
            "or dried thermal paste."))

    # --- the heavy processes, named and explained ---
    rows = _doctor_procs()
    known = []
    for name, cpu, gb in rows:
        key = (name or "").lower()
        if key in _DOCTOR_HEAVY and (cpu >= 5 or gb >= 1.5):
            what, cure = _DOCTOR_HEAVY[key]
            known.append((f"{what} ({name}: {cpu:.0f}% CPU, {gb:.1f} GB)", cure))
    findings.extend(known)
    if rows:
        notes.append("Heaviest right now: " + ", ".join(
            f"{n} {c:.0f}%/{g:.1f}GB" for n, c, g in rows[:4]))

    # --- our own footprint, honestly reported ---
    try:
        import psutil, os as _os
        me = psutil.Process(_os.getpid())
        notes.append(f"Sentinel itself: {me.memory_info().rss/1e6:.0f} MB "
                     f"(we watch the machine; we do not weigh on it)")
    except Exception:
        pass

    # --- disk pressure: the quiet cause of 'everything hangs' ---
    try:
        import psutil
        du = psutil.disk_usage("C:\\" if _os_name_is_nt() else "/")
        if du.percent >= 92:
            findings.append((
                f"The system drive is nearly full ({du.percent:.0f}%)",
                "Windows needs free space for its page file and temp files. "
                "Below about 10% free, everything slows down. Clear space or "
                "move large folders to another drive."))
    except Exception:
        pass

    if not findings:
        head = "\u2705 Nothing is wrong that I can see."
        notes.insert(0, "Vitals are inside honest thresholds and no heavy "
                        "background process is competing with you.")
    elif len(findings) == 1:
        head = "\u26a0\ufe0f One thing is slowing this machine down."
    else:
        head = f"\u26a0\ufe0f {len(findings)} things are slowing this machine down."

    lines = []
    for what, cure in findings:
        lines.append(f"\u2022 {what}")
        lines.append(f"   \u2192 {cure}")
    if notes:
        lines.append("")
        lines.extend(notes)
    return head, lines


def _os_name_is_nt():
    import os as _os
    return _os.name == "nt"


def doctor_text(cfg=None):
    """One printable diagnosis. Never raises."""
    try:
        head, lines = _diagnose(cfg)
        return head + ("\n" + "\n".join(lines) if lines else "")
    except Exception as e:
        return f"Doctor could not finish ({e.__class__.__name__}: {e})"


def run_tray(cfg):
    """[BEACON] The tray IS the status: an orb wearing the Honest Alert's
    colour truth. Monk-minimal menu — Show Panel (left-click default) and
    Quit. Tooltip carries a SHORT state word only; the old long status
    line stretched the taskbar. Every tool the menu used to carry now
    lives as a Panel button: nothing shipped was lost, it moved indoors."""
    import pystray
    import threading
    try:   # [SELF-HEAL] own taskbar identity — the orb, not Python's icon
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Lkey.Sentinel")
    except Exception:
        pass
    _heal_shortcut()   # [SELF-HEAL] a shortcut that wears its own face
    animate = bool(cfg.get("beacon_animate", True))
    frames = _beacon_frames(animate=animate)
    state = {"stop": False, "panel_alive": False, "panel_lift": False}

    def report(m):
        _BEACON["last"] = m

    # ---- the kept tools (moved from menu to Panel buttons) ----
    def act_screenshot():                 # [EYES-V2] all screens + reveal + telegram
        def _do():
            try:
                from PIL import ImageGrab
                import os
                import subprocess
                from datetime import datetime
                shots = os.path.join(os.path.expanduser("~"),
                                     "Pictures", "Lkey_Screenshots")
                os.makedirs(shots, exist_ok=True)
                _drop_safety_note(shots)
                fname = os.path.join(
                    shots, f"Sentinel_SS_{datetime.now():%Y%m%d_%H%M%S}.png")
                try:
                    ImageGrab.grab(all_screens=True).save(fname)
                except TypeError:
                    ImageGrab.grab().save(fname)   # older PIL: primary only
                report(f"\U0001f4f8 Saved: {os.path.basename(fname)}")
                try:   # reveal in Explorer with the new file selected
                    subprocess.Popen(
                        ["explorer", "/select,", os.path.normpath(fname)])
                except Exception:
                    pass
                if send_telegram_photo(fname,
                                       f"\U0001f4f8 {machine_label()} screenshot"):
                    report(_BEACON["last"] + " + sent to Telegram")
            except Exception as _e:
                report(f"Screenshot failed: {_e}")
        threading.Thread(target=_do, daemon=True).start()

    def act_open_caps():                  # [EYES-V2]
        import os as _os
        import subprocess as _sp
        d = _os.path.join(_os.path.expanduser("~"),
                          "Pictures", "Lkey_Screenshots")
        try:
            _os.makedirs(d, exist_ok=True)
            if hasattr(_os, "startfile"):
                _os.startfile(d)
            else:
                _sp.Popen(["explorer.exe", d])
        except Exception as _e:
            report(f"Could not open captures: {_e}")

    def act_free_mem():
        def _do():
            try:
                free_browser_tabs(dry_run=False, notify=report)
            except Exception as _e:
                report(f"Free memory failed: {_e}")
        threading.Thread(target=_do, daemon=True).start()

    def act_check_updates():
        def _do():
            try:
                from sentinel_updater import check_for_updates
                check_for_updates(notify=report, apply=True)
            except Exception as _e:
                report(f"Update check failed: {_e}")
        threading.Thread(target=_do, daemon=True).start()

    def act_doctor():                     # [PERF DOCTOR]
        def _do():
            try:
                report("\U0001fa7a reading the machine\u2026")
                _t = doctor_text(cfg)
                report(_t[:900])
                print("\n" + _t + "\n")
            except Exception as _e:
                report(f"Doctor failed: {_e}")
        threading.Thread(target=_do, daemon=True).start()

    actions = {
        "\U0001fa7a What's wrong?": act_doctor,
        "\U0001f4f8 Screenshot": act_screenshot,
        "Open captures": act_open_caps,
        "Free memory (safe)": act_free_mem,
        "Check for updates": act_check_updates,
    }

    def _quit(icon, item):
        state["stop"] = True
        state["panel_alive"] = False
        icon.stop()

    def _show_panel(icon=None, item=None):
        _open_panel(state, cfg, actions)

    # menu: choice AND fluidity — every Panel tool lives here too, short
    # labels only; the long status line that stretched the taskbar stays
    # gone. Built from the SAME actions dict as the Panel buttons: one
    # source of truth, change once, both surfaces update.
    _menu_items = [pystray.MenuItem("Show Panel", _show_panel, default=True)]
    for _lbl, _fn in actions.items():
        _menu_items.append(pystray.MenuItem(_lbl, (lambda f: (lambda *a: f()))(_fn)))
    _menu_items.append(pystray.MenuItem("Quit", _quit))
    icon = pystray.Icon(
        "Sentinel", frames["green"][0], "Sentinel — calm",
        menu=pystray.Menu(*_menu_items))

    def _animate():
        i = 0
        shown = None
        while not state["stop"]:
            # [SINGLE INSTANCE] a second launch left a poke — show the Panel
            try:
                _pk = _poke_path()
                if _pk.exists():
                    _pk.unlink()
                    _show_panel()
            except Exception:
                pass
            st = beacon_state()
            seq = frames[st]
            if animate:
                icon.icon = seq[i % len(seq)]
                i += 1
            elif st != shown:
                icon.icon = seq[0]        # static law: swap ONLY on change
            if st != shown:
                icon.title = f"Sentinel — {_BEACON_WORD[st]}"
                shown = st
            time.sleep(0.12 if animate else 0.5)

    def _watch():
        watch(cfg, notify=report, stop=lambda: state["stop"])

    threading.Thread(target=_watch, daemon=True).start()
    threading.Thread(target=_animate, daemon=True).start()
    icon.run()                            # must own the main thread on Windows


# ---------------------------------------------------------------
# [SINGLE INSTANCE] one Sentinel, poked awake — never a second copy
# ---------------------------------------------------------------
def _poke_path():
    try:
        return CFG.parent / ".sentinel_poke"
    except Exception:
        return Path(__file__).resolve().parent / ".sentinel_poke"


def _single_instance_guard():
    """True = we are the first Sentinel, carry on. False = one is already
    watching; we poked it to show its Panel and should exit quietly.

    Named mutex, not a lockfile — the OS drops it however the process
    ends, so there is no stale lock to strand the next launch."""
    import os as _os
    if _os.name != "nt" or "--new" in sys.argv:
        return True
    try:
        import ctypes
        _k32 = ctypes.windll.kernel32
        _k32.CreateMutexW(None, False, "Lkey.Sentinel.SingleInstance")
        if _k32.GetLastError() == 183:          # ERROR_ALREADY_EXISTS
            try:
                _poke_path().write_text(str(time.time()), encoding="utf-8")
            except OSError:
                pass
            return False
    except Exception:
        pass                                    # never block a launch on this
    return True


def main():
    # apply any verified update that was staged on a previous run (safe: nothing
    # is running from the file yet). Never blocks startup if it fails.
    try:
        from sentinel_updater import apply_staged_update
        apply_staged_update(notify=lambda m: print(m))
    except Exception:
        pass
    cfg = load_cfg()
    if "--once" in sys.argv:
        s = sample()
        print("🛡️ " + fmt(s))
        a = evaluate(s, cfg)
        for lvl, msg in a:
            print(f"  ⚠️ {msg}")
        if not a:
            print("  ✅ all readings in the safe zone")
        return
    if "--doctor" in sys.argv:         # [PERF DOCTOR]
        print(doctor_text(cfg))
        return
    if not _single_instance_guard():   # [SINGLE INSTANCE]
        print("Sentinel is already watching — poked it to show its Panel.")
        return
    if "--tray" in sys.argv:
        try:
            run_tray(cfg)
            return
        except Exception as e:
            print(f"(tray unavailable: {e} — running in console instead)")
    watch(cfg)


if __name__ == "__main__":
    main()
