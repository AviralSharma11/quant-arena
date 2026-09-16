"""Task 7.4 — the plots, generated offline from the benchmarks' own JSON.

Open Issue 012 §5 settles observability as **structured logs plus offline plots**, with no
Prometheus and no Grafana. This is the offline half: it reads the JSON each harness writes into
`benchmarks/results/data/` and produces PNGs. It talks to nothing, so a plot can always be
regenerated from the recorded run rather than by re-running the exchange.

Four figures, one per question the report asks:

- `b2-latency-vs-rate.png` — does end-to-end latency hold as throughput rises, and where is the
  knee? Percentiles, never a mean, and `send_slip` drawn alongside so a reader can see for
  themselves whether a point is the exchange or the harness.
- `conflation-delay.png` — the public feed's per-order delay against the uniform 0–50 ms the
  20 Hz window predicts. The comparison is the point: the measured shape is that prediction
  shifted up by a fixed floor.
- `scaling-update-delay.png` — update delay as the client count rises, with acknowledgement
  latency on a second axis, since Open Issue 006 §7b claims the second must not follow the first.
- `market-quality.png` — spread and depth as throughput rises.

    python benchmarks/plot_results.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

# Agg before pyplot: this runs in CI and over SSH, where there is no display and the default
# backend would fail at import rather than at draw.
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA = REPO_ROOT / "benchmarks" / "results" / "data"
PLOTS = REPO_ROOT / "benchmarks" / "results" / "plots"

#: One colour per percentile, used identically in every figure so the eye learns it once.
SERIES = {
    "p50_ms": ("p50", "#1f77b4"),
    "p95_ms": ("p95", "#ff7f0e"),
    "p99_ms": ("p99", "#d62728"),
}


def load(name: str) -> dict | None:
    path = DATA / name
    if not path.exists():
        print(f"  skipped {name} — not in {DATA}", file=sys.stderr)
        return None
    return json.loads(path.read_text())


def style(ax, title: str, xlabel: str, ylabel: str) -> None:
    ax.set_title(title, fontsize=11)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.grid(True, alpha=0.25, linewidth=0.6)
    ax.tick_params(labelsize=8)


def plot_b2() -> None:
    data = load("b2-ramp-run2.json")
    if not data:
        return
    rates = [r["rate_per_second"] for r in data["rates"]]
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4.2))

    for key, (label, colour) in SERIES.items():
        left.plot(
            rates,
            [r["http_ack"][key] for r in data["rates"]],
            marker="o", label=f"ack {label}", color=colour, linewidth=1.6,
        )
    style(left, "B2 — acknowledgement latency vs offered rate", "orders/sec offered", "ms")
    left.legend(fontsize=8)

    right.plot(
        rates, [r["private_e2e"]["p50_ms"] for r in data["rates"]],
        marker="o", label="private e2e p50", color="#2ca02c", linewidth=1.6,
    )
    right.plot(
        rates, [r["private_tail"]["p50_ms"] for r in data["rates"]],
        marker="s", label="after durability p50", color="#9467bd", linewidth=1.6,
    )
    right.plot(
        rates, [r["send_slip"]["p99_ms"] for r in data["rates"]],
        marker="^", label="harness send slip p99", color="#7f7f7f",
        linewidth=1.4, linestyle="--",
    )
    style(right, "B2 — delivery, and the harness's own lateness", "orders/sec offered", "ms")
    right.legend(fontsize=8)
    # Axes fractions, not data coordinates: the y range changes with every run and a note
    # pinned to a data point lands on top of a line sooner or later.
    right.text(
        0.02, 0.06,
        "slip stays ~1 ms — every point above it is the exchange, not the harness",
        transform=right.transAxes, fontsize=7.5, color="#555555",
    )

    fig.tight_layout()
    fig.savefig(PLOTS / "b2-latency-vs-rate.png", dpi=150)
    plt.close(fig)
    print("  wrote b2-latency-vs-rate.png")


def plot_conflation() -> None:
    separate = load("conflation-observer-separate.json")
    same = load("conflation-observer-same.json")
    if not separate:
        return
    window = separate["window_ms"]
    fig, ax = plt.subplots(figsize=(7.5, 4.4))

    for data, label, colour in (
        (separate, "observer: separate account", "#1f77b4"),
        (same, "observer: the probe's own socket", "#ff7f0e"),
    ):
        if not data:
            continue
        delays = sorted(data["delays_ms"])
        ax.plot(
            delays,
            [i / len(delays) for i in range(len(delays))],
            label=label, color=colour, linewidth=1.8,
        )

    floor = min(sorted(separate["delays_ms"])[0], window)
    ax.plot(
        [floor, floor + window], [0, 1],
        linestyle="--", color="#666666", linewidth=1.4,
        label=f"uniform 0–{window:.0f} ms, shifted by the {floor:.1f} ms floor",
    )
    ax.set_xlim(0, max(90, floor + window + 10))
    style(
        ax,
        "Public feed — per-order conflation delay (CDF)",
        "outbound append to L2 frame at the client, ms",
        "fraction of probes",
    )
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(PLOTS / "conflation-delay.png", dpi=150)
    plt.close(fig)
    print("  wrote conflation-delay.png")


def plot_scaling() -> None:
    data = load("scaling.json")
    if not data:
        return
    steps = data["steps"]
    clients = [s["clients_requested"] for s in steps]
    fig, ax = plt.subplots(figsize=(7.5, 4.4))

    for key, (label, colour) in SERIES.items():
        ax.plot(
            clients, [s["update_delay"][key] for s in steps],
            marker="o", label=f"update delay {label}", color=colour, linewidth=1.6,
        )
    ax.set_yscale("log")
    style(
        ax,
        "B3 — update delay vs connected clients (tick to arrival)",
        "concurrent WebSocket clients",
        "ms, log scale",
    )

    twin = ax.twinx()
    twin.plot(
        clients, [s["ack_latency"]["p50_ms"] for s in steps],
        marker="s", linestyle=":", color="#8c564b", linewidth=1.6,
        label="order ack p50 (right axis)",
    )
    twin.set_ylabel("ms", fontsize=9)
    twin.tick_params(labelsize=8)

    handles, labels = ax.get_legend_handles_labels()
    extra_h, extra_l = twin.get_legend_handles_labels()
    ax.legend(handles + extra_h, labels + extra_l, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(PLOTS / "scaling-update-delay.png", dpi=150)
    plt.close(fig)
    print("  wrote scaling-update-delay.png")


def plot_market_quality() -> None:
    data = load("market-quality.json")
    if not data:
        return
    steps = data["steps"]
    rates = [s["rate_per_second"] for s in steps]
    fig, ax = plt.subplots(figsize=(7.5, 4.4))

    ax.plot(
        rates, [s["spread_bps"].get("p50", 0) for s in steps],
        marker="o", color="#1f77b4", linewidth=1.8, label="spread p50",
    )
    ax.plot(
        rates, [s["spread_bps"].get("p95", 0) for s in steps],
        marker="^", color="#d62728", linewidth=1.4, linestyle="--", label="spread p95",
    )
    ax.set_ylim(0, max(60, max(s["spread_bps"].get("p95", 0) for s in steps) * 1.25))
    style(
        ax,
        "Market quality as throughput rises",
        "orders/sec of background load",
        "quoted spread, basis points",
    )

    twin = ax.twinx()
    twin.plot(
        rates, [s["thin_side_depth"].get("p50", 0) for s in steps],
        marker="s", linestyle=":", color="#2ca02c", linewidth=1.6,
        label="depth p50, thinner side",
    )
    twin.set_ylabel("resting quantity", fontsize=9)
    twin.set_ylim(0, max(120, max(s["thin_side_depth"].get("p50", 0) for s in steps) * 1.4))
    twin.tick_params(labelsize=8)

    handles, labels = ax.get_legend_handles_labels()
    extra_h, extra_l = twin.get_legend_handles_labels()
    ax.legend(handles + extra_h, labels + extra_l, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(PLOTS / "market-quality.png", dpi=150)
    plt.close(fig)
    print("  wrote market-quality.png")


def main() -> int:
    PLOTS.mkdir(parents=True, exist_ok=True)
    print(f"reading {DATA}")
    plot_b2()
    plot_conflation()
    plot_scaling()
    plot_market_quality()
    print(f"plots in {PLOTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
