"""Evaluate paired baseline/adaptive teaching responses from an annotated JSONL file.

This tool never calls a model. Each row must contain responses produced under the
same model, memory snapshot, and generation settings, plus human annotations.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

LEVELS = {"novice": 0, "familiar": 1, "advanced": 2}
FIXED_FIELDS = ("model", "memory_snapshot", "generation_config")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def summarize(rows: list[dict]) -> dict:
    result: dict = {"cases": len(rows)}
    for arm in ("baseline", "adaptive"):
        labeled = [r for r in rows if r.get("gold_start") in LEVELS]
        result[f"{arm}_start_accuracy"] = (
            sum(r[arm]["start"] == r["gold_start"] for r in labeled) / len(labeled)
            if labeled else None
        )
        result[f"{arm}_overstart_rate"] = (
            sum(r[arm]["start"] in LEVELS and LEVELS[r[arm]["start"]] > LEVELS[r["gold_start"]] for r in labeled) / len(labeled)
            if labeled else None
        )
        result[f"{arm}_labeled_cases"] = len(labeled)
        result[f"{arm}_known_start_cases"] = sum(r[arm]["start"] in LEVELS for r in labeled)
        transfer = [r[arm].get("transfer_correct") for r in rows if isinstance(r[arm].get("transfer_correct"), bool)]
        result[f"{arm}_transfer_rate"] = sum(transfer) / len(transfer) if transfer else None
        result[f"{arm}_transfer_cases"] = len(transfer)
        delays = [_number(r[arm].get("ttft_ms")) for r in rows]
        delays = [v for v in delays if v is not None]
        result[f"{arm}_ttft_median_ms"] = statistics.median(delays) if delays else None
    paired = [
        (_number(r["baseline"].get("satisfaction")), _number(r["adaptive"].get("satisfaction")))
        for r in rows
    ]
    differences = [adaptive - baseline for baseline, adaptive in paired if baseline is not None and adaptive is not None]
    result["paired_satisfaction_cases"] = len(differences)
    result["mean_satisfaction_delta"] = statistics.mean(differences) if differences else None
    return result


def load_cases(path: Path) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            case_id = row["case_id"]
            query = row["query"]
            baseline, adaptive = row["baseline"], row["adaptive"]
            if not isinstance(case_id, str) or case_id in seen:
                raise ValueError("case_id must be a unique string")
            if not isinstance(query, str) or not query.strip():
                raise ValueError("query must be a nonempty string")
            if not isinstance(baseline, dict) or not isinstance(adaptive, dict):
                raise ValueError("baseline/adaptive must be objects")
            for field in FIXED_FIELDS:
                if field not in baseline or baseline[field] != adaptive.get(field):
                    raise ValueError(f"{field} must be present and identical in both arms")
            for arm in (baseline, adaptive):
                if arm.get("start") not in {*LEVELS, "unknown"}:
                    raise ValueError("start must be unknown/novice/familiar/advanced")
                if not isinstance(arm.get("response"), str) or not arm["response"].strip():
                    raise ValueError("each arm must include its generated response")
                if not isinstance(arm.get("policy_version"), str) or not arm["policy_version"]:
                    raise ValueError("each arm must include policy_version")
                satisfaction = arm.get("satisfaction")
                if satisfaction is not None and (_number(satisfaction) is None or not 1 <= satisfaction <= 5):
                    raise ValueError("satisfaction must be within 1..5")
                delay = arm.get("ttft_ms")
                if delay is not None and (_number(delay) is None or delay < 0):
                    raise ValueError("ttft_ms must be nonnegative")
                transfer = arm.get("transfer_correct")
                if transfer is not None and not isinstance(transfer, bool):
                    raise ValueError("transfer_correct must be boolean")
            if row.get("gold_start") is not None and row["gold_start"] not in LEVELS:
                raise ValueError("gold_start must be novice/familiar/advanced")
            seen.add(case_id)
            rows.append(row)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
    if not rows:
        raise ValueError(f"{path}: no cases")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="paired, annotated JSONL cases")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    rows = load_cases(args.cases)
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("scenario", "unspecified"))].append(row)
    report = {"overall": summarize(rows), "by_scenario": {name: summarize(group) for name, group in sorted(groups.items())}}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
