"""Run the Task 5.3 kill-the-engine recovery demonstration against an isolated Compose stack."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg
import redis

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import Settings  # noqa: E402
from contracts.v1.generated.contracts import Fill, OrderAccepted, Side, Tif, unpack_any  # noqa: E402
from services.gateway.streams import RECORD_FIELD  # noqa: E402

RECOVERY_LINE = re.compile(
    r"cpp-matcher: replayed (\d+) inbound records in ([0-9.]+)s"
)
GENESIS_ERROR = "no longer begins at genesis"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("0.0.0.0", 0))
        return int(sock.getsockname()[1])


class ScratchStack:
    def __init__(self) -> None:
        self.project = f"qa-t6-{uuid.uuid4().hex[:10]}"
        self.environment = os.environ.copy()
        ports: set[int] = set()
        for key in (
            "QA_REDIS_PUBLISHED_PORT",
            "QA_POSTGRES_PUBLISHED_PORT",
            "QA_GATEWAY_PUBLISHED_PORT",
            "QA_FANOUT_PUBLISHED_PORT",
        ):
            port = free_port()
            while port in ports:
                port = free_port()
            ports.add(port)
            self.environment[key] = str(port)
        self.environment["QA_POSTGRES_PASSWORD"] = uuid.uuid4().hex

    def command(self, *args: str, capture: bool = True) -> subprocess.CompletedProcess:
        command = [
            "docker",
            "compose",
            "--project-name",
            self.project,
            *args,
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=self.environment,
            check=False,
            capture_output=capture,
            text=True,
        )
        if result.returncode:
            detail = (
                result.stderr.strip() or result.stdout.strip()
                if capture
                else f"exit code {result.returncode}"
            )
            raise RuntimeError(f"{' '.join(command)} failed: {detail}")
        return result

    def logs(self) -> str:
        return self.command("logs", "--no-color", "matcher").stdout

    def cleanup(self) -> None:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                self.project,
                "down",
                "--volumes",
                "--remove-orphans",
            ],
            cwd=ROOT,
            env=self.environment,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            print(
                f"Cleanup failed for isolated project {self.project}: "
                f"{result.stderr.strip() or result.stdout.strip()}",
                file=sys.stderr,
            )


class RecoveryRun:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.stack = ScratchStack()
        self.settings = Settings.load(config_path=ROOT / "config" / "quant_arena.toml")
        self.gateway_port = int(self.stack.environment["QA_GATEWAY_PUBLISHED_PORT"])
        self.redis_port = int(self.stack.environment["QA_REDIS_PUBLISHED_PORT"])
        self.postgres_port = int(self.stack.environment["QA_POSTGRES_PUBLISHED_PORT"])
        self.gateway_url = f"http://127.0.0.1:{self.gateway_port}"
        self.redis_client = redis.Redis(
            host="127.0.0.1", port=self.redis_port, db=0, decode_responses=False
        )
        self.dmm_client: httpx.Client | None = None
        self.buyer_client: httpx.Client | None = None
        self.submitted: list[int] = []
        self.submit_errors: list[str] = []
        self.submission_lock = threading.Lock()
        self.flow_done = threading.Event()
        self.stop_requested = threading.Event()
        self.flow_thread: threading.Thread | None = None
        self.initial_balance_and_fees = 2 * self.settings.initial_cash_ticks
        self.symbol = self.settings.symbols[0]
        self.price_ticks = max(
            self.symbol.tick_size_ticks * 100,
            self.settings.bots.fair_value_start_ticks * 10,
        )
        self.quantity = self.symbol.lot_size
        self.order_id_base = time.time_ns()
        self.maker_client_order_id = self.order_id_base
        self.buyer_client_order_ids = [
            self.order_id_base + index + 1 for index in range(args.orders)
        ]
        self.maker_order_id: int | None = None
        self.fill_records: list[Fill] = []
        self.cash_before_kill: int | None = None
        self.recovery_seconds: float | None = None
        self.matcher_replay_seconds: float | None = None
        self.replayed_records: int | None = None

    def run(self) -> int:
        try:
            self.start_stack()
            self.create_accounts()
            if self.args.negative_control:
                return self.run_negative_control()
            self.seed_book()
            self.run_order_flow()
            self.verify_run()
            self.write_artifacts()
            return 0
        finally:
            self.stop_flow()
            self.close_clients()
            self.redis_client.close()
            self.stack.cleanup()

    def start_stack(self) -> None:
        print(f"[t6] Starting isolated Compose project {self.stack.project}")
        self.stack.command("up", "--build", "-d", capture=False)
        self.wait_until(self.gateway_healthy, "gateway health", timeout=180)
        self.wait_until(
            lambda: bool(RECOVERY_LINE.search(self.stack.logs())),
            "matcher initial replay",
            timeout=120,
        )
        self.redis_client.ping()
        print("[t6] Gateway, matcher, Redis, PostgreSQL and consumers are ready")

    def gateway_healthy(self) -> bool:
        try:
            response = httpx.get(f"{self.gateway_url}/health", timeout=1)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    @staticmethod
    def wait_until(check, label: str, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(0.1)
        raise TimeoutError(f"Timed out waiting for {label}")

    def create_accounts(self) -> None:
        password = uuid.uuid4().hex
        buyer_name = f"t6buyer{uuid.uuid4().hex[:8]}"
        self.dmm_client = self.register_and_login("dmm_qaa", password)
        self.buyer_client = self.register_and_login(buyer_name, password)
        self.wait_until(
            lambda: self.account_count() == 2
            and self.read_cash_and_fees()[0] == self.initial_balance_and_fees,
            "both account grants to reach the ledger",
            timeout=30,
        )
        # The ledger and gateway risk watcher consume the same stream independently.
        time.sleep(2 * self.settings.stream_health_poll_ms / 1000)
        print("[t6] Registered and funded an isolated DMM and buyer account")

    def register_and_login(self, username: str, password: str) -> httpx.Client:
        client = httpx.Client(base_url=self.gateway_url, timeout=10)
        registered = client.post(
            "/auth/register", json={"username": username, "password": password}
        )
        if registered.status_code != 201:
            raise RuntimeError(
                f"Registration for {username} failed: "
                f"{registered.status_code} {registered.text}"
            )
        login = client.post(
            "/auth/login", json={"username": username, "password": password}
        )
        if login.status_code != 200:
            raise RuntimeError(
                f"Login for {username} failed: {login.status_code} {login.text}"
            )
        return client

    def account_count(self) -> int:
        with self.database() as connection:
            return int(connection.execute("SELECT count(*) FROM accounts").fetchone()[0])

    def database(self):
        return psycopg.connect(
            host="127.0.0.1",
            port=self.postgres_port,
            dbname="quant_arena",
            user="quant",
            password=self.stack.environment["QA_POSTGRES_PASSWORD"],
            connect_timeout=2,
        )

    def read_cash_and_fees(self) -> tuple[int, int]:
        with self.database() as connection:
            cash = int(
                connection.execute(
                    "SELECT COALESCE(sum(cash_ticks), 0) FROM accounts"
                ).fetchone()[0]
            )
            fees = int(
                connection.execute(
                    "SELECT COALESCE(max(fee_ticks), 0) FROM house_fees"
                ).fetchone()[0]
            )
        return cash, fees

    def stream_records(self) -> list:
        entries = self.redis_client.xrange(self.settings.stream_outbound, "-", "+")
        return [unpack_any(fields[RECORD_FIELD]) for _, fields in entries]

    def submit_order(
        self,
        client: httpx.Client,
        *,
        client_order_id: int,
        side: Side,
        qty: int,
        tif: Tif,
    ) -> None:
        response = client.post(
            "/orders",
            json={
                "client_order_id": client_order_id,
                "symbol_id": self.symbol.symbol_id,
                "side": int(side),
                "tif": int(tif),
                "price_ticks": self.price_ticks,
                "qty": qty,
                "order_type": "limit",
            },
        )
        if response.status_code != 202:
            raise RuntimeError(
                f"Order {client_order_id} failed: {response.status_code} {response.text}"
            )

    def wait_for_acceptance(self, client_order_id: int, *, timeout: float = 20) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for record in self.stream_records():
                if (
                    isinstance(record, OrderAccepted)
                    and record.client_order_id == client_order_id
                ):
                    return record.order_id
            time.sleep(0.05)
        raise TimeoutError(f"Order {client_order_id} was not accepted by the matcher")

    def seed_book(self) -> None:
        assert self.dmm_client is not None
        self.submit_order(
            self.dmm_client,
            client_order_id=self.maker_client_order_id,
            side=Side.SELL,
            qty=self.args.orders * self.quantity,
            tif=Tif.GTC,
        )
        self.maker_order_id = self.wait_for_acceptance(self.maker_client_order_id)
        print(
            f"[t6] Seeded {self.args.orders} lots of resting asks on "
            f"{self.symbol.name} at {self.price_ticks} ticks"
        )

    def issue_orders(self) -> None:
        assert self.buyer_client is not None
        start = time.monotonic()
        try:
            for index, client_order_id in enumerate(self.buyer_client_order_ids):
                if self.stop_requested.is_set():
                    break
                intended = start + index / self.args.rate
                delay = intended - time.monotonic()
                if delay > 0 and self.stop_requested.wait(delay):
                    break
                try:
                    self.submit_order(
                        self.buyer_client,
                        client_order_id=client_order_id,
                        side=Side.BUY,
                        qty=self.quantity,
                        tif=Tif.IOC,
                    )
                except (RuntimeError, httpx.HTTPError) as exc:
                    with self.submission_lock:
                        self.submit_errors.append(str(exc))
                    break
                with self.submission_lock:
                    self.submitted.append(client_order_id)
        finally:
            self.flow_done.set()

    def run_order_flow(self) -> None:
        self.cash_before_kill = sum(self.read_cash_and_fees())
        self.flow_thread = threading.Thread(target=self.issue_orders, name="t6-order-flow")
        self.flow_thread.start()
        try:
            self.wait_until(
                lambda: self.submission_count() >= self.args.kill_after
                or self.flow_done.is_set(),
                "order flow to reach the kill point",
                timeout=30,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"{exc}; submitted={self.submission_count()}, "
                f"errors={self.submit_errors}"
            ) from exc
        if self.submission_count() < self.args.kill_after:
            raise RuntimeError(
                f"Order flow stopped before the kill point: "
                f"submitted={self.submission_count()}, errors={self.submit_errors}"
            )
        before_logs = self.stack.logs()
        prior_recoveries = len(RECOVERY_LINE.findall(before_logs))
        print(f"[t6] SIGKILLing matcher while order flow is live ({self.args.kill_after} submitted)")
        killed_at = time.perf_counter()
        self.stack.command("stop", "--timeout", "0", "matcher")
        queued_target = min(
            self.args.orders,
            self.args.kill_after + max(2, round(self.args.rate * 0.25)),
        )
        self.wait_until(
            lambda: self.submission_count() >= queued_target or self.flow_done.is_set(),
            "additional orders to queue while the matcher is down",
            timeout=30,
        )
        time.sleep(0.1)
        start_at = time.perf_counter()
        self.stack.command("start", "matcher")
        self.wait_until(
            lambda: len(RECOVERY_LINE.findall(self.stack.logs())) > prior_recoveries,
            "matcher replay completion",
            timeout=180,
        )
        self.recovery_seconds = time.perf_counter() - killed_at
        self.matcher_replay_seconds = time.perf_counter() - start_at
        match = RECOVERY_LINE.findall(self.stack.logs())[-1]
        self.replayed_records = int(match[0])
        self.flow_thread.join(timeout=30)
        if self.flow_thread.is_alive():
            raise TimeoutError("Order flow did not finish after matcher recovery")
        if self.submit_errors:
            raise RuntimeError(f"Order flow had failures: {self.submit_errors}")
        if len(self.submitted) != self.args.orders:
            raise AssertionError(
                f"submitted {len(self.submitted)} orders, expected {self.args.orders}"
            )
        print(
            f"[t6] Matcher replayed {self.replayed_records} inputs; "
            f"kill-to-recovery {self.recovery_seconds:.3f}s "
            f"(restart-to-replay {self.matcher_replay_seconds:.3f}s)"
        )

    def submission_count(self) -> int:
        with self.submission_lock:
            return len(self.submitted)

    def verify_run(self) -> None:
        if self.maker_order_id is None:
            raise AssertionError("DMM seed order was not accepted")
        try:
            self.wait_until(
                lambda: self.outbound_fill_count() >= self.args.orders,
                "all expected fills to appear on the outbound stream",
                timeout=45,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"{exc}; inbound={self.redis_client.xlen(self.settings.stream_inbound)}, "
                f"outbound={self.redis_client.xlen(self.settings.stream_outbound)}\n"
                f"matcher logs:\n{self.stack.logs()}"
            ) from exc
        records = self.stream_records()
        accepted, fills, maker_order_id = validate_order_fills(
            records,
            maker_client_order_id=self.maker_client_order_id,
            buyer_client_order_ids=self.buyer_client_order_ids,
            expected_qty=self.quantity,
        )
        if maker_order_id != self.maker_order_id:
            raise AssertionError("the maker order id changed across recovery")

        expected_position = self.args.orders * self.quantity
        self.wait_until(
            lambda: self.ledger_positions() == (expected_position, -expected_position),
            "ledger positions to settle all fills",
            timeout=45,
        )
        cash, fees = self.wait_for_conservation()
        validate_cash_conservation(
            cash,
            fees,
            expected=self.initial_balance_and_fees,
        )
        if self.cash_before_kill != self.initial_balance_and_fees:
            raise AssertionError(
                "cash plus fees changed before the kill: "
                f"{self.cash_before_kill} != {self.initial_balance_and_fees}"
            )
        self.fill_records = fills
        print(
            f"[t6] PASS: {len(fills)} unique fills across {len(accepted)} accepted orders, "
            "no loss/duplication; "
            f"cash + fees conserved at {cash + fees} ticks"
        )

    def outbound_fill_count(self) -> int:
        return sum(isinstance(record, Fill) for record in self.stream_records())

    def ledger_positions(self) -> tuple[int, int] | None:
        with self.database() as connection:
            rows = connection.execute(
                """
                SELECT u.username, COALESCE(sum(p.qty), 0)
                FROM users AS u
                LEFT JOIN positions AS p ON p.user_id = u.id
                GROUP BY u.username
                """
            ).fetchall()
        positions = dict(rows)
        dmm = positions.get("dmm_qaa")
        buyer = next(
            (qty for username, qty in rows if username.startswith("t6buyer")),
            None,
        )
        if dmm is None or buyer is None:
            return None
        return int(buyer), int(dmm)

    def wait_for_conservation(self) -> tuple[int, int]:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            cash, fees = self.read_cash_and_fees()
            if cash + fees == self.initial_balance_and_fees:
                return cash, fees
            time.sleep(0.1)
        cash, fees = self.read_cash_and_fees()
        raise AssertionError(
            f"ledger did not conserve cash: cash={cash}, fees={fees}, "
            f"grants={self.initial_balance_and_fees}"
        )

    def run_negative_control(self) -> int:
        first = self.redis_client.xrange(self.settings.stream_inbound, "-", "+", count=1)
        if not first:
            raise RuntimeError("negative control needs a non-empty inbound stream")
        genesis_id = first[0][0]
        print("[t6] Negative control: deleting the replay genesis record in the scratch stream")
        self.stack.command("stop", "--timeout", "0", "matcher")
        self.redis_client.xdel(self.settings.stream_inbound, genesis_id)
        self.stack.command("start", "matcher")
        self.wait_until(
            lambda: GENESIS_ERROR in self.stack.logs(),
            "matcher to reject the deliberately trimmed replay",
            timeout=60,
        )
        print("[t6] Negative control detected: the matcher refused divergent replay.")
        print("[t6] This invocation exits non-zero by design.")
        return 1

    def write_artifacts(self) -> None:
        recovery = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "docker_version": subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                cwd=ROOT,
                env=self.stack.environment,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "orders": self.args.orders,
            "symbol": self.symbol.name,
            "replayed_inbound_records": self.replayed_records,
            "kill_to_recovery_seconds": round(self.recovery_seconds or 0, 6),
            "restart_to_replay_seconds": round(self.matcher_replay_seconds or 0, 6),
            "fills": len(self.fill_records),
            "duplicate_fills": 0,
            "cash_plus_fees_ticks": sum(self.read_cash_and_fees()),
            "cash_conservation_passed": True,
        }
        result_path = (ROOT / self.args.result).resolve()
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(recovery, indent=2) + "\n", encoding="utf-8")
        gif_path = (ROOT / self.args.gif).resolve()
        write_recovery_gif(
            gif_path,
            orders=self.args.orders,
            replayed=self.replayed_records or 0,
            recovery_seconds=self.recovery_seconds or 0,
        )
        print(f"[t6] Recorded result: {result_path.relative_to(ROOT)}")
        print(f"[t6] Recovery demonstration: {gif_path.relative_to(ROOT)}")

    def stop_flow(self) -> None:
        if self.flow_thread is not None and self.flow_thread.is_alive():
            self.stop_requested.set()
            self.flow_thread.join(timeout=15)
            if self.flow_thread.is_alive():
                print("[t6] Order-flow thread did not stop cleanly", file=sys.stderr)

    def close_clients(self) -> None:
        if self.dmm_client is not None:
            self.dmm_client.close()
        if self.buyer_client is not None:
            self.buyer_client.close()


FONT = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    ",": ("00000", "00000", "00000", "00000", "00110", "00100", "01000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "|": ("00100", "00100", "00100", "00100", "00100", "00100", "00100"),
    " ": ("00000",) * 7,
}


def gif_pixels(lines: list[str], width: int = 820, height: int = 320) -> bytes:
    pixels = bytearray(width * height)

    def rectangle(x: int, y: int, w: int, h: int, color: int) -> None:
        for row in range(max(0, y), min(height, y + h)):
            start = row * width + max(0, x)
            end = row * width + min(width, x + w)
            if start < end:
                pixels[start:end] = bytes([color]) * (end - start)

    rectangle(16, 16, width - 32, 3, 1)
    rectangle(16, height - 19, width - 32, 3, 1)
    for line_number, line in enumerate(lines):
        x_start, y_start = 36, 42 + line_number * 38
        text = line.upper()
        color = 3 if line_number == 0 or "PASS" in text else 2
        for char_index, char in enumerate(text):
            glyph = FONT.get(char, FONT[" "])
            for y, row in enumerate(glyph):
                for x, bit in enumerate(row):
                    if bit == "1":
                        rectangle(
                            x_start + (char_index * 6 + x) * 3,
                            y_start + y * 3,
                            3,
                            3,
                            color,
                        )
    return bytes(pixels)


def validate_order_fills(
    records: list,
    *,
    maker_client_order_id: int,
    buyer_client_order_ids: list[int],
    expected_qty: int,
) -> tuple[dict[int, int], list[Fill], int]:
    accepted_records = [
        record for record in records if isinstance(record, OrderAccepted)
    ]
    accepted = {
        record.client_order_id: record.order_id for record in accepted_records
    }
    expected_ids = {maker_client_order_id, *buyer_client_order_ids}
    missing = expected_ids - accepted.keys()
    if missing:
        raise AssertionError(f"missing accepted orders: {sorted(missing)[:5]}")
    if len(accepted_records) != len(expected_ids):
        raise AssertionError(
            f"accepted order count {len(accepted_records)} != expected {len(expected_ids)}"
        )

    fills = [record for record in records if isinstance(record, Fill)]
    if len(fills) != len(buyer_client_order_ids):
        raise AssertionError(
            f"fill count {len(fills)} != expected {len(buyer_client_order_ids)}"
        )
    maker_order_id = accepted[maker_client_order_id]
    taker_order_ids = {accepted[client_id] for client_id in buyer_client_order_ids}
    filled_takers = [fill.taker_order_id for fill in fills]
    if len(set(filled_takers)) != len(buyer_client_order_ids):
        raise AssertionError("duplicate fill detected for a taker order")
    if set(filled_takers) != taker_order_ids:
        raise AssertionError("fill taker ids do not match the submitted buyer orders")
    if any(
        fill.maker_order_id != maker_order_id or fill.qty != expected_qty
        for fill in fills
    ):
        raise AssertionError("fill maker or quantity differs from the deterministic workload")
    return accepted, fills, maker_order_id


def validate_cash_conservation(cash: int, fees: int, *, expected: int) -> None:
    if cash + fees != expected:
        raise AssertionError(
            f"cash plus fees {cash + fees} != initial grants {expected}"
        )


def lzw_encode(pixels: bytes) -> bytes:
    clear, end = 4, 5
    code_size = 3
    next_code = 6
    table = {bytes((index,)): index for index in range(4)}
    output: list[tuple[int, int]] = [(clear, code_size)]
    current = bytes((pixels[0],))
    for value in pixels[1:]:
        symbol = bytes((value,))
        candidate = current + symbol
        if candidate in table:
            current = candidate
            continue
        output.append((table[current], code_size))
        if next_code < 4096:
            table[candidate] = next_code
            next_code += 1
            if next_code > (1 << code_size) and code_size < 12:
                code_size += 1
        else:
            output.append((clear, code_size))
            table = {bytes((index,)): index for index in range(4)}
            code_size = 3
            next_code = 6
        current = symbol
    output.append((table[current], code_size))
    output.append((end, code_size))

    bits = 0
    bit_count = 0
    packed = bytearray()
    for code, width in output:
        bits |= code << bit_count
        bit_count += width
        while bit_count >= 8:
            packed.append(bits & 0xFF)
            bits >>= 8
            bit_count -= 8
    if bit_count:
        packed.append(bits & 0xFF)
    return bytes(packed)


def write_recovery_gif(
    path: Path, *, orders: int, replayed: int, recovery_seconds: float
) -> None:
    width, height = 820, 320
    recovery = f"{recovery_seconds:.3f}"
    frames = [
        [
            "QUANT ARENA - T6",
            "STACK: ISOLATED DOCKER PROJECT",
            "ACCOUNTS: DMM AND BUYER FUNDED",
            "ORDER FLOW: READY",
            "MATCHER: RUNNING",
            "RECOVERY: PENDING",
            "STATUS: TEST IN PROGRESS",
        ],
        [
            "QUANT ARENA - T6",
            f"ORDER FLOW: {orders} LOTS VIA HTTP",
            "MATCHER: SIGKILL DURING FLOW",
            "INBOUND: DURABLE ORDERS QUEUED",
            "MATCHER: RESTARTED",
            "REPLAY: RECOVERING BOOK STATE",
            "STATUS: WAITING FOR REPLAY",
        ],
        [
            "QUANT ARENA - T6",
            f"REPLAY: {replayed} INPUT RECORDS",
            "MATCHER: RESUMED ORDER FLOW",
            f"FILLS: {orders}/{orders} | DUPLICATES: 0",
            f"CASH PLUS FEES: CONSERVED",
            f"RECOVERY TIME: {recovery} SEC",
            "STATUS: PASS",
        ],
    ]
    palette = bytes(
        (
            11, 18, 32,     # dark background
            34, 197, 94,    # green accent
            209, 219, 230,  # body text
            255, 255, 255,  # title/pass
        )
    )
    output = bytearray(b"GIF89a")
    output.extend(width.to_bytes(2, "little"))
    output.extend(height.to_bytes(2, "little"))
    output.extend((0xF1, 0, 0))
    output.extend(palette)
    output.extend(b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00")
    for lines in frames:
        output.extend(b"\x21\xf9\x04\x04\x64\x00\x00\x00")
        output.extend(b"\x2c\x00\x00\x00\x00")
        output.extend(width.to_bytes(2, "little"))
        output.extend(height.to_bytes(2, "little"))
        output.append(0)
        output.append(2)
        compressed = lzw_encode(gif_pixels(lines, width, height))
        for offset in range(0, len(compressed), 255):
            chunk = compressed[offset : offset + 255]
            output.append(len(chunk))
            output.extend(chunk)
        output.append(0)
    output.append(0x3B)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=int, default=100, help="number of taker orders (default: 100)")
    parser.add_argument("--rate", type=float, default=40, help="offered orders per second (default: 40)")
    parser.add_argument(
        "--kill-after", type=int, default=20, help="kill matcher after this many requests (default: 20)"
    )
    parser.add_argument(
        "--negative-control",
        action="store_true",
        help="trim genesis in the isolated stack; expected to exit 1 when the guard catches it",
    )
    parser.add_argument(
        "--result",
        default="benchmarks/results/t6-recovery.json",
        help="successful-run JSON output path relative to the repository",
    )
    parser.add_argument(
        "--gif",
        default="benchmarks/results/t6-recovery.gif",
        help="successful-run animated GIF path relative to the repository",
    )
    args = parser.parse_args()
    if args.orders < 2 or args.rate <= 0 or not 0 < args.kill_after < args.orders:
        parser.error("--orders must be >= 2, --rate > 0, and 0 < --kill-after < --orders")
    if args.negative_control:
        args.orders = max(args.orders, 2)
    for output in (args.result, args.gif):
        path = Path(output)
        if path.is_absolute() or ".." in path.parts:
            parser.error("artifact paths must stay inside the repository")
    return args


def main() -> int:
    args = parse_args()
    return RecoveryRun(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
