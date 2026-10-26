# inventory-service

Python (FastAPI) + Redis.

Stock por producto, reservas temporales durante el pago y alertas de stock bajo.
Expone un servidor gRPC, consume `order.created` y publica
`stock.reserved` / `stock.failed` por RabbitMQ.
