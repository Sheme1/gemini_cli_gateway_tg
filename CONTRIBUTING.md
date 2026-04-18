# Contributing

Thanks for taking a look at this project.

## Project status

This repository is still an MVP.

- It works.
- It is useful.
- It is not polished.
- Forks and local customizations are completely welcome.

If you want to adapt it to your own Gemini CLI workflow or deployment setup, keeping a fork is a valid and expected way to use this project.

## Good first contributions

- Fix rough edges in the Telegram UX
- Improve deployment docs and install scripts
- Add tests around parser, session handling, and artifact delivery
- Improve Linux production behavior
- Improve approval handling in headless mode

## Before opening a PR

Run:

```bash
ruff check .
ruff format --check .
pytest tests -v
```

## Notes for contributors

- The main production target is Linux with `systemd`
- Local development often happens on Windows, but production behavior should be validated on Ubuntu or another Linux host when possible
- Gemini CLI output formats can change between versions, so parser changes should be made defensively

## Reporting issues

When filing a bug, include:

- your OS
- your Python version
- your Gemini CLI version
- your deployment mode (`python -m gateway.main`, `systemd`, or Docker)
- logs or reproduction steps
