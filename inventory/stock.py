import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from redis.asyncio import Redis

EXPIRY_KEY = "reservations:expiry"
# Long enough to cover any RabbitMQ redelivery of the same order.created.
PROCESSED_EVENT_TTL_SECONDS = 7 * 24 * 3600

_SCRIPTS = Path(__file__).parent / "scripts"


def stock_key(product_id: str) -> str:
    return f"stock:{product_id}"


def reservation_key(order_id: str) -> str:
    return f"reservation:{order_id}"


def processed_event_key(event_id: str) -> str:
    return f"processed-event:{event_id}"


@dataclass(frozen=True)
class StockLevel:
    product_id: str
    available: int
    reserved: int


@dataclass(frozen=True)
class ReserveResult:
    reserved: bool
    unavailable_product_ids: list[str] = field(default_factory=list)
    # {product_id: available} for products that just dropped below the threshold.
    became_low: dict[str, int] = field(default_factory=dict)
    duplicate: bool = False


class StockStore:
    """Stock lives in Redis as one hash per product: stock:{id} -> {available, reserved}."""

    def __init__(
        self,
        redis: Redis,
        low_stock_threshold: int,
        reservation_ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis
        self.low_stock_threshold = low_stock_threshold
        self._reservation_ttl = reservation_ttl_seconds
        self._clock = clock
        self._reserve = redis.register_script((_SCRIPTS / "reserve.lua").read_text())
        self._settle = redis.register_script((_SCRIPTS / "settle.lua").read_text())
        self._set_available = redis.register_script((_SCRIPTS / "set_available.lua").read_text())

    def is_low(self, level: StockLevel) -> bool:
        return level.available < self.low_stock_threshold

    async def get(self, product_ids: list[str]) -> list[StockLevel]:
        """Unknown products come back with zero units."""
        async with self._redis.pipeline(transaction=False) as pipe:
            for product_id in product_ids:
                pipe.hmget(stock_key(product_id), "available", "reserved")
            rows = await pipe.execute()
        return [
            StockLevel(product_id, int(available or 0), int(reserved or 0))
            for product_id, (available, reserved) in zip(product_ids, rows)
        ]

    async def set_available(self, product_id: str, available: int) -> tuple[StockLevel, bool]:
        """Returns the new level and whether this change made it low."""
        previous, reserved = await self._set_available(keys=[stock_key(product_id)], args=[available])
        level = StockLevel(product_id, available, int(reserved))
        became_low = int(previous) >= self.low_stock_threshold and self.is_low(level)
        return level, became_low

    async def reserve(self, event_id: str, order_id: str, items: dict[str, int]) -> ReserveResult:
        """All-or-nothing reservation of {product_id: quantity}, applied at most once per event_id."""
        keys = [processed_event_key(event_id), reservation_key(order_id), EXPIRY_KEY]
        args: list[str | int | float] = [
            order_id,
            self._clock() + self._reservation_ttl,
            PROCESSED_EVENT_TTL_SECONDS,
            self.low_stock_threshold,
        ]
        for product_id, quantity in items.items():
            keys.append(stock_key(product_id))
            args += [product_id, quantity]

        is_new, status, *rest = await self._reserve(keys=keys, args=args)
        if status == "failed":
            return ReserveResult(False, unavailable_product_ids=rest, duplicate=not is_new)

        became_low = {product_id: int(left) for product_id, left in zip(rest[::2], rest[1::2])}
        return ReserveResult(True, became_low=became_low, duplicate=not is_new)

    async def sell(self, order_id: str) -> bool:
        return bool(await self._settle(keys=[reservation_key(order_id), EXPIRY_KEY], args=[order_id, "sell"]))

    async def release(self, order_id: str) -> bool:
        return bool(await self._settle(keys=[reservation_key(order_id), EXPIRY_KEY], args=[order_id, "release"]))

    async def release_expired(self, limit: int = 100) -> list[str]:
        """Returns stock held by orders that never got confirmed or cancelled in time."""
        order_ids = await self._redis.zrangebyscore(EXPIRY_KEY, "-inf", self._clock(), start=0, num=limit)
        return [order_id for order_id in order_ids if await self.release(order_id)]

    async def seed(self, product_id: str, available: int) -> bool:
        """Sets initial stock only for products that don't have any yet."""
        created = await self._redis.hsetnx(stock_key(product_id), "available", available)
        await self._redis.hsetnx(stock_key(product_id), "reserved", 0)
        return bool(created)
