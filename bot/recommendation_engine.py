import math
from typing import List, Optional, Tuple

from bot.recommendation import Recommendation
from bot.trend import Trend
from config.settings import Settings


class RecommendationEngine:
    """
    Scores and selects the single best trading recommendation from all trend levels.

    Flow per candle:
      1. Traverse the trend chain (L1 → L2 → L3 …), collect one candidate per level.
      2. Discard candidates that fail structural sanity, minimum profit, or R:R filters.
      3. Score each survivor with a 0.0–1.0 precision score.
      4. Return the highest-precision candidate; break ties by R:R.

    All thresholds come from Settings so backtesting can sweep parameter sets
    without touching this class.
    """

    def __init__(self, settings: Settings):
        self._s = settings

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def generate(self, root_trend: Trend, entry_price: float) -> Optional[Recommendation]:
        """
        Returns the single best scored recommendation, or None if no valid
        candidate survives filtering.
        """
        candidates = self._collect(root_trend, entry_price)
        scored = self._score_and_filter(candidates)
        return self._select(scored)

    def collect_all(self, root_trend: Trend, entry_price: float) -> List[Recommendation]:
        """
        Returns all per-level candidates with precision/RR populated, before
        final selection filtering. Useful for dashboard display and analysis.
        """
        candidates = self._collect(root_trend, entry_price)
        return self._score_and_filter(candidates)

    # ------------------------------------------------------------------ #
    # Step 1 — collect one candidate per level                            #
    # ------------------------------------------------------------------ #

    def _collect(
        self, root_trend: Trend, entry_price: float
    ) -> List[Tuple[Recommendation, Trend, Optional[dict]]]:
        results: List[Tuple[Recommendation, Trend, Optional[dict]]] = []
        current: Optional[Trend] = root_trend
        pct = self._s.proximity_zone_pct

        while current is not None:
            correction_info = current.get_correction_end_info()
            n_points = len(current.getHighPoints()) + len(current.getLowPoints())
            if n_points >= self._s.min_swing_points:
                rec = current.getRecommendation(
                    entry_price=entry_price,
                    proximity_zone_pct=pct,
                    lower_high_sell=self._s.lower_high_sell,
                    higher_low_buy=self._s.higher_low_buy,
                )
                if rec is not None:
                    results.append((rec, current, correction_info))

            current = current.getBiggerTrend() if current.hasBiggerTrend() else None

        return results

    # ------------------------------------------------------------------ #
    # Step 2 — filter + score                                             #
    # ------------------------------------------------------------------ #

    def _score_and_filter(
        self, candidates: List[Tuple[Recommendation, Trend, Optional[dict]]]
    ) -> List[Recommendation]:
        scored: List[Recommendation] = []

        for rec, trend, correction_info in candidates:
            entry = rec.getEntryPrice()
            tp = rec.getTarget()
            sl = rec.getStop()

            if sl is None or entry == 0:
                continue

            profit_dist = abs(tp - entry)
            loss_dist = abs(sl - entry)

            if loss_dist == 0:
                continue

            # TP must be on the correct side of entry.
            if rec.getSide() == 'BUY':
                if tp <= entry or sl >= entry:
                    continue
            else:
                if tp >= entry or sl <= entry:
                    continue

            # Minimum profit percentage.
            profit_pct = profit_dist / entry * 100
            if profit_pct < self._s.min_profit_pct:
                continue

            # Minimum risk/reward ratio.
            rr = profit_dist / loss_dist
            if rr < self._s.min_profit_loss_ratio:
                continue

            precision = self._precision(rec, trend, correction_info)
            rec.setRR(rr).setPrecision(precision)
            scored.append(rec)

        return scored

    # ------------------------------------------------------------------ #
    # Step 3 — select best candidate                                      #
    # ------------------------------------------------------------------ #

    def _select(self, candidates: List[Recommendation]) -> Optional[Recommendation]:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        max_prec = max(c.getPrecision() for c in candidates)  # type: ignore[arg-type]
        threshold = self._s.precision_similarity_threshold

        # Candidates within the similarity band compete on projected profit.
        similar = [c for c in candidates if max_prec - c.getPrecision() <= threshold]  # type: ignore[operator]
        pool = similar if len(similar) > 1 else candidates

        return max(pool, key=lambda c: c.getRR() or 0.0)

    # ------------------------------------------------------------------ #
    # Precision scoring                                                    #
    # ------------------------------------------------------------------ #

    def _precision(self, rec: Recommendation, trend: Trend, correction_info: Optional[dict] = None) -> float:
        reliability = self._projection_reliability(trend) * 0.40
        alignment = self._parent_alignment(trend, rec.getSide())   # already 0 / 0.175 / 0.35
        entry_quality = self._entry_quality(rec.getHowClose()) * 0.25
        correction_bonus = self._correction_quality(correction_info) * self._s.correction_weight
        return round(reliability + alignment + entry_quality + correction_bonus, 4)

    def _projection_reliability(self, trend: Trend) -> float:
        """
        Coefficient-of-variation of recent swing amplitudes: lower spread → higher score.
        Uses the last PROJECTION_LOOKBACK highs and lows.
        """
        lookback = self._s.projection_lookback
        highs = trend.getHighPoints()[-lookback:]
        lows = trend.getLowPoints()[-lookback:]

        high_diffs = trend.getPointsDifferences(highs, True)
        low_diffs = trend.getPointsDifferences(lows, False)
        all_diffs = high_diffs + low_diffs

        if len(all_diffs) < 2:
            return 0.0

        mean = sum(all_diffs) / len(all_diffs)
        if mean == 0:
            return 0.0

        variance = sum((d - mean) ** 2 for d in all_diffs) / len(all_diffs)
        cv = math.sqrt(variance) / mean
        return 1.0 / (1.0 + cv)

    def _parent_alignment(self, trend: Trend, side: str) -> float:
        """
        0.35 if parent trend agrees with the signal direction,
        0.175 if no parent or parent undetermined,
        0.0 if parent opposes.
        """
        bigger = trend.getBiggerTrend() if trend.hasBiggerTrend() else None
        if bigger is None or not bigger.hasDefinedTrend():
            return 0.175
        aligned = bigger.isAscending() == (side == 'BUY')
        return 0.35 if aligned else 0.0

    def _entry_quality(self, how_close: float) -> float:
        """
        Proximity score: 1.0 at the boundary, 0.0 at the threshold edge.
        how_close is % of swing range from the boundary (from whichIsCloser).
        """
        pct = self._s.proximity_zone_pct
        return max(0.0, 1.0 - how_close / pct)

    def _correction_quality(self, info: Optional[dict]) -> float:
        """
        0.0–1.0 quality score for a correction that just ended.

        Returns 0 immediately for depth > 100% (price went past impulse start —
        that is a potential reversal, not a correction) or depth < 30% (noise).

        swing_score: 0.0 at 1 swing, 0.5 at 2, 1.0 at 3+ swings.
        depth_score: peaks at 1.0 at the 50% Fibonacci level; tapers to 0 at 30% and 80%.

        Final quality is multiplicative — both axes must contribute for a non-zero result.
        """
        if info is None or self._s.correction_weight == 0.0:
            return 0.0
        depth_pct = info['depth_pct']
        if depth_pct > 100.0 or depth_pct < 30.0:
            return 0.0
        swing_score = min(1.0, max(0.0, (info['swing_count'] - 1) / 2.0))
        if depth_pct <= 50.0:
            depth_score = (depth_pct - 30.0) / 20.0
        else:
            depth_score = (80.0 - depth_pct) / 30.0
        depth_score = max(0.0, depth_score)
        return swing_score * depth_score
