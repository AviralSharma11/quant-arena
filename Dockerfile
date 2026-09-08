# The Python service image, shared by the gateway and matcher. The C++ matcher is compiled in a
# separate stage so the runtime image does not need a compiler.
FROM debian:bookworm-slim AS engine-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY engine/cpp/ ./engine/cpp/
COPY contracts/v1/generated/ ./contracts/v1/generated/
RUN g++ -std=c++20 -O2 -Wall -Wextra -Werror -I/src \
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
# The pinned price history the bots replay (Task 5.1). Without it the bots fall back to the
# seeded random walk and say so in the log — a working market, but a synthetic one, which is
# not what a demonstration is meant to show. 319 KB, and the layer changes only when the
# dataset is re-fetched.
COPY data/ ./data/

# The archiver's mount point, owned by the user that will write to it (Task 6.2). Docker seeds a
# fresh named volume from the image directory it is mounted over, ownership included, so creating
# it here is what makes the volume writable by `quant`.
#
# Without this the archiver runs as a non-root user against a root-owned volume, fails on the
# first `mkdir`, and — because its healthcheck pings Redis — reports **healthy** while writing
# nothing. That is the third time this shape of fault has appeared in this project (fan-out not
# running, the matcher dead but healthy), and it is only ever found by looking at the output.
RUN mkdir -p /archive && chown quant:quant /archive

USER quant
EXPOSE 8000

CMD ["uvicorn", "services.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
