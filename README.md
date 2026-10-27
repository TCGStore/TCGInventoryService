# inventory-service

Python 3.12 + Redis. Stock por producto, reservas temporales durante el pago y
alertas de stock bajo. Implementa `proto/inventory.proto` en el puerto gRPC 50053 y
participa en la saga de compra por RabbitMQ (`proto/events.md`).

- **Consume** `order.created`, `order.confirmed` y `order.cancelled` (cola `inventory-service.events`).
- **Publica** `stock.reserved`, `stock.failed` y `stock.low` en el exchange `tcg.events`.

**¿Por qué no FastAPI?** Al principio estaba planeado, pero el servicio no expone nada
por HTTP: solo habla gRPC (`grpc.aio`) y RabbitMQ (`aio-pika`), ambos con `asyncio`.
Un framework web habría sido un servidor que nadie llama.

## Estructura

```
inventory/
  main.py          arranque: servidor gRPC + consumidor RabbitMQ + limpieza de reservas vencidas
  config.py        variables de entorno
  stock.py         todo el acceso a Redis (StockStore)
  scripts/*.lua    operaciones atómicas en Redis (reservar, vender/liberar, fijar stock)
  handlers.py      qué hacer con cada evento de la saga
  events.py        conexión a RabbitMQ: exchange, cola, publicar y consumir con ack manual
  grpc_server.py   los 3 RPCs del .proto
  seed.py          stock inicial de los 17 productos de catalog-service
  generated/       código generado desde el .proto (no se sube, ver abajo)
tests/             pytest con fakeredis (un Redis falso en memoria, no necesita Docker)
```

El código gRPC se genera con `make proto` (local) o dentro del Dockerfile; la carpeta
`generated/` está en el `.gitignore`. Por eso el Dockerfile se construye desde la raíz del repo.

## Cómo se guarda el stock en Redis

| Clave | Tipo | Contenido |
|---|---|---|
| `stock:{productId}` | hash | `available` (se puede comprar) y `reserved` (apartado por pedidos en pago) |
| `reservation:{orderId}` | hash | `productId → cantidad` reservada por ese pedido |
| `reservations:expiry` | sorted set | `orderId` ordenados por hora de vencimiento |
| `processed-event:{eventId}` | lista | resultado de un `order.created` ya procesado (dura 7 días) |

```
order.created    available -= n, reserved += n   (todo o nada)
order.confirmed  reserved  -= n                  (vendido)
order.cancelled  reserved  -= n, available += n  (vuelve a la venta)
```

### ¿Por qué scripts Lua?

Reservar un pedido son varios pasos (revisar cada producto, descontar, guardar la
reserva). Si dos pedidos llegan a la vez por la última carta, ambos podrían ver
"queda 1" y venderla dos veces. Redis ejecuta un script Lua **completo sin
intercalar otros comandos**, así que el chequeo y el descuento son una sola operación.

### Reglas importantes

- **Todo o nada:** si falta stock de un solo producto, no se reserva ninguno y se
  publica `stock.failed` con los ids que faltan.
- **Eventos repetidos:** RabbitMQ puede entregar dos veces el mismo mensaje. Un
  `order.created` repetido (mismo `eventId`) no reserva de nuevo: vuelve a publicar
  el resultado que ya tuvo. `order.confirmed` / `order.cancelled` sin reserva no hacen nada.
- **Reservas que vencen:** si un pedido no se confirma ni se cancela en
  `RESERVATION_TTL_SECONDS` (15 min), una tarea que corre cada 30 s libera el stock.
- **`stock.low`** se publica solo cuando `available` **cruza** el umbral hacia abajo
  (de 3 a 2, por ejemplo), no en cada venta posterior. También al bajar el stock con `SetStock`.
- **Mensajes inválidos** (JSON roto, faltan campos) se descartan con un log de error;
  si el fallo es temporal (Redis caído), el mensaje vuelve a la cola.

## Correr en local

Requisitos: Python 3.12 y Docker.

```bash
# desde la raíz del repo
docker compose up -d redis rabbitmq

# desde services/inventory-service
python3.12 -m venv .venv && source .venv/bin/activate
make install
make seed    # stock inicial (se puede repetir: no pisa el stock existente)
make run     # genera el código gRPC y levanta el servidor en :50053
make test
```

O todo con Docker, desde la raíz:

```bash
docker compose up -d --build inventory-service
docker compose run --rm inventory-service python -m inventory.seed
```

## Probar con grpcurl

```bash
grpcurl -plaintext localhost:50053 list
grpcurl -plaintext -d '{"product_id":"prd_mew_151_en_nm"}' \
  localhost:50053 tcgstore.inventory.InventoryService/GetStock
grpcurl -plaintext -d '{"product_ids":["prd_mew_006_en_nm","prd_obf_box_en"]}' \
  localhost:50053 tcgstore.inventory.InventoryService/GetStocks
grpcurl -plaintext -d '{"product_id":"prd_obf_box_en","available":10}' \
  localhost:50053 tcgstore.inventory.InventoryService/SetStock
```

grpcurl no muestra los campos en 0 o `false` (así es el JSON de proto3): si no aparece
`reserved`, es 0.

Para ver los eventos, abre el panel de RabbitMQ en <http://localhost:15672>
(usuario y clave de `.env`) → Queues → `inventory-service.events`.

## Variables de entorno

| Variable | Default | |
|---|---|---|
| `RABBITMQ_URL` | — (obligatoria) | `amqp://tcg:tcg@localhost:5672/` en local |
| `REDIS_URL` | `redis://localhost:6379/0` | |
| `GRPC_PORT` | `50053` | |
| `LOW_STOCK_THRESHOLD` | `3` | `low_stock` = `available` < umbral |
| `RESERVATION_TTL_SECONDS` | `900` | cuánto dura una reserva sin confirmar |

## Notas para quien consume este servicio

- Un producto desconocido devuelve `available = 0` (y `low_stock = true`), no `NOT_FOUND`.
- `SetStock` es solo para admins: el BFF debe revisar `role = admin` en el JWT antes de llamarlo.
- Los ids de producto son los de catalog-service; este servicio no valida que existan en el catálogo.
- Los datos del seed son de demo: el Charizard ex NM parte con stock bajo, el Mew ex
  queda bajo con una venta y la booster box japonesa está agotada.
