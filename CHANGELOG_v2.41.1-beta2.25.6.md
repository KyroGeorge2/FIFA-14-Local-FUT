# v2.41.1 BETA 2.25.8

## Full-match reward + stay-in-FUT hotfix

The BETA 2.25.5 full-match capture showed a valid `WIN` DestroyMatch request containing the real 8-0 match statistics, while the local response returned `secondsPlayed=0` and zero-filled both match-stat objects. The backend still settled the win and credited a completion reward, but the retail post-match screen therefore rendered zero awards.

This build:

- treats a terminal WIN/DRAW/LOSS DestroyMatch as a completed 90-minute match when the PC request omits duration;
- echoes the client's parser-native `myMatchStats` / `opponentMatchStats` instead of zeroing them;
- maps FIFA 14's `passingPercentage` and `possessionPercentage` fields into the local skill-award calculation;
- prefers the team `goalsFor` value over per-player `goals` entries when calculating match coins;
- repairs old beta profiles whose repeated QA forfeits reduced the DNF skill multiplier to zero by enforcing a 0.25 floor;
- pre-arms the existing one-shot `fcc_logout -> GameHub` repair when a real match is created/started, because a completed match's final HTTP send is not always visible to the socket hook after gameplay;
- keeps the short `/match/end` re-arm for paths where the final request is observable.

The transfer-market economy, Transfer List sale-state fixes, pack weights, consumables and tournament progression remain unchanged.
