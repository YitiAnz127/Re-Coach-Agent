import json
import tempfile
import unittest
from pathlib import Path

from evaluate_teaching_start import load_cases, summarize


def case():
    shared = {"model": "fixed", "memory_snapshot": "same", "generation_config": {"temperature": 0}}
    return {
        "case_id": "one", "query": "请解释反向传播", "scenario": "cold_start", "gold_start": "novice",
        "baseline": {**shared, "policy_version": "old", "start": "unknown", "response": "旧回答", "satisfaction": 3, "transfer_correct": False, "ttft_ms": 100},
        "adaptive": {**shared, "policy_version": "new", "start": "novice", "response": "新回答", "satisfaction": 4, "transfer_correct": True, "ttft_ms": 110},
    }


class EvaluationTest(unittest.TestCase):
    def test_unknown_counts_as_incorrect_and_reports_paired_outcome(self):
        result = summarize([case()])
        self.assertEqual(result["baseline_start_accuracy"], 0)
        self.assertEqual(result["adaptive_start_accuracy"], 1)
        self.assertEqual(result["adaptive_transfer_rate"], 1)
        self.assertEqual(result["mean_satisfaction_delta"], 1)

    def test_rejects_changed_model_between_arms(self):
        row = case()
        row["adaptive"]["model"] = "other"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cases.jsonl"
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "model must be present and identical"):
                load_cases(path)


if __name__ == "__main__":
    unittest.main()
