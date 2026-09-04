"""The bots' decisions — pure, seeded, and provable without a network.

Success Criterion 4 is "seeded runs reproduce identical bot behaviour", and Open Issue 005
sub-decision 5d is careful about what that can mean: determinism is a property of **replaying
the log**, not of reproducing a live session. Live bots run in real time over HTTP and
interleave non-deterministically. So what is asserted here is that each decision function,
given the same seed and the same inputs, produces the same output — and the live behaviour is
proven separately, against a running exchange, in `test_session.py`.
"""

from __future__ import annotations

import random

import pytest

from config.settings import NoiseSettings, ObligationLimits, Settings
from contracts.v1.generated.contracts import Side
from services.bots.fairvalue import SyntheticFairValue
from services.bots.noise import NoiseTrader
from services.bots.obligations import (
    NOT_TWO_SIDED,
    SIZE_TOO_SMALL,
    SPREAD_TOO_WIDE,
    ObligationMeter,
)
from services.bots.quoting import Quote, desired_quote, needs_requote
from services.bots.runner import derive_rng

BUY, SELL = int(Side.BUY), int(Side.SELL)


def _quote(inventory: int, *, fair: int = 1_000, size: int = 100):
    """A quote from the shipped parameters, so anything compliant here is compliant live."""
    return desired_quote(
        fair_value_ticks=fair, inventory=inventory,
        half_spread_bps=25, skew_bps=25, size=size,
    )


# --- inventory skew: Success Criterion 3 ------------------------------------------------------


def test_a_flat_maker_quotes_symmetrically_around_fair_value():
    quote = _quote(0)
    assert quote.bid.price_ticks == 998
    assert quote.ask.price_ticks == 1_002
    assert quote.mid_ticks == 1_000


def test_being_long_pushes_both_quotes_down():
    """The mechanism behind Success Criterion 3, in one assertion.

    A maker holding inventory it wants to shed lowers its market, so its ask is easier to lift
    and its bid is harder to hit. A symmetric quoter would accumulate without limit and
    eventually be unable to quote at all (Open Issue 005 sub-decision 5b).
    """
    flat, long = _quote(0), _quote(100)
    assert long.mid_ticks < flat.mid_ticks
    assert long.ask.price_ticks < flat.ask.price_ticks
    assert long.bid.price_ticks < flat.bid.price_ticks


def test_being_short_pushes_both_quotes_up():
    flat, short = _quote(0), _quote(-100)
    assert short.mid_ticks > flat.mid_ticks
    assert short.bid.price_ticks > flat.bid.price_ticks


def test_the_skew_is_symmetric_in_magnitude():
    """A signed floor division would skew a short one tick further than the equivalent long,
    quietly biasing the maker in one direction over a long session."""
    flat = _quote(0).mid_ticks
    assert _quote(100).mid_ticks - flat == -(_quote(-100).mid_ticks - flat)


def test_one_full_fill_moves_the_mid_by_less_than_a_spread():
    """The reason `inventory_skew_bps` defaults to `half_spread_bps`: enough to bias the next
    trade toward flattening, not enough to jump the market."""
    flat = _quote(0)
    after_one_fill = _quote(100)
    assert 0 < abs(after_one_fill.mid_ticks - flat.mid_ticks) < flat.spread_ticks


def test_the_skew_is_a_constant_proportion_of_price():
    """The unit is basis points of fair value, so one configuration behaves the same way on
    every symbol. A ticks-per-unit skew would be violent on a cheap one and invisible on a
    dear one — which is why that was the wrong unit."""
    shifts = {
        fair: (fair - _quote(100, fair=fair).mid_ticks) / fair
        for fair in (10_000, 100_000, 1_000_000)
    }
    # 25 bps per full quote size, one full quote size held: 0.25% of price, on every scale.
    for fraction in shifts.values():
        assert fraction == pytest.approx(0.0025, rel=0.01)


def test_the_skew_truncates_to_nothing_below_one_tick():
    """Recorded because it is a real limit, not a bug hiding.

    The skew is integer ticks, so on a symbol cheap enough that 25 bps of one quote size is
    under a tick, small inventories do not move the quote at all — the maker only starts
    skewing once it has accumulated enough to be worth a whole tick. On a 100-tick symbol with
    a 100-lot quote that threshold is four full fills. Task 5.1 chooses the real tick sizes,
    and this is the arithmetic that says what a tick size costs.
    """
    assert _quote(100, fair=100).mid_ticks == 100, "under one tick — no shift"
    assert _quote(400, fair=100).mid_ticks == 99, "four fills is finally worth a tick"


def test_the_skew_does_not_change_meaning_when_quote_size_does():
    """Skew is per *one full quote size*, so doubling the size halves the per-unit effect and
    a single fill still moves the mid by the same amount."""
    small = _quote(100, size=100)
    large = _quote(200, size=200)
    assert (1_000 - small.mid_ticks) == (1_000 - large.mid_ticks)


def test_quotes_never_cross_and_never_go_non_positive():
    """Enough inventory would otherwise push a quote through zero, and every order after that
    is INVALID_PRICE — a correct rejection but a baffling way to find out."""
    for inventory in (0, 10_000, 1_000_000, -1_000_000):
        quote = _quote(inventory)
        assert quote.bid.price_ticks >= 1
        assert quote.ask.price_ticks > quote.bid.price_ticks


def test_the_two_sides_carry_the_configured_size():
    quote = _quote(0, size=250)
    assert quote.bid.qty == quote.ask.qty == 250
    assert quote.bid.side == BUY and quote.ask.side == SELL


# --- the requote policy -----------------------------------------------------------------------


def test_an_absent_quote_always_needs_placing():
    assert needs_requote(None, Quote(BUY, 100, 10)) is True


def test_an_unchanged_quote_is_left_alone():
    """Requoting every tick regardless would burn the rate limit and fill a retained, replayed
    stream with churn carrying no information."""
    resting = Quote(BUY, 100, 10)
    assert needs_requote(resting, Quote(BUY, 100, 10)) is False


@pytest.mark.parametrize("changed", [Quote(BUY, 101, 10), Quote(BUY, 100, 11)])
def test_a_moved_price_or_size_is_replaced(changed: Quote):
    assert needs_requote(Quote(BUY, 100, 10), changed) is True


# --- fair value -------------------------------------------------------------------------------


def test_the_same_seed_produces_the_same_price_path():
    def path(seed: int) -> list[int]:
        walk = SyntheticFairValue(
            start_ticks=1_000, volatility_ticks=5, rng=random.Random(seed)
        )
        return [walk.next_ticks() for _ in range(50)]

    assert path(7) == path(7)
    assert path(7) != path(8)


def test_the_price_path_never_reaches_zero():
    """A random walk is unbounded below. Every quote derived from a non-positive fair value is
    nonsense, and the exchange would refuse them one at a time rather than loudly."""
    walk = SyntheticFairValue(
        start_ticks=5, volatility_ticks=1_000, rng=random.Random(1)
    )
    assert all(walk.next_ticks() >= 1 for _ in range(500))


def test_a_non_positive_start_is_refused():
    with pytest.raises(ValueError, match="above zero"):
        SyntheticFairValue(start_ticks=0, volatility_ticks=1, rng=random.Random(1))


# --- noise traders ----------------------------------------------------------------------------


def _noise(seed: int, settings: NoiseSettings | None = None) -> NoiseTrader:
    return NoiseTrader(
        rng=random.Random(seed),
        settings=settings or NoiseSettings(
            count_per_symbol=1, arrivals_per_second=2.0, min_qty=1, max_qty=10
        ),
    )


def test_a_retail_noise_trader_never_sells_what_it_does_not_hold():
    """It is an ordinary retail account, and Open Issue 004 section 5 invariant 2 is
    `position >= 0` for those. An order the gateway would refuse is flow that never reaches the
    book, so the constraint belongs in the decision rather than in the rejection."""
    trader = _noise(1)
    for _ in range(200):
        intent = trader.decide(position=0)
        assert intent is not None and intent.side == BUY


def test_it_never_sells_more_than_it_holds():
    trader = _noise(2)
    for _ in range(200):
        intent = trader.decide(position=3)
        assert intent is not None
        if intent.side == SELL:
            assert intent.qty <= 3


def test_it_does_sell_once_it_has_inventory():
    """Otherwise the market maker's short inventory would grow without bound in one direction,
    and the tape would be all buys."""
    trader = _noise(3)
    sides = {trader.decide(position=100).side for _ in range(200)}
    assert sides == {BUY, SELL}


def test_the_same_seed_produces_the_same_decisions():
    a, b = _noise(11), _noise(11)
    assert [a.decide(position=50) for _ in range(50)] == [
        b.decide(position=50) for _ in range(50)
    ]


def test_a_traders_random_sequence_does_not_depend_on_its_own_position():
    """The side coin is flipped whether or not selling is possible. If it were not, two traders
    with the same seed would diverge as soon as their inventories differed, and a "seeded" run
    would reproduce nothing."""
    flush, broke = _noise(21), _noise(21)
    flush_qty = [flush.decide(position=1_000).qty for _ in range(30)]
    broke_qty = [broke.decide(position=0).qty for _ in range(30)]
    assert flush_qty == broke_qty


def test_arrival_delays_are_exponential_and_seeded():
    trader = _noise(5)
    delays = [trader.next_delay_seconds() for _ in range(2_000)]
    assert all(d >= 0 for d in delays)
    # Mean of an exponential with rate 2.0 is 0.5. Loose bounds: this asserts the distribution
    # is the one intended, not that a sample matches its mean exactly.
    assert 0.4 < sum(delays) / len(delays) < 0.6
    assert _noise(5).next_delay_seconds() == delays[0]


def test_impossible_quantity_bounds_are_refused():
    with pytest.raises(ValueError, match="min_qty <= max_qty"):
        _noise(1, NoiseSettings(count_per_symbol=1, arrivals_per_second=1.0,
                                min_qty=10, max_qty=1))


# --- obligations ------------------------------------------------------------------------------


LIMITS = ObligationLimits(max_spread_bps=50, min_quote_size=100, min_two_sided_uptime=0.95)


def test_a_compliant_quote_records_no_breach():
    meter = ObligationMeter(symbol_id=1, limits=LIMITS)
    assert meter.observe(_quote(0)) == []
    assert meter.two_sided_uptime == 1.0
    assert meter.meets_uptime


def test_a_missing_side_is_a_two_sided_breach():
    meter = ObligationMeter(symbol_id=1, limits=LIMITS)
    breaches = meter.observe(None)
    assert [b.kind for b in breaches] == [NOT_TWO_SIDED]
    assert meter.two_sided_uptime == 0.0


def test_too_wide_a_spread_is_a_breach():
    meter = ObligationMeter(symbol_id=1, limits=LIMITS)
    wide = desired_quote(
        fair_value_ticks=1_000, inventory=0, half_spread_bps=500, skew_bps=0, size=100
    )
    assert SPREAD_TOO_WIDE in {b.kind for b in meter.observe(wide)}


def test_too_small_a_quote_is_a_breach_on_each_side_separately():
    meter = ObligationMeter(symbol_id=1, limits=LIMITS)
    breaches = meter.observe(_quote(0, size=1))
    assert [b.kind for b in breaches] == [SIZE_TOO_SMALL, SIZE_TOO_SMALL]
    assert {b.detail["side"] for b in breaches} == {"bid", "ask"}


def test_uptime_is_the_fraction_of_samples_with_both_sides_live():
    meter = ObligationMeter(symbol_id=1, limits=LIMITS)
    for _ in range(19):
        meter.observe(_quote(0))
    meter.observe(None)

    assert meter.two_sided_uptime == pytest.approx(0.95)
    assert meter.meets_uptime, "0.95 meets a 0.95 minimum"


def test_falling_below_the_uptime_floor_is_visible_in_the_summary():
    meter = ObligationMeter(symbol_id=1, limits=LIMITS)
    for _ in range(18):
        meter.observe(_quote(0))
    for _ in range(2):
        meter.observe(None)

    summary = meter.summary()
    assert summary["meets_uptime"] is False
    assert summary["breaches"] == {NOT_TWO_SIDED: 2}
    assert summary["total_breaches"] == 2


def test_an_unsampled_session_is_vacuously_compliant():
    """A maker that has not been asked to quote yet has not failed to."""
    assert ObligationMeter(symbol_id=1, limits=LIMITS).meets_uptime


# --- seeding ------------------------------------------------------------------------------------


def test_seeds_are_derived_by_name_so_adding_a_bot_does_not_shift_the_others():
    """With a shared counter, changing `count_per_symbol` would silently change the market
    maker's price path too, and a reproducible run would reproduce nothing recognisable."""
    before = derive_rng(99, "fairvalue", 1).random()
    _ = [derive_rng(99, "noise", 1, i).random() for i in range(10)]
    after = derive_rng(99, "fairvalue", 1).random()
    assert before == after


def test_different_roles_and_symbols_get_different_streams():
    draws = {
        derive_rng(99, role, symbol).random()
        for role in ("fairvalue", "noise")
        for symbol in (1, 2)
    }
    assert len(draws) == 4


def test_the_shipped_configuration_is_internally_coherent(settings: Settings):
    """The parameters are fixed *before* any demonstration strategy is written — tuning the
    market until a chosen strategy profits is curve-fitting one's own universe (Open Issue 005
    section 6). This asserts they are at least self-consistent."""
    bots = settings.bots
    assert bots.quote_size >= bots.obligations.min_quote_size, (
        "the maker would breach its own minimum-size obligation on every tick"
    )
    assert 2 * bots.half_spread_bps <= bots.obligations.max_spread_bps, (
        "the maker would breach its own maximum-spread obligation on every tick"
    )
    assert bots.noise.min_qty >= 1 and bots.noise.max_qty >= bots.noise.min_qty
