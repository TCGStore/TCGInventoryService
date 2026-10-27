import logging

import grpc

from inventory.generated import inventory_pb2, inventory_pb2_grpc
from inventory.handlers import Publish, publish_low_stock
from inventory.stock import StockLevel, StockStore

log = logging.getLogger(__name__)

MAX_BATCH_IDS = 100


class InventoryServicer(inventory_pb2_grpc.InventoryServiceServicer):
    def __init__(self, store: StockStore, publish: Publish) -> None:
        self._store = store
        self._publish = publish

    async def GetStock(self, request, context):
        if not request.product_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "product_id is required")
        [level] = await self._store.get([request.product_id])
        return self._to_proto(level)

    async def GetStocks(self, request, context):
        product_ids = list(request.product_ids)
        if len(product_ids) > MAX_BATCH_IDS:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"at most {MAX_BATCH_IDS} ids per request")
        if any(not product_id for product_id in product_ids):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "product_ids can't contain empty ids")
        levels = await self._store.get(product_ids)
        return inventory_pb2.GetStocksResponse(stocks=[self._to_proto(level) for level in levels])

    # Admin only: the BFF checks the JWT role before calling it.
    async def SetStock(self, request, context):
        if not request.product_id:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "product_id is required")
        if request.available < 0:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "available can't be negative")

        level, became_low = await self._store.set_available(request.product_id, request.available)
        log.info("stock of %s set to %d", level.product_id, level.available)

        if became_low:
            # The stock is already saved; a lost alert shouldn't turn this call into an error.
            try:
                await publish_low_stock(
                    self._publish, level.product_id, level.available, self._store.low_stock_threshold
                )
            except Exception:
                log.exception("could not publish stock.low for %s", level.product_id)
        return self._to_proto(level)

    def _to_proto(self, level: StockLevel) -> inventory_pb2.StockLevel:
        return inventory_pb2.StockLevel(
            product_id=level.product_id,
            available=level.available,
            reserved=level.reserved,
            low_stock=self._store.is_low(level),
        )
