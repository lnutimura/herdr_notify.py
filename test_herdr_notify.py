# -*- coding: utf-8 -*-
"""Behavior tests for herdr_notify.py. Run: python3 test_herdr_notify.py"""

import importlib.util
import os
import pathlib
import subprocess
import unittest

PATH = pathlib.Path(__file__).with_name("herdr_notify.py")
SPEC = importlib.util.spec_from_file_location("herdr_notify", PATH)
notify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notify)


def event(kind, name, nick, text, due=0.0, event_id=1):
    return notify.Event(event_id, "buffer", name, nick, text, kind, due)


class SanitizeTests(unittest.TestCase):
    def test_collapses_whitespace_and_truncates(self):
        self.assertEqual(notify.sanitize("  a\nb\t c  ", 80), "a b c")
        self.assertEqual(notify.sanitize("abcdef", 4), "abc…")

    def test_stdout_command_escapes_weechat_command_separators(self):
        command = notify.stdout_command("\x1b]0;weechat (1) #chan\x07")
        self.assertTrue(command.startswith("/print -stdout "))
        self.assertIn("0\\;weechat", command)
        self.assertNotIn(";", command.replace("\\;", ""))


class FilterTests(unittest.TestCase):
    def test_ignores_self_and_notify_none(self):
        self.assertIsNone(notify.line_kind("notify_message,self_msg", "0"))
        self.assertIsNone(notify.line_kind("notify_highlight,notify_none", "1"))

    def test_private_and_highlight_share_a_floor(self):
        self.assertEqual(notify.line_kind("notify_private", "0"), "highlight")
        self.assertEqual(notify.line_kind("notify_message", "1"), "highlight")
        self.assertEqual(notify.line_kind("notify_message", "0"), "message")

    def test_buffer_notify_and_script_floor(self):
        self.assertFalse(notify.allowed("message", 0, "message"))
        self.assertFalse(notify.allowed("message", 1, "message"))
        self.assertTrue(notify.allowed("highlight", 1, "message"))
        self.assertTrue(notify.allowed("message", 2, "message"))
        self.assertFalse(notify.allowed("message", 3, "highlight"))
        self.assertTrue(notify.allowed("highlight", 3, "highlight"))


class VisibilityTests(unittest.TestCase):
    def test_visible_buffer_in_focused_pane_is_dropped(self):
        self.assertFalse(notify.keep_pending(0, 1, True, True, True))

    def test_background_tab_keeps_a_displayed_buffer(self):
        self.assertTrue(notify.keep_pending(0, 1, True, False, True))

    def test_other_weechat_buffer_is_kept(self):
        self.assertTrue(notify.keep_pending(0, 1, False, True, True))

    def test_unfocused_window_is_kept(self):
        self.assertTrue(notify.keep_pending(0, 1, True, True, False))

    def test_delay_holds_the_event(self):
        self.assertTrue(notify.keep_pending(5, 1, True, True, True))


class SnapshotTests(unittest.TestCase):
    def test_uses_session_focus_not_layout_focus(self):
        payload = {
            "id": "cli:api:snapshot",
            "result": {
                "type": "session_snapshot",
                "snapshot": {
                    "version": "0.9.1",
                    "focused_pane_id": "w1:p2",
                    "workspaces": [],
                    "layouts": [
                        {"focused_pane_id": "w1:p1"},
                        {"focused_pane_id": "w1:p9"},
                    ],
                },
            },
        }
        self.assertEqual(notify.focused_pane_id(payload), "w1:p2")

    def test_nested_pane_is_not_taken_for_the_session_focus(self):
        payload = {
            "result": {
                "type": "session_snapshot",
                "snapshot": {
                    "workspaces": [{"panes": [{"type": "pane_info", "pane": {"pane_id": "w1:p1"}}]}],
                },
            }
        }
        self.assertIsNone(notify.focused_pane_id(payload))

    def test_reads_pane_current(self):
        payload = {"result": {"type": "pane_current", "pane": {"pane_id": "w9:p9"}}}
        self.assertEqual(notify.focused_pane_id(payload), "w9:p9")

    def test_notification_reasons(self):
        shown = {"result": {"type": "notification_show", "shown": True, "reason": "shown"}}
        busy = {"result": {"type": "notification_show", "shown": False, "reason": "busy"}}
        disabled = {"error": {"code": "disabled", "message": "off"}}
        self.assertEqual(notify.notification_reason(shown), "shown")
        self.assertEqual(notify.notification_reason(busy), "busy")
        self.assertEqual(notify.notification_reason(disabled), "disabled")


class ToastTests(unittest.TestCase):
    def test_single_highlight(self):
        events = [event("highlight", "#chan", "ada", "hello")]
        self.assertEqual(notify.choose_title(events), "ada in #chan")
        self.assertEqual(notify.choose_body(events), "hello")
        self.assertEqual(notify.choose_sound(events), "request")

    def test_single_channel_message_stays_quiet(self):
        events = [event("message", "#chan", "ada", "hello")]
        self.assertEqual(notify.choose_sound(events), "none")

    def test_burst_in_one_channel_prefers_highlight_body_and_sound(self):
        events = [
            event("message", "#chan", "ada", "one", event_id=1),
            event("highlight", "#chan", "bea", "look", event_id=2),
            event("message", "#chan", "cy", "three", event_id=3),
        ]
        self.assertEqual(notify.choose_title(events), "3 messages in #chan")
        self.assertEqual(notify.choose_body(events), "bea: look")
        self.assertEqual(notify.choose_sound(events), "request")

    def test_burst_across_channels(self):
        events = [
            event("message", "#one", "ada", "a", event_id=1),
            event("message", "#two", "bea", "b", event_id=2),
        ]
        self.assertEqual(notify.choose_title(events), "2 messages in #one, #two")
        self.assertEqual(notify.choose_body(events), "bea: b")
        self.assertEqual(notify.choose_sound(events), "none")

    def test_long_channel_list_falls_back_to_a_count(self):
        events = [
            event("message", "alpha-" + ("x" * 30), "ada", "a", event_id=1),
            event("message", "beta-" + ("y" * 30), "bea", "b", event_id=2),
            event("message", "gamma-" + ("z" * 30), "cy", "c", event_id=3),
        ]
        self.assertEqual(notify.choose_title(events), "3 messages")

    def test_badge_names_the_latest_buffer(self):
        events = [event("message", "#chan", "ada", "hi")]
        self.assertEqual(notify.badge_summary(events), "weechat (1) #chan")
        self.assertEqual(notify.badge_summary([]), "")

    def test_zero_body_length_leaves_the_cap_to_the_backend(self):
        text = "x" * 600
        events = [event("highlight", "#chan", "ada", text)]
        notify.weechat = FakeWeechat()
        try:
            notify.weechat.options["max_body_length"] = "0"
            self.assertEqual(notify.choose_body(events), text)
            notify.weechat.options["max_body_length"] = "500"
            self.assertEqual(len(notify.choose_body(events)), 500)
        finally:
            notify.weechat = None


class NotifierTests(unittest.TestCase):
    def test_auto_uses_terminal_notifier_only_on_macos(self):
        binary = "/opt/homebrew/bin/terminal-notifier"
        self.assertEqual(notify.backend_name("auto", "darwin", binary, False), "terminal-notifier")
        self.assertEqual(notify.backend_name("auto", "linux", binary, False), "herdr")
        self.assertEqual(notify.backend_name("auto", "darwin", "", False), "herdr")
        self.assertEqual(notify.backend_name("herdr", "darwin", binary, False), "herdr")
        self.assertEqual(notify.backend_name("terminal_notifier", "linux", binary, False), "terminal-notifier")
        self.assertEqual(notify.backend_name("terminal-notifier", "darwin", binary, True), "herdr")

    def test_finds_homebrew_notifier_when_path_misses_it(self):
        bundle = "/opt/homebrew/Cellar/terminal-notifier/3.1.0/terminal-notifier.app/Contents/MacOS/terminal-notifier"
        self.assertEqual(
            notify.find_notifier("linux", lambda _name: "/usr/bin/terminal-notifier", lambda _path: True),
            "",
        )
        self.assertEqual(
            notify.find_notifier("darwin", lambda _name: "/custom/terminal-notifier", lambda _path: False),
            "",
        )
        self.assertEqual(
            notify.find_notifier(
                "darwin",
                lambda _name: "",
                lambda path: path == "/opt/homebrew/bin/terminal-notifier",
            ),
            "/opt/homebrew/bin/terminal-notifier",
        )
        self.assertEqual(
            notify.find_notifier(
                "darwin",
                lambda _name: "/opt/homebrew/bin/terminal-notifier",
                lambda path: path in (bundle, "/opt/homebrew/bin/terminal-notifier"),
                [bundle],
            ),
            bundle,
        )

    def test_terminal_bundle(self):
        self.assertEqual(notify.chosen_bundle("", {"TERM_PROGRAM": "ghostty"}), "com.mitchellh.ghostty")
        self.assertEqual(notify.chosen_bundle("", {"KITTY_WINDOW_ID": "1"}), "net.kovidgoyal.kitty")
        self.assertEqual(notify.chosen_bundle("", {"ALACRITTY_SOCKET": "/tmp/alacritty.sock"}), "org.alacritty")
        self.assertEqual(notify.chosen_bundle("off", {"TERM_PROGRAM": "ghostty"}), "")
        self.assertEqual(notify.chosen_bundle("com.example.Term", {"TERM_PROGRAM": "ghostty"}), "com.example.Term")

    def test_macos_sounds_follow_the_herdr_names(self):
        self.assertEqual(notify.macos_sound("request"), "Ping")
        self.assertEqual(notify.macos_sound("done"), "Glass")
        self.assertEqual(notify.macos_sound("none"), "")

    def test_plist_looking_title_is_escaped(self):
        self.assertEqual(notify.notifier_text("[hi]"), "\\[hi]")
        self.assertEqual(notify.notifier_text("-bot"), "\\-bot")
        self.assertEqual(notify.notifier_text("ada"), "ada")

    def test_arguments_keep_the_message_out_of_the_click_command(self):
        click = notify.focus_command("/usr/bin/python3", "/tmp/sock", "w1:p3")
        args = notify.notifier_arguments(
            "[ada] in #chan",
            "hello; rm -rf /",
            "Ping",
            "herdr-notify-w1-p3",
            "com.mitchellh.ghostty",
            click,
        )
        execute = args[args.index("-execute") + 1]
        self.assertNotIn("rm -rf", execute)
        self.assertEqual(args[args.index("-message") + 1], "hello; rm -rf /")
        self.assertEqual(args[args.index("-title") + 1], "\\[ada] in #chan")
        self.assertEqual(args[args.index("-activate") + 1], "com.mitchellh.ghostty")
        self.assertEqual(args[args.index("-sound") + 1], "Ping")
        self.assertEqual(args[args.index("-group") + 1], "herdr-notify-w1-p3")
        self.assertNotIn("-message=hello; rm -rf /", args)

    def test_group_id_is_safe(self):
        self.assertEqual(notify.notification_group("w1:p3"), "herdr-notify-w1-p3")

    def test_embedded_weechat_python_is_not_used_for_the_click(self):
        def usable(path):
            return path.endswith("python3")

        self.assertEqual(
            notify.resolve_python("/usr/bin/weechat", lambda _name: "/usr/bin/python3", usable),
            "/usr/bin/python3",
        )
        self.assertEqual(
            notify.resolve_python("/opt/homebrew/bin/python3", lambda _name: "", usable),
            "/opt/homebrew/bin/python3",
        )

    def test_focus_command_is_a_pane_focus_request(self):
        command = notify.focus_command("/bin/true", "/tmp/herdr's.sock", "w1:p3")
        self.assertIn("pane.focus", command)
        self.assertIn("w1:p3", command)
        self.assertIn("'/tmp/herdr'\\''s.sock'", command)
        compile(notify.focus_python("w1:p3"), "<focus>", "exec")
        subprocess.check_call(["sh", "-c", command])
        self.assertEqual(notify.focus_command("", "/tmp/sock", "w1:p3"), "")

    def test_notifier_failure_keeps_the_events_and_falls_back(self):
        notify.prefer_herdr = False
        notify.notify_failures = 0
        self.assertEqual(notify.notifier_finished("done", "0", ""), "shown")
        self.assertFalse(notify.prefer_herdr)
        self.assertEqual(notify.notifier_finished("done", "4", ""), "retry")
        self.assertFalse(notify.prefer_herdr)
        self.assertEqual(notify.notify_failures, 1)
        self.assertEqual(notify.notifier_finished("done", "3", ""), "fallback")
        self.assertTrue(notify.prefer_herdr)
        notify.prefer_herdr = False
        notify.notify_failures = 0


class FakeWeechat:
    """Just enough of the weechat module to drive the callbacks."""

    WEECHAT_RC_OK = 0
    WEECHAT_HOOK_PROCESS_RUNNING = -1
    WEECHAT_HOOK_PROCESS_ERROR = -2

    def __init__(self):
        self.options = {"delivery": "herdr"}
        self.commands = []
        self.printed = []
        self.processes = []
        self.process_starts = True
        self.displayed = {}

    def config_get_plugin(self, name):
        return self.options.get(name, "")

    def config_set_plugin(self, name, value):
        self.options[name] = value

    def prnt(self, buffer, text):
        self.printed.append(text)

    def command(self, buffer, text):
        self.commands.append(text)

    def hook_timer(self, interval, align, calls, callback, data):
        return "timer"

    def unhook(self, hook):
        pass

    def hook_process_hashtable(self, command, options, timeout, callback, data):
        args = [options["arg%d" % index] for index in range(1, len(options) + 1)]
        self.processes.append((command, args, timeout, callback))
        return "process" if self.process_starts else ""

    def buffer_get_integer(self, buffer, name):
        if name == "num_displayed":
            return self.displayed.get(buffer, 0)
        return 3

    def buffer_get_string(self, buffer, name):
        return buffer


class CallbackTests(unittest.TestCase):
    """Drive the queue, focus check and notify callbacks through a fake WeeChat."""

    def setUp(self):
        self.weechat = FakeWeechat()
        self.saved_pane = os.environ.get("HERDR_PANE_ID")
        os.environ["HERDR_PANE_ID"] = "w1:p1"
        notify.weechat = self.weechat
        notify.queued = []
        notify.next_id = 1
        notify.timer_hook = ""
        notify.focus_hook = ""
        notify.notify_hook = ""
        notify.focus_cache = {"at": 0.0, "pane_id": None}
        notify.next_attempt = 0.0
        notify.notify_failures = 0
        notify.focus_failures = 0
        notify.inflight_ids = []
        notify.inflight_backend = ""
        notify.prefer_herdr = False
        notify.give_up = False
        notify.beep_suspended = False
        notify.logged = set()
        notify.outer_focused = True
        notify.title_current = None
        notify.bound_keys = []

    def tearDown(self):
        notify.weechat = None
        if self.saved_pane is None:
            os.environ.pop("HERDR_PANE_ID", None)
        else:
            os.environ["HERDR_PANE_ID"] = self.saved_pane

    def queue_due_highlight(self, buffer="#chan"):
        notify.enqueue(buffer, "ada", "hello", "highlight")
        notify.queued[-1].due = 0.0

    def focus_on(self, pane_id):
        notify.focus_cache["at"] = notify.time.monotonic()
        notify.focus_cache["pane_id"] = pane_id

    def test_timed_out_herdr_call_is_retried(self):
        self.queue_due_highlight()
        self.focus_on("w1:p2")
        notify.consider_send()
        command, args, timeout, callback = self.weechat.processes[-1]
        self.assertEqual(args[:2], ["notification", "show"])
        self.assertGreater(timeout, 10000)
        notify.notify_cb("", command, self.weechat.WEECHAT_HOOK_PROCESS_ERROR, "", "")
        self.assertFalse(notify.give_up)
        self.assertEqual(len(notify.queued), 1)
        self.assertGreater(notify.next_attempt, 0.0)

    def test_shown_notification_empties_the_queue(self):
        self.queue_due_highlight()
        self.focus_on("w1:p2")
        notify.consider_send()
        shown = '{"result": {"type": "notification_show", "shown": true, "reason": "shown"}}'
        notify.notify_cb("", "herdr", 0, shown, "")
        self.assertEqual(notify.queued, [])
        self.assertEqual(notify.notify_failures, 0)

    def test_missing_binary_gives_up_and_restores_beep(self):
        self.weechat.options["beep_suspended"] = "on"
        self.weechat.process_starts = False
        self.queue_due_highlight()
        self.focus_on("w1:p2")
        notify.consider_send()
        self.assertTrue(notify.give_up)
        self.assertIn("/mute /trigger enable beep", self.weechat.commands)
        self.assertEqual(self.weechat.options["beep_suspended"], "off")

    def test_disabled_toasts_restore_beep(self):
        self.weechat.options["beep_suspended"] = "on"
        self.queue_due_highlight()
        self.focus_on("w1:p2")
        notify.consider_send()
        notify.notify_cb("", "herdr", 1, '{"error": {"code": "disabled"}}', "")
        self.assertTrue(notify.give_up)
        self.assertIn("/mute /trigger enable beep", self.weechat.commands)

    def test_snapshot_split_across_callbacks_is_joined(self):
        self.queue_due_highlight()
        notify.consider_send()
        command, args, _timeout, callback = self.weechat.processes[-1]
        self.assertEqual((args, callback), (["api", "snapshot"], "focus_cb"))
        snapshot = (
            '{"result": {"type": "session_snapshot", "snapshot": {"focused_pane_id": "w1:p2", '
            '"workspaces": [], "padding": "' + "x" * 70000 + '"}}}'
        )
        running = self.weechat.WEECHAT_HOOK_PROCESS_RUNNING
        notify.focus_cb("", command, running, snapshot[:65536], "")
        notify.focus_cb("", command, 0, snapshot[65536:], "")
        self.assertEqual(notify.focus_cache["pane_id"], "w1:p2")
        self.assertEqual(self.weechat.processes[-1][1][:2], ["notification", "show"])

    def test_visible_buffer_in_focused_pane_is_not_notified(self):
        self.weechat.displayed["#chan"] = 1
        self.queue_due_highlight()
        self.focus_on("w1:p1")
        notify.consider_send()
        self.assertEqual(notify.queued, [])
        self.assertEqual(self.weechat.processes, [])

    def test_switching_to_the_buffer_silences_it(self):
        notify.enqueue("#chan", "ada", "hello", "highlight")
        self.weechat.displayed["#chan"] = 1
        self.focus_on("w1:p1")
        notify.buffer_switch_cb("", "buffer_switch", "#chan")
        self.assertEqual(notify.queued, [])
        self.assertEqual(self.weechat.processes, [])

    def test_shutdown_turns_focus_reporting_off(self):
        notify.shutdown_cb()
        self.assertTrue(any("?1004l" in command for command in self.weechat.commands))


if __name__ == "__main__":
    unittest.main()
