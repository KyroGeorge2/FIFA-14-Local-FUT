# FIFA 14 Local FUT v2.41.1 BETA 2.25.9 — post-match reporting / tournament reward hotfix

BETA 2.25.9 is built directly on the working 2.25.8 Store/market/consumables branch. It targets the completed-match failure captured after a real Gold Cup game.

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
3. Run `RUN_FIFA14_LOCAL_BETA.cmd` as Administrator.
4. Wait for the launcher/server to report that it is ready before entering Ultimate Team.

This project expects an existing legitimate FIFA 14 PC installation and does not include the game itself.

## Repository / development

If you cloned the source repository rather than downloading a release, see [GITHUB_SETUP.md](GITHUB_SETUP.md), [CONTRIBUTING.md](CONTRIBUTING.md), and [RELEASING.md](RELEASING.md).

Generated runtime state, certificates, diagnostics, virtual environments, and release ZIPs are excluded by `.gitignore`.

## License

See [LICENSE](LICENSE). FIFA, FIFA 14, Ultimate Team, EA SPORTS, and related marks/assets belong to their respective owners. This is an independent preservation/revival project and is not affiliated with or endorsed by Electronic Arts.
