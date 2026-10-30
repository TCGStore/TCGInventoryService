# Eventos de RabbitMQ

Contrato de los mensajes asíncronos entre orders-service, inventory-service y
notifications-service. Igual que los `.proto`, cualquier cambio se acuerda con el equipo.

## Topología

- **Exchange:** `tcg.events`, tipo `topic`, durable.
- **Routing key = tipo de evento** (`order.created`, `stock.low`, ...).
- Cada servicio declara **su propia cola durable** y la enlaza al exchange:

| Cola | Bindings |
|---|---|
| `inventory-service.events` | `order.created`, `order.confirmed`, `order.cancelled` |
| `orders-service.events` | `stock.reserved`, `stock.failed` |
| `notifications-service.events` | `order.*`, `stock.low` |

`order.*` significa "cualquier evento que empiece con `order.`".

## Formato de los mensajes

JSON con `content_type: application/json`, mensajes persistentes (`delivery_mode: 2`)
y campos en camelCase. Todos usan el mismo sobre:

```json
{
  "eventId": "8f14e45f-ceea-4e7a-9b1c-1f2d3c4b5a69",
  "type": "order.created",
  "occurredAt": "2026-10-26T18:00:00Z",
  "data": { }
}
```

- `eventId`: UUID v4 único por evento.
- `occurredAt`: RFC 3339 en UTC.
- Montos en centavos (`*Cents`), igual que en los `.proto`.

## Eventos

### `order.created` — publica orders-service

```json
{
  "orderId": "ord_123",
  "userId": "usr_42",
  "userEmail": "ash@example.com",
  "items": [
    { "productId": "prd_1", "name": "Charizard ex", "quantity": 1, "unitPriceCents": 4599 }
  ],
  "totalCents": 4599
}
```

### `stock.reserved` — publica inventory-service

```json
{ "orderId": "ord_123" }
```

### `stock.failed` — publica inventory-service

```json
{ "orderId": "ord_123", "unavailableProductIds": ["prd_1"] }
```

### `order.confirmed` — publica orders-service

```json
{ "orderId": "ord_123", "userId": "usr_42", "userEmail": "ash@example.com", "totalCents": 4599 }
```

### `order.cancelled` — publica orders-service

```json
{
  "orderId": "ord_123",
  "userId": "usr_42",
  "userEmail": "ash@example.com",
  "reason": "OUT_OF_STOCK"
}
```

`reason`: `OUT_OF_STOCK` o `PAYMENT_DECLINED` (mismos valores que `CancellationReason`).

### `stock.low` — publica inventory-service

```json
{ "productId": "prd_1", "available": 2, "threshold": 3 }
```

Se publica cuando `available` baja del umbral (`LOW_STOCK_THRESHOLD`), no en cada venta posterior.

## Flujo de compra (saga)

```
orders            inventory                 notifications
  │ order.created ──►│ reserva todo o nada ──►  guarda + email
  │◄── stock.reserved │
  │    (o stock.failed ─► order.cancelled OUT_OF_STOCK)
  │ simula el pago
  │ order.confirmed ─►│ descuenta definitivo ──► guarda + email
  │ (o order.cancelled PAYMENT_DECLINED ─► libera la reserva)
```

1. orders guarda el pedido `PENDING` y publica `order.created`.
2. inventory intenta reservar **todos** los ítems. Si falta uno, no reserva ninguno
   y publica `stock.failed`; si no, publica `stock.reserved`.
3. Con `stock.failed`, orders cancela con `OUT_OF_STOCK`.
4. Con `stock.reserved`, orders simula el pago y publica `order.confirmed` o
   `order.cancelled` con `PAYMENT_DECLINED`.
5. Con `order.confirmed`, inventory convierte la reserva en venta.
   Con `order.cancelled`, libera la reserva si existe (si no existe, no hace nada).

## Reglas para los consumidores

- **Ack manual, después de procesar.** Si el servicio se cae a mitad de camino,
  RabbitMQ vuelve a entregar el mensaje.
- **Idempotencia.** RabbitMQ puede entregar el mismo mensaje más de una vez:
  guarden los `eventId` procesados y descarten los repetidos.
- **Tipos desconocidos se ignoran** (con ack), para poder agregar eventos nuevos sin romper a nadie.
