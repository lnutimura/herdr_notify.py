# herdr_notify

[![CI](https://github.com/lnutimura/herdr_notify.py/actions/workflows/ci.yml/badge.svg)](https://github.com/lnutimura/herdr_notify.py/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A WeeChat script that notifies you about messages you cannot see, including a channel left open in WeeChat while you work in another Herdr tab.

<!-- TODO: screenshot of a banner, e.g. docs/banner.png -->

WeeChat's hotlist does not count a buffer that is on screen in a tab you are not looking at, and Herdr does not notify for ordinary terminal output. This script fills that gap. After a short delay, and only if the buffer is still out of sight, it posts one notification:

- **macOS:** a Notification Center banner through [`terminal-notifier`](https://github.com/julienXX/terminal-notifier). Clicking it focuses the WeeChat pane and brings your terminal to the front.
- **Linux, or macOS without `terminal-notifier`:** a Herdr toast (`herdr notification show`).

## Quick start

1. On macOS, install the notifier (skip this on Linux):

   ```bash
   brew install terminal-notifier
   ```

2. Turn on Herdr toasts in `~/.config/herdr/config.toml`, then run `herdr server reload-config`:

   ```toml
   [ui.toast]
   delivery = "herdr"
   delay_seconds = 1

   [ui.toast.herdr]
   position = "bottom-right"
   ```

3. Link the script into WeeChat's autoload directory and start WeeChat from a Herdr pane:

   ```bash
   mkdir -p ~/.local/share/weechat/python/autoload
   ln -sf "$PWD/herdr_notify.py" ~/.local/share/weechat/python/autoload/herdr_notify.py
   ```

On macOS, the first banner asks for permission. Allow **terminal-notifier**.

## Requirements

- WeeChat with Python support
- Herdr 0.7.4 or newer, with `herdr` on `PATH` or `HERDR_BIN_PATH` set (Herdr sets it inside a pane)
- Optional, macOS only: `terminal-notifier` 3.x for Notification Center banners

## What you get

- **One mention or private message:** title `nick in #chan`, the message as the body, and the `request` sound.
- **A burst:** one notification, such as `3 messages in #chan`. The body shows the latest mention if there is one, and a mention in the batch plays the `request` sound.
- **Quiet when you are looking:** if you switch to the buffer in the focused Herdr pane during the 3-second delay, nothing is shown.
- **Quiet for noise:** joins, parts, quits and your own messages never notify. `/buffer notify highlight` and `/buffer notify none` still apply, per buffer.
- **An unread badge:** while something is unread, the pane title reads `weechat (4) #chan`. Add `"terminal_title"` to a Herdr sidebar row (for example `["state_text", "terminal_title"]`) to see it there.

By default only mentions and private messages notify. To be told about every channel message:

```
/set plugins.var.python.herdr_notify.level message
```

## How it works

1. WeeChat prints a line. The script keeps it if its tags and the buffer's notify level allow it, and queues it with a deadline `delay` seconds away.
2. When the deadline passes, the script asks Herdr which pane is focused (`herdr api snapshot`). It also tracks whether the terminal window itself has focus, through focus reporting (mode 1004).
3. Lines from a buffer that is on screen, in the focused pane, in a focused window, are dropped. The rest are batched into one notification.
4. The notification goes to `terminal-notifier` or `herdr notification show`. Busy, rate-limited or slow replies are retried. If `terminal-notifier` fails, the script falls back to Herdr toasts.

Switching to a buffer or focusing the window makes its pending lines due at once, so they are checked and dropped right away rather than at the deadline.

## Configuration

Settings live under `plugins.var.python.herdr_notify.*` and are changed with `/set`.

| Setting | Default | Values |
| --- | --- | --- |
| `level` | `highlight` | `highlight` (mentions and private messages) or `message` (every channel message too) |
| `delay` | `3` | Seconds to wait before notifying |
| `sound_highlight` | `request` | `none`, `done` or `request` |
| `sound_message` | `none` | `none`, `done` or `request` |
| `title_badge` | `on` | `on` or `off` |
| `max_body_length` | `240` | Body cap; `0` leaves it to Herdr or macOS |
| `retry_limit` | `30` | Retries when Herdr is busy, rate-limited or does not answer within 15 seconds, and when `terminal-notifier` cannot reach Notification Center |
| `delivery` | `auto` | `auto` (`terminal-notifier` on macOS when installed), `herdr` or `terminal-notifier` |
| `terminal_bundle` | empty | macOS bundle id to bring to the front on click; empty detects it; `off` skips it |

On macOS, `request` plays the sound Ping and `done` plays Glass. Upgrading from 0.2 keeps your saved `level`.

### Herdr toasts

`delivery = "herdr"` draws the toast inside the Herdr window, which you can see from any tab. On Linux, `delivery = "system"` uses the desktop notifier instead, so the alert stays visible after you leave the terminal. Herdr shows one toast at a time and accepts about one per second, so a burst is folded into a single toast.

On macOS, Herdr's own agent toasts keep using that setting while WeeChat uses Notification Center. To send WeeChat through Herdr toasts too:

```
/set plugins.var.python.herdr_notify.delivery herdr
```

### terminal-notifier on macOS

The script runs the binary inside `terminal-notifier.app` (Homebrew's Cellar, `/Applications` or `~/Applications`). A copy that only lives on `PATH` is a different app to Notification Center.

If no banner appears, open System Settings → Notifications → terminal-notifier, turn notifications on, and run `/script reload herdr_notify`. A denial is remembered until that reload.

Clicking a banner focuses the WeeChat pane through Herdr's `pane.focus` request. Ghostty, kitty, iTerm, Terminal, WezTerm, Alacritty and Warp are detected and brought to the front. For another terminal, look up its bundle id and set it:

```bash
osascript -e 'id of app "Ghostty"'
```

```
/set plugins.var.python.herdr_notify.terminal_bundle com.mitchellh.ghostty
```

A newer banner for the same pane replaces the previous one.

### Other WeeChat homes

Scripts live in WeeChat's data directory: `~/.local/share/weechat/python/` with the default XDG layout, or `$XDG_DATA_HOME/weechat/python`. If WeeChat runs with `-d`/`--dir` or `WEECHAT_HOME`, or still uses an old `~/.weechat` home, link the script into that home's `python/autoload` instead. `/debug dirs` prints the directory in use.

To load the script into a running WeeChat, use `/python load autoload/herdr_notify.py`. After the file changes, use `/script reload herdr_notify`.

## Limits

- Outside Herdr the script loads and does nothing.
- A Herdr toast is not tied to the WeeChat pane, so clicking it or pressing `prefix+o` does not jump there. A `terminal-notifier` banner does.
- Click-to-focus needs `HERDR_SOCKET_PATH`, which Herdr sets in the pane. The click runs a short Python program with that path baked in, because the click does not inherit the pane's environment.
- The window-focus check uses the keys `meta-[I` and `meta-[O`. If you already bound them, the script leaves them alone and only tracks Herdr tabs. Focus reporting is turned off when the script unloads.
- Inside Herdr the script turns WeeChat's `beep` trigger off, and back on when it unloads or stops notifying (Herdr missing, or toasts disabled). A hard kill can leave `beep` off; the next start restores it, or run `/trigger enable beep`.
- If `terminal-notifier` is blocked, or Herdr toasts are disabled, the script says so once on the core buffer.

## Development

```bash
python3 test_herdr_notify.py
uvx ruff check . && uvx ruff format --check .
```

The tests need no WeeChat or Herdr. Pure helpers are tested directly, and the callbacks run against a small stand-in for the `weechat` module. See [CHANGELOG.md](CHANGELOG.md) for release notes.

## License

[MIT](LICENSE)
