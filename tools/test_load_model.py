"""Contract tests for shared, policy-neutral LOAD estimates."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "hoymiles_hit_modbus"))

from load_model import (  # noqa: E402
    robust_weighted_estimate,
    robust_weighted_upper_estimate,
)


def main() -> None:
    assert robust_weighted_estimate([]) == (None, 0.0, 0)
    assert robust_weighted_upper_estimate([]) == (None, 0)

    sample = [30.0] * 20 + [31.0, 29.0, 30.5, 200.0]
    expected, uncertainty, count = robust_weighted_estimate(sample)
    upper, upper_count = robust_weighted_upper_estimate(sample)
    assert expected is not None and 29.0 <= expected <= 34.0
    assert 0.0 <= uncertainty <= 1.0
    assert upper is not None and expected <= upper <= 52.5
    assert count == upper_count == len(sample)

    # Keep only the newest 28 complete, physically credible days.
    long_sample = [10.0] * 10 + [20.0] * 28
    expected, _, count = robust_weighted_estimate(long_sample)
    assert expected == 20.0 and count == 28

    print("Shared LOAD model: robust estimate contracts passed")


if __name__ == "__main__":
    main()
