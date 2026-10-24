"""Print the headline metrics table used in the README.

    python tools/headline.py [runs]
"""

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from kc.engine import monte_carlo
from kc.model import load_all

runs = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
scenarios = load_all("scenarios")

header = f"{'scenario':28s} {'p50':>8s} {'p95':>8s} {'p99':>8s} {'succ':>7s} {'recyc':>6s} {'net':>6s} {'blocked':>8s}"
print(header)
print("-" * len(header))

start = time.time()
for scenario in scenarios:
    mc = monte_carlo(scenario, runs=runs, seed=7)
    total = (sum(mc.time_in.values()) + mc.link_s) or 1
    blocked = (mc.outcomes.get("denied", 0) + mc.outcomes.get("abandoned", 0)) / mc.runs
    if mc.durations:
        latency = f"{mc.pct(0.50) / 60:7.1f}m {mc.pct(0.95) / 60:7.1f}m {mc.pct(0.99) / 60:7.1f}m "
        network = f"{mc.link_s / total * 100:5.1f}%"
    else:
        latency = f"{'n/a':>8s} {'n/a':>8s} {'n/a':>8s} "
        network = f"{'n/a':>6s}"
    print(
        f"{scenario.id:28s} "
        f"{latency}"
        f"{mc.success_rate * 100:6.1f}% "
        f"{mc.recycles:6.2f} "
        f"{network} "
        f"{blocked * 100:7.1f}%"
    )
print(f"\n{runs:,} runs each, seed 7, elapsed {time.time() - start:.1f}s")
