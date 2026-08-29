#!/usr/bin/env python3
"""Offline, deterministic evidence packs for native Freqtrade exports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath


MAX_MEMBERS = 32
MAX_TOTAL_SIZE = 128 * 1024 * 1024
MAX_MEMBER_SIZE = 64 * 1024 * 1024
PASS_FOR_REVIEW = "PASS_FOR_REVIEW"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
BLOCKED = "BLOCKED"


class SecretError(ValueError):
    """An input contains a secret which must never be copied into a pack."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()


def write_json(path: Path, value: object) -> None:
    path.write_bytes(json_bytes(value))


def safe_members(path: Path) -> dict[str, bytes]:
    """Read a small Freqtrade ZIP without ever extracting it."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(infos) > MAX_MEMBERS:
                raise ValueError(f"archive has more than {MAX_MEMBERS} members")
            if len(set(names)) != len(names):
                raise ValueError("archive has duplicate member names")
            total = 0
            for item in infos:
                member = PurePosixPath(item.filename)
                if (
                    not item.filename
                    or "\\" in item.filename
                    or item.filename.startswith("/")
                    or ".." in member.parts
                    or stat.S_ISLNK(item.external_attr >> 16)
                ):
                    raise ValueError("archive contains an unsafe member path")
                if item.file_size > MAX_MEMBER_SIZE:
                    raise ValueError(f"archive member exceeds {MAX_MEMBER_SIZE} bytes")
                total += item.file_size
                if total > MAX_TOTAL_SIZE:
                    raise ValueError(f"archive exceeds {MAX_TOTAL_SIZE} bytes uncompressed")
            bad = archive.testzip()
            if bad:
                raise ValueError(f"archive member has a bad checksum: {bad}")
            return {item.filename: archive.read(item) for item in infos if not item.is_dir()}
    except zipfile.BadZipFile as exc:
        raise ValueError("malformed ZIP archive") from exc


def only_member(members: dict[str, bytes], predicate, label: str) -> tuple[str, bytes]:
    hits = [(name, data) for name, data in members.items() if predicate(name)]
    if len(hits) != 1:
        raise ValueError(f"missing or ambiguous {label} member")
    return hits[0]


def read_json(data: bytes, label: str) -> dict:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"malformed {label} JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} JSON must be an object")
    return value


def secret_in_config(value: object, key: str = "") -> bool:
    if isinstance(value, dict):
        return any(secret_in_config(item, str(name).lower()) for name, item in value.items())
    if isinstance(value, list):
        return any(secret_in_config(item, key) for item in value)
    if key not in {"key", "secret", "api_key", "api_secret"}:
        return False
    if value is None or value == "":
        return False
    return str(value).strip().upper() not in {"REDACTED", "***", "<REDACTED>"}


def number(value: object, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"missing or invalid numeric field: {field}") from exc
    if not math.isfinite(result):
        raise ValueError(f"missing or invalid numeric field: {field}")
    return result


def string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing or invalid field: {field}")
    return value


def list_of_strings(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"missing or invalid field: {field}")
    return value


def parse_artifact(path: Path) -> dict:
    """Parse one native Freqtrade backtest export and its reproducibility identity."""
    members = safe_members(path)
    _, report_data = only_member(
        members,
        lambda name: name.endswith(".json") and not name.endswith("_config.json"),
        "report",
    )
    _, config_data = only_member(members, lambda name: name.endswith("_config.json"), "config")
    _, source_data = only_member(members, lambda name: name.endswith(".py"), "strategy")
    report = read_json(report_data, "report")
    config = read_json(config_data, "config")
    if secret_in_config(config):
        raise SecretError("exported config contains an unredacted exchange key or secret")
    strategies = report.get("strategy")
    if not isinstance(strategies, dict) or len(strategies) != 1:
        raise ValueError("report must contain exactly one strategy")
    strategy_name, summary = next(iter(strategies.items()))
    if not isinstance(summary, dict):
        raise ValueError("strategy summary must be an object")
    strategy_name = string(strategy_name, "strategy name")
    if config.get("strategy") is not None and string(config.get("strategy"), "config.strategy") != strategy_name:
        raise ValueError("config strategy does not match report strategy")
    if string(summary.get("strategy_name"), "summary.strategy_name") != strategy_name:
        raise ValueError("summary strategy does not match report strategy")

    pairlists = config.get("pairlists")
    if not isinstance(pairlists, list) or not pairlists or not all(isinstance(item, dict) for item in pairlists):
        raise ValueError("missing or invalid field: config.pairlists")
    methods = [item.get("method") for item in pairlists]
    exchange = config.get("exchange")
    if not isinstance(exchange, dict):
        raise ValueError("missing or invalid field: config.exchange")
    config_pairs = list_of_strings(exchange.get("pair_whitelist"), "exchange.pair_whitelist")
    result_pairs = list_of_strings(summary.get("pairlist"), "summary.pairlist")
    if config_pairs != result_pairs:
        raise ValueError("config pairlist does not match report pairlist")

    timeframe = string(summary.get("timeframe"), "summary.timeframe")
    if config.get("timeframe") is not None and string(config.get("timeframe"), "config.timeframe") != timeframe:
        raise ValueError("config timeframe does not match report timeframe")
    identity = {
        "strategy": strategy_name,
        "strategy_source_sha256": hashlib.sha256(source_data).hexdigest(),
        "timeframe": timeframe,
        "timerange": string(summary.get("timerange"), "summary.timerange"),
        "pairlist": config_pairs,
        "trading_mode": string(summary.get("trading_mode"), "summary.trading_mode"),
        "margin_mode": summary.get("margin_mode"),
        "stake_currency": string(summary.get("stake_currency"), "summary.stake_currency"),
        "max_open_trades": number(summary.get("max_open_trades"), "summary.max_open_trades"),
    }
    for field in ("trading_mode", "margin_mode", "stake_currency", "max_open_trades"):
        # Native exports may omit values inherited from the command line; the
        # report remains authoritative, while an exported value must agree.
        if config.get(field) is not None and config.get(field) != identity[field]:
            raise ValueError(f"config {field} does not match report")
    if identity["margin_mode"] is not None and not isinstance(identity["margin_mode"], str):
        raise ValueError("missing or invalid field: summary.margin_mode")

    rows = summary.get("results_per_pair")
    if not isinstance(rows, list):
        raise ValueError("missing or invalid field: results_per_pair")
    positive_pair_profit = []
    for row in rows:
        if not isinstance(row, dict) or row.get("key") == "TOTAL":
            continue
        profit = number(row.get("profit_total_abs"), "results_per_pair.profit_total_abs")
        if profit > 0:
            positive_pair_profit.append(profit)
    return {
        "source": {"sha256": sha256_file(path), "size_bytes": path.stat().st_size},
        "identity": identity,
        "pairlist_methods": methods,
        "metrics": {
            "total_trades": number(summary.get("total_trades"), "total_trades"),
            "total_return": number(summary.get("profit_total"), "profit_total"),
            "profit_factor": number(summary.get("profit_factor"), "profit_factor"),
            "max_drawdown_account": number(summary.get("max_drawdown_account"), "max_drawdown_account"),
            "positive_pair_profit": positive_pair_profit,
        },
    }


def parse_lookahead(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fieldnames = reader.fieldnames
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ValueError("malformed lookahead CSV") from exc
    required = {"strategy", "has_bias", "total_signals", "biased_entry_signals", "biased_exit_signals"}
    if (
        not rows
        or not rows[0]
        or not required.issubset(rows[0])
        or not fieldnames
        or len(fieldnames) != len(set(fieldnames))
    ):
        raise ValueError("malformed lookahead CSV")
    strategies = {str(row["strategy"]).strip() for row in rows}
    if len(strategies) != 1 or not next(iter(strategies)):
        raise ValueError("lookahead CSV must contain exactly one strategy")
    checked = entry = exit_signals = 0
    has_bias = False
    for row in rows:
        try:
            checked += int(row["total_signals"])
            entry += int(row["biased_entry_signals"])
            exit_signals += int(row["biased_exit_signals"])
        except (TypeError, ValueError) as exc:
            raise ValueError("malformed lookahead CSV") from exc
        value = str(row["has_bias"]).strip().lower()
        if value not in {"true", "false"}:
            raise ValueError("malformed lookahead CSV")
        has_bias = has_bias or value == "true"
    if checked < 0 or entry < 0 or exit_signals < 0:
        raise ValueError("malformed lookahead CSV")
    return {
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "has_bias": has_bias,
        "total_signals": checked,
        "biased_entry_signals": entry,
        "biased_exit_signals": exit_signals,
        "strategy": next(iter(strategies)),
    }


def check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def evaluate(base: dict | None, stress: dict | None, lookahead: dict | None, errors: list[str], base_fee: float | None, stress_fee: float | None) -> tuple[str, list[dict]]:
    checks: list[dict] = [check("input_parsing", not errors, "; ".join(errors) if errors else "all supplied artifacts parsed")]
    if errors or base is None:
        return BLOCKED, checks
    checks.append(check("static_pairlist", all(method == "StaticPairList" for method in base["pairlist_methods"]), ", ".join(map(str, base["pairlist_methods"]))))
    metrics = base["metrics"]
    checks.extend(
        [
            check("base_total_trades", metrics["total_trades"] >= 100, f"{metrics['total_trades']} >= 100"),
            check("base_total_return", metrics["total_return"] > 0, f"{metrics['total_return']} > 0"),
            check("base_profit_factor", metrics["profit_factor"] > 1.05, f"{metrics['profit_factor']} > 1.05"),
            check("base_max_drawdown", metrics["max_drawdown_account"] < 0.35, f"{metrics['max_drawdown_account']} < 0.35"),
        ]
    )
    positives = metrics["positive_pair_profit"]
    contribution = max(positives) / sum(positives) if positives and sum(positives) > 0 else math.inf
    checks.append(check("base_pair_concentration", contribution <= 0.60, f"largest positive-pair contribution={contribution}"))
    if stress is None:
        checks.append({"name": "stress_evidence", "status": "MISSING", "detail": "higher-fee export not supplied"})
    else:
        checks.append(check("stress_static_pairlist", all(method == "StaticPairList" for method in stress["pairlist_methods"]), ", ".join(map(str, stress["pairlist_methods"]))))
        mismatch = [field for field in base["identity"] if base["identity"][field] != stress["identity"].get(field)]
        checks.append(check("base_stress_identity", not mismatch, "matched" if not mismatch else "mismatch: " + ", ".join(mismatch)))
        fee_ok = (
            base_fee is not None
            and stress_fee is not None
            and math.isfinite(base_fee)
            and math.isfinite(stress_fee)
            and base_fee > 0
            and stress_fee >= 2 * base_fee
        )
        checks.append(check("stress_fee", fee_ok, f"base_fee={base_fee}; stress_fee={stress_fee}"))
        stress_metrics = stress["metrics"]
        checks.extend(
            [
                check("stress_total_return", stress_metrics["total_return"] > 0, f"{stress_metrics['total_return']} > 0"),
                check("stress_profit_factor", stress_metrics["profit_factor"] > 1.00, f"{stress_metrics['profit_factor']} > 1.00"),
            ]
        )
    if lookahead is None:
        checks.append({"name": "lookahead_evidence", "status": "MISSING", "detail": "lookahead CSV not supplied"})
    else:
        checks.extend(
            [
                check("lookahead_strategy", lookahead["strategy"] == base["identity"]["strategy"], f"{lookahead['strategy']} == {base['identity']['strategy']}"),
                check("lookahead_has_bias", not lookahead["has_bias"], f"has_bias={lookahead['has_bias']}"),
                check("lookahead_entry_signals", lookahead["biased_entry_signals"] == 0, str(lookahead["biased_entry_signals"])),
                check("lookahead_exit_signals", lookahead["biased_exit_signals"] == 0, str(lookahead["biased_exit_signals"])),
                check("lookahead_signal_count", lookahead["total_signals"] >= 20, f"{lookahead['total_signals']} >= 20"),
            ]
        )
    if any(item["status"] == "FAIL" for item in checks):
        return BLOCKED, checks
    if any(item["status"] == "MISSING" for item in checks):
        return REVIEW_REQUIRED, checks
    return PASS_FOR_REVIEW, checks


def markdown_report(verdict: str, checks: list[dict], base: dict | None, stress: dict | None, lookahead: dict | None) -> str:
    lines = [
        "# GMAQ Evidence Pack",
        "",
        f"**Verdict: `{verdict}`**",
        "",
        "This pack audits supplied native Freqtrade artifacts offline. It does not establish Alpha, profitability, live readiness, or safety to trade.",
        "",
        "## Checks",
        "",
        "| Check | Status | Detail |",
        "| --- | --- | --- |",
    ]
    for item in checks:
        lines.append(f"| {item['name']} | {item['status']} | {item['detail']} |")
    if base:
        lines.extend(["", "## Base identity", "", "```json", json.dumps(base["identity"], indent=2, sort_keys=True), "```"])
    if stress:
        lines.extend(["", "## Stress identity", "", "```json", json.dumps(stress["identity"], indent=2, sort_keys=True), "```"])
    if lookahead:
        lines.extend(["", "## Lookahead summary", "", "```json", json.dumps(lookahead, indent=2, sort_keys=True), "```"])
    return "\n".join(lines) + "\n"


def build_pack(base_path: Path, output: Path, stress_path: Path | None = None, lookahead_path: Path | None = None, base_fee: float | None = None, stress_fee: float | None = None) -> str:
    """Build a deterministic self-contained pack. Returns its one allowed verdict."""
    paths = {"base.zip": base_path}
    if stress_path:
        paths["stress.zip"] = stress_path
    if lookahead_path:
        paths["lookahead.csv"] = lookahead_path
    if output.exists():
        raise ValueError("output directory must be new")
    for path in paths.values():
        if not path.is_file():
            raise ValueError(f"input is not a file: {path}")

    # Parse first: a secret must never be copied into a supposedly safe pack.
    errors: list[str] = []
    base = stress = lookahead = None
    try:
        base = parse_artifact(base_path)
    except SecretError:
        raise
    except ValueError as exc:
        errors.append(f"base: {exc}")
    if stress_path:
        try:
            stress = parse_artifact(stress_path)
        except SecretError:
            raise
        except ValueError as exc:
            errors.append(f"stress: {exc}")
    if lookahead_path:
        try:
            lookahead = parse_lookahead(lookahead_path)
        except ValueError as exc:
            errors.append(f"lookahead: {exc}")
    verdict, checks = evaluate(base, stress, lookahead, errors, base_fee, stress_fee)

    output.mkdir()
    inputs = output / "inputs"
    inputs.mkdir()
    for name, path in paths.items():
        shutil.copyfile(path, inputs / name)
    manifest = {
        "base": base,
        "claims": {"alpha": False, "live_ready": False, "profitability": False, "safe_to_trade": False},
        "declared_fees": {"base": base_fee, "stress": stress_fee, "verification": "DECLARED_NOT_EMBEDDED_IN_FREQTRADE_EXPORT"},
        "format": "gmaq-evidence-pack-v0.1",
        "inputs": {name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size} for name, path in sorted(paths.items())},
        "lookahead": lookahead,
        "stress": stress,
    }
    verdict_data = {
        "checks": checks,
        "claims": {"alpha": False, "live_ready": False, "profitability": False, "safe_to_trade": False},
        "verdict": verdict,
    }
    write_json(output / "manifest.json", manifest)
    write_json(output / "verdict.json", verdict_data)
    (output / "report.md").write_text(markdown_report(verdict, checks, base, stress, lookahead), encoding="utf-8")
    files = sorted(path for path in output.rglob("*") if path.is_file() and path.name != "checksums.sha256")
    checksum_lines = [f"{sha256_file(path)}  {path.relative_to(output).as_posix()}" for path in files]
    (output / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="ascii")
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an offline GMAQ evidence pack from native Freqtrade exports.")
    parser.add_argument("--base", required=True, type=Path, help="base-fee Freqtrade backtest ZIP")
    parser.add_argument("--stress", type=Path, help="higher-fee Freqtrade backtest ZIP")
    parser.add_argument("--lookahead", type=Path, help="Freqtrade lookahead-analysis CSV")
    parser.add_argument("--base-fee", type=float, help="declared base fee; required with --stress")
    parser.add_argument("--stress-fee", type=float, help="declared stress fee; required with --stress")
    parser.add_argument("--output", required=True, type=Path, help="new evidence-pack directory")
    args = parser.parse_args(argv)
    if args.stress and (args.base_fee is None or args.stress_fee is None):
        parser.error("--base-fee and --stress-fee are required with --stress")
    if not args.stress and (args.base_fee is not None or args.stress_fee is not None):
        parser.error("fee flags are only valid with --stress")
    try:
        verdict = build_pack(args.base, args.output, args.stress, args.lookahead, args.base_fee, args.stress_fee)
    except SecretError as exc:
        print(f"refusing to create pack: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
