# FIFA 14 Local FUT v2.41.1 BETA 2.25.9 — post-match reporting / tournament reward hotfix

BETA 2.25.9 is built directly on the working 2.25.8 Store/market/consumables branch. It targets the completed-match failure captured after a real Gold Cup game.

## Game-build compatibility

The launcher does **not** hard-block FIFA 14 installations based on the SHA-256 hash of `fifa14.exe`, `CardsDLLzf.dll`, or `powdllzf.dll`. If the required files are present, startup is allowed to continue. This makes the local FUT runtime usable with other FIFA 14 executable revisions where the underlying game data is compatible.

This is not a promise that every modified or alternate executable is compatible. Native signatures and archive layouts can differ between game builds. For the helperFunctions branch patch, the patcher now resolves a compatible package by its reviewed **decoded content** instead of assuming retail record index 2146; if an older/smaller `patch.bh` does not contain it, the launcher tries the base `data0` archive. If neither archive has a uniquely verified package, that write is skipped and startup continues rather than guessing an offset. No crack, DRM-bypass, or executable files are included in this repository.


## BETA 2.25.9 changes

- Completes Blaze GameReporting component 28 / command 2 with its asynchronous terminal ResultNotification instead of returning only an empty observation success.
- Derives the played offline-tournament round difficulty when FIFA omits `matchDifficulty` from `/match/end`.
- Publishes the committed Completion Award, Skill Award, total match reward and refreshed coin balance in the completed DestroyMatch response.
- Publishes the refreshed wallet balance in `/ut/game/fifa14/user` after settlement.
- Advances knockout tournament progress on WIN, keeps the round on DRAW, and resets the cup on LOSS/DNF/QUIT.
- Persists round 2+ independently of the unsafe first-round opaque tournament blob, so leaving/re-entering FUT does not reset a won round.
- Awards the advertised tournament prize once after a final-round WIN and leaves the cup replayable.
- Preserves BETA 2.25.8 consumables, market, special-pack weighting, squad, record and pack-FPS fixes.

## Target test

Enter Gold Cup, finish round 1 with a win, verify non-zero match coins on the result screen, press Advance, remain connected to FUT, then reopen the cup and verify round 2 is the active round.

## Installation

1. Extract the release ZIP to a normal writable folder.
2. Run `INSTALL_PREREQUISITES.cmd` as Administrator once if dependencies are missing.
3. Run `RUN_FIFA14_LOCAL_BETA.cmd` as Administrator. The launcher auto-detects FIFA 14; if needed, paste the `Game` folder once and it will be remembered in `config.local.psd1`.
4. Wait for the launcher/server to report that it is ready before entering Ultimate Team.

## If something does not work

Run `DIAGNOSE.cmd`. It checks the conditions behind most startup and connection
reports, and prints what to do about each one:

- Python version, and whether `cryptography` and `frida` are installed
- whether OpenSSL can be found, which the legacy certificates require
- whether ports 42127, 42128, 8080, 8099 and 44125 are free
- whether the process is elevated, which Frida injection needs
- whether the FIFA 14 folder resolves and contains the expected files
- whether the bundled catalogues are complete and the server modules import

It also writes `diagnostics-report.txt`. Personal paths and your Windows user
name are redacted, so it is safe to paste into an issue.

This project expects an existing legitimate FIFA 14 PC installation and does not include the game itself.

## Repository / development

This repository is already prepared for GitHub with `.gitignore`, `.gitattributes`, issue templates, repository checks, and release-packaging scripts.

To publish a fresh clone/folder:

1. Create an empty GitHub repository.
2. Run `SETUP_GITHUB_REPO.cmd`.
3. Run `PUSH_TO_GITHUB.cmd` and paste the repository URL.

For future releases, run `PACKAGE_RELEASE.cmd`; the clean runtime ZIP is written to `dist\` and can be attached to a GitHub Release. Keep version history/changelogs in GitHub Releases rather than adding separate Markdown files to the repository root.

Generated runtime state, certificates, diagnostics, virtual environments, and release ZIPs are excluded by `.gitignore`. Do not commit FIFA 14 executables, EA DLLs, game archives, account credentials, private keys, or files copied from a user's game installation.

For contributions, keep changes focused and include the exact gameplay/runtime test performed where relevant. For security-sensitive reports, do not post credentials, private keys, access tokens, or personal data in a public issue.

## License

See [LICENSE](LICENSE). FIFA, FIFA 14, Ultimate Team, EA SPORTS, and related marks/assets belong to their respective owners. This is an independent preservation/revival project and is not affiliated with or endorsed by Electronic Arts.

## GitHub issue hotfix notes

This package can now use FIFA 14 from any drive. On first launch it checks `config.local.psd1`, the `FIFA14_GAME_ROOT` environment variable, and common EA/Origin/Steam library locations. If nothing is found, paste the folder that contains `fifa14.exe` once; the choice is saved locally and is ignored by Git. An editable template is included as `config.local.psd1.example`.

Fixed in this hotfix: pack/tournament localization keys no longer resolve to `*`; unlisted pile-5 cards remain visible in the Transfer List; the player catalogue contains 61 goalkeepers instead of 5; and an unknown `futPackSelect` package is preserved with a warning instead of killing startup.

Public compatibility update: if `futPackSelect` cannot be inspected at all because an installation uses a different `patch.big`/`patch.bh` layout, startup continues in unverified compatibility mode and does **not** write recovery bytes to that unknown archive. The branch-only helperFunctions patch additionally supports shifted/repacked BH indexes by content scanning and can fall back from `patch` to `data0`; writes still occur only after the decoded helperFunctions package matches the reviewed identity.

NAV compatibility update: the returning-user/first-use `futLogInFlow.nav` helper no longer assumes retail `data1.bh` record index 16469 or offset 299329536. It resolves the stable path hash, verifies the decoded FUT login transition structure, and uses the installed record offset/capacity. If an alternate NAV payload cannot be verified safely, it is left untouched and startup continues in compatibility mode instead of aborting.

**Known stadium workaround (deferred):** before entering a single-player tournament match, own and apply a stadium card in My Club. A missing active stadium can produce the dark/void match presentation reported in issues #4/#5; this package intentionally does not attempt another risky client stadium patch yet.
