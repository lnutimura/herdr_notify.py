# herdr_notify

A WeeChat script that notifies you when a message arrives in a buffer you cannot see. That includes a channel left open inside WeeChat while you are on another Herdr tab.

WeeChat's own hotlist does not record that case, and Herdr does not notify for ordinary terminal output. After a short delay, and only if the buffer is still hidden, the script posts the notification. On macOS it uses `terminal-notifier` when that is installed, so the banner is a real Notification Center notification and a click focuses the WeeChat pane. Otherwise it calls `herdr notification show`.

## Requirements

- WeeChat with the Python support it uses to load scripts
- Herdr 0.7.4 or newer (`notification.show`, and focus events forwarded into the focused pane). A click focuses the pane with the `pane.focus` socket request.
- `herdr` on `PATH`, or `HERDR_BIN_PATH` set to the Herdr binary. Herdr sets that variable inside a pane.
- On macOS, [`terminal-notifier`](https://github.com/julienXX/terminal-notifier) for a Notification Center banner. Without it, the script uses a Herdr toast.

## Install

1. On macOS, install the notifier:

```bash
brew install terminal-notifier
```

The script runs the binary inside `terminal-notifier.app` (Homebrew's Cellar, or an app in `/Applications` or `~/Applications`). A copy that only lives on `PATH` is a different program to Notification Center. Skip this step on Linux.

2. Allow Herdr toasts. Linux always uses them. macOS uses them when `terminal-notifier` is missing or Notification Center has blocked it. In the Herdr config (`~/.config/herdr/config.toml`):

```toml
[ui.toast]
delivery = "herdr"
delay_seconds = 1

[ui.toast.herdr]
position = "bottom-right"
```

`delivery = "herdr"` draws the toast inside the Herdr window, which is the one you can see from another tab. On Linux, `delivery = "system"` asks the desktop notifier instead, for an alert that is still visible after you leave the terminal. Reload with `herdr server reload-config`.

On macOS, WeeChat notifications go to Notification Center while `terminal-notifier` is available, and Herdr's own agent toasts keep using the setting above. To force WeeChat through the in-app toast:

```
/set plugins.var.python.herdr_notify.delivery herdr
```

3. Link the script into WeeChat's Python autoload directory.

Scripts live in the data directory. With the default XDG layout that is `~/.local/share/weechat/python/` (`$XDG_DATA_HOME/weechat/python` when that variable is set). `~/.config/weechat` holds the `*.conf` files only.

```bash
# from a clone of this repository
mkdir -p ~/.local/share/weechat/python/autoload
ln -sf "$PWD/herdr_notify.py" ~/.local/share/weechat/python/autoload/herdr_notify.py
```

If WeeChat was started with `-d` / `--dir` or `WEECHAT_HOME`, or it is still using an old home because `~/.weechat/weechat.conf` exists and `~/.config/weechat/weechat.conf` does not, put the link in that home's `python/autoload` directory instead. `/debug dirs` prints the data directory this WeeChat is using.

4. Start WeeChat from a Herdr pane so autoload picks the script up. Outside Herdr it loads and does nothing, and the beep trigger stays on.

If WeeChat is already running, load the new symlink:

```
/python load autoload/herdr_notify.py
```

After the file changes, `/script reload herdr_notify`.

5. On macOS, the first banner is the permission prompt. Allow **terminal-notifier**. If no banner appears, open System Settings → Notifications → terminal-notifier, turn notifications on, and run `/script reload herdr_notify`. A denial is remembered until that reload.

Clicking a banner focuses the WeeChat pane. Ghostty, kitty, iTerm, Terminal, WezTerm, Alacritty, and Warp are also brought to the front. For another terminal, print its bundle id (the name is the name of the app):

```bash
osascript -e 'id of app "Ghostty"'
```

Set the id that command prints:

```
/set plugins.var.python.herdr_notify.terminal_bundle com.mitchellh.ghostty
```

`/set plugins.var.python.herdr_notify.terminal_bundle off` skips raising the app. The click still focuses the pane.

## What you get

A mention or private message waits 3 seconds. If you open that buffer in the focused Herdr pane during the wait, it stays quiet. Otherwise one notification is shown. With `level` set to `message`, ordinary channel messages do the same.

- One mention or private message: title `nick in #chan`, the message as the body, and the `request` sound.
- Several lines: one notification, such as `3 messages in #chan`. A mention in the batch uses the `request` sound. A batch of ordinary messages does not.
- Joins, parts, quits, and your own messages stay quiet.
- `/buffer notify highlight` and `/buffer notify none` still apply, per buffer.

With `terminal-notifier`, a newer notification for this pane replaces the previous one. `request` plays the macOS sound Ping, `done` plays Glass, and `none` stays silent. A click sends `pane.focus` for this pane.

Herdr's own toast path shows one in-app toast at a time and accepts about one notification per second. A burst is folded into that single toast. `prefix+o` does not jump to the WeeChat pane; the title names the buffer.

The script also sets the pane title to `weechat (4) #chan` while something is unread, and back to `weechat` when the queue is empty. To see that title in the sidebar, add the `terminal_title` token to a row you already have:

```toml
["state_text", "terminal_title"]
```

That row is optional. The toast does not depend on it.

## Settings

Change these in WeeChat. They live under `plugins.var.python.herdr_notify.*`.

- `level` — `highlight` (default) or `message`. `highlight` keeps mentions and private messages; `message` adds every channel message.
- `delay` — seconds to wait before notifying. Default `3`.
- `sound_highlight` — `none`, `done`, or `request`. Default `request`.
- `sound_message` — `none`, `done`, or `request`. Default `none`.
- `title_badge` — `on` or `off`. Default `on`.
- `max_body_length` — cap for the notification body. Default `240`. `0` leaves the cap to Herdr or macOS.
- `retry_limit` — retries when Herdr answers `busy` or `rate_limited` or does not answer within 15 seconds, and when `terminal-notifier` cannot reach Notification Center. Default `30`.
- `delivery` — `auto` (default), `herdr`, or `terminal-notifier`. `auto` uses `terminal-notifier` on macOS when it is installed.
- `terminal_bundle` — bundle id to activate on click. Empty detects the terminal. `off` does not activate an app.

Example:

```
/set plugins.var.python.herdr_notify.level message
```

Upgrading from 0.2: an existing `level` setting is kept, so a setup that already has `message` stays on `message`.

## Limits

- A Herdr toast is not attached to the WeeChat pane, so clicking it or pressing `prefix+o` does not focus that tab. A `terminal-notifier` banner does focus it, through `pane.focus`.
- If `terminal-notifier` is installed but notifications are turned off for it, the script says so once and uses Herdr toasts until it is reloaded.
- Click-to-focus needs `HERDR_SOCKET_PATH`, which Herdr sets in the pane. The click runs a short Python program with that socket path baked in, because the click does not inherit the pane's environment.
- If `meta-[I` or `meta-[O` is already bound, the script leaves that key alone. Those keys are how it learns that the terminal window was blurred. Tab changes are read from Herdr either way. Focus reporting is turned off again when the script unloads.
- While the script is running inside Herdr it turns WeeChat's `beep` trigger off, and turns it back on when the script unloads or stops notifying (Herdr missing, or toasts disabled). A hard kill can leave `beep` off; the next start restores it. `/trigger enable beep` restores it by hand.
- When the script is posting through Herdr and toasts are disabled, WeeChat prints the config snippet once on the core buffer and stays quiet until the script is reloaded.

## Tests

```bash
python3 test_herdr_notify.py
```

## License

[MIT](LICENSE)
