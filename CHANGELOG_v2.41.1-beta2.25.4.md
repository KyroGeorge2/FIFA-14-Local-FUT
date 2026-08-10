# v2.41.1 BETA 2.25.4

## Transfer List crash hotfix

- Fixes a retail `CardsDLLzf.dll` access violation when opening Transfer List after upgrading pre-2.25.3 listings.
- Preserves a full ItemData snapshot when a local market bot buys a user listing.
- Keeps the sold item in the transfer pile until **Clear Sold**, matching the parser's expectation that closed auctions still contain ItemData.
- Repairs legacy active listings whose newly-added `item_payload` column was `{}`.
- Clears only unrecoverable already-sold malformed rows; sale coins already credited by 2.25.3 are preserved.
- Re-ages migrated active listings so an upgrade does not instantly sell them simply because their old `created_at` timestamp predates the bot economy.


# FIFA 14 Local FUT v2.41.1 BETA 2.25.4

## Transfer Market Economy BETA 2

- `/hub` now reports the complete synthetic live auction population instead of only the user's own listings.
- Every normal FUT player/special remains searchable; each card now has 3-7 simultaneous listings with varied BINs, starting bids, sellers and durations.
- Long-run old-era price references are preserved (92 NIF Ronaldo = 1.2m reference), while live values fluctuate in 30-minute market snapshots.
- Market purchases add demand pressure; successful user sales add supply pressure. Pressure persists in SQLite and mean-reverts over time.
- Synthetic auctions that are bought disappear for 15 minutes before supply regenerates.
- User listings at or below +10% of current market value are bought automatically by local market bots after a deterministic delay; cheapest listings sell fastest.
- Sold transfer-list items persist as closed/sold entries until cleared, and the seller receives proceeds minus the classic 5% transfer tax.
- DELETE `/trade/{id}` now clears sold entries or withdraws active ones without resurrecting sold cards.
- Existing club, squad, tournament, consumables, pack weights and 100m test balance are preserved.
