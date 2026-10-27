from inventory.stock import StockLevel, StockStore
from tests.conftest import TTL, FakeClock


async def levels(store: StockStore, *product_ids: str) -> dict[str, tuple[int, int]]:
    return {level.product_id: (level.available, level.reserved) for level in await store.get(list(product_ids))}


async def test_unknown_product_has_zero_stock(store: StockStore):
    assert await store.get(["prd_missing"]) == [StockLevel("prd_missing", 0, 0)]


async def test_reserve_moves_units_from_available_to_reserved(store: StockStore):
    await store.seed("prd_a", 10)
    await store.seed("prd_b", 5)

    result = await store.reserve("evt_1", "ord_1", {"prd_a": 2, "prd_b": 1})

    assert result.reserved and not result.duplicate
    assert await levels(store, "prd_a", "prd_b") == {"prd_a": (8, 2), "prd_b": (4, 1)}


async def test_reserve_is_all_or_nothing(store: StockStore):
    await store.seed("prd_a", 10)
    await store.seed("prd_b", 1)

    result = await store.reserve("evt_1", "ord_1", {"prd_a": 2, "prd_b": 3, "prd_missing": 1})

    assert not result.reserved
    assert sorted(result.unavailable_product_ids) == ["prd_b", "prd_missing"]
    assert await levels(store, "prd_a", "prd_b") == {"prd_a": (10, 0), "prd_b": (1, 0)}


async def test_repeated_event_returns_first_outcome_without_reserving_twice(store: StockStore):
    await store.seed("prd_a", 10)
    await store.reserve("evt_1", "ord_1", {"prd_a": 2})

    again = await store.reserve("evt_1", "ord_1", {"prd_a": 2})

    assert again.reserved and again.duplicate
    assert await levels(store, "prd_a") == {"prd_a": (8, 2)}


async def test_reserve_reports_low_stock_only_when_crossing_the_threshold(store: StockStore):
    await store.seed("prd_a", 4)  # threshold is 3

    first = await store.reserve("evt_1", "ord_1", {"prd_a": 2})
    second = await store.reserve("evt_2", "ord_2", {"prd_a": 1})

    assert first.became_low == {"prd_a": 2}
    assert second.became_low == {}


async def test_sell_keeps_units_out_of_available(store: StockStore):
    await store.seed("prd_a", 10)
    await store.reserve("evt_1", "ord_1", {"prd_a": 3})

    assert await store.sell("ord_1")
    assert not await store.sell("ord_1")
    assert await levels(store, "prd_a") == {"prd_a": (7, 0)}


async def test_release_returns_units_to_available(store: StockStore):
    await store.seed("prd_a", 10)
    await store.reserve("evt_1", "ord_1", {"prd_a": 3})

    assert await store.release("ord_1")
    assert not await store.release("ord_1")
    assert await levels(store, "prd_a") == {"prd_a": (10, 0)}


async def test_release_expired_frees_only_old_reservations(store: StockStore, clock: FakeClock):
    await store.seed("prd_a", 10)
    await store.reserve("evt_1", "ord_old", {"prd_a": 1})
    clock.now += TTL / 2
    await store.reserve("evt_2", "ord_new", {"prd_a": 1})

    clock.now += TTL / 2 + 1

    assert await store.release_expired() == ["ord_old"]
    assert await levels(store, "prd_a") == {"prd_a": (9, 1)}


async def test_set_available_keeps_reserved_units(store: StockStore):
    await store.seed("prd_a", 10)
    await store.reserve("evt_1", "ord_1", {"prd_a": 2})

    level, became_low = await store.set_available("prd_a", 1)

    assert level == StockLevel("prd_a", 1, 2)
    assert became_low


async def test_seed_does_not_overwrite_existing_stock(store: StockStore):
    assert await store.seed("prd_a", 10)
    assert not await store.seed("prd_a", 50)
    assert await levels(store, "prd_a") == {"prd_a": (10, 0)}
