# The gateway image. One stage: the dependency set is small and there is nothing to compile,
# so a builder stage would add moving parts without saving meaningful size.
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

# Dependencies first, so editing source does not invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY config/ ./config/
COPY contracts/ ./contracts/
COPY services/ ./services/

USER quant
EXPOSE 8000

CMD ["uvicorn", "services.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
