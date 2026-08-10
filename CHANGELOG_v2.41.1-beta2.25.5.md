# v2.41.1 BETA 2.25.5

## Transfer List count / false-offer hotfix

The BETA 2.25.4 capture showed a real user auction in `/tradePile` (`total=1`) while the transfer hub still displayed zero, then a bot Buy Now rendered as `offers=1`. Selecting the resulting **View Offer** action requested `/trade/<id>/offer`, which previously fell through to the generic empty-auction response.

This build exposes owner transfer counts to the hub/list summary binders, emits zero outstanding offers for completed local-bot Buy Now sales, and gives stale GET `/offer` requests a complete safe auction-status response.
