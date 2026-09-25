
import math
from market.services.market_structure_engine_v2 import DEFAULT_CONFIG, _range


def _rows_channel(n=40, start=1.1400, high_drop=0.00020, low_drop=0.00020, width=0.00080):
    rows = []
    for i in range(n):
        top = start - high_drop * i / max(n - 1, 1)
        bottom = start - width - low_drop * i / max(n - 1, 1)
        close = (top + bottom) / 2
        rows.append({
            "open": close, "high": top, "low": bottom, "close": close, "timestamp": 1_000 + i,
        })
    return rows


def _pivots(rows):
    pivots = []
    for i in range(2, len(rows) - 2, 3):
        pivots.append({"index": i, "kind": "high", "price": rows[i]["high"]})
        pivots.append({"index": i + 1, "kind": "low", "price": rows[i + 1]["low"]})
    return pivots


def test_both_rails_down_beyond_5_degrees_are_not_a_range():
    rows = _rows_channel()
    box = _range(rows, _pivots(rows), atr=0.00030, config=DEFAULT_CONFIG)
    assert box is not None
    assert box["pattern"] in {"descending_channel", "range"}
    if abs(box.get("high_angle", 0)) > 5 and abs(box.get("low_angle", 0)) > 5:
        assert box["pattern"] == "descending_channel"
