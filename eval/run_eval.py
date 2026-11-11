"""Run every chaos scenario several times and compare BlackBox with the recorded ground truth.

    python eval/run_eval.py            # 5 scenarios x 10 seeds
    python eval/run_eval.py --seeds 10

Writes eval/results.md.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "demo"))

from app.importer import import_bundle  # noqa: E402
from app.pipeline import analyze  # noqa: E402
from app.postmortem import build_postmortem  # noqa: E402
from app.store import Store  # noqa: E402
from simulate import SCENARIOS, simulate  # noqa: E402


TUNED_SEEDS = 4  # seeds 1-4 were inspected while building the analyzer


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def err_min(a: datetime | None, b: str) -> float | None:
    return None if a is None else abs((a - ts(b)).total_seconds()) / 60


def true_rank(analysis, truth) -> int | None:
    trig = truth["trigger"]
    for c in analysis.triggers:
        if trig is None and c.event_id is None:
            return c.rank
        if trig and c.event_id and c.service == trig["service"] and trig["version"] in c.title:
            return c.rank
    return None


def fmt(x: float | None, digits: int = 1) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


def main(seeds: int) -> None:
    rows, failures = [], []
    started = time.time()
    for scenario in SCENARIOS:
        for seed in range(1, seeds + 1):
            st = Store(":memory:")
            with tempfile.TemporaryDirectory() as tmp:
                truth = simulate(scenario, seed, Path(tmp))
                inc = st.create_incident(scenario, scenario=scenario, ground_truth=truth)
                import_bundle(st, inc.id, Path(tmp))
            a = analyze(st, inc.id)
            pm = build_postmortem(st, a, scenario)
            sentences = [s for ss in pm.sections.values() for s in ss]
            rank = true_rank(a, truth)
            row = {
                "scenario": scenario, "seed": seed, "rank": rank,
                "root_ok": a.first_failing_service == truth["root_service"],
                "start_err": err_min(a.start, truth["start"]), "detect_err": err_min(a.detection, truth["detect"]),
                "mit_err": err_min(a.mitigation, truth["mitigation"]), "rec_err": err_min(a.recovery, truth["recovery"]),
                "lines": a.log_lines, "items": a.timeline_items, "moments": len(a.key_moments),
                "evidence": sum(s.verified for s in sentences) / max(1, len(sentences)),
                "ttd": a.time_to_detect_min, "ttr": a.time_to_recover_min,
            }
            rows.append(row)
            if rank != 1:
                top = a.triggers[0]
                failures.append(f"`{scenario}` seed {seed}: true trigger ranked {rank or 'not found'}; "
                                f"top was \"{top.title[:80]}\" (score {top.score}: {'; '.join(top.evidence[:2])})")
            if not row["root_ok"]:
                failures.append(f"`{scenario}` seed {seed}: first failing service {a.first_failing_service}, truth {truth['root_service']}")
            if row["rec_err"] is None or row["rec_err"] > 5:
                failures.append(f"`{scenario}` seed {seed}: recovery time off by {fmt(row['rec_err'])} min")
            print(f"{scenario:18} seed {seed}: rank {rank}, root {'ok' if row['root_ok'] else 'WRONG'}, "
                  f"start ±{fmt(row['start_err'])}m, recover ±{fmt(row['rec_err'])}m, {row['lines']} lines -> {row['items']} items")

    def agg(rs: list[dict]) -> dict:
        def mean(k: str) -> float | None:
            v = [r[k] for r in rs if r[k] is not None]
            return statistics.mean(v) if v else None
        return {
            "n": len(rs), "top1": sum(r["rank"] == 1 for r in rs), "top3": sum(r["rank"] is not None and r["rank"] <= 3 for r in rs),
            "root": sum(r["root_ok"] for r in rs), "start": mean("start_err"), "detect": mean("detect_err"),
            "mit": mean("mit_err"), "rec": mean("rec_err"), "lines": mean("lines"), "items": mean("items"),
            "moments": mean("moments"), "evidence": mean("evidence"),
        }

    total = agg(rows)
    held = agg([r for r in rows if r["seed"] > TUNED_SEEDS]) if seeds > TUNED_SEEDS else None
    lines = [
        "# BlackBox evaluation results", "",
        f"{total['n']} injected incidents ({len(SCENARIOS)} scenarios x {seeds} seeds), run in {time.time() - started:.0f}s with the "
        "offline engine. Each run has two decoy changes (random services, random times, sometimes on the failing service) "
        "next to the real trigger.", "",
        "## Overall", "",
        "| Metric | Result |", "|---|---|",
        f"| True trigger ranked first | **{total['top1']} of {total['n']}** ({total['top1'] / total['n']:.0%}) |",
        f"| True trigger in top 3 | {total['top3']} of {total['n']} |",
        f"| First failing service correct | {total['root']} of {total['n']} |",
        f"| Mean error in detected start | {fmt(total['start'], 2)} min |",
        f"| Mean error in detection time | {fmt(total['detect'], 2)} min |",
        f"| Mean error in mitigation time | {fmt(total['mit'], 2)} min |",
        f"| Mean error in recovery time | {fmt(total['rec'], 2)} min |",
        f"| Log lines in, timeline items out (mean) | {total['lines']:,.0f} lines -> {total['items']:.0f} items ({total['moments']:.0f} key moments) |",
        f"| Postmortem sentences with valid evidence | {total['evidence']:.0%} |", "",
    ]
    if held:
        lines += [f"Held-out runs only (seeds {TUNED_SEEDS + 1} to {seeds}, never looked at while the rules were tuned): "
                  f"trigger ranked first in **{held['top1']} of {held['n']}**, first failing service correct in "
                  f"{held['root']} of {held['n']}, mean recovery-time error {fmt(held['rec'], 2)} min.", ""]
    lines += [
        "## Per scenario", "",
        "| Scenario | Runs | Trigger #1 | Top 3 | Root service | Start err (min) | Recovery err (min) | Lines -> items |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for sc in SCENARIOS:
        g = agg([r for r in rows if r["scenario"] == sc])
        lines.append(f"| `{sc}` | {g['n']} | {g['top1']}/{g['n']} | {g['top3']}/{g['n']} | {g['root']}/{g['n']} | "
                     f"{fmt(g['start'], 2)} | {fmt(g['rec'], 2)} | {g['lines']:,.0f} -> {g['items']:.0f} |")
    lines += ["", "Scenario notes:", ""]
    lines += [f"- `{k}`: {v}" for k, v in SCENARIOS.items()]
    lines += ["", "## Failure cases", ""]
    lines += [f"- {f}" for f in failures] or ["- None in this run. More seeds or new scenarios will find some; see limitations in the README."]
    lines += ["", "## Honest notes", "",
              "- These are simulated incidents. The simulator and the analyzer were written by the same person, so real",
              "  incidents will be messier (clock skew between hosts, missing logs, several changes at once).",
              "- Timing errors are measured against the simulator's exact fault times; BlackBox works at one-minute",
              "  resolution for spikes, so errors under one minute are expected.",
              "- The `slow_dependency` scenario has no internal trigger: a correct answer is the \"outside cause\" candidate.",
              "- Detection error is zero by construction: both the simulator and BlackBox define detection as the first",
              "  firing alert. It checks that alerts are ingested and timed correctly, not that detection is clever."]
    out = ROOT / "eval" / "results.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:16]))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    main(ap.parse_args().seeds)
