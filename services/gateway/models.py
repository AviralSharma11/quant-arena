"""The accounts read model.

Open Issue 004: PostgreSQL is a **derived read model**, rebuildable from the stream, and never
the source of truth. In week 1 there is no stream yet, so these tables are simply where users
and accounts live; from week 2 they become a projection.

Every money and timestamp column is explicitly BIGINT. SQLAlchemy maps a bare `int` to a 32-bit
INTEGER, which silently cannot hold either an int64 tick amount or a nanosecond timestamp
(~1.7e18). Getting this wrong would not fail loudly — it would fail on a large number, later.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, ForeignKey, String
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(
        sa_column=Column(String(64), unique=True, index=True, nullable=False)
    )
    #: Argon2id, never the password. See services/gateway/security.py.
    password_hash: str = Field(sa_column=Column(String(255), nullable=False))
    created_at_ns: int = Field(sa_column=Column(BigInteger, nullable=False))


class Account(SQLModel, table=True):
    __tablename__ = "accounts"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            BigInteger, ForeignKey("users.id"), unique=True, index=True, nullable=False
        )
    )
    #: int64 ticks. Never a float below the presentation layer (Open Issue 016 section 6).
    cash_ticks: int = Field(sa_column=Column(BigInteger, nullable=False))
    created_at_ns: int = Field(sa_column=Column(BigInteger, nullable=False))


class Position(SQLModel, table=True):
    __tablename__ = "positions"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("users.id"), index=True, nullable=False)
    )
    symbol_id: int = Field(sa_column=Column(BigInteger, index=True, nullable=False))
    qty: int = Field(sa_column=Column(BigInteger, nullable=False, default=0))
    updated_at_ns: int = Field(sa_column=Column(BigInteger, nullable=False))


class OpenOrder(SQLModel, table=True):
    __tablename__ = "open_orders"

    order_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    client_order_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    user_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("users.id"), index=True, nullable=False)
    )
    symbol_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    side: int = Field(sa_column=Column(BigInteger, nullable=False))
    price_ticks: int = Field(sa_column=Column(BigInteger, nullable=False))
    qty: int = Field(sa_column=Column(BigInteger, nullable=False))
    remaining_qty: int = Field(sa_column=Column(BigInteger, nullable=False))
    tif: int = Field(sa_column=Column(BigInteger, nullable=False))
    created_at_ns: int = Field(sa_column=Column(BigInteger, nullable=False))


class HouseFeeAccount(SQLModel, table=True):
    __tablename__ = "house_fees"

    id: int | None = Field(default=None, primary_key=True)
    fee_ticks: int = Field(sa_column=Column(BigInteger, nullable=False, default=0))
    updated_at_ns: int = Field(sa_column=Column(BigInteger, nullable=False))

