# herdr_notify

[![CI](https://github.com/lnutimura/herdr_notify.py/actions/workflows/ci.yml/badge.svg)](https://github.com/lnutimura/herdr_notify.py/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A WeeChat script that tells Herdr when you have messages you have not seen, including a channel left open in WeeChat while you work in another Herdr tab.

<!-- TODO: screenshot of a banner, e.g. docs/banner.png -->

WeeChat's hotlist does not count a buffer that is on screen in a tab you are not looking at, and Herdr does not notify for ordinary terminal output. This script fills that gap. After a short delay, and only if the buffer is still out of sight, it reports WeeChat to Herdr the way coding agents report themselves: **blocked** while something is unread, **idle** once you have looked at it. Herdr then does what it does for any agent that needs you:

- marks the WeeChat pane, its tab and its workspace in the sidebar
- shows its toast, "weechat needs attention", and plays its request sound
- lists WeeChat under blocked agents in the Goto picker (`prefix+g`, then `b`)

On macOS with [`terminal-notifier`](https://github.com/julienXX/terminal-notifier), you also get a Notification Center banner with the message itself. Clicking it focuses the WeeChat pane, switches WeeChat to that buffer and brings your terminal to the front.

## Quick start

1. On macOS, install the notifier (skip this on Linux):

   ```bash
   brew install terminal-notifier
   ```

2. Turn on Herdr toasts and show the unread summary in `~/.config/herdr/config.toml`, then run `herdr server reload-config`:

   ```toml
   [ui.toast]
   delivery = "herdr"
   delay_seconds = 1

   [ui.sidebar.agents]
   rows = [
     ["state_icon", "agent", "state_text"],
     ["$summary"],
     ["workspace", "tab"],
   ]
   ```

3. Link the script into WeeChat's autoload directory and start WeeChat from a Herdr pane:

   ```bash
   mkdir -p ~/.local/share/weechat/python/autoload
   ln -sf "$PWD/herdr_notify.py" ~/.local/share/weechat/python/autoload/herdr_notify.py
   ```

On macOS, the first banner asks for permission. Allow **terminal-notifier**.

## Requirements

- WeeChat with Python support
- Herdr 0.7.4 or newer (tested with 0.9.0), with `herdr` on `PATH` or `HERDR_BIN_PATH` set (Herdr sets it inside a pane)
- Optional, macOS only: `terminal-notifier` 3.x for Notification Center banners
- Optional: WeeChat's fifo plugin (on by default) so a banner click can switch buffers

## What you get

- **An alert when you are away:** a mention or private message in a buffer you cannot see turns WeeChat blocked. Herdr toasts once and keeps the pane marked until you look at the buffer. More mentions while it is still blocked update the summary but do not toast again.
- **The message in the sidebar:** `$summary` reads `ada in #chan: hello`, or `(3) ada in #chan: hello` when there are more. `$weechat_unread` holds just the count.
- **macOS banners:** title `nick in #chan`, the message as the body, and the `request` sound. A burst becomes one banner, such as `3 messages in #chan`, showing the latest mention. A newer banner replaces the previous one.
- **Quiet when you are looking:** if you switch to the buffer in the focused Herdr pane during the 3-second delay, nothing happens.
- **Quiet for noise:** joins, parts, quits and your own messages never count. `/buffer notify highlight` and `/buffer notify none` still apply, per buffer.

By default only mentions and private messages count. To count every channel message too:

```
/set plugins.var.python.herdr_notify.level message
```

## How it works

1. WeeChat prints a line. The script keeps it if its tags and the buffer's notify level allow it, and queues it with a deadline `delay` seconds away.
2. When the deadline passes, the script asks Herdr which pane is focused (`herdr api snapshot`). It also tracks whether the terminal window itself has focus, through focus reporting (mode 1004).
3. Lines from a buffer that is on screen, in the focused pane, in a focused window, are dropped. On macOS the rest become one banner.
4. The remaining lines are now unread. The script reports `herdr pane report-agent --state blocked` with a summary, and the unread count and summary as sidebar tokens (`herdr pane report-metadata`).
5. Lines stay unread until their buffer is in front of you: you switch to it, or the WeeChat pane gains focus with it on screen. Then the script reports `idle` and clears the tokens.

Reports run one at a time, send only the latest state, and are retried if Herdr does not accept them. When the script unloads, it clears its tokens and releases the agent.

## Configuration

Settings live under `plugins.var.python.herdr_notify.*` and are changed with `/set`.

| Setting | Default | Values |
| --- | --- | --- |
| `level` | `highlight` | `highlight` (mentions and private messages) or `message` (every channel message too) |
| `delay` | `3` | Seconds to wait before a line counts as unseen |
| `sound_highlight` | `request` | macOS banner sound: `none`, `done` or `request` |
| `sound_message` | `none` | macOS banner sound: `none`, `done` or `request` |
| `title_badge` | `on` | `on` or `off`: report the `$weechat_unread` and `$summary` tokens |
| `max_body_length` | `240` | macOS banner body cap; `0` leaves it to macOS |
| `retry_limit` | `30` | Retries for a report Herdr did not accept, and for a banner when `terminal-notifier` cannot reach Notification Center |
| `delivery` | `auto` | `auto` (banners on macOS when `terminal-notifier` is installed), `herdr` (Herdr alerts only) or `terminal-notifier` |
| `terminal_bundle` | empty | macOS bundle id to bring to the front on click; empty detects it; `off` skips it |

On macOS, `request` plays the sound Ping and `done` plays Glass. Herdr's own alert uses Herdr's sound settings (`ui.sound.*`).

### Herdr alerts

Herdr decides how its alert looks. `ui.toast.delivery = "herdr"` draws the toast inside the Herdr window, which you can see from any tab. On Linux, `delivery = "system"` uses the desktop notifier instead. `ui.toast.delay_seconds` waits before toasting, and Herdr skips the toast if WeeChat was read in the meantime. Herdr does not toast while the WeeChat pane is in the tab you are looking at; its sound follows `ui.sound.*`.

The sidebar tokens appear wherever you put `$summary` or `$weechat_unread` in `ui.sidebar.agents.rows`. Herdr styles them; see its configuration docs for colors and rules.

### terminal-notifier on macOS

The script runs the binary inside `terminal-notifier.app` (Homebrew's Cellar, `/Applications` or `~/Applications`). A copy that only lives on `PATH` is a different app to Notification Center.

If no banner appears, open System Settings → Notifications → terminal-notifier, turn notifications on, and run `/script reload herdr_notify`. A denial is remembered until that reload, and in the meantime only Herdr's alert is used.

With banners, Herdr still gets the blocked report, so you may see both the banner and Herdr's in-app toast. Set `delivery` to `herdr` for Herdr's alert only.

Clicking a banner focuses the WeeChat pane through Herdr's `pane.focus` request and switches WeeChat to the buffer through its fifo. Ghostty, kitty, iTerm, Terminal, WezTerm, Alacritty and Warp are detected and brought to the front. For another terminal, look up its bundle id and set it:

```bash
osascript -e 'id of app "Ghostty"'
```

```
/set plugins.var.python.herdr_notify.terminal_bundle com.mitchellh.ghostty
```

### Other WeeChat homes

Scripts live in WeeChat's data directory: `~/.local/share/weechat/python/` with the default XDG layout, or `$XDG_DATA_HOME/weechat/python`. If WeeChat runs with `-d`/`--dir` or `WEECHAT_HOME`, or still uses an old `~/.weechat` home, link the script into that home's `python/autoload` instead. `/debug dirs` prints the directory in use.

To load the script into a running WeeChat, use `/python load autoload/herdr_notify.py`. After the file changes, use `/script reload herdr_notify`.

## Limits

- Outside Herdr the script loads and does nothing.
- WeeChat appears in Herdr as an agent named `weechat`. Herdr's toast names the agent and the tab, not the message; the message is in `$summary` and in macOS banners.
- Reading a buffer from somewhere else, such as a relay client, does not clear the unread state. Open the buffer in this WeeChat, or focus its pane with the buffer on screen.
- Click-to-focus needs `HERDR_SOCKET_PATH`, which Herdr sets in the pane. The click runs a short Python program with that path baked in, because the click does not inherit the pane's environment. A buffer whose name contains `;` or a backslash is focused but not switched to.
- The window-focus check uses the keys `meta-[I` and `meta-[O`. If you already bound them, the script leaves them alone and only tracks Herdr tabs. Focus reporting is turned off when the script unloads.
- Inside Herdr the script turns WeeChat's `beep` trigger off, and back on when it unloads or cannot run `herdr`. A hard kill can leave `beep` off; the next start restores it, or run `/trigger enable beep`.

## Development

```bash
python3 test_herdr_notify.py
uvx ruff check . && uvx ruff format --check .
```

The tests need no WeeChat or Herdr. Pure helpers are tested directly, the callbacks run against a small stand-in for the `weechat` module, and the banner click is run end to end against a temporary fifo and socket. See [CHANGELOG.md](CHANGELOG.md) for release notes.

## License

[MIT](LICENSE)
