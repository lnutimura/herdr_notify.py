# -*- coding: utf-8 -*-
#
# herdr_notify.py — tell Herdr when a WeeChat buffer you cannot see has unread messages
#
# Copyright (c) 2026 lnutimura
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# Install and the Herdr config snippet: see README.md in this repository.
#
# Settings (plugins.var.python.herdr_notify.*):
#   level            message | highlight
#   delay            seconds to wait before notifying
#   sound_highlight  none | done | request (macOS banners)
#   sound_message    none | done | request (macOS banners)
#   title_badge      on | off (sidebar tokens $weechat_unread, $summary)
#   max_body_length  banner body cap; 0 leaves the cap to macOS
#   retry_limit      attempts for a banner or a Herdr report
#   delivery         auto | herdr | terminal-notifier
#   terminal_bundle  macOS bundle id, empty to detect, or off

import glob
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

try:
    import weechat
except ImportError:  # unit tests load this file outside WeeChat
    weechat = None

SCRIPT_NAME = "herdr_notify"
SCRIPT_AUTHOR = "lnutimura"
SCRIPT_VERSION = "0.4.0"
SCRIPT_LICENSE = "MIT"
SCRIPT_DESC = "Tell Herdr when a WeeChat buffer you cannot see has unread messages"

SETTINGS = (
    ("level", "highlight", "highlight (mentions and private messages) or message (all channel text)"),
    ("delay", "3", "seconds to wait before notifying, so a buffer you open stays quiet"),
    ("sound_highlight", "request", "macOS banner sound for a mention or private message: none, done, or request"),
    ("sound_message", "none", "macOS banner sound for other channel messages: none, done, or request"),
    ("title_badge", "on", "on or off; publish the unread count and summary as Herdr sidebar tokens"),
    ("max_body_length", "240", "maximum macOS banner body length; 0 leaves the limit to macOS"),
    ("retry_limit", "30", "how many times to retry a macOS banner or a report to Herdr"),
    (
        "delivery",
        "auto",
        "auto, herdr, or terminal-notifier; auto uses terminal-notifier on macOS when it is installed",
    ),
    (
        "terminal_bundle",
        "",
        "macOS bundle id to activate on click; empty detects the terminal; off skips activation",
    ),
)

TITLE_LIMIT = 80
BODY_LIMIT = 240
# What one queued line keeps. The body cap applies when the notification is built.
TEXT_LIMIT = 4096
FOCUS_CACHE_SECONDS = 0.3
RETRY_SECONDS = 1.0
TIMER_MS = 500
# Longer than terminal-notifier's own 10 s watchdog, so its exit code 4 arrives
# before WeeChat kills the process.
PROCESS_TIMEOUT_MS = 15000
# WeeChat buffer notify property: 0 none, 1 highlight, 2 message, 3 all.
NOTIFY_NONE = 0
NOTIFY_HIGHLIGHT = 1
# How this script appears to Herdr. Herdr reserves the "herdr:" prefix.
AGENT_SOURCE = "custom:weechat"
AGENT_LABEL = "weechat"
# Sidebar tokens come from a separate source, as Herdr's docs do it.
METADATA_SOURCE = "custom:weechat-display"
# Herdr's maximum. Tokens disappear by themselves if WeeChat dies.
METADATA_TTL_MS = 86400000
NOTIFIER_CANDIDATES = (
    "/opt/homebrew/bin/terminal-notifier",
    "/usr/local/bin/terminal-notifier",
)
NOTIFIER_APP_GLOBS = (
    "/opt/homebrew/opt/terminal-notifier/terminal-notifier.app/Contents/MacOS/terminal-notifier",
    "/usr/local/opt/terminal-notifier/terminal-notifier.app/Contents/MacOS/terminal-notifier",
    "/opt/homebrew/Cellar/terminal-notifier/*/terminal-notifier.app/Contents/MacOS/terminal-notifier",
    "/usr/local/Cellar/terminal-notifier/*/terminal-notifier.app/Contents/MacOS/terminal-notifier",
    "~/Applications/terminal-notifier.app/Contents/MacOS/terminal-notifier",
    "/Applications/terminal-notifier.app/Contents/MacOS/terminal-notifier",
)
NOTIFIER_MISSING = (
    "terminal-notifier was not found. Herdr's own alerts will be used. "
    "Install it with: brew install terminal-notifier"
)
NOTIFIER_DENIED = (
    "terminal-notifier is not allowed to post notifications. Turn it on in "
    "System Settings, under Notifications, for terminal-notifier. Herdr's own "
    "alerts will be used until this script is reloaded."
)
# TERM_PROGRAM, or a terminal-specific variable, to the bundle id -activate expects.
TERMINAL_BUNDLES = {
    "ghostty": "com.mitchellh.ghostty",
    "iTerm.app": "com.googlecode.iterm2",
    "Apple_Terminal": "com.apple.Terminal",
    "WezTerm": "com.github.wez.wezterm",
    "WarpTerminal": "dev.warp.Warp-Stable",
}
MACOS_SOUNDS = {"request": "Ping", "done": "Glass"}

queued = []
next_id = 1
timer_hook = ""
focus_hook = ""
notify_hook = ""
focus_cache = {"at": 0.0, "pane_id": None}
next_attempt = 0.0
notify_failures = 0
focus_failures = 0
inflight_ids = []
# Lines you were alerted about and have not looked at yet. While this is not
# empty, Herdr shows WeeChat as blocked.
unread = []
# One Herdr report per kind ("agent"): what it should say, what Herdr last
# accepted, and the process sending it. Only the latest wanted value is sent.
reports = {}
last_seq = 0
# hook_process delivers large output in several calls; these collect it.
notify_output = {"out": "", "err": ""}
focus_output = {"out": ""}
prefer_herdr = False
give_up = False
beep_suspended = False
logged = set()
outer_focused = True
bound_keys = []


@dataclass
class Event:
    """One queued line. `due` is a time.monotonic() deadline."""

    id: int
    buffer: str
    name: str
    nick: str
    text: str
    kind: str
    due: float
    # WeeChat's full buffer name (irc.libera.#chan), for /buffer on click.
    full_name: str = ""


def setting(name):
    if weechat is None:
        for key, default, _desc in SETTINGS:
            if key == name:
                return default
        return ""
    value = weechat.config_get_plugin(name)
    if value == "":
        for key, default, _desc in SETTINGS:
            if key == name:
                return default
    return value


def level_floor():
    if setting("level").strip().lower() == "highlight":
        return "highlight"
    return "message"


def sound_choice(name):
    value = setting(name).strip().lower()
    if value in ("none", "done", "request"):
        return value
    return "none"


def delay_seconds():
    try:
        value = float(setting("delay"))
    except ValueError:
        return 3.0
    return value if value > 0 else 0.0


def retry_limit():
    try:
        value = int(setting("retry_limit"))
    except ValueError:
        return 30
    return value if value > 0 else 1


def body_limit():
    """0 means no cap here; Herdr or macOS shortens the body itself."""
    try:
        value = int(setting("max_body_length"))
    except ValueError:
        return BODY_LIMIT
    return max(value, 0)


def title_badge_enabled():
    return setting("title_badge").strip().lower() != "off"


def sanitize(text, limit):
    cleaned = []
    for ch in "" if text is None else str(text):
        cleaned.append(ch if ch.isprintable() and ch != "\x7f" else " ")
    collapsed = " ".join("".join(cleaned).split())
    if limit > 0 and len(collapsed) > limit:
        collapsed = collapsed[: limit - 1].rstrip() + "…"
    return collapsed


def stdout_command(payload):
    """Build a /print -stdout command. WeeChat treats ';' as a command separator."""
    escaped = payload.replace("\\", "\\\\").replace(";", r"\;")
    return "/print -stdout " + escaped


def line_kind(tags, highlight):
    tagset = {tag for tag in (tags or "").split(",") if tag}
    if "self_msg" in tagset or "notify_none" in tagset:
        return None
    if str(highlight) == "1" or "notify_highlight" in tagset or "notify_private" in tagset:
        return "highlight"
    if "notify_message" in tagset:
        return "message"
    return None


def allowed(kind, buffer_notify, floor):
    if kind is None:
        return False
    try:
        notify_value = int(buffer_notify)
    except (TypeError, ValueError):
        notify_value = 3
    if notify_value <= NOTIFY_NONE:
        return False
    if notify_value == NOTIFY_HIGHLIGHT and kind != "highlight":
        return False
    if floor == "highlight" and kind != "highlight":
        return False
    return True


def keep_pending(due, now, on_screen, pane_focused, outer_is_focused):
    if due > now:
        return True
    return not (on_screen and pane_focused and outer_is_focused)


def parse_json(text):
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def focused_pane_id(payload):
    """Return the session focus. Per-tab layout focus is a different field.

    Only the reply's own level is read. A pane listed deeper in a snapshot is
    not the focused one, so a snapshot without a session focus returns None.
    """
    if not isinstance(payload, dict):
        return None
    result = payload.get("result", payload)
    if not isinstance(result, dict):
        return None
    if result.get("type") in ("pane_current", "pane_info"):
        pane = result.get("pane")
        pane_id = pane.get("pane_id") if isinstance(pane, dict) else None
        return pane_id if isinstance(pane_id, str) and pane_id else None
    for node in (result.get("snapshot"), result):
        if isinstance(node, dict) and ("layouts" in node or "workspaces" in node):
            pane_id = node.get("focused_pane_id")
            if isinstance(pane_id, str) and pane_id:
                return pane_id
    return None


def notification_reason(payload):
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        return error.get("code") or error.get("message") or "error"
    result = payload.get("result", payload)
    if isinstance(result, dict):
        reason = result.get("reason")
        if isinstance(reason, str) and reason:
            return reason
        if result.get("shown") is True:
            return "shown"
    return None


def choose_title(events):
    if len(events) == 1:
        nick = events[0].nick or events[0].name or "WeeChat"
        name = events[0].name or "WeeChat"
        return sanitize("%s in %s" % (nick, name), TITLE_LIMIT)
    names = []
    for event in events:
        if event.name and event.name not in names:
            names.append(event.name)
    noun = "messages"
    if len(names) == 1:
        title = "%d %s in %s" % (len(events), noun, names[0])
    elif names:
        title = "%d %s in %s" % (len(events), noun, ", ".join(names))
    else:
        title = "%d %s" % (len(events), noun)
    if len(title) > TITLE_LIMIT:
        title = "%d %s" % (len(events), noun)
    return sanitize(title, TITLE_LIMIT)


def featured_event(events):
    """The latest mention if there is one, else the latest line."""
    highlights = [event for event in events if event.kind == "highlight"]
    return highlights[-1] if highlights else events[-1]


def choose_body(events):
    chosen = featured_event(events)
    if len(events) == 1:
        return sanitize(chosen.text, body_limit())
    if chosen.nick and chosen.text:
        body = "%s: %s" % (chosen.nick, chosen.text)
    else:
        body = chosen.text or chosen.nick
    return sanitize(body, body_limit())


def choose_sound(events):
    if any(event.kind == "highlight" for event in events):
        return sound_choice("sound_highlight")
    return sound_choice("sound_message")


def delivery_mode(value):
    return (value or "auto").strip().lower().replace("_", "-")


def backend_name(delivery, platform, binary, fallback):
    """auto posts with terminal-notifier on macOS when the binary exists."""
    mode = delivery_mode(delivery)
    if mode == "herdr" or fallback:
        return "herdr"
    wants_notifier = mode == "terminal-notifier" or (mode == "auto" and platform == "darwin")
    if wants_notifier and binary:
        return "terminal-notifier"
    return "herdr"


def find_notifier(platform, which, usable, bundles=()):
    """Prefer the binary inside terminal-notifier.app. A loose copy is a different app to macOS."""
    if platform != "darwin":
        return ""
    found = which("terminal-notifier") if which else ""
    resolved = os.path.realpath(found) if found else ""
    app_paths = []
    other_paths = []
    if "terminal-notifier.app" in resolved:
        app_paths.append(resolved)
    elif found:
        other_paths.append(found)
    for path in list(bundles) + list(NOTIFIER_CANDIDATES):
        if "terminal-notifier.app" in path:
            app_paths.append(path)
        else:
            other_paths.append(path)
    seen = set()
    for path in app_paths + other_paths:
        if not path or path in seen:
            continue
        seen.add(path)
        if usable(path):
            return path
    return ""


def bundled_notifier_paths():
    found = []
    for pattern in NOTIFIER_APP_GLOBS:
        found.extend(sorted(glob.glob(os.path.expanduser(pattern))))
    return found


def notifier_path():
    def usable(path):
        return os.path.isfile(path) and os.access(path, os.X_OK)

    return find_notifier(sys.platform, shutil.which, usable, bundled_notifier_paths())


def detect_terminal_bundle(environ):
    program = environ.get("TERM_PROGRAM", "")
    if program in TERMINAL_BUNDLES:
        return TERMINAL_BUNDLES[program]
    if environ.get("KITTY_WINDOW_ID") or environ.get("KITTY_PID"):
        return "net.kovidgoyal.kitty"
    if environ.get("ALACRITTY_WINDOW_ID") or environ.get("ALACRITTY_SOCKET"):
        return "org.alacritty"
    return ""


def chosen_bundle(configured, environ):
    value = (configured or "").strip()
    if value.lower() == "off":
        return ""
    if value:
        return value
    return detect_terminal_bundle(environ)


def macos_sound(name):
    return MACOS_SOUNDS.get(name, "")


def notifier_text(text):
    """NSUserDefaults misreads a value whose first character looks like a plist or a flag."""
    value = "" if text is None else str(text)
    if value[:1] in "[{(\"'\\-":
        return "\\" + value
    return value


def notification_group(pane_id):
    safe = "".join(ch if ch.isalnum() else "-" for ch in (pane_id or "pane"))
    return "herdr-notify-" + safe.strip("-")


def shell_quote(text):
    return "'" + str(text).replace("'", "'\\''") + "'"


FOCUS_PROGRAM = """\
import os, socket
if FIFO:
    try:
        fd = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, LINE)
        finally:
            os.close(fd)
    except OSError:
        pass
s = socket.socket(socket.AF_UNIX)
s.settimeout(5)
s.connect(os.environ["HERDR_SOCKET_PATH"])
s.sendall(REQUEST)
s.recv(65536)
"""


def fifo_line(full_name):
    """A WeeChat fifo line that switches to the buffer, or b"" when the name is unsafe.

    The fifo runs one command per line, and ';' or a backslash could change
    what /buffer receives, so such names are not switched to.
    """
    if not full_name or any(ch in full_name for ch in ";\\") or not full_name.isprintable():
        return b""
    return ("*/buffer %s\n" % full_name).encode("utf-8")


def focus_python(pane_id, fifo="", full_name=""):
    """Herdr's CLI focuses a neighbor, not an arbitrary pane. pane.focus does.

    The program is one exec() call, so it fits on a single command line.
    """
    payload = json.dumps(
        {
            "id": "herdr-notify:focus",
            "method": "pane.focus",
            "params": {"pane_id": pane_id},
        },
        separators=(",", ":"),
    )
    line = fifo_line(full_name)
    program = (
        "FIFO = %r\nLINE = %r\nREQUEST = %r\n" % (fifo if line else "", line, (payload + "\n").encode("utf-8"))
        + FOCUS_PROGRAM
    )
    return "exec(%r)" % program


def resolve_python(executable, which, usable):
    """WeeChat's embedded Python often reports the WeeChat binary as sys.executable."""
    if os.path.basename(executable or "").startswith("python") and usable(executable):
        return executable
    found = which("python3") if which else ""
    if found:
        return found
    for path in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"):
        if usable(path):
            return path
    return ""


def python_bin():
    def usable(path):
        return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)

    return resolve_python(sys.executable, shutil.which, usable)


def focus_command(python, socket_path, pane_id, fifo="", full_name=""):
    if not python or not socket_path or not pane_id:
        return ""
    return "HERDR_SOCKET_PATH=%s %s -c %s" % (
        shell_quote(socket_path),
        shell_quote(python),
        shell_quote(focus_python(pane_id, fifo, full_name)),
    )


def notifier_arguments(title, body, sound, group, bundle, execute):
    """Flags and values are separate argv entries. `-message=text` is not a `-message`."""
    shown = title or "WeeChat"

    def option(flag, value):
        return ["-" + flag, notifier_text(value)]

    args = option("title", shown) + option("message", body or shown) + option("group", group)
    if sound:
        args.extend(option("sound", sound))
    if bundle:
        args.extend(option("activate", bundle))
    if execute:
        args.extend(option("execute", execute))
    return args


def unread_summary(events):
    """One line for Herdr: the latest mention if there is one, else the latest line."""
    if not events:
        return ""
    latest = featured_event(events)
    where = latest.name or "WeeChat"
    if latest.nick:
        line = "%s in %s: %s" % (latest.nick, where, latest.text)
    else:
        line = "%s: %s" % (where, latest.text)
    if len(events) > 1:
        line = "(%d) %s" % (len(events), line)
    return sanitize(line, TITLE_LIMIT)


def metadata_report_args(pane_id, events):
    args = ["pane", "report-metadata", pane_id, "--source", METADATA_SOURCE]
    if not events:
        return tuple(args + ["--clear-token", "weechat_unread", "--clear-token", "summary"])
    return tuple(
        args
        + ["--token", "weechat_unread=%d" % len(events)]
        + ["--token", "summary=" + unread_summary(events)]
        + ["--ttl-ms", str(METADATA_TTL_MS)]
    )


def agent_report_args(pane_id, events):
    """blocked makes Herdr mark the pane, toast and play its request sound."""
    args = ["pane", "report-agent", pane_id, "--source", AGENT_SOURCE, "--agent", AGENT_LABEL]
    if events:
        return tuple(args + ["--state", "blocked", "--message", unread_summary(events)])
    return tuple(args + ["--state", "idle"])


def next_seq():
    """Herdr ignores a report whose seq is not above the last one it accepted."""
    global last_seq
    last_seq = max(last_seq + 1, time.time_ns())
    return last_seq


def notifier_problem(code, err, out=""):
    detail = " ".join(((err or "") + "\n" + (out or "")).split())
    if len(detail) > 200:
        detail = detail[:199].rstrip() + "…"
    if detail:
        return "terminal-notifier failed (%s): %s" % (code, detail)
    return "terminal-notifier failed (%s)." % code


def herdr_bin():
    binary = os.environ.get("HERDR_BIN_PATH", "").strip()
    return binary or "herdr"


def our_pane_id():
    return os.environ.get("HERDR_PANE_ID", "").strip()


def log_once(key, text):
    if weechat is None or key in logged:
        return
    logged.add(key)
    weechat.prnt("", "%s: %s" % (SCRIPT_NAME, text))


def init_settings():
    for name, default, desc in SETTINGS:
        if not weechat.config_is_set_plugin(name):
            weechat.config_set_plugin(name, default)
        weechat.config_set_desc_plugin(name, desc)


def beep_enabled():
    option = weechat.config_get("trigger.trigger.beep.enabled")
    if not option:
        return False
    return weechat.config_string(option) == "on"


def suspend_beep():
    global beep_suspended
    if weechat.config_get_plugin("beep_suspended") == "on":
        beep_suspended = True
        return
    if not beep_enabled():
        return
    weechat.command("", "/mute /trigger disable beep")
    weechat.config_set_plugin("beep_suspended", "on")
    beep_suspended = True


def restore_beep():
    global beep_suspended
    if weechat.config_get_plugin("beep_suspended") != "on" and not beep_suspended:
        return
    weechat.command("", "/mute /trigger enable beep")
    weechat.config_set_plugin("beep_suspended", "off")
    beep_suspended = False


def key_is_bound(key):
    infolist = weechat.infolist_get("key", "", "default")
    if not infolist:
        return False
    found = False
    while weechat.infolist_next(infolist):
        if weechat.infolist_string(infolist, "key") == key:
            found = True
            break
    weechat.infolist_free(infolist)
    return found


def bind_focus_keys():
    for key, arg in (("meta-[I", "focus"), ("meta-[O", "unfocus")):
        if key_is_bound(key):
            continue
        weechat.command("", "/key bind %s /%s %s" % (key, SCRIPT_NAME, arg))
        bound_keys.append(key)


def unbind_focus_keys():
    global bound_keys
    for key in bound_keys:
        weechat.command("", "/mute /key unbind %s" % key)
    bound_keys = []


def set_focus_reporting(enabled):
    """Mode 1004 makes the terminal send meta-[I and meta-[O on focus changes."""
    weechat.command("", stdout_command("\x1b[?1004%s\n" % ("h" if enabled else "l")))


def refresh_badge():
    """Unread and pending lines, as sidebar tokens. Off clears them."""
    pane_id = our_pane_id()
    if pane_id:
        events = unread + queued if title_badge_enabled() else []
        want_report("metadata", metadata_report_args(pane_id, events))


def ensure_timer():
    global timer_hook
    if not timer_hook:
        timer_hook = weechat.hook_timer(TIMER_MS, 0, 0, "timer_cb", "")


def stop_timer_if_idle():
    global timer_hook
    if queued or focus_hook or notify_hook or reports_pending():
        return
    if timer_hook:
        weechat.unhook(timer_hook)
        timer_hook = ""


def buffer_name(buffer):
    name = weechat.buffer_get_string(buffer, "short_name")
    if not name:
        name = weechat.buffer_get_string(buffer, "name")
    return sanitize(name or "", TITLE_LIMIT)


def buffer_on_screen(buffer):
    return weechat.buffer_get_integer(buffer, "num_displayed") > 0


def drop_buffer(buffer):
    global queued
    before = len(queued)
    queued = [event for event in queued if event.buffer != buffer]
    if len(queued) != before:
        refresh_badge()
    mark_seen({buffer})
    stop_timer_if_idle()


def due_events(now):
    return [event for event in queued if event.due <= now]


def drop_visible(now, pane_id):
    global queued
    pane_focused = bool(pane_id) and pane_id == our_pane_id()
    queued = [
        event
        for event in queued
        if keep_pending(
            event.due,
            now,
            buffer_on_screen(event.buffer),
            pane_focused,
            outer_focused,
        )
    ]
    if pane_focused and outer_focused:
        mark_seen({event.buffer for event in unread if buffer_on_screen(event.buffer)})


def cache_is_fresh(now):
    return focus_cache["pane_id"] is not None and now - focus_cache["at"] <= FOCUS_CACHE_SECONDS


def process_status(return_code):
    try:
        code = int(return_code)
    except (TypeError, ValueError):
        return "done"
    running = getattr(weechat, "WEECHAT_HOOK_PROCESS_RUNNING", -1)
    error = getattr(weechat, "WEECHAT_HOOK_PROCESS_ERROR", -2)
    if code == running:
        return "running"
    if code == error:
        return "error"
    return "done"


def start_process(command, args, callback, data=""):
    options = {}
    for index, arg in enumerate(args, start=1):
        options["arg%d" % index] = arg
    return weechat.hook_process_hashtable(command, options, PROCESS_TIMEOUT_MS, callback, data)


def fail_focus(text):
    """After a few misses, notify anyway. A focus failure must not hide messages."""
    global focus_failures, next_attempt
    focus_failures += 1
    if focus_failures < 3:
        next_attempt = time.monotonic() + RETRY_SECONDS
        return
    log_once("focus", "%s Notifications will be sent without a focus check." % text)
    focus_cache["at"] = time.monotonic()
    focus_cache["pane_id"] = ""
    focus_failures = 0
    next_attempt = 0.0


def forget(sent_ids):
    global queued
    queued = [event for event in queued if event.id not in sent_ids]


def deliver(events):
    """The user has been alerted about these lines; they stay unread until seen."""
    forget({event.id for event in events})
    unread.extend(events)
    refresh_badge()
    sync_agent()


def mark_seen(buffers):
    global unread
    if not buffers:
        return
    before = len(unread)
    unread = [event for event in unread if event.buffer not in buffers]
    if len(unread) != before:
        refresh_badge()
        sync_agent()


def sync_agent():
    pane_id = our_pane_id()
    if pane_id:
        want_report("agent", agent_report_args(pane_id, unread))


def want_report(kind, args):
    report = reports.setdefault(
        kind,
        {"want": None, "sent": None, "inflight": None, "hook": "", "failures": 0, "retry_at": 0.0},
    )
    report["want"] = tuple(args)
    flush_reports()


def reports_pending():
    return any(report["want"] != report["sent"] for report in reports.values())


def flush_reports():
    """Send each report whose wanted value changed. One process per kind at a time."""
    if give_up:
        return
    now = time.monotonic()
    for kind, report in reports.items():
        if report["hook"] or report["want"] == report["sent"] or now < report["retry_at"]:
            continue
        args = report["want"] + ("--seq", str(next_seq()))
        hook = start_process(herdr_bin(), args, "report_cb", kind)
        if not hook:
            give_up_binary()
            return
        report["hook"] = hook
        report["inflight"] = report["want"]
    if reports_pending():
        ensure_timer()


def report_cb(data, command, return_code, out, err):
    report = reports.get(data)
    if report is None or process_status(return_code) == "running":
        return weechat.WEECHAT_RC_OK
    report["hook"] = ""
    # The CLI exits 0 when Herdr accepted the report, 1 when Herdr returned an error.
    if process_status(return_code) == "done" and exit_code(return_code) == 0:
        report["sent"] = report["inflight"]
        report["failures"] = 0
    elif report["failures"] < retry_limit():
        report["failures"] += 1
        report["retry_at"] = time.monotonic() + RETRY_SECONDS
    else:
        detail = " ".join((err or out or "no reply").split())[:200]
        log_once("report-" + data, "Herdr did not accept the %s report: %s" % (data, detail))
        report["sent"] = report["inflight"]
        report["failures"] = 0
    flush_reports()
    stop_timer_if_idle()
    return weechat.WEECHAT_RC_OK


def release_agent():
    """Runs at unload, where a hooked process would be killed before it finishes."""
    pane_id = our_pane_id()
    if not pane_id or give_up:
        return
    run_now(metadata_report_args(pane_id, []))
    run_now(("pane", "release-agent", pane_id, "--source", AGENT_SOURCE, "--agent", AGENT_LABEL))


def run_now(args):
    try:
        subprocess.run(
            [herdr_bin()] + list(args) + ["--seq", str(next_seq())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def consider_send():
    if give_up:
        queued.clear()
        stop_timer_if_idle()
        return
    now = time.monotonic()
    if now < next_attempt or notify_hook or focus_hook:
        return
    if not due_events(now):
        stop_timer_if_idle()
        return
    if not cache_is_fresh(now):
        start_focus_query()
        return
    drop_visible(now, focus_cache["pane_id"])
    refresh_badge()
    ready = due_events(now)
    if not ready:
        stop_timer_if_idle()
        return
    start_notify(ready)


def start_focus_query():
    global focus_hook
    if focus_hook:
        return
    focus_output["out"] = ""
    hook = start_process(herdr_bin(), ("api", "snapshot"), "focus_cb")
    if not hook:
        fail_focus("could not run %s." % herdr_bin())
        return
    focus_hook = hook


def notifier_binary():
    """The terminal-notifier to post with, or "" when Herdr's own alert is used."""
    binary = notifier_path()
    delivery = setting("delivery")
    if delivery_mode(delivery) == "terminal-notifier" and not binary:
        log_once("notifier-missing", NOTIFIER_MISSING)
    if backend_name(delivery, sys.platform, binary, prefer_herdr) != "terminal-notifier":
        return ""
    return binary


def notifier_invocation(binary, title, body, sound, full_name=""):
    bundle = chosen_bundle(setting("terminal_bundle"), os.environ)
    socket_path = os.environ.get("HERDR_SOCKET_PATH", "").strip()
    python = python_bin()
    # Empty when the fifo plugin is not loaded; the click then only focuses the pane.
    fifo = weechat.info_get("fifo_filename", "")
    execute = focus_command(python, socket_path, our_pane_id(), fifo, full_name)
    if not socket_path:
        log_once(
            "notifier-focus",
            "HERDR_SOCKET_PATH is unset, so clicking the notification cannot focus this pane.",
        )
    elif not python:
        log_once(
            "notifier-python",
            "python3 was not found, so clicking the notification cannot focus this pane.",
        )
    return notifier_arguments(
        title,
        body,
        macos_sound(sound),
        notification_group(our_pane_id()),
        bundle,
        execute,
    )


def start_notify(events):
    """Herdr alerts on the blocked report itself; macOS also gets a banner first."""
    global notify_hook, inflight_ids, prefer_herdr, next_attempt
    binary = notifier_binary()
    if not binary:
        deliver(events)
        return
    title = choose_title(events) or "WeeChat"
    target = featured_event(events).full_name
    args = notifier_invocation(binary, title, choose_body(events), choose_sound(events), target)
    notify_output["out"] = ""
    notify_output["err"] = ""
    inflight_ids = [event.id for event in events]
    hook = start_process(binary, args, "notify_cb")
    if hook:
        notify_hook = hook
        return
    inflight_ids = []
    prefer_herdr = True
    next_attempt = 0.0
    log_once("notifier-missing", NOTIFIER_MISSING)


def give_up_binary():
    global give_up
    give_up = True
    queued.clear()
    unread.clear()
    restore_beep()
    log_once("binary", "could not run %s. Is it on PATH?" % herdr_bin())
    stop_timer_if_idle()


def enqueue(buffer, nick, text, kind):
    global next_id
    queued.append(
        Event(
            next_id,
            buffer,
            buffer_name(buffer),
            sanitize(nick, TITLE_LIMIT),
            sanitize(text, TEXT_LIMIT),
            kind,
            time.monotonic() + delay_seconds(),
            weechat.buffer_get_string(buffer, "full_name"),
        )
    )
    next_id += 1
    refresh_badge()
    ensure_timer()


def command_cb(data, buffer, args):
    global outer_focused
    arg = (args or "").strip()
    if arg == "focus":
        # Herdr forwards focus-in only to the focused pane, so the buffers on
        # screen in this WeeChat are now in front of the user.
        outer_focused = True
        mark_seen({event.buffer for event in unread if buffer_on_screen(event.buffer)})
        now = time.monotonic()
        for event in queued:
            if event.buffer == buffer:
                event.due = now
        consider_send()
    elif arg == "unfocus":
        outer_focused = False
    return weechat.WEECHAT_RC_OK


def message_cb(data, buffer, date, tags, displayed, highlight, prefix, message):
    if give_up or str(displayed) != "1":
        return weechat.WEECHAT_RC_OK
    kind = line_kind(tags, highlight)
    if not allowed(kind, weechat.buffer_get_integer(buffer, "notify"), level_floor()):
        return weechat.WEECHAT_RC_OK
    enqueue(buffer, prefix, message, kind)
    return weechat.WEECHAT_RC_OK


def buffer_switch_cb(data, signal, buffer):
    mark_seen({buffer})
    now = time.monotonic()
    matched = False
    for event in queued:
        if event.buffer == buffer:
            event.due = now
            matched = True
    if matched:
        ensure_timer()
        consider_send()
    return weechat.WEECHAT_RC_OK


def buffer_closing_cb(data, signal, buffer):
    drop_buffer(buffer)
    return weechat.WEECHAT_RC_OK


def timer_cb(data, remaining):
    consider_send()
    flush_reports()
    stop_timer_if_idle()
    return weechat.WEECHAT_RC_OK


def focus_cb(data, command, return_code, out, err):
    global focus_hook, focus_failures, next_attempt
    status = process_status(return_code)
    focus_output["out"] += out or ""
    if status == "running":
        return weechat.WEECHAT_RC_OK
    focus_hook = ""
    captured = focus_output["out"]
    focus_output["out"] = ""
    if status == "error":
        fail_focus("could not run %s." % herdr_bin())
    else:
        payload = parse_json(captured)
        pane_id = focused_pane_id(payload) if payload else None
        if pane_id is None:
            reason = notification_reason(payload) if payload else None
            fail_focus(reason or "could not read the focused Herdr pane.")
        else:
            focus_failures = 0
            next_attempt = 0.0
            focus_cache["at"] = time.monotonic()
            focus_cache["pane_id"] = pane_id
    consider_send()
    return weechat.WEECHAT_RC_OK


def exit_code(return_code):
    try:
        return int(return_code)
    except (TypeError, ValueError):
        return 1


def notifier_finished(status, return_code, err, out=""):
    """A terminal-notifier failure falls back to Herdr's own alert. The events stay queued."""
    global prefer_herdr, notify_failures, next_attempt
    if status != "error":
        code = exit_code(return_code)
        if code == 0:
            notify_failures = 0
            return "shown"
        if code == 4 and notify_failures < retry_limit():
            notify_failures += 1
            next_attempt = time.monotonic() + RETRY_SECONDS
            return "retry"
    prefer_herdr = True
    notify_failures = 0
    next_attempt = 0.0
    if status != "error" and exit_code(return_code) == 3:
        log_once("notifier-auth", NOTIFIER_DENIED)
    else:
        code = "error" if status == "error" else exit_code(return_code)
        log_once("notifier-fail", notifier_problem(code, err, out))
    return "fallback"


def notify_cb(data, command, return_code, out, err):
    """terminal-notifier finished. A failure leaves the events queued for Herdr."""
    global notify_hook, inflight_ids
    status = process_status(return_code)
    notify_output["out"] += out or ""
    notify_output["err"] += err or ""
    if status == "running":
        return weechat.WEECHAT_RC_OK
    notify_hook = ""
    sent_ids = set(inflight_ids)
    inflight_ids = []
    captured_out = notify_output["out"]
    captured_err = notify_output["err"]
    notify_output["out"] = ""
    notify_output["err"] = ""
    outcome = notifier_finished(status, return_code, captured_err, captured_out)
    if outcome == "shown":
        deliver([event for event in queued if event.id in sent_ids])
    stop_timer_if_idle()
    return weechat.WEECHAT_RC_OK


def shutdown_cb():
    restore_beep()
    if our_pane_id():
        set_focus_reporting(False)
        release_agent()
    unbind_focus_keys()
    return weechat.WEECHAT_RC_OK


def register():
    if not weechat.register(
        SCRIPT_NAME,
        SCRIPT_AUTHOR,
        SCRIPT_VERSION,
        SCRIPT_LICENSE,
        SCRIPT_DESC,
        "shutdown_cb",
        "",
    ):
        return
    if not our_pane_id():
        restore_beep()
        return
    init_settings()
    suspend_beep()
    set_focus_reporting(True)
    weechat.hook_command(
        SCRIPT_NAME,
        "Track whether the terminal window is focused.",
        "focus|unfocus",
        "",
        "focus || unfocus",
        "command_cb",
        "",
    )
    bind_focus_keys()
    weechat.hook_print(
        "",
        "notify_message,notify_private,notify_highlight",
        "",
        1,
        "message_cb",
        "",
    )
    weechat.hook_signal("buffer_switch", "buffer_switch_cb", "")
    weechat.hook_signal("buffer_closing", "buffer_closing_cb", "")
    # Start idle and empty, which also clears what a crash left behind.
    sync_agent()
    refresh_badge()


if weechat is not None:
    register()
