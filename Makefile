# Local development defaults; they match .env.example and docker-compose.yml.
REDIS_URL ?= redis://localhost:6379/0
RABBITMQ_URL ?= amqp://tcg:tcg@localhost:5672/
export REDIS_URL RABBITMQ_URL

.PHONY: install proto run seed test

install:
	pip install -r requirements-dev.txt

# The "inventory/generated=" prefix makes the generated imports resolve as inventory.generated.*
proto:
	python -m grpc_tools.protoc -Iinventory/generated=proto \
		--python_out=. --grpc_python_out=. --pyi_out=. \
		inventory/generated/inventory.proto

run: proto
	python -m inventory.main

seed:
	python -m inventory.seed

test:
	pytest
