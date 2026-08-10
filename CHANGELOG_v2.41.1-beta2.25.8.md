# FIFA 14 Local FUT v2.41.1 BETA 2.25.8

- Fixed squad consumable discovery by combining context-6 StickerBook stats with `/clubUser` consumable ItemData preloading.
- Fixed false duplicate flags on newly purchased market cards by excluding New Items and Transfer List rows from the already-owned duplicate scan and preventing self-pairing.
- Restored non-zero market-card quick-sell values, including lazy repair for previously persisted tradeable cards with `discardValue: 0`.
- Rebalanced normal-mode special selection by family so IF, TOTS/blue, MOTM, TOTY, green and other special variants are all reachable in packs.
- Kept the existing 100k-pack special chance and hard two-special maximum.
- Added a BETA 2.25.8 regression verifier covering the exact four fixes above and actual 100k-pack special-family simulation.
