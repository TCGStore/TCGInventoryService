from typing import Any

import pytest

from inventory.events import InvalidEvent
from inventory.handlers import OrderEventHandler
from inventory.stock import StockStore


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, event_type: str, data: dict[str, Any]) -> None:
        self.events.append((event_type, data))


def order_created(event_id: str, order_id: str, *items: tuple[str, int]) -> dict[str, Any]:
    return {
        "eventId": event_id,
        "type": "order.created",
        "occurredAt": "2026-10-27T12:00:00Z",
        "data": {
            "orderId": order_id,
            "userId": "usr_42",
            "userEmail": "ash@example.com",
            "items": [
                {"productId": product_id, "name": "Card", "quantity": quantity, "unitPriceCents": 100}
                for product_id, quantity in items
            ],
            "totalCents": 100,
        },
    }


def order_event(event_type: str, order_id: str) -> dict[str, Any]:
    return {"eventId": "evt_x", "type": event_type, "data": {"orderId": order_id}}


@pytest.fixture
def published() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def handler(store: StockStore, published: RecordingPublisher) -> OrderEventHandler:
    return OrderEventHandler(store, published)


async def test_order_created_reserves_and_publishes_stock_reserved(store, handler, published):
    await store.seed("prd_a", 10)

    await handler.handle(order_created("evt_1", "ord_1", ("prd_a", 1), ("prd_a", 2)))

    assert published.events == [("stock.reserved", {"orderId": "ord_1"})]
    [level] = await store.get(["prd_a"])
    assert (level.available, level.reserved) == (7, 3)


async def test_order_created_without_stock_publishes_stock_failed(store, handler, published):
    await store.seed("prd_a", 1)

    await handler.handle(order_created("evt_1", "ord_1", ("prd_a", 2)))

    assert published.events == [("stock.failed", {"orderId": "ord_1", "unavailableProductIds": ["prd_a"]})]


async def test_order_created_publishes_stock_low_once(store, handler, published):
    await store.seed("prd_a", 3)
    event = order_created("evt_1", "ord_1", ("prd_a", 1))

    await handler.handle(event)
    await handler.handle(event)  # RabbitMQ redelivery

    assert published.events == [
        ("stock.reserved", {"orderId": "ord_1"}),
        ("stock.low", {"productId": "prd_a", "available": 2, "threshold": 3}),
        ("stock.reserved", {"orderId": "ord_1"}),
    ]


async def test_order_confirmed_sells_and_cancelled_releases(store, handler):
    await store.seed("prd_a", 10)
    await handler.handle(order_created("evt_1", "ord_sold", ("prd_a", 2)))
    await handler.handle(order_created("evt_2", "ord_cancelled", ("prd_a", 3)))

    await handler.handle(order_event("order.confirmed", "ord_sold"))
    await handler.handle(order_event("order.cancelled", "ord_cancelled"))

    [level] = await store.get(["prd_a"])
    assert (level.available, level.reserved) == (8, 0)


async def test_unknown_event_type_is_ignored(handler, published):
    await handler.handle({"eventId": "evt_1", "type": "price.changed", "data": {}})

    assert published.events == []


@pytest.mark.parametrize(
    "event",
    [
        {"eventId": "evt_1", "type": "order.created"},
        order_created("evt_1", "ord_1"),
        order_created("evt_1", "ord_1", ("prd_a", 0)),
        order_created("", "ord_1", ("prd_a", 1)),
        order_event("order.confirmed", ""),
    ],
)
async def test_malformed_events_are_rejected(handler, event):
    with pytest.raises(InvalidEvent):
        await handler.handle(event)
