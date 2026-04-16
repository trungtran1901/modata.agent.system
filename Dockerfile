# ===== Stage 1: Builder =====
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# tạo virtualenv để copy sang stage sau
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

RUN pip install --no-cache-dir -r requirements.txt

# ===== Stage 2: Runtime =====
FROM python:3.11-slim

WORKDIR /app

# chỉ cài runtime cần thiết (không cần build-essential)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# copy môi trường đã build
COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

# copy source code
COPY . .

# tạo user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]