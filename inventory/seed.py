"""Initial stock for some catalog-service products (same ids).

Cards come from catalog-service's pokemontcg.io import and sealed products from its seed.

Only fills products without stock, so running it again never undoes sales.
"""

import asyncio
import os

from redis.asyncio import Redis

from inventory.stock import StockStore

INITIAL_STOCK = {
    "prd_mew_006_en_nm": 2,  # starts low, to show the low_stock flag
    "prd_mew_003_en_nm": 4,
    "prd_mew_009_en_nm": 4,
    "prd_mew_007_en_nm": 12,
    "prd_mew_008_en_nm": 8,
    "prd_mew_004_en_nm": 12,
    "prd_mew_025_en_nm": 10,
    "prd_mew_151_en_nm": 3,  # one sale away from stock.low
    "prd_obf_125_en_nm": 1,
    "prd_svi_191_en_nm": 20,
    "prd_svi_196_en_nm": 20,
    "prd_mew_etb_en": 6,
    "prd_obf_box_en": 4,
    "prd_obf_pack_en": 36,
    "prd_sv2a_box_ja": 0,  # out of stock
}


async def seed() -> None:
    redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    # The threshold and TTL don't matter for seeding.
    store = StockStore(redis, low_stock_threshold=0, reservation_ttl_seconds=0)
    created = [product_id for product_id, units in INITIAL_STOCK.items() if await store.seed(product_id, units)]
    await redis.aclose()
    print(f"seeded {len(created)} products, {len(INITIAL_STOCK) - len(created)} already had stock")


if __name__ == "__main__":
    asyncio.run(seed())
