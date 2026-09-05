# The Python service image, shared by the gateway and matcher. The C++ matcher is compiled in a
# separate stage so the runtime image does not need a compiler.
FROM debian:bookworm-slim AS engine-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY engine/cpp/ ./engine/cpp/
RUN g++ -std=c++20 -O2 -Wall -Wextra -Werror \
    -o /quant-arena-engine \
    engine/cpp/order_book.cpp engine/cpp/stream_engine.cpp

FROM python:3.13-slim

# PYTHONUNBUFFERED matters here specifically: without it the startup line carrying the
# configuration hash sits in a buffer instead of appearing in `docker compose logs`, and Task
# 1.4's third success criterion is about that line being visible.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN adduser --system --group --no-create-home quant
COPY --from=engine-builder /quant-arena-engine /usr/local/bin/quant-arena-engine

# Dependencies first, so editing source does not invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY config/ ./config/
COPY contracts/ ./contracts/
# Dev A's naive model. The matcher process wraps it (services/matcher), which is the
# end-of-week-2 integration point; the gateway itself never imports it.
COPY engine/ ./engine/
COPY services/ ./services/

USER quant
EXPOSE 8000

CMD ["uvicorn", "services.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
