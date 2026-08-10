# BETA 2.25.4 Transfer List crash fix

The BETA 2.25.3 market migration added `item_payload` to existing `market_listings`.
SQLite initialized pre-existing rows to `{}`. When the new local buyer simulation
auto-sold one of those legacy listings, the backing item was deleted before the
closed auction was rendered. FIFA 14's retail CardsDLL dereferences ItemData even
for sold auctions, so `/tradePile` could return a closed listing with
`itemData: {}` and crash inside CardsDLL.

BETA 2.25.4:
- snapshots legacy active listing ItemData before market simulation can sell it;
- re-ages migrated active listings so an upgrade cannot instantly sell them just
  because their old `created_at` predates the bot market;
- keeps a sold item's full ItemData in the transfer pile until Clear Sold;
- removes the retained item only when the sold listing is cleared;
- safely clears already-corrupted closed 2.25.3 rows while preserving coins
  previously credited for those sales;
- includes a verifier regression that explicitly sets `item_payload='{}'`,
  auto-sells the listing, and checks that the sold auction remains renderable.
