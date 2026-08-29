import contextlib
import csv
import importlib.util
import io
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


def export(
    path,
    *,
    dynamic=False,
    return_value=0.12,
    source=b"class Example: pass\n",
    key="REDACTED",
    total_trades=120,
    drawdown=0.2,
    config_extra=None,
    extra_members=None,
):
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
        "total_trades": total_trades,
        "profit_total": return_value,
        "profit_factor": 1.2,
        "max_drawdown_account": drawdown,
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
    if config_extra:
        config.update(config_extra)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("result.json", json.dumps({"strategy": {strategy: summary}}, sort_keys=True))
        zf.writestr("result_config.json", json.dumps(config, sort_keys=True))
        zf.writestr("ExampleStrategy.py", source)
        for name, data in (extra_members or {}).items():
            zf.writestr(name, data)


def lookahead(path, *, biased=False, extra_fields=None):
    extra_fields = extra_fields or {}
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["strategy", "has_bias", "total_signals", "biased_entry_signals", "biased_exit_signals", *extra_fields],
        )
        writer.writeheader()
        writer.writerow(
            {
                "strategy": "ExampleStrategy",
                "has_bias": str(biased),
                "total_signals": 20,
                "biased_entry_signals": int(biased),
                "biased_exit_signals": 0,
                **extra_fields,
            }
        )


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
        return json.loads((directory / "public-summary.json").read_text())["verdict"]

    def public_summary(self, directory):
        return json.loads((directory / "public-summary.json").read_text())

    def assert_public_schema(self, summary, verdict, *, stress, lookahead):
        self.assertEqual(
            set(summary),
            {"checks", "claims", "evidence", "format", "verdict"},
        )
        self.assertEqual(summary["format"], evidence_pack.PUBLIC_FORMAT)
        self.assertEqual(summary["verdict"], verdict)
        self.assertEqual(summary["evidence"], {"base": True, "lookahead": lookahead, "stress": stress})
        self.assertEqual(
            summary["claims"],
            {"alpha": False, "live_ready": False, "profitability": False, "safe_to_trade": False},
        )
        self.assertTrue(summary["checks"])
        self.assertTrue(all(set(item) == {"name", "status"} for item in summary["checks"]))

    def test_complete_pack_is_pass_and_deterministic(self):
        first = self.root / "first"
        second = self.root / "second"
        self.assertEqual(evidence_pack.build_pack(self.base, first, self.stress, self.lookahead, 0.001, 0.002), evidence_pack.PASS_FOR_REVIEW)
        self.assertEqual(evidence_pack.build_pack(self.base, second, self.stress, self.lookahead, 0.001, 0.002), evidence_pack.PASS_FOR_REVIEW)
        self.assertEqual([path.name for path in first.iterdir()], ["public-summary.json"])
        self.assertEqual((first / "public-summary.json").read_bytes(), (second / "public-summary.json").read_bytes())
        self.assert_public_schema(
            self.public_summary(first),
            evidence_pack.PASS_FOR_REVIEW,
            stress=True,
            lookahead=True,
        )
        serialized = (first / "public-summary.json").read_text()
        for private_value in ("ExampleStrategy", "BTC/USDT", "0.001", "sha256", "timerange", "detail"):
            self.assertNotIn(private_value, serialized)

    def test_private_artifacts_are_explicit_and_deterministic(self):
        first = self.root / "private-first"
        second = self.root / "private-second"
        for output in (first, second):
            self.assertEqual(
                evidence_pack.build_pack(
                    self.base,
                    output,
                    self.stress,
                    self.lookahead,
                    0.001,
                    0.002,
                    include_private_artifacts=True,
                ),
                evidence_pack.PASS_FOR_REVIEW,
            )
        for name in ("public-summary.json", "manifest.json", "verdict.json", "report.md", "checksums.sha256"):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        self.assertEqual((first / "inputs" / "base.zip").read_bytes(), self.base.read_bytes())
        manifest = json.loads((first / "manifest.json").read_text())
        self.assertEqual(manifest["base"]["identity"]["strategy"], "ExampleStrategy")
        self.assertEqual(manifest["privacy"], "PRIVATE_DO_NOT_UPLOAD")

    def test_missing_optional_evidence_requires_review(self):
        output = self.root / "review"
        self.assertEqual(evidence_pack.build_pack(self.base, output), evidence_pack.REVIEW_REQUIRED)
        self.assertEqual(self.verdict(output), evidence_pack.REVIEW_REQUIRED)
        self.assert_public_schema(
            self.public_summary(output),
            evidence_pack.REVIEW_REQUIRED,
            stress=False,
            lookahead=False,
        )

    def test_bias_and_dynamic_pairlist_are_blocked(self):
        lookahead(self.lookahead, biased=True)
        output = self.root / "bias"
        self.assertEqual(evidence_pack.build_pack(self.base, output, self.stress, self.lookahead, 0.001, 0.002), evidence_pack.BLOCKED)
        self.assert_public_schema(
            self.public_summary(output),
            evidence_pack.BLOCKED,
            stress=True,
            lookahead=True,
        )
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

    def test_nested_mixed_case_secrets_are_blocked_but_placeholders_are_allowed(self):
        for field, value in (("apiKey", "canary-api-value"), ("Pass_Word", "canary-pass-value"), ("wsToken", "canary-token-value")):
            secret = self.root / f"{field}.zip"
            export(secret, config_extra={"service": {field: value}})
            output = self.root / f"{field}-output"
            with self.assertRaises(evidence_pack.SecretError):
                evidence_pack.build_pack(secret, output)
            self.assertFalse(output.exists())

        safe = self.root / "safe-placeholders.zip"
        export(
            safe,
            source=b"class Example:\n    def token_bucket(self):\n        return 'ordinary strategy code'\n",
            config_extra={
                "service": {
                    "apiKey": "${EXCHANGE_API_KEY}",
                    "password": "<REDACTED>",
                    "token": "***",
                }
            },
            extra_members={
                "notes.txt": "Authorization: Bearer ${TOKEN}\nproxy = \"https://${PROXY_USER}:${PROXY_PASSWORD}@example.test\"\n"
            },
        )
        output = self.root / "safe-placeholders-output"
        self.assertEqual(evidence_pack.build_pack(safe, output), evidence_pack.REVIEW_REQUIRED)

    def test_literal_secrets_anywhere_are_blocked_without_output(self):
        cases = []

        source = self.root / "source-secret.zip"
        export(source, source=b'class Example:\n    api_token = "canary-source-token"\n')
        cases.append((source, self.lookahead, "source"))

        unused = self.root / "unused-secret.zip"
        export(unused, extra_members={"notes.txt": "Authorization: Bearer canary-unused-token\n"})
        cases.append((unused, self.lookahead, "unused"))

        url = self.root / "url-secret.zip"
        export(url, extra_members={"notes.txt": 'proxy = "https://canary-user:canary-pass@example.test"\n'})
        cases.append((url, self.lookahead, "url"))

        csv_secret = self.root / "lookahead-secret.csv"
        lookahead(csv_secret, extra_fields={"apiToken": "canary-csv-token"})
        cases.append((self.base, csv_secret, "csv"))

        for artifact, lookahead_path, label in cases:
            output = self.root / f"{label}-output"
            with self.assertRaises(evidence_pack.SecretError):
                evidence_pack.build_pack(artifact, output, None, lookahead_path)
            self.assertFalse(output.exists())

    def test_cli_error_does_not_echo_secret(self):
        secret_value = "canary-must-not-echo"
        secret = self.root / "cli-secret.zip"
        export(secret, config_extra={"telegram": {"token": secret_value}})
        output = self.root / "cli-secret-output"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = evidence_pack.main(["--base", str(secret), "--output", str(output)])
        self.assertEqual(exit_code, 2)
        self.assertNotIn(secret_value, stderr.getvalue())
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

    def test_negative_lookahead_rows_cannot_cancel(self):
        with self.lookahead.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["strategy", "has_bias", "total_signals", "biased_entry_signals", "biased_exit_signals"])
            writer.writeheader()
            writer.writerow({"strategy": "ExampleStrategy", "has_bias": "False", "total_signals": -1, "biased_entry_signals": -1, "biased_exit_signals": 0})
            writer.writerow({"strategy": "ExampleStrategy", "has_bias": "False", "total_signals": 21, "biased_entry_signals": 1, "biased_exit_signals": 0})
        output = self.root / "negative-lookahead"
        self.assertEqual(
            evidence_pack.build_pack(self.base, output, self.stress, self.lookahead, 0.001, 0.002),
            evidence_pack.BLOCKED,
        )

    def test_fractional_trade_count_and_negative_drawdown_are_blocked(self):
        fractional = self.root / "fractional.zip"
        export(fractional, total_trades=100.5)
        self.assertEqual(evidence_pack.build_pack(fractional, self.root / "fractional-output"), evidence_pack.BLOCKED)
        negative_drawdown = self.root / "negative-drawdown.zip"
        export(negative_drawdown, drawdown=-0.1)
        self.assertEqual(evidence_pack.build_pack(negative_drawdown, self.root / "negative-drawdown-output"), evidence_pack.BLOCKED)


if __name__ == "__main__":
    unittest.main()
