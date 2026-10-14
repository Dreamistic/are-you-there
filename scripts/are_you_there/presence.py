"""Is the developer at the terminal, somewhere else on the computer, or away?

Two signals, both read from the OS:
  - how long since the last keyboard or mouse input (system-wide idle time)
  - which app has focus, and whether it is the terminal this hook runs in

The terminal check walks this process's parent chain: if the focused window
belongs to one of our ancestors, the user is looking at us. Terminals that
detach their shells (iTerm2, tmux) fall back to a list of known app names.

When the signals can't be read (SSH, containers, WSL, Wayland without GNOME),
the level is "unknown" and the hooks stay quiet instead of guessing.
"""
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

TERMINAL = "terminal"      # terminal focused, recent input
PRESENT = "present"        # recent input, focus unknown (treated like terminal)
ELSEWHERE = "elsewhere"    # recent input, another app focused
AWAY = "away"              # no input for a while, or screen locked
UNKNOWN = "unknown"        # can't tell

TERMINAL_APPS = {
    # Windows
    "windowsterminal.exe", "openconsole.exe", "conhost.exe", "mintty.exe", "wezterm-gui.exe",
    "alacritty.exe", "kitty.exe", "tabby.exe", "hyper.exe", "warp.exe", "ghostty.exe",
    "code.exe", "code - insiders.exe", "cursor.exe", "windsurf.exe", "zed.exe", "claude.exe",
    "idea64.exe", "pycharm64.exe", "webstorm64.exe", "goland64.exe", "clion64.exe", "rider64.exe",
    "phpstorm64.exe", "rubymine64.exe", "datagrip64.exe", "rustrover64.exe",
    # macOS (lsappinfo display names) and Linux (process names)
    "terminal", "iterm2", "ghostty", "wezterm", "wezterm-gui", "alacritty", "kitty", "warp", "hyper",
    "tabby", "code", "visual studio code", "code - insiders", "cursor", "windsurf", "zed", "claude",
    "intellij idea", "pycharm", "webstorm", "goland", "clion", "rider", "rustrover",
    "gnome-terminal-server", "gnome-terminal", "kgx", "ptyxis", "konsole", "xterm", "urxvt", "tilix",
    "terminator", "xfce4-terminal", "mate-terminal", "lxterminal", "foot", "st", "yakuake", "guake",
}
LOCK_APPS = {"lockapp.exe", "logonui.exe"}


@dataclass
class Presence:
    level: str
    idle: Optional[float] = None        # seconds since last input
    app: str = ""                       # focused app name
    on_terminal: Optional[bool] = None  # None: couldn't tell
    locked: bool = False
    reason: str = ""                    # why the level is unknown

    @property
    def away_minutes(self) -> int:
        return int((self.idle or 0) // 60)


def _run(args, timeout=2.0) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------- Windows

def _windows():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetTickCount.restype = wintypes.DWORD
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    idle = None
    if user32.GetLastInputInfo(ctypes.byref(info)):
        idle = ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0

    fg_pid = 0
    hwnd = user32.GetForegroundWindow()
    if hwnd:
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        fg_pid = pid.value

    # One snapshot gives every process's parent and exe name.
    table = {}
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snapshot and snapshot != wintypes.HANDLE(-1).value:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        kernel32.CloseHandle(snapshot)

    app = table.get(fg_pid, (0, ""))[1]
    ancestors = _ancestors(os.getpid(), {pid: parent for pid, (parent, _) in table.items()})
    locked = app.lower() in LOCK_APPS
    return idle, app, (fg_pid in ancestors) if fg_pid else None, locked


# ---------------------------------------------------------------- macOS

def _macos():
    idle = None
    match = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', _run(["ioreg", "-c", "IOHIDSystem", "-d", "4", "-r", "-k", "HIDIdleTime"]))
    if match:
        idle = int(match.group(1)) / 1e9

    locked = '"CGSSessionScreenIsLocked"=Yes' in _run(["ioreg", "-n", "Root", "-d1"]).replace(" ", "")

    # lsappinfo needs no Automation permission, unlike osascript + System Events.
    fg_pid, app = 0, ""
    asn = _run(["lsappinfo", "front"]).strip()
    if asn:
        info = _run(["lsappinfo", "info", "-only", "pid", asn])
        match = re.search(r'"?pid"?\s*=\s*(\d+)', info)
        fg_pid = int(match.group(1)) if match else 0
        match = re.search(r'=\s*"([^"]+)"', _run(["lsappinfo", "info", "-only", "name", asn]))
        app = match.group(1) if match else ""

    parents = {}
    for line in _run(["ps", "-axo", "pid=,ppid="]).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            parents[int(parts[0])] = int(parts[1])
    on_terminal = (fg_pid in _ancestors(os.getpid(), parents)) if fg_pid and parents else None
    return idle, app, on_terminal, locked


# ---------------------------------------------------------------- Linux

def _linux():
    idle = None
    if os.environ.get("WAYLAND_DISPLAY"):
        out = _run(["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor",
                    "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
                    "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"])
        match = re.search(r"(\d+)", out)
        if match:
            idle = int(match.group(1)) / 1000.0
    if idle is None and os.environ.get("DISPLAY"):
        out = _run(["xprintidle"]).strip()
        if out.isdigit():
            idle = int(out) / 1000.0

    locked = False
    session = os.environ.get("XDG_SESSION_ID")
    if session:
        locked = "LockedHint=yes" in _run(["loginctl", "show-session", session, "-p", "LockedHint"])

    fg_pid, app, on_terminal = 0, "", None
    if os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        out = _run(["xdotool", "getactivewindow", "getwindowpid"]).strip()
        if out.isdigit():
            fg_pid = int(out)
            try:
                app = Path(f"/proc/{fg_pid}/comm").read_text().strip()
            except OSError:
                pass
    if fg_pid:
        parents = {}
        for stat in Path("/proc").glob("[0-9]*/stat"):
            try:
                # The process name can contain spaces and parentheses; ppid follows the last ')'.
                fields = stat.read_text().rsplit(")", 1)[1].split()
                parents[int(stat.parent.name)] = int(fields[1])
            except (OSError, IndexError, ValueError):
                continue
        on_terminal = fg_pid in _ancestors(os.getpid(), parents)
    return idle, app, on_terminal, locked


# ---------------------------------------------------------------- common

def _ancestors(pid: int, parents: dict) -> set:
    seen = set()
    while pid and pid not in seen and len(seen) < 64:
        seen.add(pid)
        pid = parents.get(pid, 0)
    return seen


def _unreachable_reason() -> str:
    """Why OS signals here wouldn't describe the person at the keyboard, or '' if they would."""
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return "ssh"
    if sys.platform.startswith("linux"):
        if Path("/.dockerenv").exists() or os.environ.get("container"):
            return "container"
        try:
            if "microsoft" in Path("/proc/version").read_text().lower():
                return "wsl"
        except OSError:
            pass
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return "no-display"
    return ""


def classify(idle, on_terminal, locked, cfg) -> str:
    if idle is None:
        return UNKNOWN
    if locked:
        return AWAY
    away = cfg["away_minutes"] * 60
    # Reading a long answer without touching anything shouldn't count as leaving.
    reading = max(away, cfg["reading_minutes"] * 60)
    if on_terminal:
        return TERMINAL if idle < reading else AWAY
    if idle >= away:
        return AWAY
    return PRESENT if on_terminal is None else ELSEWHERE


def detect(cfg: dict) -> Presence:
    forced = _forced_level()
    if forced:
        idle = float(os.environ.get("ARE_YOU_THERE_FORCE_IDLE", 600 if forced == AWAY else 0))
        return Presence(level=forced, idle=idle, app=os.environ.get("ARE_YOU_THERE_FORCE_APP", ""),
                        on_terminal=forced == TERMINAL, reason="forced")
    reason = _unreachable_reason()
    if reason:
        return Presence(level=UNKNOWN, reason=reason)
    try:
        if sys.platform == "win32":
            idle, app, on_terminal, locked = _windows()
        elif sys.platform == "darwin":
            idle, app, on_terminal, locked = _macos()
        elif sys.platform.startswith("linux"):
            idle, app, on_terminal, locked = _linux()
        else:
            return Presence(level=UNKNOWN, reason="platform")
    except Exception as error:  # never let detection break Claude Code
        return Presence(level=UNKNOWN, reason=f"error: {error}")

    extra = {name.lower() for name in cfg.get("terminal_apps", [])}
    if on_terminal is False and app.lower() in (TERMINAL_APPS | extra):
        # Focused app is a terminal or editor that doesn't parent our shell (iTerm2, tmux, a second
        # window of the same app). Close enough: they are looking at code.
        on_terminal = True
    level = classify(idle, on_terminal, locked, cfg)
    return Presence(level=level, idle=idle, app=app, on_terminal=on_terminal, locked=locked,
                    reason="" if idle is not None else "no-idle-signal")


def _forced_level() -> str:
    """Testing hooks: ARE_YOU_THERE_FORCE_LEVEL, or a file whose content is the level."""
    path = os.environ.get("ARE_YOU_THERE_FORCE_FILE")
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return os.environ.get("ARE_YOU_THERE_FORCE_LEVEL", "").strip()
