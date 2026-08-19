"""Tests for FakeOrder trail_activation_pct and trail_min_distance_pct parameters."""
import pytest
from bot.fake_order import FakeOrder

# ── Setup helpers ─────────────────────────────────────────────────────────────
#
# All BUY orders: entry=100, tp=200, sl=80, partial_take_pct=0.10.
# partial_price = 100 + 0.10*(200-100) = 110.
# trailing_stop_pct = 0.15.
#
# All SELL orders: entry=100, tp=80, sl=120, partial_take_pct=0.10.
# partial_price = 100 - 0.10*(100-80) = 98.
# trailing_stop_pct = 0.15.
#
# arm_buy uses high=110.0 exactly so _max_favorable = 110 after arming.
# gained on subsequent candles starts from 110-100=10.


def make_buy_trail(
    trail_activation_pct: float = 0.0,
    trail_min_distance_pct: float = 0.0,
) -> FakeOrder:
    return FakeOrder(
        side='BUY',
        entry_price=100.0,
        tp=200.0,
        sl=80.0,
        level=1,
        signal_type='test',
        candle_index=0,
        partial_take_pct=0.10,
        trailing_stop_pct=0.15,
        trail_activation_pct=trail_activation_pct,
        trail_min_distance_pct=trail_min_distance_pct,
    )


def make_sell_trail(
    trail_activation_pct: float = 0.0,
    trail_min_distance_pct: float = 0.0,
) -> FakeOrder:
    return FakeOrder(
        side='SELL',
        entry_price=100.0,
        tp=80.0,
        sl=120.0,
        level=1,
        signal_type='test',
        candle_index=0,
        partial_take_pct=0.10,
        trailing_stop_pct=0.15,
        trail_activation_pct=trail_activation_pct,
        trail_min_distance_pct=trail_min_distance_pct,
    )


def arm_buy(order: FakeOrder) -> None:
    """Arm at exactly partial_price=110 so _max_favorable=110 after this candle."""
    result = order.check(110.0, 105.0, 1, candle_open=105.0, candle_close=108.0)
    assert order._partial_armed is True
    assert result is None  # arming candle cannot also trigger trail
    # After arming candle: _max_favorable = 110, gained = 10.


def arm_sell(order: FakeOrder) -> None:
    """Arm at exactly partial_price=98 so _max_favorable=98 after this candle."""
    result = order.check(99.0, 98.0, 1, candle_open=99.0, candle_close=98.5)
    assert order._partial_armed is True
    assert result is None
    # After arming candle: _max_favorable = 98, gained = 100-98 = 2.


# ── test 1: no activation_pct, trail fires normally ───────────────────────────

def test_trail_fires_normally_without_activation_pct():
    """Without activation_pct the trail fires as soon as max_favorable > entry."""
    order = make_buy_trail()
    arm_buy(order)
    # After arm: _max_favorable=110, gained=10.
    # trail_price = 110 - 0.15*10 = 108.5.
    # Candle 2: high=110, low=108.4 ≤ 108.5 → trail fires.
    result = order.check(110.0, 108.4, 2, candle_open=109.0, candle_close=108.6)
    assert result == 'trail'
    assert order.close_price == pytest.approx(108.5)


# ── test 2: trail blocked below activation threshold ─────────────────────────

def test_trail_blocked_below_activation_threshold():
    """
    activation_pct=12.0 means trail requires 12% gain from entry.
    After arm_buy, gained=10 (10%<12%) so trail is blocked.
    The fixed SL at 80 fires when low drops that far.
    """
    order = make_buy_trail(trail_activation_pct=12.0)
    arm_buy(order)
    # gained=10, 10% < 12% → trail blocked on this candle.
    # Low falls all the way to SL at 80 → loss (SL safety net fires).
    result = order.check(110.0, 79.0, 2, candle_open=110.0, candle_close=80.0)
    assert result == 'loss'


# ── test 3: trail fires above activation threshold ────────────────────────────

def test_trail_fires_above_activation_threshold():
    """
    activation_pct=8.0: trail fires once gained/entry >= 8%.
    After arm_buy, gained=10 (10%>=8%) so trail is immediately active.
    trail_price = 110 - 0.15*10 = 108.5; low=108.4 ≤ 108.5 → fire.
    """
    order = make_buy_trail(trail_activation_pct=8.0)
    arm_buy(order)
    result = order.check(110.0, 108.4, 2, candle_open=109.0, candle_close=108.6)
    assert result == 'trail'
    assert order.close_price == pytest.approx(108.5)


# ── test 4: trail_min_distance applied ───────────────────────────────────────

def test_trail_min_distance_applied():
    """
    trail_min_distance_pct=2.0 → _trail_min_distance = 100*2/100 = 2.0.
    After arm_buy: gained=10, formula=0.15*10=1.5 < 2.0.
    Effective trail_distance = 2.0; trail_price = 110-2.0 = 108.0.
    low=107.9 ≤ 108.0 → fire.
    """
    order = make_buy_trail(trail_min_distance_pct=2.0)
    arm_buy(order)
    # Formula: 0.15*10=1.5; min_distance: 2.0 → max=2.0; trail_price=108.0.
    result = order.check(110.0, 107.9, 2, candle_open=110.0, candle_close=108.1)
    assert result == 'trail'
    assert order.close_price == pytest.approx(108.0)


# ── test 5: normal trail distance wins when above min ────────────────────────

def test_trail_normal_distance_when_above_min():
    """
    trail_min_distance_pct=1.0 → _trail_min_distance = 1.0.
    Drive max_favorable to 130: gained=30, formula=0.15*30=4.5 > 1.0.
    Formula wins; trail_price=130-4.5=125.5.
    Low=126 does not fire; low=125.4 fires.
    """
    order = make_buy_trail(trail_min_distance_pct=1.0)
    arm_buy(order)
    # Drive max_favorable to 130 (candle 2)
    assert order.check(130.0, 128.0, 2, candle_open=129.0, candle_close=128.5) is None
    # Candle 3: trail_price=130-4.5=125.5; low=126 > 125.5 → no fire
    assert order.check(129.0, 126.0, 3, candle_open=129.0, candle_close=126.5) is None
    # Candle 4: low=125.4 ≤ 125.5 → fire
    result = order.check(127.0, 125.4, 4, candle_open=127.0, candle_close=125.6)
    assert result == 'trail'
    assert order.close_price == pytest.approx(125.5)


# ── test 6: both params together ─────────────────────────────────────────────

def test_both_params_together():
    """
    activation_pct=12.0, min_distance_pct=2.0.
    After arm_buy: gained=10 (10%<12%) → trail completely blocked.
    Candle 2: drive max_favorable to 113 (gained=13, 13%>=12%) → now active.
    trail_distance = max(0.15*13=1.95, 2.0) = 2.0; trail_price = 113-2.0 = 111.0.
    low=110.9 ≤ 111.0 → fire.
    """
    order = make_buy_trail(trail_activation_pct=12.0, trail_min_distance_pct=2.0)
    arm_buy(order)
    # Candle 2: high=113 → max_favorable=113, gained=13 ≥ 12%; trail_price=111.0; low=110.9 → fire
    result = order.check(113.0, 110.9, 2, candle_open=112.0, candle_close=111.1)
    assert result == 'trail'
    assert order.close_price == pytest.approx(111.0)


# ── test 7: SELL side activation ─────────────────────────────────────────────

def test_sell_trail_activation():
    """
    SELL activation_pct=1.0: trail fires once (entry-_max_favorable)/entry >= 1%.
    After arm_sell: _max_favorable=98, gained=2 (2%>=1%) → immediately active.

    On candle 2 _max_favorable is updated first (min with low), then trail checked.
    Candle 2: high=98.5, low=97.5 → _max_favorable=min(98,97.5)=97.5, gained=2.5.
    trail_distance=max(0.15*2.5=0.375, 0)=0.375; trail_price=97.5+0.375=97.875.
    high=98.5 >= 97.875 → fire at 97.875.
    """
    order = make_sell_trail(trail_activation_pct=1.0)
    arm_sell(order)
    result = order.check(98.5, 97.5, 2, candle_open=98.2, candle_close=97.8)
    assert result == 'trail'
    assert order.close_price == pytest.approx(97.875)


# ── test 8: get_state / from_state roundtrip ─────────────────────────────────

def test_get_state_roundtrip():
    """Both trail params survive a get_state() → from_state() round-trip."""
    original = FakeOrder(
        side='BUY',
        entry_price=100.0,
        tp=200.0,
        sl=80.0,
        level=1,
        signal_type='test',
        candle_index=0,
        partial_take_pct=0.10,
        trailing_stop_pct=0.15,
        trail_activation_pct=3.5,
        trail_min_distance_pct=1.2,
    )
    state = original.get_state()
    restored = FakeOrder.from_state(state)

    assert restored._trail_activation_pct == pytest.approx(3.5)
    # _trail_min_distance is stored as the absolute price distance: entry_price * pct / 100
    assert restored._trail_min_distance == pytest.approx(100.0 * 1.2 / 100.0)


# ── Trail-only presets (no partial_take_pct) arm off trail_activation_pct ────
#
# Regression tests for the dead-trail bug: presets like l2_regime_aware set
# trailing_stop_pct > 0 with partial_take_pct == 0. Before the fix the trail
# could never arm (arming required partial_price), so the only exits were a
# far TP or the SL — a real TIAUSDT position rode +11.66% back to a loss.


def make_trail_only(side: str, trail_activation_pct: float = 2.0) -> FakeOrder:
    tp = 200.0 if side == 'BUY' else 80.0
    sl = 80.0 if side == 'BUY' else 120.0
    return FakeOrder(
        side=side,
        entry_price=100.0,
        tp=tp,
        sl=sl,
        level=1,
        signal_type='test',
        candle_index=0,
        partial_take_pct=0.0,
        trailing_stop_pct=0.15,
        trail_activation_pct=trail_activation_pct,
        trail_min_distance_pct=0.0,
    )


def test_trail_only_buy_arms_at_activation_and_trails():
    """BUY with no partial: arms at entry*(1+activation%) and trails from there."""
    order = make_trail_only('BUY', trail_activation_pct=2.0)
    assert order._partial_price == pytest.approx(102.0)

    # Candle 1: reaches arm threshold — arms, must not trigger same candle.
    assert order.check(102.0, 100.5, 1) is None
    assert order._partial_armed is True

    # Candle 2: runs to 110, low stays above trail 110 - 0.15*(110-100) = 108.5.
    assert order.check(110.0, 109.0, 2) is None

    # Candle 3: retraces through the trail price.
    result = order.check(109.0, 108.0, 3)
    assert result == 'trail'
    assert order.close_price == pytest.approx(108.5)


def test_trail_only_sell_arms_at_activation_and_trails():
    """SELL mirror: arms at entry*(1-activation%), trails below."""
    order = make_trail_only('SELL', trail_activation_pct=2.0)
    assert order._partial_price == pytest.approx(98.0)

    assert order.check(99.5, 98.0, 1) is None
    assert order._partial_armed is True

    # Runs to 90, high stays below trail 90 + 0.15*(100-90) = 91.5.
    assert order.check(91.0, 90.0, 2) is None
    result = order.check(92.0, 91.0, 3)
    assert result == 'trail'
    assert order.close_price == pytest.approx(91.5)


def test_trail_only_without_activation_stays_dead():
    """No partial AND no activation pct: no arm threshold exists — trail stays
    inactive and the order exits only via TP/SL (documented legacy behavior)."""
    order = make_trail_only('BUY', trail_activation_pct=0.0)
    assert order._partial_price is None
    assert order.check(150.0, 100.5, 1) is None   # huge favorable move, no arm
    assert order._partial_armed is False
    assert order.check(200.0, 150.0, 2) == 'win'  # TP still works


def test_far_partial_arm_capped_by_activation_price():
    """Preset with partial AND trail AND activation: when the partial arm point
    (fraction of a far TP) sits beyond the activation price, arming happens at
    the activation price. Regression for the wide-TP presets (l2_bos_*) whose
    +4% favorable moves never armed because arm sat at 25% of a 20-35% TP."""
    order = FakeOrder(
        side='BUY', entry_price=100.0, tp=200.0, sl=80.0,
        level=1, signal_type='test', candle_index=0,
        partial_take_pct=0.60,        # raw arm would be 100 + 0.6*100 = 160
        trailing_stop_pct=0.15,
        trail_activation_pct=2.0,     # activation price 102 — closer, wins
    )
    assert order._partial_price == pytest.approx(102.0)

    assert order.check(102.0, 100.5, 1) is None    # arms at +2%
    assert order._partial_armed is True

    assert order.check(110.0, 109.0, 2) is None    # runs, trail = 108.5
    assert order.check(109.0, 108.0, 3) == 'trail'
    assert order.close_price == pytest.approx(108.5)


def test_near_partial_arm_unchanged_when_closer_than_activation():
    """When the partial arm point is CLOSER than the activation price, the
    original partial-price arming is preserved (no behavior change)."""
    order = FakeOrder(
        side='SELL', entry_price=100.0, tp=90.0, sl=120.0,
        level=1, signal_type='test', candle_index=0,
        partial_take_pct=0.10,        # arm at 100 - 0.1*10 = 99 (1% away)
        trailing_stop_pct=0.15,
        trail_activation_pct=2.0,     # activation price 98 — farther, ignored
    )
    assert order._partial_price == pytest.approx(99.0)


def test_partial_without_trail_unchanged():
    """Pure partial-take preset (no trail): arm price must stay the classic
    partial fraction — the activation cap only applies when a trail exists."""
    order = FakeOrder(
        side='BUY', entry_price=100.0, tp=200.0, sl=80.0,
        level=1, signal_type='test', candle_index=0,
        partial_take_pct=0.60,
        trailing_stop_pct=0.0,
        trail_activation_pct=2.0,
    )
    assert order._partial_price == pytest.approx(160.0)
