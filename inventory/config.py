import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    grpc_port: int
    redis_url: str
    rabbitmq_url: str
    low_stock_threshold: int
    reservation_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        rabbitmq_url = os.environ.get("RABBITMQ_URL", "")
        if not rabbitmq_url:
            raise RuntimeError("RABBITMQ_URL is required, e.g. amqp://tcg:tcg@localhost:5672/")
        return cls(
            grpc_port=int(os.environ.get("GRPC_PORT", "50053")),
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            rabbitmq_url=rabbitmq_url,
            low_stock_threshold=int(os.environ.get("LOW_STOCK_THRESHOLD", "3")),
            reservation_ttl_seconds=int(os.environ.get("RESERVATION_TTL_SECONDS", "900")),
        )
