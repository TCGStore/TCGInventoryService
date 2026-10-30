FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_ROOT_USER_ACTION=ignore

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY proto/inventory.proto /proto/
COPY inventory/ inventory/
RUN python -m grpc_tools.protoc -Iinventory/generated=/proto \
    --python_out=. --grpc_python_out=. inventory/generated/inventory.proto

USER nobody
EXPOSE 50053
CMD ["python", "-m", "inventory.main"]
