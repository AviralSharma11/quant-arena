"""Success Criteria 1 and 4 — registration grants virtual capital, and passwords are Argon2id."""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient

from services.gateway.security import ARGON2ID_PREFIX

TEST_DSN = "postgresql://quant:quant@localhost:5432/quant_arena_test"


def _rows(sql: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(TEST_DSN) as conn:
        return conn.execute(sql, params).fetchall()


# --- Criterion 1: a user registers, logs in, and receives virtual capital -------------------


def test_register_grants_virtual_capital(client: TestClient, settings):
    response = client.post(
        "/auth/register", json={"username": "alice", "password": "a-long-enough-pw"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["username"] == "alice"
    assert body["cash_ticks"] == settings.initial_cash_ticks


def test_the_grant_actually_lands_in_the_accounts_table(client: TestClient, settings):
    client.post("/auth/register", json={"username": "bob", "password": "a-long-enough-pw"})
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
