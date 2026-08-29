import csv
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("evidence_pack", ROOT / "gmaq_evidence_pack.py")
evidence_pack = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(evidence_pack)


def export(path, *, dynamic=False, return_value=0.12, source=b"class Example: pass\n", key="REDACTED"):
    strategy = "ExampleStrategy"
    pairs = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    summary = {
        "strategy_name": strategy,
        "timeframe": "5m",
        "timerange": "20240101-20250101",
        "pairlist": pairs,
        "trading_mode": "futures",
        "margin_mode": "isolated",
        "stake_currency": "USDT",
        "max_open_trades": 2,
        "total_trades": 120,
        "profit_total": return_value,
        "profit_factor": 1.2,
        "max_drawdown_account": 0.2,
        "results_per_pair": [
            {"key": pairs[0], "profit_total_abs": 60},
            {"key": pairs[1], "profit_total_abs": 40},
            {"key": "TOTAL", "profit_total_abs": 100},
        ],
    }
    config = {
        "strategy": strategy,
        "timeframe": "5m",
        "pairlists": [{"method": "VolumePairList" if dynamic else "StaticPairList"}],
        "trading_mode": "futures",
        "margin_mode": "isolated",
        "stake_currency": "USDT",
        "max_open_trades": 2,
        "exchange": {"pair_whitelist": pairs, "key": key, "secret": "REDACTED"},
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("result.json", json.dumps({"strategy": {strategy: summary}}, sort_keys=True))
        zf.writestr("result_config.json", json.dumps(config, sort_keys=True))
        zf.writestr("ExampleStrategy.py", source)


def lookahead(path, *, biased=False):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["strategy", "has_bias", "total_signals", "biased_entry_signals", "biased_exit_signals"])
        writer.writeheader()
        writer.writerow({"strategy": "ExampleStrategy", "has_bias": str(biased), "total_signals": 20, "biased_entry_signals": int(biased), "biased_exit_signals": 0})


class EvidencePackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = self.root / "base.zip"
        self.stress = self.root / "stress.zip"
        self.lookahead = self.root / "lookahead.csv"
        export(self.base)
        export(self.stress)
        lookahead(self.lookahead)

    def tearDown(self):
        self.temp.cleanup()

    def verdict(self, directory):
        return json.loads((directory / "verdict.json").read_text())["verdict"]

    def test_complete_pack_is_pass_and_deterministic(self):
        first = self.root / "first"
        second = self.root / "second"
        self.assertEqual(evidence_pack.build_pack(self.base, first, self.stress, self.lookahead, 0.001, 0.002), evidence_pack.PASS_FOR_REVIEW)
        self.assertEqual(evidence_pack.build_pack(self.base, second, self.stress, self.lookahead, 0.001, 0.002), evidence_pack.PASS_FOR_REVIEW)
        for name in ("manifest.json", "verdict.json", "report.md", "checksums.sha256"):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        manifest = json.loads((first / "manifest.json").read_text())
        self.assertEqual(manifest["base"]["identity"]["strategy"], "ExampleStrategy")
        self.assertEqual(manifest["lookahead"]["strategy"], "ExampleStrategy")
        self.assertFalse(manifest["claims"]["profitability"])

    def test_missing_optional_evidence_requires_review(self):
        output = self.root / "review"
        self.assertEqual(evidence_pack.build_pack(self.base, output), evidence_pack.REVIEW_REQUIRED)
        self.assertEqual(self.verdict(output), evidence_pack.REVIEW_REQUIRED)

    def test_bias_and_dynamic_pairlist_are_blocked(self):
        lookahead(self.lookahead, biased=True)
        output = self.root / "bias"
        self.assertEqual(evidence_pack.build_pack(self.base, output, self.stress, self.lookahead, 0.001, 0.002), evidence_pack.BLOCKED)
        dynamic = self.root / "dynamic.zip"
        export(dynamic, dynamic=True)
        output = self.root / "dynamic"
        self.assertEqual(evidence_pack.build_pack(dynamic, output, self.stress, None, 0.001, 0.002), evidence_pack.BLOCKED)

    def test_mismatched_lookahead_and_dynamic_stress_are_blocked(self):
        with self.lookahead.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["strategy", "has_bias", "total_signals", "biased_entry_signals", "biased_exit_signals"])
            writer.writeheader()
            writer.writerow({"strategy": "OtherStrategy", "has_bias": "False", "total_signals": 20, "biased_entry_signals": 0, "biased_exit_signals": 0})
        self.assertEqual(
            evidence_pack.build_pack(self.base, self.root / "wrong-lookahead", self.stress, self.lookahead, 0.001, 0.002),
            evidence_pack.BLOCKED,
        )
        dynamic_stress = self.root / "dynamic-stress.zip"
        export(dynamic_stress, dynamic=True)
        lookahead(self.lookahead)
        self.assertEqual(
            evidence_pack.build_pack(self.base, self.root / "dynamic-stress", dynamic_stress, self.lookahead, 0.001, 0.002),
            evidence_pack.BLOCKED,
        )

    def test_traversal_archive_is_blocked_without_escape(self):
        traversal = self.root / "traversal.zip"
        with zipfile.ZipFile(traversal, "w") as zf:
            zf.writestr("../escape", b"no")
        output = self.root / "blocked"
        self.assertEqual(evidence_pack.build_pack(traversal, output), evidence_pack.BLOCKED)
        self.assertFalse((self.root / "escape").exists())
        self.assertEqual(self.verdict(output), evidence_pack.BLOCKED)

    def test_unredacted_credential_is_not_copied(self):
        secret = self.root / "secret.zip"
        export(secret, key="not-redacted")
        output = self.root / "secret-output"
        with self.assertRaises(evidence_pack.SecretError):
            evidence_pack.build_pack(secret, output)
        self.assertFalse(output.exists())

    def test_duplicate_json_keys_are_blocked(self):
        duplicate = self.root / "duplicate.zip"
        with zipfile.ZipFile(self.base) as source, zipfile.ZipFile(duplicate, "w") as target:
            for item in source.infolist():
                data = source.read(item)
                if item.filename.endswith("_config.json"):
                    data = data.replace(b'{"exchange":', b'{"strategy":"Wrong","strategy":"ExampleStrategy","exchange":', 1)
                target.writestr(item.filename, data)
        output = self.root / "duplicate-output"
        self.assertEqual(evidence_pack.build_pack(duplicate, output), evidence_pack.BLOCKED)
        self.assertEqual(self.verdict(output), evidence_pack.BLOCKED)


if __name__ == "__main__":
    unittest.main()
