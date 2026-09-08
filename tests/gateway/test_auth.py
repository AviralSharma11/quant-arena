"""Success Criteria 1 and 4 — registration grants virtual capital, and passwords are Argon2id."""

from __future__ import annotations

import asyncio
import re

import psycopg
import pytest
import redis as redis_sync
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from contracts.v1.generated.contracts import ConfigureReplay, CreateAccount, unpack_any
from services.gateway.security import ARGON2ID_PREFIX
from services.gateway.streams import RECORD_FIELD
from services.ledger.consumer import LedgerConsumer

TEST_DSN = "postgresql://quant:quant@localhost:5432/quant_arena_test"


def _rows(sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(TEST_DSN) as conn:
        return conn.execute(sql, params).fetchall()


def _one_inbound_record(settings):
    """Return the registration record, excluding the gateway startup configuration stamp."""
    r = redis_sync.Redis.from_url(settings.redis_url)
    try:
        entries = r.xrange(settings.stream_inbound, "-", "+")
    finally:
        r.close()
    records = [unpack_any(fields[RECORD_FIELD]) for _, fields in entries]
    registration = [record for record in records if isinstance(record, CreateAccount)]
    assert len(registration) == 1, records
    return registration[0]


def _project_to_read_model(settings):
    """Run the real `LedgerConsumer` once, the way `python -m services.ledger` does."""

    async def _run():
        redis = redis_sync.asyncio.Redis.from_url(
            settings.redis_url, decode_responses=False
        )
        db = create_async_engine(settings.database_url)
        try:
            await LedgerConsumer(
                redis, db, settings.stream_outbound
            ).replay_from_genesis()
        finally:
            await db.dispose()
            await redis.aclose()

    asyncio.run(_run())


def test_gateway_stamps_replay_configuration_at_startup(client: TestClient, settings):
    r = redis_sync.Redis.from_url(settings.redis_url)
    try:
        entries = r.xrange(settings.stream_inbound, "-", "+")
    finally:
        r.close()

    assert len(entries) == 1
    record = unpack_any(entries[0][1][RECORD_FIELD])
    digest = bytes.fromhex(settings.config_hash)
    assert isinstance(record, ConfigureReplay)
    assert record.client_order_id == 0
    assert record.real_seconds_per_simulated_minute == (
        settings.replay_real_seconds_per_simulated_minute
    )
    assert record.config_hash_hi == int.from_bytes(digest[:8], "big")
    assert record.config_hash_lo == int.from_bytes(digest[8:16], "big")


# --- Criterion 1: a user registers, logs in, and receives virtual capital -------------------


def test_register_records_the_grant_as_an_event(client: TestClient, settings):
    """The grant is a `CreateAccount` on the inbound stream, not a row the gateway wrote.

    This changed in week 4. Writing `accounts` directly made PostgreSQL the source of truth for
    a balance, which Open Issue 004 forbids, and left `LedgerConsumer` unable to run: the ledger
    rebuilds `accounts` by replaying the stream, and a stream that never carried the grant would
    have replayed every balance to zero.

    So the response is acknowledgement-shaped, exactly like `POST /orders` — it carries the
    stream id the record landed at and no `cash_ticks`, because at that moment the gateway has
    not observed a balance and must not assert one.
    """
    response = client.post(
        "/auth/register", json={"username": "alice", "password": "a-long-enough-pw"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["username"] == "alice"
    assert "cash_ticks" not in body
    # A Redis stream id, "<ms>-<ord>" — which *is* the sequence number (Open Issue 003).
    assert re.fullmatch(r"\d+-\d+", body["seq"]), body["seq"]

    record = _one_inbound_record(settings)
    assert isinstance(record, CreateAccount)
    assert record.user_id == body["user_id"]
    # `CreateAccount` has no cash field: the engine is money-blind (Open Issue 001) and reads
    # the grant from the shared configuration when it forwards `AccountCreated`.
    assert not hasattr(record, "amount_ticks")


def test_the_grant_lands_in_the_accounts_table_once_the_stream_is_consumed(
    client: TestClient, settings, pump
):
    """The read model is derived, so it moves only when something derives it.

    `pump` is the matcher and the projection the deployed stack runs as their own processes.
    Registration alone leaves `accounts` empty, and that is correct rather than broken.
    """
    client.post("/auth/register", json={"username": "bob", "password": "a-long-enough-pw"})
    assert _rows("select 1 from accounts") == [], "no consumer has run yet"

    pump()
    _project_to_read_model(settings)

    rows = _rows(
        "select a.cash_ticks from accounts a join users u on u.id = a.user_id"
        " where u.username = %s",
        ("bob",),
    )
    assert rows == [(settings.initial_cash_ticks,)]


def test_register_then_login_is_the_full_path(client: TestClient, registered: dict):
    response = client.post(
        "/auth/login",
        json={"username": registered["username"], "password": registered["password"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["username"] == registered["username"]


def test_cash_ticks_survives_a_value_larger_than_a_32_bit_integer(client: TestClient, settings):
    """The columns are BIGINT. A bare SQLModel int would be INTEGER and fail here."""
    big = 5_000_000_000  # > 2**32
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        conn.execute(
            "insert into users (username, password_hash, created_at_ns)"
            " values ('whale', 'x', 1700000000000000000) returning id"
        )
        user_id = conn.execute("select id from users where username='whale'").fetchone()[0]
        conn.execute(
            "insert into accounts (user_id, cash_ticks, created_at_ns)"
            " values (%s, %s, 1700000000000000000)",
            (user_id, big),
        )
    assert _rows("select cash_ticks from accounts where user_id = %s", (user_id,)) == [(big,)]


def test_duplicate_username_is_rejected(client: TestClient, registered: dict):
    response = client.post(
        "/auth/register",
        json={"username": registered["username"], "password": "another-password"},
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"username": "ab", "password": "a-long-enough-pw"},      # username too short
        {"username": "alice", "password": "short"},              # password too short
        {"username": "has spaces", "password": "a-long-enough-pw"},
        {"password": "a-long-enough-pw"},                        # username missing
        {"username": "alice"},                                   # password missing
    ],
)
def test_malformed_registration_is_400_not_422(client: TestClient, body: dict):
    """FastAPI's default is 422; the contract says 400."""
    assert client.post("/auth/register", json=body).status_code == 400


# --- Criterion 4: Argon2id, no plaintext, no general-purpose hash ---------------------------


def test_password_is_stored_as_an_argon2id_hash(client: TestClient, registered: dict):
    stored = _rows("select password_hash from users where username = %s",
                   (registered["username"],))[0][0]
    assert stored.startswith(ARGON2ID_PREFIX), stored[:32]


def test_the_plaintext_password_appears_nowhere_in_the_database(
    client: TestClient, registered: dict
):
    password = registered["password"]
    with psycopg.connect(TEST_DSN) as conn:
        tables = conn.execute(
            "select table_name, column_name from information_schema.columns"
            " where table_schema = 'public' and data_type in"
            " ('character varying','text','character')"
        ).fetchall()
        for table, column in tables:
            values = conn.execute(f'select "{column}" from "{table}"').fetchall()
            for (value,) in values:
                assert value != password, f"plaintext password found in {table}.{column}"
                assert password not in (value or ""), f"password embedded in {table}.{column}"


def test_the_hash_is_not_a_fast_general_purpose_digest(client: TestClient, registered: dict):
    """An MD5/SHA-1/SHA-256 hex digest is 32/40/64 characters of hex and nothing else.
    Catching that shape is how a "just use hashlib" regression would be found."""
    import re

    stored = _rows("select password_hash from users where username = %s",
                   (registered["username"],))[0][0]
    assert not re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64}", stored)


def test_two_users_with_the_same_password_get_different_hashes(client: TestClient):
    """Per-password salting. Identical hashes would mean no salt."""
    for name in ("carol", "dave"):
        client.post("/auth/register", json={"username": name, "password": "identical-pw-here"})
    hashes = {row[0] for row in _rows("select password_hash from users")}
    assert len(hashes) == 2


def test_wrong_password_is_rejected(client: TestClient, registered: dict):
    response = client.post(
        "/auth/login", json={"username": registered["username"], "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_unknown_user_answers_exactly_like_a_wrong_password(client: TestClient, registered: dict):
    """Different answers would turn login into a username-enumeration oracle."""
    wrong_pw = client.post(
        "/auth/login", json={"username": registered["username"], "password": "wrong-password"}
    )
    no_user = client.post(
        "/auth/login", json={"username": "nobody_here", "password": "wrong-password"}
    )
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json() == no_user.json()
