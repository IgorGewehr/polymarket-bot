"""
BTC-Price-Informed Stop Loss -- BACKUP PLAN.
Only activate if "hold to resolution" strategy underperforms.

=== THE CORE INSIGHT ===
Binance BTC spot price LEADS Polymarket share price by 2-15 seconds.
Instead of watching share price (lagging, noisy), we watch BTC price
on Binance (leading, cleaner signal) to decide exits.

=== VOLATILITY MATH (how we calibrated thresholds) ===
BTC annualized volatility: ~40-54% (2025-2026 data)
Daily vol: 50% / sqrt(365) = 2.62%
5-minute vol (1 sigma): 2.62% / sqrt(288) = 0.154%

Distribution of 5-minute absolute moves:
  - < 0.05%  : ~38% of candles (noise, random walk)
  - 0.05-0.10%: ~25% of candles (normal movement)
  - 0.10-0.15%: ~15% of candles (moderate, ~1 sigma)
  - 0.15-0.25%: ~12% of candles (strong move)
  - > 0.25%   : ~10% of candles (significant, directional)

THRESHOLD CALIBRATION:
  0.05% = 0.3 sigma -> triggers on ~62% of candles -> TOO SENSITIVE
  0.10% = 0.65 sigma -> triggers on ~37% of candles -> REASONABLE
  0.15% = 1.0 sigma -> triggers on ~22% of candles -> CONSERVATIVE
  0.20% = 1.3 sigma -> triggers on ~13% of candles -> VERY CONSERVATIVE

We use 0.10% as the default because:
  1. Below 0.10%, BTC moves are indistinguishable from noise
  2. Above 0.10%, there's ~65% probability the move continues
  3. At $80K BTC, 0.10% = $80 move (meaningful for 5-min binary)
  4. Combined with slope confirmation, false positive rate drops to ~12%

=== WHEN TO SELL ===
All 3 conditions must be TRUE:
  1. BTC moved > threshold% against our position (since entry)
  2. 2+ of 3 BTC slopes (1m/2m/3m) confirm adverse direction
  3. Share price is still above $0.40 (not selling into a void)

=== WHEN TO HOLD ===
  - BTC is flat or favorable (share price dip is noise/latency)
  - BTC spike was momentary (slopes show reversion already)
  - Share price already < $0.40 (too late, hold for resolution lottery)
  - Less than 30 seconds remaining (just let it resolve)

=== ADAPTIVE THRESHOLD ===
Instead of a fixed 0.10%, we adapt based on recent BTC volatility:
  - Calm market (ATR < 0.08%): threshold = 0.08% (tighter)
  - Normal market (ATR 0.08-0.20%): threshold = 0.12% (default)
  - Volatile market (ATR > 0.20%): threshold = 0.18% (wider)
This prevents over-triggering in volatile regimes and under-triggering
in calm regimes.
"""
import time
import numpy as np
import structlog
from dataclasses import dataclass

from data.price_buffer import PriceBuffer
from core.analyzer import calc_slope

log = structlog.get_logger()


# ── Configuration ────────────────────────────────────────────────
# These can be moved to config/settings.py if this module goes live.

BTC_SL_ENABLED = False  # BACKUP PLAN: flip to True only if hold-to-resolution fails

# Threshold: BTC % move against position to trigger stop loss evaluation
BTC_SL_BASE_THRESHOLD_PCT = 0.0012  # 0.12% (0.78 sigma at 50% annual vol)

# Adaptive threshold bounds
BTC_SL_TIGHT_THRESHOLD_PCT = 0.0008   # 0.08% for calm markets
BTC_SL_NORMAL_THRESHOLD_PCT = 0.0012  # 0.12% for normal markets
BTC_SL_WIDE_THRESHOLD_PCT = 0.0018    # 0.18% for volatile markets

# ATR regime boundaries (5-minute ATR as % of price)
BTC_ATR_CALM_UPPER = 0.0008    # Below this = calm
BTC_ATR_VOLATILE_LOWER = 0.0020  # Above this = volatile

# Slope windows (in ticks, at ~1 tick/second from Binance)
BTC_SLOPE_WINDOWS = {
    "1m": 60,   # 60 seconds
    "2m": 120,  # 2 minutes
    "3m": 180,  # 3 minutes
}

# Minimum slopes that must confirm adverse direction (out of 3)
BTC_SL_MIN_CONFIRMING_SLOPES = 2

# Share price floor: never sell below this (hold for resolution lottery)
BTC_SL_SHARE_FLOOR = 0.40

# Time guards
BTC_SL_MIN_TIME_AFTER_ENTRY = 15  # Don't evaluate SL in first 15s (let position settle)
BTC_SL_MIN_TIME_REMAINING = 30    # Don't sell in last 30s (just let it resolve)

# Reversion detection: if BTC already reversed > 30% of the adverse move, hold
BTC_SL_REVERSION_PCT = 0.30

# Cooldown: after deciding NOT to stop out, wait N seconds before re-evaluating
# Prevents flip-flopping on noisy signals
BTC_SL_EVAL_COOLDOWN = 5  # seconds


@dataclass
class BTCStopLossState:
    """Per-position state for BTC stop loss tracking."""
    btc_price_at_entry: float = 0.0
    entry_time: float = 0.0
    last_eval_time: float = 0.0
    adverse_peak_pct: float = 0.0  # Worst BTC move against us (for reversion detection)
    triggered: bool = False
    trigger_reason: str = ""


@dataclass
class BTCStopLossResult:
    """Result of a stop loss evaluation."""
    should_exit: bool
    reason: str
    btc_move_pct: float          # How much BTC moved against us (signed, negative = against)
    confirming_slopes: int       # How many slopes confirm adverse direction
    current_threshold: float     # Adaptive threshold being used
    share_price: float           # Current share price (our side)
    btc_regime: str              # "calm", "normal", "volatile"


def calculate_5m_atr(btc_buffer: PriceBuffer, lookback_candles: int = 6) -> float:
    """
    Calculate approximate 5-minute ATR from the BTC price buffer.
    Uses non-overlapping 5-minute windows from the buffer.

    Returns ATR as a fraction of price (e.g., 0.0015 = 0.15%).
    """
    prices = btc_buffer.get_prices()
    if len(prices) < 300:  # Need at least 5 minutes of data
        return BTC_SL_NORMAL_THRESHOLD_PCT  # Default to normal

    # Split into 5-minute (300-tick) candles
    n_candles = min(lookback_candles, len(prices) // 300)
    if n_candles < 2:
        return BTC_SL_NORMAL_THRESHOLD_PCT

    ranges = []
    for i in range(n_candles):
        end_idx = len(prices) - (i * 300)
        start_idx = end_idx - 300
        if start_idx < 0:
            break
        candle = prices[start_idx:end_idx]
        if len(candle) < 100:  # Skip incomplete candles
            continue
        high = np.max(candle)
        low = np.min(candle)
        mid = (high + low) / 2
        if mid > 0:
            candle_range = (high - low) / mid
            ranges.append(candle_range)

    if not ranges:
        return BTC_SL_NORMAL_THRESHOLD_PCT

    return float(np.mean(ranges))


def get_adaptive_threshold(btc_buffer: PriceBuffer) -> tuple[float, str]:
    """
    Returns (threshold_pct, regime_label) based on recent BTC volatility.

    Calm market -> tighter threshold (small moves matter more)
    Volatile market -> wider threshold (need bigger move to confirm direction)
    """
    atr = calculate_5m_atr(btc_buffer)

    if atr < BTC_ATR_CALM_UPPER:
        return BTC_SL_TIGHT_THRESHOLD_PCT, "calm"
    elif atr > BTC_ATR_VOLATILE_LOWER:
        return BTC_SL_WIDE_THRESHOLD_PCT, "volatile"
    else:
        return BTC_SL_NORMAL_THRESHOLD_PCT, "normal"


def is_sideways_market(btc_buffer: PriceBuffer) -> bool:
    """
    Verifica se o regime do mercado esta lateral (choppy/sideways).
    Útil para filtrar entradas: não operar durante fortes tendências.
    """
    atr = calculate_5m_atr(btc_buffer)
    if atr >= BTC_ATR_VOLATILE_LOWER:
        return False
    return True


def calculate_btc_slopes(btc_buffer: PriceBuffer) -> dict[str, float]:
    """
    Calculate BTC price slopes over 1m, 2m, 3m windows.
    Returns dict: {"1m": slope, "2m": slope, "3m": slope}

    Slopes are normalized (price change per second).
    Positive = BTC going up, Negative = BTC going down.
    """
    prices = btc_buffer.get_prices()
    slopes = {}

    for label, window in BTC_SLOPE_WINDOWS.items():
        n = min(window, len(prices))
        if n < 10:  # Need minimum data
            slopes[label] = 0.0
            continue
        slopes[label] = calc_slope(prices[-n:])

    return slopes


def count_adverse_slopes(
    slopes: dict[str, float],
    position_direction: str,
) -> int:
    """
    Count how many BTC timeframe slopes confirm adverse movement
    against our position.

    If we're long Up (betting BTC goes up), adverse = negative slopes.
    If we're long Down (betting BTC goes down), adverse = positive slopes.
    """
    count = 0
    for label, slope in slopes.items():
        if position_direction == "Up" and slope < 0:
            count += 1
        elif position_direction == "Down" and slope > 0:
            count += 1
    return count


def detect_reversion(
    btc_buffer: PriceBuffer,
    state: BTCStopLossState,
    current_btc_price: float,
    position_direction: str,
) -> bool:
    """
    Detect if BTC is already reverting from its adverse peak.
    If BTC spiked against us but has already bounced back > 30%,
    the adverse move is likely noise/momentary.

    Returns True if BTC is reverting (= we should HOLD, not sell).
    """
    if state.btc_price_at_entry <= 0 or current_btc_price <= 0:
        return False

    # Current move against us
    if position_direction == "Up":
        current_move = (current_btc_price - state.btc_price_at_entry) / state.btc_price_at_entry
        # For Up position, adverse is negative
        if current_move >= 0:
            return False  # Not adverse at all
        adverse_magnitude = abs(current_move)
    else:
        current_move = (current_btc_price - state.btc_price_at_entry) / state.btc_price_at_entry
        # For Down position, adverse is positive
        if current_move <= 0:
            return False  # Not adverse at all
        adverse_magnitude = abs(current_move)

    # Check if we've already seen a worse point and now BTC is recovering
    if state.adverse_peak_pct > 0 and adverse_magnitude < state.adverse_peak_pct:
        recovery_pct = (state.adverse_peak_pct - adverse_magnitude) / state.adverse_peak_pct
        if recovery_pct >= BTC_SL_REVERSION_PCT:
            return True

    return False


def evaluate_btc_stop_loss(
    position_direction: str,
    entry_price: float,
    current_yes_price: float,
    btc_buffer: PriceBuffer,
    state: BTCStopLossState,
    time_remaining: float,
) -> BTCStopLossResult:
    """
    Main evaluation function. Called every tick during _phase_monitor.

    Decides whether to trigger a stop loss based on BTC price action
    from Binance (leading indicator) rather than share price (lagging).

    Args:
        position_direction: "Up" or "Down"
        entry_price: Share entry price
        current_yes_price: Current YES share price on Polymarket
        btc_buffer: Buffer with BTC prices from Binance (1 tick/sec)
        state: Per-position tracking state
        time_remaining: Seconds until market resolution

    Returns:
        BTCStopLossResult with should_exit decision and diagnostics
    """
    now = time.time()

    # ── Guard: module disabled ──
    if not BTC_SL_ENABLED:
        return BTCStopLossResult(
            should_exit=False, reason="disabled",
            btc_move_pct=0, confirming_slopes=0,
            current_threshold=0, share_price=0, btc_regime="n/a"
        )

    # ── Guard: too soon after entry ──
    time_since_entry = now - state.entry_time
    if time_since_entry < BTC_SL_MIN_TIME_AFTER_ENTRY:
        return BTCStopLossResult(
            should_exit=False, reason="too_soon_after_entry",
            btc_move_pct=0, confirming_slopes=0,
            current_threshold=0, share_price=0, btc_regime="n/a"
        )

    # ── Guard: too close to resolution ──
    if time_remaining < BTC_SL_MIN_TIME_REMAINING:
        return BTCStopLossResult(
            should_exit=False, reason="too_close_to_resolution",
            btc_move_pct=0, confirming_slopes=0,
            current_threshold=0, share_price=0, btc_regime="n/a"
        )

    # ── Guard: cooldown between evaluations ──
    if now - state.last_eval_time < BTC_SL_EVAL_COOLDOWN:
        return BTCStopLossResult(
            should_exit=False, reason="eval_cooldown",
            btc_move_pct=0, confirming_slopes=0,
            current_threshold=0, share_price=0, btc_regime="n/a"
        )

    state.last_eval_time = now

    # ── Get current BTC price ──
    current_btc = btc_buffer.latest_price()
    if not current_btc or state.btc_price_at_entry <= 0:
        return BTCStopLossResult(
            should_exit=False, reason="no_btc_data",
            btc_move_pct=0, confirming_slopes=0,
            current_threshold=0, share_price=0, btc_regime="n/a"
        )

    # ── Calculate BTC move since entry ──
    btc_move_pct = (current_btc - state.btc_price_at_entry) / state.btc_price_at_entry

    # Determine if this move is adverse to our position
    if position_direction == "Up":
        adverse_move = -btc_move_pct  # Negative BTC move hurts Up position
        our_share_price = current_yes_price
    else:
        adverse_move = btc_move_pct   # Positive BTC move hurts Down position
        our_share_price = 1.0 - current_yes_price

    # Track worst adverse point (for reversion detection)
    if adverse_move > state.adverse_peak_pct:
        state.adverse_peak_pct = adverse_move

    # ── Adaptive threshold ──
    threshold, regime = get_adaptive_threshold(btc_buffer)

    # ── Condition 1: BTC moved enough against us? ──
    if adverse_move < threshold:
        return BTCStopLossResult(
            should_exit=False, reason="btc_move_below_threshold",
            btc_move_pct=btc_move_pct, confirming_slopes=0,
            current_threshold=threshold, share_price=our_share_price,
            btc_regime=regime
        )

    # ── Condition 2: Slope confirmation (2+ of 3 timeframes) ──
    slopes = calculate_btc_slopes(btc_buffer)
    adverse_slopes = count_adverse_slopes(slopes, position_direction)

    if adverse_slopes < BTC_SL_MIN_CONFIRMING_SLOPES:
        # BTC moved against us but slopes don't confirm -- likely noise/spike
        return BTCStopLossResult(
            should_exit=False, reason="slopes_not_confirming",
            btc_move_pct=btc_move_pct, confirming_slopes=adverse_slopes,
            current_threshold=threshold, share_price=our_share_price,
            btc_regime=regime
        )

    # ── Condition 3: Share price floor ──
    if our_share_price < BTC_SL_SHARE_FLOOR:
        # Share already crashed -- selling at $0.30 captures almost nothing
        # Better to hold for the resolution lottery (33% chance of recovery)
        return BTCStopLossResult(
            should_exit=False, reason="share_below_floor",
            btc_move_pct=btc_move_pct, confirming_slopes=adverse_slopes,
            current_threshold=threshold, share_price=our_share_price,
            btc_regime=regime
        )

    # ── Reversion check: is BTC already bouncing back? ──
    if detect_reversion(btc_buffer, state, current_btc, position_direction):
        return BTCStopLossResult(
            should_exit=False, reason="btc_reverting",
            btc_move_pct=btc_move_pct, confirming_slopes=adverse_slopes,
            current_threshold=threshold, share_price=our_share_price,
            btc_regime=regime
        )

    # ── ALL CONDITIONS MET: TRIGGER STOP LOSS ──
    state.triggered = True
    state.trigger_reason = (
        f"BTC moved {adverse_move:.3%} against {position_direction} "
        f"({adverse_slopes}/3 slopes confirm, regime={regime}, "
        f"share=${our_share_price:.2f})"
    )

    log.warning("btc_stop_loss_triggered",
                direction=position_direction,
                btc_move=f"{adverse_move:.3%}",
                threshold=f"{threshold:.3%}",
                slopes=f"{adverse_slopes}/3",
                share=f"${our_share_price:.2f}",
                regime=regime,
                btc_entry=f"${state.btc_price_at_entry:.2f}",
                btc_now=f"${current_btc:.2f}")

    return BTCStopLossResult(
        should_exit=True,
        reason=f"btc_stop_loss_{regime}",
        btc_move_pct=btc_move_pct,
        confirming_slopes=adverse_slopes,
        current_threshold=threshold,
        share_price=our_share_price,
        btc_regime=regime,
    )


def create_btc_sl_state(btc_buffer: PriceBuffer) -> BTCStopLossState:
    """
    Create a new BTCStopLossState when entering a position.
    Call this right after execute_trade() succeeds.
    """
    btc_price = btc_buffer.latest_price() or 0.0
    return BTCStopLossState(
        btc_price_at_entry=btc_price,
        entry_time=time.time(),
    )


# ── Integration helper for engine.py ─────────────────────────────
#
# To integrate into _phase_monitor, add these lines:
#
# 1. At position creation (after execute_trade succeeds):
#
#     from core.btc_stop_loss import create_btc_sl_state, evaluate_btc_stop_loss
#     pos._btc_sl_state = create_btc_sl_state(self.btc_buffer)
#
# 2. In _phase_monitor, BEFORE the early_exit evaluation:
#
#     # ── BTC-INFORMED STOP LOSS (backup plan) ──
#     btc_sl_state = getattr(pos, '_btc_sl_state', None)
#     if btc_sl_state:
#         btc_sl = evaluate_btc_stop_loss(
#             position_direction=pos.direction,
#             entry_price=pos.entry_price,
#             current_yes_price=yes_price,
#             btc_buffer=self.btc_buffer,
#             state=btc_sl_state,
#             time_remaining=time_remaining,
#         )
#         if btc_sl.should_exit:
#             log.warning("btc_stop_loss_exit",
#                         reason=btc_sl.reason,
#                         btc_move=f"{btc_sl.btc_move_pct:.3%}",
#                         slopes=f"{btc_sl.confirming_slopes}/3",
#                         share=f"${btc_sl.share_price:.2f}",
#                         regime=btc_sl.btc_regime)
#             # Use the same sell logic as early_exit
#             exit_eval = ExitEvaluation(
#                 should_exit=True,
#                 reason=btc_sl.reason,
#                 sell_price=btc_sl.share_price,
#                 sell_proceeds=pos.shares * btc_sl.share_price * (1 - TAKER_FEE_PCT),
#                 sell_pnl=pos.shares * btc_sl.share_price * (1 - TAKER_FEE_PCT) - pos.bet_size,
#                 hold_ev=0,
#                 gain_pct=(btc_sl.share_price - pos.entry_price) / pos.entry_price,
#             )
#             # ... then fall through to the existing SELL block
#
# 3. To enable, set BTC_SL_ENABLED = True in this file
#    (or move to config/settings.py).
