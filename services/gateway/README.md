# The gateway

The system's entry point, and from week 2 its **single producer** — the component whose
single-threadedness makes ordering and reservation state race-free (Open Issue 007).

Task 1.3 builds the skeleton: sessions, accounts, and the order endpoints. No risk checks
(week 3), no idempotency (week 3), no streams (week 2).

## Running it

The whole stack, gateway included:

```bash
docker compose up --build            # from the repository root
```

Or the stores in containers and the gateway on the host, so reloads are instant:

```bash
docker compose up -d redis postgres
.venv/bin/python -m uvicorn services.gateway.app:app --reload --port 8000
```

Interactive docs at `http://localhost:8000/docs`. Set `QA_SESSION_COOKIE_SECURE=false` when
driving it from a browser over plain http, or the browser will silently discard the cookie —
`docker-compose.yml` already does this for the containerised gateway.

Domain parameters come from `config/quant_arena.toml` and nowhere else; the environment carries
only infrastructure. See the root `README.md` for the full run procedure.

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
| `streams.py` | **The durable log.** Batching producer, consumer helper, halt state |
| `engine_port.py` | The week-1 seam. No longer on the order path — see below |

## The stream (Task 2.1)

The gateway is the single **producer**. `POST /orders` `XADD`s to `qa.inbound` and returns the
stream ID as `seq`. It does not match, and it does not know the `order_id` — that is
engine-assigned and arrives on the private stream, which is what "acknowledgement, not result"
means (Open Issue 008 §9h).

**The stream ID is the sequence number.** No counter is kept beside it. A producer cannot know
its own ID before `XADD` returns, so records are written with `SEQ_UNASSIGNED` and stamped on
read by `contracts.with_seq()` — so every replay re-derives the same value.

**Durability is `appendfsync always`, and the batching is what pays for it.** Sequential `XADD`
under `always` measured 1,996 orders/sec; through the batching producer it is 72,034. The
producer drains everything queued while the previous pipeline was in flight and writes it as
one command batch, giving one fsync per batch instead of one per order. Remove the batching and
the durability decision has to be reversed. Numbers and reasoning:
`benchmarks/results/2.1-stream-durability.md`.

**The halt state is a watchdog, not a flag.** When Redis goes away, orders are rejected `503`
with a reason and `/health` says so; when it comes back the halt lifts on its own, with no
gateway restart. A halt set only on a failed order would never lift.

Two paths reach Redis, and the second one is easy to forget: `current_user_id` reads the session
before the order handler runs. An early version answered `500` on `POST /orders` while `/health`
correctly reported the halt, because only the order path was handled. The exception handler in
`app.py` covers both.

`engine_stub/` is retained but no longer called. The end-of-week-2 integration point removes it,
when Dev A's naive model starts consuming the stream (Appendix D.2).

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
