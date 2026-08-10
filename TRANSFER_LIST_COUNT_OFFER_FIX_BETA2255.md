# BETA 2.25.5 Transfer List count + false-offer fix

Observed in the uploaded BETA 2.25.4 trace:

- POST `/auctionhouse` succeeded.
- GET `/tradePile` returned `total=1`.
- after the bot sale, FIFA requested GET `/trade/2000000001/offer`.
- that request hit the generic 54-byte trade fallback.

Root cause: `_market_auction()` marked every closed owner sale as `offers=1`, which is a bid/offer semantic rather than a completed Buy Now semantic. The UI therefore exposed **View Offer**.

BETA 2.25.5 keeps `currentBid` as the completed sale price but emits `offers=0`, publishes active/sold/total owner counts, and handles stale GET `/offer` with a complete status document.
