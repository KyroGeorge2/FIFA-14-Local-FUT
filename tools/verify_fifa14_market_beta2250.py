#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

import local_identity as li  # noqa: E402
import probe  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    base_assets = {int(p["assetId"]) for p in li.PLAYER_CATALOG}
    market_base_assets = {int(p["assetId"]) for p in li.MARKET_PLAYER_CATALOG}
    require(len(base_assets) >= 10_000, f"base catalogue unexpectedly small: {len(base_assets)}")
    require(base_assets <= market_base_assets, "one or more base FIFA 14 players are missing from market")
    require(len(li.MARKET_PLAYER_CATALOG) > len(li.PLAYER_CATALOG), "normal-mode specials missing from market")
    require(li.MARKET_LIVE_LISTING_COUNT > len(li.MARKET_PLAYER_CATALOG) * 2,
            f"market does not contain multiple listings per card: {li.MARKET_LIVE_LISTING_COUNT}")

    pique = li.PLAYER_BY_ASSET[152729]
    with tempfile.TemporaryDirectory(prefix="fifa14-beta2255-") as td:
        store = li.LocalIdentityStore(Path(td) / "market.sqlite3", initial_mode="new")
        store.create_club("Market Test", "MKT")
        payload = store._canonical_player_payload(item_id=181000000001, asset_id=152729, existing=pique, pile=7)
        keys = list(payload)
        require(payload["preferredPosition"] == "CB", f"Pique position is {payload['preferredPosition']!r}, expected CB")
        require(payload["cardsubtypeid"] == 1, "rare outfield player subtype must be 1")
        require(keys[:5] == ["id", "assetId", "resourceId", "rating", "preferredPosition"], f"unexpected ItemData prefix: {keys[:5]}")

        pile = probe.build_fut_empty_pile_sizes_response()
        require(len(pile.get("entries", [])) == 3, f"pileSize response still empty: {pile}")
        require(all(int(row.get("value", 0)) > 0 for row in pile["entries"]), f"invalid pile capacities: {pile}")

        # The hub tile must report the whole synthetic market, not only the user's listings.
        hub = store.hub_data()
        require(int(hub.get("auctionCount", 0)) == li.MARKET_LIVE_LISTING_COUNT,
                f"hub live transfers {hub} != synthetic market {li.MARKET_LIVE_LISTING_COUNT}")

        # Unfiltered market total is listing count (3-7 copies/card), not unique-card count.
        all_market = store.market_search({"start": ["0"], "num": ["12"]})
        require(int(all_market.get("total", 0)) == li.MARKET_LIVE_LISTING_COUNT,
                f"unfiltered market total mismatch: {all_market.get('total')}")

        # Ronaldo: long-run reference remains 1.2m, but live listings fluctuate around it.
        ronaldo = next(p for p in li.MARKET_PLAYER_CATALOG if int(p.get("resourceId", 0)) == 20801)
        require(store._market_price_for(ronaldo) == 1_200_000, "Ronaldo long-run reference changed")
        result = store.market_search({"definitionId": ["20801"], "start": ["0"], "num": ["100"]})
        normal = [a for a in result["auctionInfo"] if int(a["itemData"].get("resourceId", 0)) == 20801]
        require(len(normal) >= 3, f"normal Ronaldo has too few simultaneous listings: {len(normal)}")
        normal_prices = sorted(int(a["buyNowPrice"]) for a in normal)
        require(normal_prices[0] < normal_prices[-1], f"Ronaldo listings have no price spread: {normal_prices}")
        require(all(900_000 <= p <= 1_500_000 for p in normal_prices), f"Ronaldo live values left expected band: {normal_prices}")
        require(all(a["itemData"].get("preferredPosition") == "LW" for a in normal), "Ronaldo market position is not LW")

        # Buy Now debits exact live price, creates New Items and removes that exact auction temporarily.
        target = normal[0]
        trade_id = int(target["tradeId"])
        price = int(target["buyNowPrice"])
        before = store.currencies()["credits"]
        bought = store.market_bid(trade_id, price)
        require(bought.get("tradeState") == "closed", f"Buy Now did not close auction: {bought}")
        after = store.currencies()["credits"]
        require(before - after == price, f"market debit mismatch: {before} -> {after}, price {price}")
        bought_id = int(bought["itemData"]["id"])
        pending = store.purchased_items()
        require(any(int(x.get("id", 0)) == bought_id for x in pending.get("itemData", [])), "market win missing from New Items")
        refreshed = store.market_search({"definitionId": ["20801"], "start": ["0"], "num": ["100"]})
        require(not any(int(a["tradeId"]) == trade_id for a in refreshed["auctionInfo"]), "bought synthetic auction regenerated immediately")
        require(store.hub_data()["auctionCount"] == li.MARKET_LIVE_LISTING_COUNT - 1,
                "hub did not remove recently sold synthetic auction")
        with closing(sqlite3.connect(store.database)) as con:
            trend = con.execute("SELECT pressure,total_buys FROM market_trends WHERE resource_id=20801").fetchone()
            require(trend is not None and trend[0] > 0 and trend[1] >= 1, f"market buy did not add demand trend: {trend}")

        moved = store.move_items([{"id": bought_id, "pile": 7}])
        require(moved["itemData"][0]["success"] is True, f"market win could not be assigned: {moved}")
        listed = store.move_items([{"id": bought_id, "pile": 5}])
        require(listed["itemData"][0]["success"] is True, f"move to transfer pile failed: {listed}")
        unlisted_pile = store.trade_pile()
        require(unlisted_pile.get("total") == 1 and unlisted_pile.get("unlisted") == 1,
                f"unlisted pile-5 card disappeared from transfer list: {unlisted_pile}")
        require(any(int(a.get("itemData", {}).get("id", 0)) == bought_id for a in unlisted_pile.get("auctionInfo", [])),
                f"unlisted transfer card missing from auctionInfo renderer: {unlisted_pile}")

        # Competitive user listing is auto-bought; proceeds use the classic 5% market tax.
        live_value = store._market_current_value_for(ronaldo, now=1_786_383_600)
        ask = store._market_round_price(live_value * 0.95)
        own = store.list_for_sale({"itemData": {"id": bought_id}, "startingBid": max(150, int(ask * .8)), "buyNowPrice": ask, "duration": 3600})
        own_trade = int(own["tradeId"])
        active_pile = store.trade_pile()
        require(active_pile.get("selling") == 1 and active_pile.get("total") == 1,
                f"trade pile did not report active user listing: {active_pile}")
        require(active_pile.get("tradePileCount") == 1 and active_pile.get("transferListCount") == 1,
                f"transfer-list summary aliases did not report one item: {active_pile}")
        owner_hub = store.hub_data()
        require(owner_hub["auctionCount"] == li.MARKET_LIVE_LISTING_COUNT,
                "hub did not combine synthetic + user active listing after one synthetic sale")
        require(owner_hub.get("tradePileCount") == 1 and owner_hub.get("selling") == 1 and owner_hub.get("sold") == 0,
                f"hub owner transfer-list counters are wrong: {owner_hub}")
        # Reproduce the exact BETA 2.25.4 upgrade bug: an old market_listings
        # row gained item_payload with SQLite's '{}' default. The live backing
        # item still exists when the bot tick begins, so the server must snapshot
        # it before closing the auction and must retain renderable Sold ItemData.
        # Backdate only in the verifier so lazy bot-buy logic can be tested instantly.
        with closing(sqlite3.connect(store.database)) as con, con:
            con.execute(
                "UPDATE market_listings SET item_payload='{}',created_at=created_at-1000 "
                "WHERE trade_id=?",
                (own_trade,),
            )
        coins_before_sale = store.currencies()["credits"]
        pile_after = store.trade_pile()
        coins_after_sale = store.currencies()["credits"]
        expected_net = int(round(ask * (1.0 - li.MARKET_SELL_TAX_RATE)))
        require(pile_after.get("selling") == 0 and pile_after.get("sold") == 1, f"auto-sale did not close listing: {pile_after}")
        require(coins_after_sale - coins_before_sale == expected_net,
                f"seller proceeds mismatch: got {coins_after_sale-coins_before_sale}, expected {expected_net}")
        sold_listing = next(a for a in pile_after["auctionInfo"] if int(a["tradeId"]) == own_trade)
        require(sold_listing.get("tradeState") == "closed" and int(sold_listing.get("currentBid", 0)) == ask,
                f"sold listing shape invalid: {sold_listing}")
        require(int(sold_listing.get("offers", -1)) == 0,
                f"auto-sold Buy Now incorrectly advertises a pending offer: {sold_listing}")
        sold_hub = store.hub_data()
        require(sold_hub.get("tradePileCount") == 1 and sold_hub.get("selling") == 0 and sold_hub.get("sold") == 1,
                f"hub sold counters are wrong: {sold_hub}")
        safe_status = store.market_status([own_trade])
        require(safe_status.get("total") == 1 and int(safe_status["auctionInfo"][0].get("offers", -1)) == 0,
                f"defensive View Offer status is not safe: {safe_status}")
        require('trade-offer-safe-beta2255' in (ROOT / 'server' / 'probe.py').read_text(encoding='utf-8'),
                'defensive GET /trade/<id>/offer route missing')
        sold_item = sold_listing.get("itemData")
        require(isinstance(sold_item, dict) and int(sold_item.get("id", sold_item.get("itemId", 0)) or 0) == bought_id,
                f"sold listing lost ItemData snapshot: {sold_listing}")
        require(sold_item.get("itemState") == "sold",
                f"sold listing ItemData was not marked sold: {sold_item}")

        # Clearing Sold must not resurrect the card.
        store.withdraw_listing(own_trade)
        require(store.trade_pile().get("total") == 0, "clear sold did not remove closed listing")
        with closing(sqlite3.connect(store.database)) as con:
            row = con.execute("SELECT 1 FROM items WHERE item_id=?", (bought_id,)).fetchone()
            require(row is None, "clearing sold resurrected the sold item")
            trend = con.execute("SELECT pressure,total_buys,total_sales FROM market_trends WHERE resource_id=20801").fetchone()
            require(trend is not None and trend[1] >= 1 and trend[2] >= 1, f"supply/demand trend counters not persisted: {trend}")

        # Pack-weight contract remains untouched: lenient specials, hard max 2.
        definition = li.PACK_DEFINITIONS[105]
        special_counts: list[int] = []
        with closing(sqlite3.connect(store.database)) as con, con:
            for pack_id in range(900_000, 900_060):
                items = store._generate_pack_contents_locked(con, pack_id=pack_id, definition=definition)
                count = sum(1 for item in items if bool(item.get("specialCard")) or int(item.get("rareflag", 0) or 0) > 1)
                special_counts.append(count)
        require(max(special_counts) <= 2, f"pack emitted >2 specials: max={max(special_counts)}")
        require(sum(c >= 1 for c in special_counts) >= 25, f"100k special rate too low: {special_counts}")
        require(sum(c == 2 for c in special_counts) <= 8, f"double-special packs too common: {special_counts}")

    report = {
        "ok": True,
        "build": "2.41.1-beta2.25.9",
        "basePlayersOnMarket": len(base_assets),
        "normalModeMarketCards": len(li.MARKET_PLAYER_CATALOG),
        "syntheticLiveTransfers": li.MARKET_LIVE_LISTING_COUNT,
        "listingsPerCardMin": min(li._market_listing_copies_for_card(p) for p in li.MARKET_PLAYER_CATALOG),
        "listingsPerCardMax": max(li._market_listing_copies_for_card(p) for p in li.MARKET_PLAYER_CATALOG),
        "normalRonaldoReference": 1_200_000,
        "userAutoBuyBand": "+10% current market value",
        "marketSellTaxPct": int(li.MARKET_SELL_TAX_RATE * 100),
        "transferListCapacity": li.TRANSFER_LIST_CAPACITY,
        "jumboRareSpecialChance": li.PACK_WEIGHTS_DOCUMENT["specialChancePerPack"]["105"],
        "maxSpecialsPerPack": li.PACK_WEIGHTS_DOCUMENT["maxSpecialsPerPack"],
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
