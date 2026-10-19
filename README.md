# herdr_notify

A WeeChat script that asks Herdr to show a toast when a message arrives in a buffer you cannot see. That includes a channel left open inside WeeChat while you are on another Herdr tab.

WeeChat's own hotlist does not record that case, and Herdr does not notify for ordinary terminal output. The script calls `herdr notification show` after a short delay, and only if the buffer is still hidden.

## Requirements

- WeeChat with the Python support it uses to load scripts
- Herdr 0.7.4 or newer (`notification.show`, and focus events forwarded into the focused pane)
- `herdr` on `PATH`, or `HERDR_BIN_PATH` set to the Herdr binary. Herdr sets that variable inside a pane.

## Install

1. Allow Herdr toasts. In the Herdr config (`~/.config/herdr/config.toml`):

```toml
[ui.toast]
delivery = "herdr"
delay_seconds = 1

[ui.toast.herdr]
position = "bottom-right"
```

`delivery = "herdr"` draws the toast inside the Herdr window, which is the one you can see from another tab. Use `system` instead when the alert must also appear after you leave the terminal entirely. That needs a desktop notification daemon on Linux. Reload the config with `herdr server reload-config`.

2. Link the script into WeeChat's Python autoload directory.

Scripts live in the data directory. With the default XDG layout that is `~/.local/share/weechat/python/` (`$XDG_DATA_HOME/weechat/python` when that variable is set). `~/.config/weechat` holds the `*.conf` files only.

```bash
# from a clone of this repository
mkdir -p ~/.local/share/weechat/python/autoload
ln -sf "$PWD/herdr_notify.py" ~/.local/share/weechat/python/autoload/herdr_notify.py
```

If WeeChat was started with `-d` / `--dir` or `WEECHAT_HOME`, or it is still using an old home because `~/.weechat/weechat.conf` exists and `~/.config/weechat/weechat.conf` does not, put the link in that home's `python/autoload` directory instead. `/debug dirs` prints the data directory this WeeChat is using.

3. Start WeeChat from a Herdr pane, or `/script reload herdr_notify` in a WeeChat that is already running there.

Outside Herdr the script loads and does nothing. The beep trigger stays on.

## What you get

A channel message, mention, or private message waits 3 seconds. If you open that buffer in the focused Herdr pane during the wait, it stays quiet. Otherwise one toast is shown.

- One mention or private message: title `nick in #chan`, the message as the body, and the `request` sound.
- Several lines: one toast, such as `3 messages in #chan`. A mention in the batch uses the `request` sound. A batch of ordinary messages does not.
- Joins, parts, quits, and your own messages stay quiet.
- `/buffer notify highlight` and `/buffer notify none` still apply, per buffer.

Herdr shows one in-app toast at a time and accepts about one notification per second. A burst is folded into that single toast. `prefix+o` does not jump to the WeeChat pane; the title names the buffer.

The script also sets the pane title to `weechat (4) #chan` while something is unread, and back to `weechat` when the queue is empty. To see that title in the sidebar, add the `terminal_title` token to a row you already have:

```toml
["state_text", "terminal_title"]
```

That row is optional. The toast does not depend on it.

## Settings

Change these in WeeChat. They live under `plugins.var.python.herdr_notify.*`.

- `level` — `message` (default) or `highlight`. `highlight` keeps mentions and private messages.
- `delay` — seconds to wait before notifying. Default `3`.
- `sound_highlight` — `none`, `done`, or `request`. Default `request`.
- `sound_message` — `none`, `done`, or `request`. Default `none`.
- `title_badge` — `on` or `off`. Default `on`.
- `max_body_length` — cap for the toast body. Default `240`.
- `retry_limit` — retries when Herdr answers `busy` or `rate_limited`. Default `30`.

Example:

```
/set plugins.var.python.herdr_notify.level highlight
```

## Limits

- Herdr does not attach this toast to the WeeChat pane, so clicking it or pressing `prefix+o` does not focus that tab.
- If `meta-[I` or `meta-[O` is already bound, the script leaves that key alone. Those keys are how it learns that the terminal window was blurred. Tab changes are read from Herdr either way.
- While the script is running inside Herdr it turns WeeChat's `beep` trigger off, and turns it back on when the script unloads. A hard kill can leave `beep` off; the next start restores it. `/trigger enable beep` restores it by hand.
- If toasts are disabled, WeeChat prints the config snippet once on the core buffer and stays quiet until the script is reloaded.

## Tests

```bash
python3 test_herdr_notify.py
```

## License

[MIT](LICENSE)
