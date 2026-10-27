import pytest
from fakeredis import FakeAsyncRedis

from inventory.stock import StockStore

THRESHOLD = 3
TTL = 900


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def store(clock: FakeClock):
    redis = FakeAsyncRedis(decode_responses=True)
    yield StockStore(redis, low_stock_threshold=THRESHOLD, reservation_ttl_seconds=TTL, clock=clock)
    await redis.aclose()
