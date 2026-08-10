# FIFA 14 Local FUT v2.41.1 BETA 2.25.9 — FRIEND / FRESH PROFILE PACKAGE

This is the clean shareable form of BETA 2.25.9. It intentionally contains **no user's local FUT SQLite save**.

## First launch on another PC

- The launcher creates its own persistent profile at `%LOCALAPPDATA%\FIFA14LocalFUTBeta\local-fut-beta-v2410.sqlite3`.
- A fresh profile gets a new local club, a 23-player untradeable bronze starter squad, 7 contracts per starter player, 99 starting fitness, starter club cosmetics, a 0-0-0 record, and 0 FIFA Points.
- Fresh-profile coins start at **0** in this share package. Match/store/market progression then belongs only to that PC's local save.
- `GIVE_100M_TEST_COINS.cmd` is included only as an optional developer/testing shortcut.

## Requirements / compatibility

1. FIFA 14 must currently be installed at `C:\Program Files\EA Games\FIFA 14\Game`.
2. This build is exact-binary guarded. It currently expects:
   - `fifa14.exe` SHA-256 `034991BCE371BB2D4E802184DC43E423B0FD7B6D06BF0E41EF12CA0DBC623916`
   - `CardsDLLzf.dll` SHA-256 `642B11EF3DA7EF28E55A40965A2F364012FA6090252A84C3D9BFBA5AB1F060E6`
   - `powdllzf.dll` SHA-256 `AC39EE88E8F0D3A90C0C9EB3C01C030110F892EBA38EC52ED8AD05038C2B24F0`
3. Python 3.10+ is needed on first launch. The launcher creates a local `.venv` and installs the small `requirements.txt` dependency set automatically.
4. Run `RUN_FIFA14_LOCAL_BETA.cmd`; it requests Administrator elevation because the build patches FIFA files under Program Files.

If the friend's FIFA binaries do not match the hashes above, the launcher deliberately stops rather than applying native patches to an unknown executable/DLL build.

## Independence

The two PCs do **not** share club state, coins, squads, packs, transfer listings, tournament progress, or records in this package. Each machine has its own `%LOCALAPPDATA%` database. Networked/shared-club functionality is a separate future server/Discord-integration step.


## Friend slot-capacity hotfix

This copy includes the helperFunctions relocation fallback described in `FRIEND_SLOT_CAPACITY_FIX.md`. It is intended for installs where the branch-only payload recompresses larger than the 20,544-byte retail slot.
