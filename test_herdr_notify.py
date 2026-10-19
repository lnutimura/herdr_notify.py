# -*- coding: utf-8 -*-
"""Behavior tests for herdr_notify.py. Run: python3 test_herdr_notify.py"""

import importlib.util
import pathlib
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


if __name__ == "__main__":
    unittest.main()
