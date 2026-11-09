# Contributing to MousePods

Thanks for helping! MousePods is a small project, so the rules are simple.

## The easiest ways to help (no code)

- **Test it in a game** and tell us how it went: [game report](https://github.com/starman-tech/mousepods/issues/new?template=game_report.yml).
  Working games go in the README table.
- **Try other earbuds** (AirPods Pro 1, AirPods 4, AirPods Max…): [device report](https://github.com/starman-tech/mousepods/issues/new?template=device_report.yml).
  A log from `--diag` helps a lot.
- **Report a bug**: [bug report](https://github.com/starman-tech/mousepods/issues/new?template=bug_report.yml).
- **Translate the interface** (see [Translations](#translations)).

## Running it from source

```bash
git clone https://github.com/starman-tech/mousepods && cd mousepods
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python airpods_mouse_controller.py --ui     # the app
.venv/bin/python -m unittest discover -s tests        # the tests, no hardware needed
```

On Bazzite or Fedora Atomic, use a distrobox (see the README). Don't run the source version and the
Flatpak at the same time: both want the AirPods and port 8737.

## Pull requests

- **One topic per PR.** Small PRs get merged fast; a big one is fine if it really is one feature.
- **All tests must pass**, and new behaviour comes with a test. Most of the code is testable without
  AirPods: protocol parsing, the engine, calibration, and replays of real captures in `captures/`.
- **Say what you tested on real hardware** (which earbuds, distro, desktop) and what you could not test.
- **No new dependency** without asking first in an issue. The core needs only `evdev` and `numpy`.
- Keep the existing style: plain Python, short functions, comments that explain *why*.
- AI-assisted contributions are welcome (this project was built with Claude), as long as you have
  read and tested what you send.

## Translations

Interface texts live in `ui/i18n/<lang>.json`, one file per language, English is the source.
To add a language: copy `en.json`, translate the values (not the keys), add the language code to
`SUPPORTED` in `i18n.py` and a button in the language switch in `ui/index.html`. The tests check
that every language has every key.

## Captures and privacy

Captures of real Bluetooth traffic are very useful, but they can contain identifiers. Before you
attach one or commit it to `captures/`, remove MAC addresses, serial numbers and device names
(the existing captures show the masked format). Put private files in `captures/private/`, which git ignores.

## Protocol findings

New findings about AirPods (a new report, a sensor, a command) are the most valuable contribution.
Document them in `docs/` (see `DISCOVERIES.md` and `REMOTEHID-PROTOCOL.md`): what you sent, what came
back, on which model and firmware, and how sure you are.

## License

By contributing you agree that your code is released under Apache-2.0 and your documentation and
captures under CC BY 4.0, like the rest of the project (see `LICENSE`, `NOTICE` and the README).

## Releases

Maintainers: see [docs/RELEASING.md](docs/RELEASING.md).
