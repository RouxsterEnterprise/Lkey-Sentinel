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

DEFAULTS = {
    "poll_seconds": 5,          # slow + gentle; raise to 10 for even less load
    "gpu_temp_warn": 83,        # °C — 5090 throttles ~83-88; warn before that
    "cpu_temp_warn": 90,        # °C
    "ram_percent_warn": 92,     # % — approaching exhaustion
    "vram_percent_warn": 94,    # % — VRAM pressure precedes many game crashes
    "history_ring": 12,         # keep last N samples in the black box on alert
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
        util = pynvml.nvmlDeviceGetUtilizationRates(h)
        s["gpu_load"] = util.gpu
        pynvml.nvmlShutdown()
    except Exception:
        pass  # no NVML -> GPU fields simply absent; CPU/RAM watch still works
    return s


def evaluate(s, cfg):
    """Return list of (level, message) warnings for this sample."""
    alerts = []
    def chk(key, limit, label, unit="°C"):
        v = s.get(key)
        if isinstance(v, (int, float)) and v >= limit:
            alerts.append(("DANGER", f"{label} {v}{unit} ≥ {limit}{unit}"))
    chk("gpu_temp", cfg["gpu_temp_warn"], "GPU temp")
    chk("cpu_temp", cfg["cpu_temp_warn"], "CPU temp")
    chk("ram", cfg["ram_percent_warn"], "RAM", "%")
    chk("vram", cfg["vram_percent_warn"], "VRAM", "%")
    return alerts


def write_blackbox(ring, alerts):
    """On alert, dump the recent sample ring so the pre-crash state survives."""
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
        bits.append(f"VRAM {s['vram']}%")
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
    if send_telegram(f"🛡️ Lkey Sentinel is now watching {machine_name}. "
                     "You'll get a message here if anything gets dangerous."):
        say("   📡 Telegram link confirmed — startup ping sent to you.")
    last_alert = 0
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
        if alerts:
            now = time.time()
            if now - last_alert > 20:   # don't spam; one alert / 20s
                for lvl, msg in alerts:
                    say(f"⚠️ {msg} — save your game / expect instability")
                write_blackbox(ring, alerts)
                # ping YOU on Telegram (her machine can't read logs; you can)
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
    if "--tray" in sys.argv:
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (64, 64), (10, 10, 10))
            d = ImageDraw.Draw(img)
            d.ellipse([16, 16, 48, 48], fill=(0, 200, 0))
            state = {"stop": False, "last": "starting..."}
            def _quit(icon, item):
                state["stop"] = True
                icon.stop()
            def _free_mem(icon, item):
                free_browser_tabs(dry_run=False, notify=lambda m: setattr(state, "last", m) if False else state.update(last=m))
            def _check_updates(icon, item):
                def _do():
                    try:
                        from sentinel_updater import check_for_updates
                        def _report(msg):
                            state["last"] = msg
                            try:
                                icon.update_menu()
                            except Exception:
                                pass
                        check_for_updates(notify=_report, apply=True)
                    except Exception as _e:
                        state["last"] = f"Update check failed: {_e}"
                        try:
                            icon.update_menu()
                        except Exception:
                            pass
                import threading as _th
                _th.Thread(target=_do, daemon=True).start()
            icon = pystray.Icon("LkeySentinel", img, "Lkey Sentinel",
                                menu=pystray.Menu(
                                    pystray.MenuItem(lambda i: state["last"], None, enabled=False),
                                    pystray.MenuItem("Check for updates", _check_updates),
                                    pystray.MenuItem("Free memory (safe)", _free_mem),
                                    pystray.MenuItem("Quit", _quit)))
            import threading
            def run():
                def note(m):
                    state["last"] = m
                    icon.title = "Lkey Sentinel: " + m[:40]
                    # refresh the menu so the status label actually updates
                    try:
                        icon.update_menu()
                    except Exception:
                        pass
                # flip from "starting..." to an active status once the watch loop begins
                import socket as _sock
                note(f"\u2705 Watching {_sock.gethostname()} \u2014 all clear")
                watch(cfg, notify=note, stop=lambda: state["stop"])
            threading.Thread(target=run, daemon=True).start()
            icon.run()
            return
        except Exception as e:
            print(f"(tray unavailable: {e} — running in console instead)")
    watch(cfg)


if __name__ == "__main__":
    main()
