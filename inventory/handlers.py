import logging
from collections.abc import Awaitable, Callable
from typing import Any

from inventory.events import InvalidEvent
from inventory.stock import StockStore

log = logging.getLogger(__name__)

Publish = Callable[[str, dict[str, Any]], Awaitable[None]]


class OrderEventHandler:
    """Inventory's side of the order saga (proto/events.md)."""

    def __init__(self, store: StockStore, publish: Publish) -> None:
        self._store = store
        self._publish = publish

    async def handle(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        data = event.get("data")
        if not isinstance(data, dict):
            raise InvalidEvent(f"{event_type}: data must be an object")

        if event_type == "order.created":
            await self._on_order_created(_required_str(event, "eventId"), data)
        elif event_type == "order.confirmed":
            order_id = _required_str(data, "orderId")
            if not await self._store.sell(order_id):
                log.warning("order %s confirmed without a reservation (expired or already sold)", order_id)
        elif event_type == "order.cancelled":
            # No reservation is normal here: the order may have failed for lack of stock.
            await self._store.release(_required_str(data, "orderId"))
        else:
            log.info("ignoring event type %s", event_type)

    async def _on_order_created(self, event_id: str, data: dict[str, Any]) -> None:
        order_id = _required_str(data, "orderId")
        result = await self._store.reserve(event_id, order_id, _quantities(data.get("items")))

        # A redelivered event republishes the stored outcome, in case the first publish never went out.
        if result.reserved:
            await self._publish("stock.reserved", {"orderId": order_id})
        else:
            await self._publish(
                "stock.failed", {"orderId": order_id, "unavailableProductIds": result.unavailable_product_ids}
            )

        if not result.duplicate:
            for product_id, available in result.became_low.items():
                await publish_low_stock(self._publish, product_id, available, self._store.low_stock_threshold)


async def publish_low_stock(publish: Publish, product_id: str, available: int, threshold: int) -> None:
    await publish("stock.low", {"productId": product_id, "available": available, "threshold": threshold})


def _quantities(items: Any) -> dict[str, int]:
    """Sums repeated products so each stock key appears once in the reservation."""
    if not isinstance(items, list) or not items:
        raise InvalidEvent("order.created: items must be a non-empty list")

    quantities: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            raise InvalidEvent("order.created: each item must be an object")
        product_id = _required_str(item, "productId")
        quantity = item.get("quantity")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise InvalidEvent(f"order.created: invalid quantity for {product_id}")
        quantities[product_id] = quantities.get(product_id, 0) + quantity
    return quantities


def _required_str(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidEvent(f"missing {key}")
    return value
