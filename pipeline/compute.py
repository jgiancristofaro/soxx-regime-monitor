"""
Main pipeline orchestrator: fetch OHLCV → compute signals → write JSON artifacts.
Run: python pipeline/compute.py
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"


def load_manual() -> dict:
    path = DATA_DIR / "manual.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def load_events() -> list:
    path = DATA_DIR / "events.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def main():
    sys.path.insert(0, str(ROOT))
    from pipeline.sources import fetch_ohlcv, load_fixture
    from pipeline.state_machine import compute_signals

    history_path = DATA_DIR / "history.csv"
    signals_path = DATA_DIR / "signals.json"
    stale = False

    print("Fetching OHLCV data...")
    try:
        df = fetch_ohlcv(days=420)
        df.to_csv(history_path)
        print(f"  Fetched {len(df)} rows, last session: {df.index[-1].date()}")
    except RuntimeError as e:
        print(f"  All live sources failed: {e}")
        if history_path.exists():
            print("  Falling back to committed history.csv")
            df = load_fixture(str(history_path))
            stale = True
        else:
            print("  No fallback available — aborting")
            sys.exit(1)

    manual = load_manual()
    events = load_events()

    print("Computing signals...")
    result = compute_signals(df, manual)
    result["generated_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result["data_stale"] = stale
    result["events"] = events

    signals_path.parent.mkdir(parents=True, exist_ok=True)
    with open(signals_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nSignals written to {signals_path}")
    print(f"  State: {result['state']['machine']}")
    print(f"  Since: {result['state']['since']}")
    print(f"  Last session: {result['last_session']}")
    print(f"  id20: {result['today']['id20']:.4f}")
    print(f"  on20: {result['today']['on20']:.4f}")
    print(f"  Trades: {len(result['trades'])}")


if __name__ == "__main__":
    main()
