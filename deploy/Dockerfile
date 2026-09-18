FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

VOLUME ["/var/lib/model-courier"]
EXPOSE 8080
CMD ["uvicorn", "model_courier.server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
