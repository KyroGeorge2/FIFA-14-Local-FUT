# Contributing

Thanks for helping improve FIFA 14 Local FUT.

## Before submitting a change

- Keep patches focused and document what changed.
- Do not commit generated `artifacts`, certificates, virtual environments, logs, or local club state.
- Do not commit EA game executables, DLLs, archives, credentials, or other proprietary files copied from a FIFA installation.
- Run the repository checks locally when possible:

  `python -m compileall -q server tools`

- Run the test suite when possible. It uses the bundled catalogues and a
  temporary database, so no FIFA 14 installation is required:

  `python -m pip install -r requirements-dev.txt`

  `python -m pytest tests -q`

- For gameplay/runtime changes, include the exact test performed and whether the user remained connected to FUT afterwards.

## Bug reports

Attach the relevant local build diagnostics where safe to do so, but remove personal paths, account information, credentials, and proprietary game files before posting.
