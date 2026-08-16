# Custom Cards: Workflow and Implementation Notes

## Purpose

The custom-card feature creates FIFA 14 FUT card variants for verified base-game players. A card keeps the original player identity while allowing local edits to the rating, rarity, six face attributes, detailed gameplay attributes, and player traits.

This feature is experimental. The local editor and persistence path work, but the FIFA 14 client does not yet render every edited gameplay value exactly as entered. This document is a handoff guide for reproducing the feature and investigating the remaining client-side mapping issue.

## User workflow

### Prerequisites

- A working FIFA 14 Local FUT checkout.
- The repository virtual environment at `.venv`.
- A local FIFA 14 installation configured through the normal launcher.

The player-data cache is generated from the installed FIFA 14 `cards_ng_db` data by the normal launcher when it is missing. An existing cache is preserved and reused. The editor refuses to operate when the cache is missing, invalid, or cannot be generated.

### Start the server and editor

1. Start the local runtime with `RUN_FIFA14_LOCAL_BETA.cmd`.
2. On first launch, wait for the launcher message that it generated `artifacts/fifa14-player-data-v1.json` from the installed game database. Later launches report that the existing cache is being reused.
3. Wait until the local FUT HTTP server is ready.
4. Open `http://127.0.0.1:8099/local-editor/` in a browser.
5. Search for a verified player.
6. Select the player and edit the card fields.
7. Keep **Grant to My Club** enabled when saving a new card, or use **Grant** beside an existing saved card.
8. Restarting the server is not required between editor changes, but it is useful when testing a new server build.
9. After a code change, grant the card again. Granting rebuilds the stored My Club ItemData payload.

Existing saved cards can be reused. Creating a new card is only necessary when testing a different version or wanting to preserve an earlier card unchanged.

### Recommended test card

For a clear mapping test, use a player whose base values are visibly different from the requested values. Set:

- Overall: `99`
- All six face attributes: `99`
- All gameplay attributes: `99`
- Weak foot: `5`
- Skill moves: `4`
- Preferred foot: opposite of the base player
- Attacking work rate: `High` (`2`)
- Defensive work rate: `Low` (`0`)

Record the player's original values before editing. In-game, inspect all four pages:

- Player Information
- Physical Attributes
- Mental Attributes
- Skill Attributes

Do not use only the overall rating as proof that gameplay mapping worked. The rating and face values can change even when detailed fields are mapped incorrectly.

## Editor/API workflow

The editor assets are served by `server/probe.py` and live in `server/editor/`.

### Browser routes

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/local-editor/` | Editor page |
| GET | `/local-editor/api/players?q=...` | Search verified editable players |
| GET | `/local-editor/api/cards` | List saved cards |
| GET | `/local-editor/api/cards/export` | Export the custom-card catalog |
| POST | `/local-editor/api/cards` | Create a card; optional `grant: true` |
| PUT | `/local-editor/api/cards/{cardId}` | Update a card; optional `grant: true` |
| POST | `/local-editor/api/cards/{cardId}/grant` | Rebuild/grant the card to My Club |
| DELETE | `/local-editor/api/cards/{cardId}` | Delete a saved definition |
| POST | `/local-editor/api/cards/import` | Import a catalog document |

The request and response bodies are JSON. Errors are returned as HTTP 400 JSON objects with an `error` field.

### Editor serialization

`server/editor/app.js` defines the fields and sends this shape:

```json
{
  "assetId": 158023,
  "rating": 99,
  "rareFlag": 3,
  "teamId": 1,
  "leagueId": 1,
  "attributes": [99, 99, 99, 99, 99, 99],
  "gameplayAttributes": {
    "acceleration": 99,
    "sprintspeed": 99,
    "positioning": 99,
    "finishing": 99,
    "shotpower": 99,
    "longshots": 99,
    "volleys": 99,
    "penalties": 99,
    "vision": 99,
    "crossing": 99,
    "freekickaccuracy": 99,
    "shortpassing": 99,
    "longpassing": 99,
    "curve": 99,
    "agility": 99,
    "balance": 99,
    "reactions": 99,
    "ballcontrol": 99,
    "dribbling": 99,
    "interceptions": 99,
    "headingaccuracy": 99,
    "marking": 99,
    "standingtackle": 99,
    "slidingtackle": 99,
    "jumping": 99,
    "stamina": 99,
    "strength": 99,
    "aggression": 99,
    "gkdiving": 99,
    "gkhandling": 99,
    "gkkicking": 99,
    "gkreflexes": 99,
    "gkpositioning": 99
  },
  "traits": {
    "weakfootabilitytypecode": 5,
    "skillmoves": 4,
    "preferredfoot": 2,
    "attackingworkrate": 2,
    "defensiveworkrate": 0
  }
}
```

Skill moves being limited to `0` through `4` is intentional. The FIFA 14 `cards_ng_db` descriptor defines `skillmoves` as `0..4`. The other relevant ranges are gameplay attributes `1..99`, weak foot `1..5`, preferred foot `1..2`, and work rates `0..2`.

## Code path

### 1. Player-data cache

`tools/extract_fifa14_player_data.py` reads verified player rows from the installed game database and writes `artifacts/fifa14-player-data-v1.json`. `tools/launch_fifa14_hub_store.ps1` invokes it automatically when the output file does not exist, using the resolved FIFA 14 `Game` directory and the project virtual-environment Python interpreter. The launcher does not overwrite an existing cache.

`server/player_data.py` validates that cache. It defines:

- `GAMEPLAY_FIELDS`: the 33 named gameplay fields used by the editor.
- `TRAIT_RANGES`: the five editable trait fields and their valid ranges.

`server/local_identity.py` merges the detailed cache data into `EDITOR_PLAYER_BY_ASSET`. The editor only searches this verified set.

### 2. Catalog validation and persistence

`server/custom_cards.py` owns the saved custom-card catalog.

The catalog is stored beside the identity database as:

```text
custom-cards.v1.json
```

The catalog validates:

- `assetId` exists in the verified base-player set.
- Exactly six face attributes are supplied.
- Gameplay fields are integers from `1` to `99`.
- Trait values obey `TRAIT_RANGES`.
- Custom versions are between `50` and `99`.
- A player/version pair is not duplicated.

The saved definition receives:

- `resourceId = resource_id_for(assetId, version)`
- `definitionId = assetId`
- `specialCard = true`
- `cardType = custom`

The resource/definition regression is covered by `tests/test_custom_cards.py`.

### 3. Grant and ItemData construction

`LocalIdentityStore.grant_custom_card()` in `server/local_identity.py`:

1. Loads the saved card definition.
2. Allocates or reuses an item ID in the `196000000000` custom-card range.
3. Adds normal My Club state such as contract, fitness, morale, and pile.
4. Calls `_canonical_player_payload()`.
5. Stores the resulting JSON in the `items` SQLite table.
6. Records the card-to-item relationship in `custom_card_grants`.

Granting an existing card reuses its item ID and replaces the stored payload. This is the required operation after changing the server code.

### 4. Canonical payload

`_canonical_player_payload()` creates the game-facing ItemData object. It currently emits:

- `assetId`, `resourceId`, and `definitionId`.
- `rating`, `preferredPosition`, team, league, nation, rarity, and player identity fields.
- `attributeList` and `attributeArray` for the six face attributes.
- `statsList` and `statsArray` for the separate five-value lifetime/stat counter structure.
- Flat gameplay and trait keys, such as `acceleration`, `finishing`, `preferredfoot`, and `defensiveworkrate`.
- Nested `gameplayAttributes` and `traits` metadata for local persistence/editor use.

The implementation currently places the flat gameplay and trait overrides in the early/native portion of the JSON object, before compatibility aliases. It also retains nested copies at the end of the payload.

Important: the unit tests prove that these values survive Python canonicalization and SQLite persistence. They do not prove that the FIFA client consumes every flat key as an ItemData override.

## Current known issue

The feature remains partially functional in-game:

- Card identity and overall rating change.
- Six face attributes can change.
- Preferred foot has been observed changing correctly.
- Gameplay pages show values that are changed, but not necessarily the values entered in the editor.
- Weak foot, skill moves, and defensive work rate have not consistently matched the editor input.
- Some values appear too high while others appear too low.

This does not currently look like integer overflow. The descriptor confirms the relevant numeric ranges, and values remain ordinary small integers in the exported catalog and canonical Python payload.

The unresolved question is the native FIFA 14 ItemData representation. The project currently sends long field names such as `acceleration` and `defensiveworkrate`. The installed `cards_ng_db` descriptor also defines short names, for example:

```text
acceleration          SPge
sprintspeed           NrcP
attackingworkrate     BqFe
defensiveworkrate     boFm
skillmoves            BAPc
weakfootability...    aOBn
```

Those short names belong to the database descriptor and are a strong lead, but they have not yet been proven to be valid keys in the FUT ItemData response. Do not assume that adding short keys is correct without comparing them to a captured retail ItemData payload or testing against a disposable card.

A second lead is array mapping. `attributeArray` is known to contain six face values, while `statsList` is currently treated as a five-value counter array. No verified conversion currently exists from the 33 named gameplay fields into an ordered native ItemData array. A collaborator should confirm whether the client expects named fields, short-name fields, a packed bitfield, or static database data combined with item-level overrides.

## Reproduction and evidence collection

For every test, save three artifacts:

1. The exported catalog from `/local-editor/api/cards/export`.
2. The exact stored item payload from the SQLite `items.payload` row for the granted item.
3. Screenshots or a written table of the four in-game attribute pages.

The comparison table should contain:

| Field | Editor input | Export JSON | SQLite ItemData | In-game value |
| --- | ---: | ---: | ---: | ---: |
| acceleration | 99 | 99 | 99 | ? |
| finishing | 99 | 99 | 99 | ? |
| defensiveworkrate | 0 | 0 | 0 | ? |
| skillmoves | 4 | 4 | 4 | ? |
| preferredfoot | 2 | 2 | 2 | ? |

Use a fresh item grant after each payload-format experiment. FIFA may cache an item or retain an older payload in the local client session.

To inspect the SQLite payload, first identify the active identity database path from the launcher configuration or runtime logs, then query the custom item range. Do not include personal paths, credentials, or unrelated account data in a public issue.

## Tests

The focused test command is:

```powershell
$env:PYTHONPATH = "server"
.\.venv\Scripts\python.exe -m unittest tests.test_custom_cards -q
```

The full repository test command is:

```powershell
$env:PYTHONPATH = "server"
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

The current tests verify:

- Versioned custom-card resource identity.
- Retention of gameplay and trait dictionaries during canonicalization.
- Presence and ordering of flat override keys in the generated Python payload.

They do not launch FIFA or validate client rendering. A future regression test should compare a captured/certified native ItemData payload with the client-visible values.

## Collaboration request

A useful issue or pull request should include:

- FIFA 14 build and platform.
- Local FUT branch/commit.
- Whether the card was newly created or re-granted after the server restart.
- The exported card JSON with unrelated identity data removed if necessary.
- The stored SQLite ItemData payload for the same item.
- The base player asset ID and original values.
- A table mapping editor value to in-game value.
- Screenshots of Player Information, Physical, Mental, and Skill pages.
- Whether the client was restarted between tests.

The main question for collaborators is:

> Which exact ItemData fields or encoded structure does FIFA 14 PC read for gameplay attributes and traits on a versioned FUT player item, and how do those fields map to the `cards_ng_db` descriptor names?

## Files involved

- `server/editor/index.html`: editor controls and valid HTML input ranges.
- `server/editor/app.js`: browser serialization and API calls.
- `server/probe.py`: local editor HTTP routes.
- `server/custom_cards.py`: validation and catalog persistence.
- `server/player_data.py`: gameplay field names and trait ranges.
- `server/local_identity.py`: grant flow, SQLite persistence, and canonical ItemData construction.
- `tools/extract_fifa14_player_data.py`: installed-game database extraction.
- `artifacts/cards-ng-db-descriptor/cards_ng_db-meta.manifest.json`: extracted database field names, short names, and ranges.
- `tests/test_custom_cards.py`: current regression coverage.
