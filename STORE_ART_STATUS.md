# FIFA 14 Store Art Status — BETA 2.22

## Current live result

The BETA 2.3 PC test solved the immediate Store-art regression: the normal tier mapping now renders the rich native FIFA 14 player-art pack fronts instead of plain Bronze/Silver/Gold labels or green `NOT FOUND` placeholders.

BETA 2.22 **continues to lock that working mapping**. No Store art IDs or display-group rules were changed.

## Mapping kept by the normal launcher

- Bronze -> asset 1
- Silver -> asset 2
- Gold -> asset 3

Promo/large packs still use their own names, prices and purchase definitions, while the retail frontend supplies the native player-art treatment around the safe tier asset.

## Failed survey that remains disabled

- Asset 4 resolved to EA Season Ticket artwork.
- Attempted IDs 6 through 11 produced green `NOT FOUND` placeholders.

Those IDs are not used by the normal launcher.

## Read-only research remains enabled

`fut-store-ui-static-extract.zip` still captures StoreFrontWC routing around `ASSET_PATH`, `BANNER_PATH`, `FOREGROUND_ASSET_PATH`, overrides, special-pack, promo/deal and Season Ticket identifiers. This remains useful for later historical fidelity work, but BETA 2.22 will not risk the now-working Store visuals while Seasons/Tournaments are being fixed.
