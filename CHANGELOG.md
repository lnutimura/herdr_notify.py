# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-10-26

### Changed

- **Breaking:** `level` now defaults to `highlight`, so only mentions and
  private messages notify. A saved setting is kept; `level message` restores
  the old behavior.
- `max_body_length 0` now leaves the cap to Herdr or macOS, and values above
  240 are allowed.

### Fixed

- A slow or timed-out `herdr notification show` is retried instead of turning
  notifications off until the script is reloaded.
- Large `herdr api snapshot` output, which WeeChat delivers in several pieces,
  is joined before parsing, so the focus check works in big sessions.
- Terminal focus reporting is turned off when the script unloads, so stray
  `[I` and `[O` no longer appear in the input line.
- WeeChat's `beep` trigger is turned back on when the script stops notifying.
- The process timeout is longer than terminal-notifier's 10-second watchdog,
  so its "no response" exit is retried rather than treated as a failure.
- The focused pane is read only from the top level of Herdr's reply.
- Delays and retries use a monotonic clock, so clock changes do not stall them.

## [0.2.0] - 2026-10-20

### Added

- macOS Notification Center banners through `terminal-notifier`, with
  click-to-focus for the WeeChat pane and the terminal app.
- `delivery` and `terminal_bundle` settings.

## [0.1.0] - 2026-10-19

### Added

- Herdr toasts for messages in WeeChat buffers you cannot see, with a delay,
  batching, sounds, and an unread badge in the pane title.

[0.3.0]: https://github.com/lnutimura/herdr_notify.py/compare/ce9f038...v0.3.0
[0.2.0]: https://github.com/lnutimura/herdr_notify.py/compare/d25b964...ce9f038
[0.1.0]: https://github.com/lnutimura/herdr_notify.py/commit/d25b964
