# -*- coding: utf-8 -*-
#
# herdr_notify.py — tell Herdr when a WeeChat buffer you cannot see gets a message
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
#   sound_highlight  none | done | request
#   sound_message    none | done | request
#   title_badge      on | off
#   max_body_length  body cap; 0 leaves Herdr's own cap
#   retry_limit      attempts when Herdr is busy or rate-limited

from __future__ import annotations

import json
import os
import time

try:
    import weechat
except ImportError:  # unit tests load this file outside WeeChat
    weechat = None

SCRIPT_NAME = "herdr_notify"
SCRIPT_AUTHOR = "lnutimura"
SCRIPT_VERSION = "0.1.0"
SCRIPT_LICENSE = "MIT"
SCRIPT_DESC = "Notify through Herdr when a WeeChat buffer you cannot see gets a message"

SETTINGS = (
    ("level", "message", "message (channel text) or highlight (mentions and private messages)"),
    ("delay", "3", "seconds to wait before notifying, so a buffer you open stays quiet"),
    ("sound_highlight", "request", "sound for a mention or private message: none, done, or request"),
    ("sound_message", "none", "sound for other channel messages: none, done, or request"),
    ("title_badge", "on", "on or off; write the unread summary into the terminal title"),
    ("max_body_length", "240", "maximum body length; 0 leaves Herdr's own limit"),
    ("retry_limit", "30", "how many times to retry when Herdr is busy or rate-limited"),
)

TITLE_LIMIT = 80
BODY_LIMIT = 240
FOCUS_CACHE_SECONDS = 0.3
RETRY_SECONDS = 1.0
TIMER_MS = 500
# WeeChat buffer notify property: 0 none, 1 highlight, 2 message, 3 all.
NOTIFY_NONE = 0
NOTIFY_HIGHLIGHT = 1
TOAST_SNIPPET = (
    "Herdr toasts are off, so notifications cannot be shown. Add this to the "
    "Herdr config and reload it:\n"
    "[ui.toast]\n"
    'delivery = "herdr"\n'
    "\n"
    "[ui.toast.herdr]\n"
    'position = "bottom-right"'
)

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
give_up = False
beep_suspended = False
logged = set()
outer_focused = True
title_current = None
bound_keys = []


class Event:
    def __init__(self, event_id, buffer, name, nick, text, kind, due):
        self.id = event_id
        self.buffer = buffer
        self.name = name
        self.nick = nick
        self.text = text
        self.kind = kind
        self.due = due


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
    try:
        value = int(setting("max_body_length"))
    except ValueError:
        return BODY_LIMIT
    if value <= 0:
        return BODY_LIMIT
    return min(value, BODY_LIMIT)


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
    """Return the session focus. Per-tab layout focus is a different field."""

    def walk(node):
        if isinstance(node, dict):
            pane_id = node.get("focused_pane_id")
            if isinstance(pane_id, str) and pane_id and (
                "layouts" in node or "workspaces" in node
            ):
                return pane_id
            pane = node.get("pane")
            if (
                isinstance(pane, dict)
                and node.get("type") in ("pane_current", "pane_info")
                and isinstance(pane.get("pane_id"), str)
            ):
                return pane["pane_id"]
            for value in node.values():
                found = walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        return None

    if not isinstance(payload, dict):
        return None
    return walk(payload)


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
    noun = "message" if len(events) == 1 else "messages"
    if len(names) == 1:
        title = "%d %s in %s" % (len(events), noun, names[0])
    elif names:
        title = "%d %s in %s" % (len(events), noun, ", ".join(names))
    else:
        title = "%d %s" % (len(events), noun)
    if len(title) > TITLE_LIMIT:
        title = "%d %s" % (len(events), noun)
    return sanitize(title, TITLE_LIMIT)


def choose_body(events):
    highlights = [event for event in events if event.kind == "highlight"]
    chosen = highlights[-1] if highlights else events[-1]
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


def badge_summary(events):
    if not events:
        return ""
    if events[-1].name:
        return sanitize("weechat (%d) %s" % (len(events), events[-1].name), TITLE_LIMIT)
    return sanitize("weechat (%d)" % len(events), TITLE_LIMIT)


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


def enable_focus_reporting():
    weechat.command("", stdout_command("\x1b[?1004h\n"))


def set_terminal_title(text):
    global title_current
    if text == title_current:
        return
    title_current = text
    safe = "".join(ch if ch.isalnum() or ch in " #@()[]-_.,+/" else " " for ch in text)
    safe = " ".join(safe.split())
    weechat.command("", stdout_command("\x1b]0;%s\x07" % safe))


def refresh_title():
    if title_badge_enabled():
        set_terminal_title(badge_summary(queued))


def clear_title():
    if title_current:
        set_terminal_title("weechat")


def ensure_timer():
    global timer_hook
    if not timer_hook:
        timer_hook = weechat.hook_timer(TIMER_MS, 0, 0, "timer_cb", "")


def stop_timer_if_idle():
    global timer_hook
    if queued or focus_hook or notify_hook:
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
        refresh_title()
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


def start_process(args, callback):
    options = {}
    for index, arg in enumerate(args, start=1):
        options["arg%d" % index] = arg
    return weechat.hook_process_hashtable(herdr_bin(), options, 10000, callback, "")


def fail_focus(text):
    """After a few misses, notify anyway. A focus failure must not hide messages."""
    global focus_failures, next_attempt
    focus_failures += 1
    if focus_failures < 3:
        next_attempt = time.time() + RETRY_SECONDS
        return
    log_once("focus", "%s Notifications will be sent without a focus check." % text)
    focus_cache["at"] = time.time()
    focus_cache["pane_id"] = ""
    focus_failures = 0
    next_attempt = 0.0


def forget(sent_ids):
    global queued
    queued = [event for event in queued if event.id not in sent_ids]


def consider_send():
    if give_up:
        queued.clear()
        clear_title()
        stop_timer_if_idle()
        return
    now = time.time()
    if now < next_attempt or notify_hook or focus_hook:
        return
    if not due_events(now):
        stop_timer_if_idle()
        return
    if not cache_is_fresh(now):
        start_focus_query()
        return
    drop_visible(now, focus_cache["pane_id"])
    refresh_title()
    ready = due_events(now)
    if not ready:
        stop_timer_if_idle()
        return
    start_notify(ready)


def start_focus_query():
    global focus_hook
    if focus_hook:
        return
    hook = start_process(("api", "snapshot"), "focus_cb")
    if not hook:
        fail_focus("could not run %s." % herdr_bin())
        return
    focus_hook = hook


def start_notify(events):
    global notify_hook, inflight_ids
    title = choose_title(events) or "WeeChat"
    body = choose_body(events)
    args = ["notification", "show", title]
    if body:
        args.extend(("--body", body))
    args.extend(("--sound", choose_sound(events)))
    inflight_ids = [event.id for event in events]
    hook = start_process(args, "notify_cb")
    if not hook:
        inflight_ids = []
        give_up_binary()
        return
    notify_hook = hook


def give_up_binary():
    global give_up
    give_up = True
    queued.clear()
    log_once("binary", "could not run %s. Is it on PATH?" % herdr_bin())
    clear_title()
    stop_timer_if_idle()


def enqueue(buffer, nick, text, kind):
    global next_id
    queued.append(
        Event(
            next_id,
            buffer,
            buffer_name(buffer),
            sanitize(nick, TITLE_LIMIT),
            sanitize(text, BODY_LIMIT),
            kind,
            time.time() + delay_seconds(),
        )
    )
    next_id += 1
    refresh_title()
    ensure_timer()


def command_cb(data, buffer, args):
    global outer_focused
    arg = (args or "").strip()
    if arg == "focus":
        outer_focused = True
        now = time.time()
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
    now = time.time()
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
    return weechat.WEECHAT_RC_OK


def focus_cb(data, command, return_code, out, err):
    global focus_hook, focus_failures, next_attempt
    status = process_status(return_code)
    if status == "running":
        return weechat.WEECHAT_RC_OK
    focus_hook = ""
    if status == "error":
        fail_focus("could not run %s." % herdr_bin())
    else:
        payload = parse_json(out or "")
        pane_id = focused_pane_id(payload) if payload else None
        if pane_id is None:
            reason = notification_reason(payload) if payload else None
            fail_focus(reason or "could not read the focused Herdr pane.")
        else:
            focus_failures = 0
            next_attempt = 0.0
            focus_cache["at"] = time.time()
            focus_cache["pane_id"] = pane_id
    consider_send()
    return weechat.WEECHAT_RC_OK


def notify_cb(data, command, return_code, out, err):
    global notify_hook, notify_failures, next_attempt, give_up, inflight_ids
    status = process_status(return_code)
    if status == "running":
        return weechat.WEECHAT_RC_OK
    notify_hook = ""
    sent_ids = set(inflight_ids)
    inflight_ids = []
    if status == "error":
        forget(sent_ids)
        give_up_binary()
        return weechat.WEECHAT_RC_OK

    reason = notification_reason(parse_json(out or "") or {})
    if reason == "shown":
        notify_failures = 0
        forget(sent_ids)
        refresh_title()
        stop_timer_if_idle()
        return weechat.WEECHAT_RC_OK
    if reason == "disabled":
        give_up = True
        forget(sent_ids)
        log_once("disabled", TOAST_SNIPPET)
        refresh_title()
        stop_timer_if_idle()
        return weechat.WEECHAT_RC_OK

    notify_failures += 1
    next_attempt = time.time() + RETRY_SECONDS
    if reason in ("busy", "rate_limited", "no_foreground_client", None) and notify_failures <= retry_limit():
        stop_timer_if_idle()
        return weechat.WEECHAT_RC_OK
    forget(sent_ids)
    notify_failures = 0
    if reason in ("busy", "rate_limited", "no_foreground_client", None):
        log_once("retry", "Herdr did not show a notification (%s)." % (reason or "unknown"))
    else:
        log_once("notify", "Herdr rejected a notification (%s)." % reason)
    refresh_title()
    stop_timer_if_idle()
    return weechat.WEECHAT_RC_OK


def shutdown_cb():
    restore_beep()
    unbind_focus_keys()
    clear_title()
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
    enable_focus_reporting()
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


if weechat is not None:
    register()
