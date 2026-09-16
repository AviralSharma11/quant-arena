"""Ledger stream consumer and PostgreSQL read-model projection.

Tails the outbound stream (`qa.outbound`), processes fills/deposits/orders, and batches
writes to PostgreSQL without ORM overhead on the write path (Task 2.2).

Full replay recovery on startup (Success Criterion 3):
- Wipes in-memory ledger.
- Replays from "0-0" to current end of stream.
- Re-populates PostgreSQL read model.
- Proves byte-identical state reproduction.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services import checkpoint
from services.gateway.streams import read_records
from services.ledger.ledger import Ledger

logger = logging.getLogger(__name__)

PROCESS = "ledger"


class LedgerConsumer:
    """Consumes the outbound stream and syncs the PostgreSQL read model."""

    def __init__(
        self,
        redis: Redis,
        db_engine: AsyncEngine,
        stream_name: str,
        ledger: Ledger | None = None,
        *,
        batch_size: int = 100,
        poll_block_ms: int = 500,
        config_hash: str = "",
        checkpoint_interval_ms: int = 30_000,
    ) -> None:
        self.redis = redis
        self.db = db_engine
        self.stream_name = stream_name
        self.ledger = ledger or Ledger()
        self.batch_size = batch_size
        self.poll_block_ms = poll_block_ms
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._needs_full_flush = False
        self.config_hash = config_hash
        self.checkpoint_interval = checkpoint_interval_ms / 1000
        self._last_checkpoint = time.monotonic()
        self.resumed_from_checkpoint = False

    async def recover(self) -> int:
        """Resume from the ledger's checkpoint, or from genesis if it has none (Open Issue 020).

        Replays the tail, then rewrites the read model in full: PostgreSQL may hold anything,
        including rows from before a crash that the checkpoint does not reflect. Raises
        `CheckpointRefused` if the outbound stream was trimmed past the resume point.
        """
        saved = await checkpoint.resume_positions(
            self.redis, PROCESS, [self.stream_name], config_hash=self.config_hash
        )
        if saved is None:
            return await self.replay_from_genesis()
        self.ledger = Ledger.load_state(saved.state)
        self.ledger.last_seq = saved.positions[self.stream_name]
        self.resumed_from_checkpoint = True
        replayed = await self._replay_after(self.ledger.last_seq)
        await self.flush_to_db(full=True)
        return replayed

    async def write_checkpoint(self) -> None:
        """Written only after a flush, so PostgreSQL and the checkpoint describe the same id."""
        await checkpoint.save(
            self.redis,
            PROCESS,
            positions={self.stream_name: self.ledger.last_seq},
            state=self.ledger.dump_state(),
            config_hash=self.config_hash,
        )
        self._last_checkpoint = time.monotonic()

    async def _replay_after(self, last_id: str) -> int:
        replayed = 0
        while True:
            batch = await read_records(
                self.redis, self.stream_name, last_id=last_id, count=self.batch_size, block_ms=20
            )
            if not batch:
                return replayed
            for item in batch:
                self.ledger.apply(item.record, stream_id=item.stream_id)
                last_id = item.stream_id
                replayed += 1

    async def replay_from_genesis(self) -> int:
        """Rebuild entire state by replaying the retained stream from 0-0."""
        self.ledger.reset()
        replayed_count = await self._replay_after("0-0")
        await self.flush_to_db(full=True)
        return replayed_count

    async def flush_to_db(self, *, full: bool = False) -> None:
        """Write the in-memory ledger into PostgreSQL.

        `full` rewrites every table, and is only for the end of a replay, when the database may
        hold anything. Every other flush writes only the rows `Ledger.apply` touched since the
        previous one. Rewriting everything after every batch copied the whole open-order table
        per hundred records; with ~230,000 orders resting that took longer than the stream took
        to produce the next batch, and the read model froze hours behind — new accounts had no
        row, so `GET /portfolio` reported no cash and no positions.

        Each statement goes out once with a list of parameters, which SQLAlchemy sends as an
        executemany rather than a round trip per row.
        """
        now_ns = time.time_ns()
        ledger = self.ledger
        dirty_accounts, dirty_positions, dirty_orders = ledger.take_dirty()
        if full:
            dirty_accounts = set(ledger.cash_balances)
            dirty_positions = set(ledger.positions)
            dirty_orders = set(ledger.open_orders)

        async with self.db.begin() as conn:
            if dirty_accounts:
                await conn.execute(
                    text(
                        """
                        INSERT INTO accounts (user_id, cash_ticks, created_at_ns)
                        VALUES (:user_id, :cash_ticks, :now_ns)
                        ON CONFLICT (user_id) DO UPDATE
                        SET cash_ticks = EXCLUDED.cash_ticks
                        """
                    ),
                    [
                        {"user_id": user_id, "cash_ticks": ledger.cash_balances[user_id], "now_ns": now_ns}
                        for user_id in sorted(dirty_accounts)
                    ],
                )

            # Positions have no unique key on (user_id, symbol_id), so a changed position is
            # deleted and re-inserted rather than upserted. A position back at zero is only
            # deleted: the table holds non-zero holdings.
            if full:
                await conn.execute(text("DELETE FROM positions"))
            elif dirty_positions:
                await conn.execute(
                    text("DELETE FROM positions WHERE user_id = :user_id AND symbol_id = :symbol_id"),
                    [{"user_id": u, "symbol_id": s} for u, s in sorted(dirty_positions)],
                )
            position_rows = [
                {"user_id": u, "symbol_id": s, "qty": ledger.positions[(u, s)], "now_ns": now_ns}
                for u, s in sorted(dirty_positions)
                if ledger.positions.get((u, s), 0) != 0
            ]
            if position_rows:
                await conn.execute(
                    text(
                        """
                        INSERT INTO positions (user_id, symbol_id, qty, updated_at_ns)
                        VALUES (:user_id, :symbol_id, :qty, :now_ns)
                        """
                    ),
                    position_rows,
                )

            # An order the ledger still holds is upserted — its remaining quantity may have
            # moved. One it no longer holds filled or was cancelled, and is deleted.
            if full:
                await conn.execute(text("DELETE FROM open_orders"))
            else:
                gone = [{"order_id": o} for o in sorted(dirty_orders) if o not in ledger.open_orders]
                if gone:
                    await conn.execute(
                        text("DELETE FROM open_orders WHERE order_id = :order_id"), gone
                    )
            order_rows = [
                {
                    "order_id": order.order_id,
                    "client_order_id": order.client_order_id,
                    "user_id": order.user_id,
                    "symbol_id": order.symbol_id,
                    "side": order.side,
                    "price_ticks": order.price_ticks,
                    "qty": order.qty,
                    "remaining_qty": order.remaining_qty,
                    "tif": order.tif,
                    "created_at_ns": order.created_at_ns,
                }
                for order_id in sorted(dirty_orders)
                if (order := ledger.open_orders.get(order_id)) is not None
            ]
            if order_rows:
                await conn.execute(
                    text(
                        """
                        INSERT INTO open_orders (
                            order_id, client_order_id, user_id, symbol_id, side,
                            price_ticks, qty, remaining_qty, tif, created_at_ns
                        )
                        VALUES (
                            :order_id, :client_order_id, :user_id, :symbol_id, :side,
                            :price_ticks, :qty, :remaining_qty, :tif, :created_at_ns
                        )
                        ON CONFLICT (order_id) DO UPDATE
                        SET remaining_qty = EXCLUDED.remaining_qty
                        """
                    ),
                    order_rows,
                )

            await conn.execute(
                text(
                    """
                    INSERT INTO house_fees (id, fee_ticks, updated_at_ns)
                    VALUES (1, :fee_ticks, :now_ns)
                    ON CONFLICT (id) DO UPDATE
                    SET fee_ticks = EXCLUDED.fee_ticks, updated_at_ns = EXCLUDED.updated_at_ns
                    """
                ),
                {"fee_ticks": ledger.house_fee_ticks, "now_ns": now_ns},
            )

    async def run(self) -> None:
        """Main loop tailing the stream and applying records in real-time."""
        last_id = self.ledger.last_seq
        while not self._stop_event.is_set():
            try:
                batch = await read_records(
                    self.redis,
                    self.stream_name,
                    last_id=last_id,
                    count=self.batch_size,
                    block_ms=self.poll_block_ms,
                )
                if batch:
                    for item in batch:
                        self.ledger.apply(item.record, stream_id=item.stream_id)
                        last_id = item.stream_id
                    await self.flush_to_db(full=self._needs_full_flush)
                    self._needs_full_flush = False
                    if time.monotonic() - self._last_checkpoint >= self.checkpoint_interval:
                        await self.write_checkpoint()
                else:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception:
                # Logged, never swallowed: a silent retry here is how the read model sat four
                # hours stale with nothing in any log to say so. Records already applied are
                # not re-read — `last_id` moved with each — but the rows they dirtied may not
                # have reached the database, so the next flush rewrites everything.
                logger.exception("ledger batch failed at %s; retrying", last_id)
                self._needs_full_flush = True
                await asyncio.sleep(0.1)

    def start(self) -> None:
        if self._task is None:
            self._stop_event.clear()
            self._task = asyncio.create_task(self.run(), name="ledger-consumer")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
