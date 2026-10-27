import asyncio
import logging
import signal

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection
from redis.asyncio import Redis

from inventory.config import Config
from inventory.events import EventBus
from inventory.generated import inventory_pb2, inventory_pb2_grpc
from inventory.grpc_server import InventoryServicer
from inventory.handlers import OrderEventHandler
from inventory.stock import StockStore

log = logging.getLogger("inventory")

EXPIRY_SWEEP_SECONDS = 30


async def release_expired_reservations(store: StockStore) -> None:
    while True:
        try:
            for order_id in await store.release_expired():
                log.warning("released expired reservation of order %s", order_id)
        except Exception:
            log.exception("expired reservation sweep failed, retrying next round")
        await asyncio.sleep(EXPIRY_SWEEP_SECONDS)


async def serve() -> None:
    config = Config.from_env()

    redis = Redis.from_url(config.redis_url, decode_responses=True)
    await redis.ping()
    store = StockStore(redis, config.low_stock_threshold, config.reservation_ttl_seconds)

    bus = EventBus(config.rabbitmq_url)
    await bus.connect()

    server = grpc.aio.server()
    inventory_pb2_grpc.add_InventoryServiceServicer_to_server(InventoryServicer(store, bus.publish), server)
    health_servicer = health.aio.HealthServicer()
    await health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    # Lets grpcurl and Postman discover the API without the .proto file.
    reflection.enable_server_reflection(
        [
            inventory_pb2.DESCRIPTOR.services_by_name["InventoryService"].full_name,
            health_pb2.DESCRIPTOR.services_by_name["Health"].full_name,
            reflection.SERVICE_NAME,
        ],
        server,
    )
    server.add_insecure_port(f"[::]:{config.grpc_port}")
    await server.start()
    log.info("inventory-service listening on port %d", config.grpc_port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    workers = [
        asyncio.create_task(bus.consume(OrderEventHandler(store, bus.publish))),
        asyncio.create_task(release_expired_reservations(store)),
    ]
    stopper = asyncio.create_task(stop.wait())
    # If the consumer dies, exit too, so Docker/Kubernetes restart the whole service.
    done, _ = await asyncio.wait([stopper, *workers], return_when=asyncio.FIRST_COMPLETED)

    log.info("shutting down")
    await health_servicer.enter_graceful_shutdown()
    for task in [stopper, *workers]:
        task.cancel()
    await server.stop(grace=5)
    await bus.close()
    await redis.aclose()

    for task in done:
        if task is not stopper and not task.cancelled() and task.exception():
            raise task.exception()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aio_pika").setLevel(logging.WARNING)
    logging.getLogger("aiormq").setLevel(logging.WARNING)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
