# BETA 2.25.9 validation

The dedicated `verify_fifa14_postmatch_beta2259.py` regression covers the exact completed Gold Cup path observed in the BETA 2.25.8 capture.

It verifies:

- a completed WIN defaults to 5400 seconds;
- Gold Cup round 1 derives difficulty 3 instead of returning 0;
- Completion Award and Skill Award are non-zero/native settlement values;
- DestroyMatch reward/credits match the committed wallet delta;
- Gold Cup advances from round 1 to round 2 and is advertised as resumable;
- a later knockout LOSS resets the resumable state;
- `/user` republishes the current coin balance;
- GameReporting component 28 command 2 is a typed success;
- ResultNotification component 28 command 114 contains terminal success, final-result and report IDs.

The existing progression, consumables, transfer-market, pack-performance and install verifiers remain part of launcher preflight.
