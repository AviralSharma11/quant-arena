# Quant Arena — Operations Runbook

This runbook covers how to start, operate, monitor, back up, and debug Quant Arena.

Quant Arena is designed as **one project, split into separate programs, sharing one flow of events** (see `ARCHITECTURE.md`).
- The **Redis append-only event stream** is the single source of truth.
- **PostgreSQL** is a derived read model, rebuildable from the stream.
- The **C++ matching engine** is single-threaded, zero-I/O, and money-blind.
- **Fan-out** conflates market data to 20 Hz, while keeping private order/fill data un-conflated.

---

## 1. Quick Reference

```bash
# --- 1. Start the entire core stack (redis, postgres, gateway, matcher, ledger, fanout, archiver) ---
docker compose up --build -d

# --- 2. Check service health and startup configuration ---
docker compose ps
docker compose logs gateway | grep config_hash   # All services must agree on this hash
curl -s http://localhost:8000/health             # Gateway health
curl -s http://localhost:8001/health             # Fan-out stream position and metrics

# --- 3. Start live market bots (designated market makers + noise traders) ---
# Secrets are in secrets-local.txt or environment
QA_BOT_PASSWORD=quant_arena_dev_password docker compose --profile bots up -d --build

# --- 4. Start frontend UI (development server) ---
cd web && npm install && npm run dev             # UI accessible at http://localhost:5173

# --- 5. Stop the stack ---
docker compose down                              # Preserves volumes (redisdata, pgdata, archive)
docker compose down -v                           # Wipes all volumes and resets state completely

# --- 6. Run test suite ---
# Requires redis and postgres running
docker compose up -d redis postgres
.venv/bin/python -m pytest -q                    # 842 collected: 835 passed, 7 skipped
```

---

## 2. Service Map

| Service | Role | Ports | Depends On | Healthcheck | Restart Policy |
|---|---|---|---|---|---|
| **`redis`** | Durable append-only event stream (`qa.inbound`, `qa.outbound`) & sessions | `6379:6379` | None | `redis-cli ping` | `unless-stopped` |
| **`postgres`** | Derived relational read model (accounts, positions, open orders, house fees) | `5432:5432` | None | `pg_isready -U quant -d quant_arena` | `unless-stopped` |
| **`gateway`** | FastAPI app: auth, validation, pre-trade risk, single sequencer for `qa.inbound` | `8000:8000` | `redis`, `postgres` | `GET http://localhost:8000/health` | `unless-stopped` |
| **`matcher`** | C++ matching engine wrapper (`services.matcher.cpp_runner`): executes `qa.inbound` -> `qa.outbound` | None | `redis` | Python script pinging Redis | `unless-stopped` |
| **`ledger`** | Projection consumer (`services.ledger`): `qa.outbound` -> PostgreSQL tables | None | `redis`, `postgres`, `gateway` | Python script pinging Redis | `unless-stopped` |
| **`fanout`** | Real-time WebSocket server (`services.fanout`): 20 Hz L2 book snapshots, private streams | `8001:8001` | `redis`, `gateway` | `GET http://localhost:8001/health` | `unless-stopped` |
| **`archiver`** | Outbound stream consumer (`services.archiver`): writes daily/hourly Parquet files | None | `redis` | Python script checking Redis ping & volume write access | `unless-stopped` |
| **`bots`** (profile) | Robot market makers & noise traders driven by replayed historical prices | None | `gateway`, `matcher` | None (obligation monitoring via scripts) | `unless-stopped` |

---

## 3. Starting and Stopping

### Fresh Start / Reset
When starting fresh or after a breaking schema update (`schema_version = 2`):
```bash
docker compose down -v
docker compose up --build -d
```
*Note: A fresh stack wipe clears all accounts. You must register a new user before trading.*

### Development Mode with Host Hot-Reloading
Run only storage dependencies in Docker and application services on your host:
```bash
docker compose up -d redis postgres
source .venv/bin/activate
uvicorn services.gateway.app:app --reload --port 8000
```

---

## 4. Known Failure Modes & Diagnostic Procedures

### The "Healthy-but-Idle" Pattern
Container healthchecks verify network connectivity to Redis or PostgreSQL, **not that the service is making progress**. A container reported as `healthy` by Docker can be completely frozen.

> **Key Rule: The container status is NOT what to check. The stream output is what to check.**

To detect a stalled pipeline, sample stream lengths 5 seconds apart:
```bash
docker compose exec redis redis-cli XLEN qa.inbound
sleep 5
docker compose exec redis redis-cli XLEN qa.inbound
```
If `qa.inbound` increases but `qa.outbound` does not advance, the matching engine or ledger is stalled.

### Documented Failure Cases

1. **Matcher Dies Silently After Redis Restarts (HANDOFF §3a)**
   - **Symptom:** `CppMatcher.run()` does not catch connection errors. If Redis restarts (e.g. during `test_halt.py`), the matcher task crashes, but the Docker container remains "healthy".
   - **Fix:** Restart the matcher container:
     ```bash
     docker compose restart matcher
     ```
   - **Observation:** On restart, the matcher silently replays the stream from the start (~85–110s for 1.6M records) before answering new orders. Look for log confirmation: `replayed N inbound records`.

2. **Balances Reset to Zero After Stream Trimming**
   - **Symptom:** Cash grants occur on account creation (`CreateAccount`). If `qa.inbound` exceeds `MAXLEN ~ 2,000,000` records, old grants are trimmed. A subsequent restart replaying from the stream start reconstructs accounts with 0 ticks.
   - **Remedy:** Reset the volume: `docker compose down -v` (Full snapshots are planned for Phase 2).

3. **Archiver Permission Fault on Volume Mount**
   - **Symptom:** Archiver fails `mkdir /archive/...` if volume ownership is root.
   - **Fix:** Built-in Dockerfile `RUN mkdir -p /archive && chown quant:quant /archive` handles this. Healthcheck also explicitly tests `os.access(QA_ARCHIVE_DIR, os.W_OK)`.

4. **Bot Re-Authentication on Expiry**
   - **Symptom:** Sessions expire after an absolute TTL of 12 hours (`ttl_seconds = 43200`).
   - **Behaviour:** Bots automatically catch `401 Unauthorized` and re-authenticate without cancelling open quotes.

---

## 5. Monitoring & Health Inspection

### Gateway Health
```bash
curl -s http://localhost:8000/health | jq .
```
Returns `{"status": "ok", "redis": true, "database": true, "halted": false}`.

### Fan-Out WebSocket Engine Metrics
```bash
curl -s http://localhost:8001/health | jq .
```
Returns:
- `stream_position`: current Redis stream ID read
- `records_applied`: count of raw events processed
- `ticks`: count of 50 ms conflation ticks emitted
- `subscribers`: active WebSocket client count
- `max_tick_seconds`: longest time taken to build and broadcast a conflation tick

### Configuration Hash Audit
Every process prints its loaded configuration hash upon booting. Verify that all running processes match:
```bash
docker compose logs | grep config_hash
```

---

## 6. Backups and Disaster Recovery

### PostgreSQL Read-Model Backup
PostgreSQL is a derived projection, but backups provide convenient operational snapshots.
```bash
# Backup using Docker Compose mode (default)
./scripts/backup_postgres.sh

# Backup using direct host connection
./scripts/backup_postgres.sh -m direct -H localhost -p 5432 -U quant -D quant_arena
```
Backups are saved to `./backups/quant_arena_YYYYMMDD_HHMMSS.sql.gz` and rotated automatically (retains latest 7).

### Redis Event Stream Persistence
Redis is configured with Append-Only File (AOF) durability:
- Flags: `--appendonly yes --appendfsync always`
- Data directory: `redisdata` volume
- Group commit is handled by the gateway pipelining concurrent `XADD` requests.

---

## 7. Tracing Orders

Quant Arena provides deterministic, zero-overhead order tracing directly from the event log without OpenTelemetry:

```bash
python scripts/trace.py <CLIENT_ORDER_ID>
```

Output reconstructs the complete lifecycle across services:
```
Order 100249 (Symbol: QAA, BUY 50 @ 84,200.00)
├── 10:00:00.001 [Gateway] Accepted and sequenced (Inbound seq: 1041)
├── 10:00:00.003 [Matcher] Matched vs resting ask 100120 (Fill 30 @ 84,200.00)
├── 10:00:00.003 [Matcher] Remainder resting on book (20 @ 84,200.00)
├── 10:00:00.005 [Ledger]  Cash settled: -2,526,000 ticks, Position: +30
└── 10:00:00.050 [FanOut]  L2 snapshot published to 14 subscribers
```

---

## 8. Common Administrative Operations

### Run a Backtest from CLI
```bash
# Human-readable table report
python -m services.backtest --symbol QAA --bar-minutes 5

# Machine-readable JSON output
python -m services.backtest --symbol QAA --json
```

### Validate Schema and Code Generation
```bash
python contracts/v1/generate.py --check
```

### Verify Pinned Historical Market Data
```bash
python scripts/fetch_market_history.py --check
```

---

## 9. Deploying to Azure (public, Task 3.3)

One VM runs the whole stack behind Caddy, which terminates TLS and serves the frontend. The VM
never builds anything: CI publishes `quant-arena-gateway` and `quant-arena-web` for every merge to
`main`, tagged with the commit SHA, and the VM pulls them. The files are `deploy/`:
`docker-compose.prod.yml` (layered over `docker-compose.yml`), `Caddyfile`, `web.Dockerfile`,
`redeploy.sh`, `backup.sh`.

Measured footprint with bots trading: **~390 MiB** for all nine containers.

### 9.1 One-time: make the images pullable
The VM pulls anonymously, so both GHCR packages must be public (the repository already is).
On GitHub: profile → **Packages** → `quant-arena-gateway` → **Package settings** → **Change
visibility** → Public. Repeat for `quant-arena-web` once its first publish has run.

### 9.2 One-time: create the VM (Azure portal, Azure for Students subscription)
1. **Virtual machines → Create.**
   - Image: **Ubuntu Server 24.04 LTS**.
   - Size: the 1 GB burstable size your subscription lists under **free services** (B1s, or
     B2ats v2 / B2pts v2 for ARM — the images are built for both).
   - Authentication: **SSH public key**, username `azureuser`.
   - Inbound ports: **SSH (22)**, **HTTP (80)**, **HTTPS (443)**.
   - Disk: Standard SSD, the size the free services cover.
2. After creation: **Networking → port 22 rule → Source: My IP address.** Only 80 and 443 stay
   open to the world. PostgreSQL, Redis, the gateway and fan-out are never published by the
   production compose file, so there is nothing else to close.
3. **Public IP address → Configuration → DNS name label**, e.g. `quant-arena`. The hostname is
   then `quant-arena.<region>.cloudapp.azure.com` — free, stable, and enough for Let's Encrypt.
   Set the IP assignment to **Static** so the name never points at someone else.

### 9.3 One-time: prepare the VM
```bash
ssh azureuser@quant-arena.<region>.cloudapp.azure.com

# 2 GB of swap: the stack fits in 1 GB, but Postgres and image pulls spike.
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile
sudo swapon /swapfile && echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker azureuser && exit          # log in again for the group to apply

ssh azureuser@quant-arena.<region>.cloudapp.azure.com
git clone https://github.com/AviralSharma11/quant-arena.git && cd quant-arena

# Secrets. Gitignored; compose refuses to start without each of them.
cat > .env <<ENV
QA_DOMAIN=quant-arena.<region>.cloudapp.azure.com
QA_POSTGRES_PASSWORD=$(openssl rand -hex 24)
QA_BOT_PASSWORD=$(openssl rand -hex 24)
ENV
chmod 600 .env
```
Never change `QA_POSTGRES_PASSWORD` after the first start: PostgreSQL stores it in its volume on
first boot and ignores the variable afterwards.

### 9.4 Deploy and redeploy — one command
```bash
deploy/redeploy.sh <commit-sha>
```
Pick a commit whose **Publish images** run succeeded (`gh run list --workflow publish.yml`).
A run cancelled by a later merge publishes nothing, and the script then stops at `pull`
before touching anything running. Volumes are kept, so accounts and the order log survive and
every process resumes from its checkpoint. The first start obtains the certificate, which takes
a few seconds.

Check from outside the VM:
```bash
curl -I https://quant-arena.<region>.cloudapp.azure.com/            # 200, valid certificate
nc -zv -w 3 quant-arena.<region>.cloudapp.azure.com 5432           # must fail: not published
```

### 9.5 Backups — and get them off the VM
```bash
crontab -e      # on the VM:
15 3 * * * cd ~/quant-arena && deploy/backup.sh >> ~/backups/backup.log 2>&1
```
`deploy/backup.sh` writes a PostgreSQL dump and a Redis snapshot to `~/backups`, keeping seven of
each. Both are needed: `users` exists only in PostgreSQL, and balances older than the newest
checkpoint exist only in Redis. **Copy them off the VM regularly**, from your own machine:
```bash
scp -r azureuser@quant-arena.<region>.cloudapp.azure.com:~/backups ./azure-backups
```
When the student credit runs out, Azure disables the VM rather than billing; backups on its disk
go with it.

### 9.6 Watching the credit
**Cost Management → Cost analysis** on the Azure for Students subscription. The VM itself may be
covered by free services; the static public IP and disk draw on the credit. Re-check before the
subscription's renewal date.
