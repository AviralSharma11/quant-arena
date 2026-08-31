# The gateway

The system's entry point, and from week 2 its **single producer** — the component whose
single-threadedness makes ordering and reservation state race-free (Open Issue 007).

Task 1.3 builds the skeleton: sessions, accounts, and the order endpoints. No risk checks
(week 3), no idempotency (week 3), no streams (week 2).

## Running it

```bash
docker start quant-arena-redis quant-arena-postgres     # or docker run, see below
.venv/bin/python -m uvicorn services.gateway.app:app --reload --port 8000
```

Interactive docs at `http://localhost:8000/docs`. Set `QA_SESSION_COOKIE_SECURE=false` when
driving it from a browser over plain http, or the browser will silently discard the cookie.

First time, to create the containers:

```bash
docker run -d --name quant-arena-redis -p 6379:6379 redis:7-alpine
docker run -d --name quant-arena-postgres -p 5432:5432 \
  -e POSTGRES_USER=quant -e POSTGRES_PASSWORD=quant -e POSTGRES_DB=quant_arena \
  postgres:16-alpine
```

Task 1.4 replaces both commands with `docker compose up`.

## Shape

| File | What |
|---|---|
| `app.py` | `create_app()` factory, lifespan, the 422→400 handler |
| `security.py` | Argon2id hash and verify |
| `sessions.py` | Redis-backed session store |
| `models.py` | `User` and `Account` — the accounts read model |
| `deps.py` | Request-scoped dependencies, `current_user_id` |
| `routes_auth.py` | register · login · logout |
| `routes_orders.py` | `POST /orders` · `DELETE /orders/{client_order_id}` |
| `engine_port.py` | **The seam.** Swapped end of week 2, then end of week 5 |

## Three things that look like details and are not

**`create_app()` is a factory, not a module-level singleton.** Success Criterion 2 needs two
independent gateways in sequence, and a singleton makes that untestable.

**The 422→400 handler.** FastAPI answers a validation error with `422`; Open Issue 008 §9h and
Task 1.3's third criterion both say `400`. Without the handler the criterion silently fails.

**Money and timestamp columns are explicitly `BIGINT`.** SQLAlchemy maps a bare `int` to a
32-bit `INTEGER`, which cannot hold an int64 tick amount or a nanosecond timestamp. That would
not fail on the happy path — it would fail later, on a large number.

## Testing

```bash
.venv/bin/python -m pytest tests/gateway -q
```

Runs against the real Redis and PostgreSQL, in `quant_arena_test` and Redis database 15, both
wiped between tests. `test_restart.py` starts uvicorn as an actual subprocess — that is the only
honest way to test "the session survives a restart of the application process", because two
apps inside one interpreter still share module and class state.
