"""
Symbol Discovery runner — spawned by the dashboard's /api/discovery/run endpoint.

Reads config from data/discovery_config.json.
Writes progress to dashboard/public/discovery_state.json (atomic).
Writes passing candidates to dashboard/public/discovery_candidates.json.
Cancellable via SIGTERM.
"""
import json
import logging
import os
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from config.settings import load_symbols
from bot.symbol_discovery import SymbolDiscovery
from config.presets import ALL_PRESETS, LOCKED_PRESETS, PRESETS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("discover")

_STATE_PATH = Path("dashboard") / "public" / "discovery_state.json"
_CANDIDATES_PATH = Path("dashboard") / "public" / "discovery_candidates.json"
_CONFIG_PATH = Path("data") / "discovery_config.json"
_TEMP_DIR = Path("data") / "discovery"


def _write_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _read_state() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text())
    except Exception:
        return {}


def _update_state(**kwargs) -> None:
    state = _read_state()
    state.update(kwargs)
    _write_atomic(_STATE_PATH, state)


def main() -> None:
    # Read config written by the API route.
    try:
        cfg = json.loads(_CONFIG_PATH.read_text())
    except Exception as exc:
        logger.error(f"Cannot read discovery config: {exc}")
        sys.exit(1)

    min_volume: float = float(cfg.get("min_volume", 1_000_000))
    preset_count: int = int(cfg.get("preset_count", 12))
    batch_size: int = int(cfg.get("batch_size", 3))
    baseline_ratio: float = float(cfg.get("baseline_ratio", 0.7))
    min_floor: float = float(cfg.get("min_floor", 0.0))
    position_size: float = float(cfg.get("position_size", 1000.0))
    leverage: float = float(cfg.get("leverage", 1.0))
    klines_count: int = int(cfg.get("klines_count", 500))

    stop_event = threading.Event()

    def _handle_sigterm(signum, frame):  # noqa: ANN001
        logger.info("SIGTERM received — stopping discovery")
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_sigterm)

    # Clean up stale temp files from any previous crashed run.
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    for f in _TEMP_DIR.glob("*.json"):
        try:
            f.unlink()
        except Exception:
            pass

    discovery = SymbolDiscovery()

    # Load active symbols from registry.
    try:
        import json as _j
        reg = _j.loads(Path("symbol_registry.json").read_text())
        active = reg.get("symbols", [])
    except Exception:
        active = load_symbols()

    # Get pre-candidates.
    try:
        precandidates = discovery.get_precandidates(active, min_volume)
    except RuntimeError as exc:
        _update_state(status="error", error=str(exc), in_progress=[])
        logger.error(str(exc))
        sys.exit(1)

    _update_state(total_precandidates=len(precandidates), processed_count=0, in_progress=[])
    logger.info(f"Found {len(precandidates)} pre-candidates: {precandidates}")

    if not precandidates:
        _update_state(
            status="complete",
            in_progress=[],
            last_run_timestamp=datetime.now(timezone.utc).isoformat(),
        )
        _write_atomic(_CANDIDATES_PATH, {"generated_at": datetime.now(timezone.utc).isoformat(), "candidates": []})
        return

    # Fast preset subset.
    all_preset_names = list(ALL_PRESETS.keys())
    fast_preset_names = discovery.get_fast_presets(active, preset_count, all_preset_names)
    preset_subset = {n: ALL_PRESETS[n] for n in fast_preset_names if n in ALL_PRESETS}
    logger.info(f"Fast preset subset ({len(preset_subset)}): {list(preset_subset)}")

    baseline = discovery.compute_baseline(active)
    logger.info(f"Baseline efficiency: {baseline:.4f}")

    passing: list[dict] = []
    processed = 0
    in_progress: list[str] = []
    lock = threading.Lock()

    def _score(symbol: str) -> tuple[str, object]:
        return symbol, discovery.score_candidate(
            symbol=symbol,
            preset_subset=preset_subset,
            klines_count=klines_count,
            baseline=baseline,
            baseline_ratio=baseline_ratio,
            min_floor=min_floor,
            position_size=position_size,
            leverage=leverage,
        )

    futures = {}
    with ThreadPoolExecutor(max_workers=batch_size) as executor:
        for sym in precandidates:
            if stop_event.is_set():
                break
            future = executor.submit(_score, sym)
            futures[future] = sym
            with lock:
                in_progress.append(sym)
            _update_state(in_progress=list(in_progress))

        for future in as_completed(futures):
            sym = futures[future]
            try:
                _, result = future.result()
                if result is not None:
                    passing.append(result.to_dict())
                    logger.info(f"[{sym}] PASSED — efficiency={result.efficiency_score:.4f}")
                else:
                    logger.info(f"[{sym}] filtered out")
            except Exception as exc:
                logger.warning(f"[{sym}] error during scoring: {exc}")

            with lock:
                processed += 1
                if sym in in_progress:
                    in_progress.remove(sym)

            _update_state(processed_count=processed, in_progress=list(in_progress))

    final_status = "cancelled" if stop_event.is_set() else "complete"
    now = datetime.now(timezone.utc).isoformat()
    _update_state(status=final_status, in_progress=[], last_run_timestamp=now)
    _write_atomic(_CANDIDATES_PATH, {"generated_at": now, "candidates": passing})
    logger.info(f"Discovery {final_status}: {len(passing)} candidates")


if __name__ == "__main__":
    main()
