FROM python:3.11-slim as builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements-prod.txt .
RUN pip install --no-cache-dir --user -r requirements-prod.txt

FROM python:3.11-slim

LABEL maintainer="VE-JIT Platform Team"
LABEL version="4.1.0"

RUN groupadd -r vejit && useradd -r -g vejit vejit
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /home/vejit/.local
COPY app/ /app/app/
COPY .env.example /app/.env.example
ENV EDGE_SKILLS_ROOT=/app/skills

RUN mkdir -p /app/logs && chown -R vejit:vejit /app
USER vejit

ENV PATH=/home/vejit/.local/bin:$PATH
ENV PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:8000/health/live || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
