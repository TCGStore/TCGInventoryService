"""RabbitMQ wiring for the contract in proto/events.md."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

import aio_pika
from aio_pika.abc import AbstractExchange, AbstractIncomingMessage, AbstractRobustConnection

log = logging.getLogger(__name__)

EXCHANGE = "tcg.events"
QUEUE = "inventory-service.events"
BINDINGS = ("order.created", "order.confirmed", "order.cancelled")


class InvalidEvent(ValueError):
    """The message can never be processed, so retrying it is pointless."""


class EventHandler(Protocol):
    async def handle(self, event: dict[str, Any]) -> None: ...


def envelope(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventId": str(uuid.uuid4()),
        "type": event_type,
        "occurredAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": data,
    }


class EventBus:
    def __init__(self, url: str, prefetch: int = 10) -> None:
        self._url = url
        self._prefetch = prefetch
        self._connection: AbstractRobustConnection | None = None
        self._exchange: AbstractExchange | None = None

    async def connect(self) -> None:
        # connect_robust reconnects on its own if RabbitMQ restarts later on.
        self._connection = await aio_pika.connect_robust(self._url)
        channel = await self._connection.channel(publisher_confirms=True)
        await channel.set_qos(prefetch_count=self._prefetch)
        self._exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        assert self._exchange, "connect() first"
        event = envelope(event_type, data)
        message = aio_pika.Message(
            json.dumps(event).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=event["eventId"],
        )
        await self._exchange.publish(message, routing_key=event_type)
        log.info("published %s %s", event_type, data)

    async def consume(self, handler: EventHandler) -> None:
        assert self._exchange, "connect() first"
        queue = await self._exchange.channel.declare_queue(QUEUE, durable=True)
        for routing_key in BINDINGS:
            await queue.bind(self._exchange, routing_key)

        log.info("consuming %s from %s", ", ".join(BINDINGS), QUEUE)
        async with queue.iterator() as messages:
            async for message in messages:
                await self._process(message, handler)

    @staticmethod
    async def _process(message: AbstractIncomingMessage, handler: EventHandler) -> None:
        # Ack only after the handler finishes: if the service dies midway, RabbitMQ redelivers.
        try:
            event = json.loads(message.body)
            if not isinstance(event, dict):
                raise InvalidEvent("event is not a JSON object")
            await handler.handle(event)
        except (json.JSONDecodeError, InvalidEvent) as exc:
            log.error("dropping invalid message %s: %s", message.message_id, exc)
            await message.reject(requeue=False)
        except Exception:
            log.exception("failed to process message %s, requeueing", message.message_id)
            await asyncio.sleep(1)  # avoids a hot loop while Redis or RabbitMQ recover
            await message.nack(requeue=True)
        else:
            await message.ack()
