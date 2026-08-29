#!/usr/bin/env python3
"""Offline, deterministic evidence packs for native Freqtrade exports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath


MAX_MEMBERS = 32
MAX_INPUT_SIZE = 128 * 1024 * 1024
MAX_TOTAL_SIZE = 128 * 1024 * 1024
MAX_MEMBER_SIZE = 64 * 1024 * 1024
PASS_FOR_REVIEW = "PASS_FOR_REVIEW"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
BLOCKED = "BLOCKED"
PUBLIC_FORMAT = "gmaq-evidence-public-summary-v0.1"

SENSITIVE_CONFIG_KEYS = {
    "accesskey",
    "accesstoken",
    "accountid",
    "apikey",
    "apikeys",
    "apisecret",
    "authorization",
    "chatid",
    "clientkey",
    "clientsecret",
    "cookie",
    "jwt",
    "jwtsecret",
    "jwtsecretkey",
    "key",
    "passphrase",
    "password",
    "privatekey",
    "refreshtoken",
    "secret",
    "sessioncookie",
    "signingkey",
    "token",
    "uid",
    "userid",
    "walletaddress",
    "webhook",
    "webhookauthorization",
    "webhookurl",
    "wstoken",
}
SENSITIVE_CONFIG_SUFFIXES = (
    "apikey",
    "apisecret",
    "authorization",
    "chatid",
    "clientsecret",
    "cookie",
    "passphrase",
    "password",
    "privatekey",
    "secret",
    "token",
)
TEXT_MEMBER_SUFFIXES = {".cfg", ".conf", ".csv", ".ini", ".json", ".md", ".py", ".txt", ".yaml", ".yml"}
KNOWN_BINARY_SUFFIXES = {".feather"}
KEY_VALUE_LITERAL = re.compile(
    r"(?i)(?P<key>[\"']?[A-Za-z][A-Za-z0-9_-]{0,63}[\"']?)\s*[:=]\s*(?P<quote>[\"'])(?P<value>[^\"'\r\n]*)(?P=quote)"
)
HEADER_LITERAL = re.compile(r"(?im)^\s*(authorization|proxy-authorization|cookie|set-cookie)\s*:\s*(?P<value>\S.*)$")
CREDENTIAL_URL = re.compile(r"(?i)https?://[^\s/:@]+:[^\s/@]+@")
KNOWN_SECRET_MARKERS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(rb"\bgh[pousr]_[0-9A-Za-z]{20,}\b"),
    re.compile(rb"\bsk-[0-9A-Za-z_-]{16,}\b"),
    re.compile(rb"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
)


class SecretError(ValueError):
    """An input contains a secret which must never be copied into a pack."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_snapshot(path: Path) -> bytes:
    try:
        if not path.is_file():
            raise ValueError(f"input is not a file: {path}")
        if path.stat().st_size > MAX_INPUT_SIZE:
            raise ValueError(f"input exceeds {MAX_INPUT_SIZE} bytes")
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read input: {path}") from exc
    if len(data) > MAX_INPUT_SIZE:
        raise ValueError(f"input exceeds {MAX_INPUT_SIZE} bytes")
    return data


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()


def write_json(path: Path, value: object) -> None:
    path.write_bytes(json_bytes(value))


def safe_members(data: bytes) -> dict[str, bytes]:
    """Read a small Freqtrade ZIP without ever extracting it."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
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


def normalize_key(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def sensitive_key(value: str) -> bool:
    key = normalize_key(value.strip("\"'"))
    return key in SENSITIVE_CONFIG_KEYS or key.endswith(SENSITIVE_CONFIG_SUFFIXES)


def safe_placeholder(value: object) -> bool:
    if value is None or value == "":
        return True
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if stripped.upper() in {"REDACTED", "***", "<REDACTED>"}:
        return True
    placeholder = r"(?:REDACTED|\*\*\*|<REDACTED>|\$\{[A-Za-z_][A-Za-z0-9_]*\})"
    return bool(re.fullmatch(placeholder, stripped, re.IGNORECASE)) or bool(
        re.fullmatch(rf"(?:Bearer|Basic)\s+{placeholder}", stripped, re.IGNORECASE)
    )


def sensitive_value_present(value: object) -> bool:
    if isinstance(value, dict):
        return any(sensitive_value_present(item) for item in value.values())
    if isinstance(value, list):
        return any(sensitive_value_present(item) for item in value)
    return not safe_placeholder(value)


def secret_in_config(value: object, key: str = "") -> bool:
    if key and sensitive_key(key) and sensitive_value_present(value):
        return True
    if isinstance(value, dict):
        return any(secret_in_config(item, str(name)) for name, item in value.items())
    if isinstance(value, list):
        return any(secret_in_config(item, key) for item in value)
    return False


def text_has_literal_secret(text: str) -> bool:
    for match in KEY_VALUE_LITERAL.finditer(text):
        key = normalize_key(match.group("key").strip("\"'"))
        # A generic JSON "key" commonly names result rows; only structured
        # config paths treat that short field as a credential.
        if key != "key" and sensitive_key(key) and not safe_placeholder(match.group("value")):
            return True
    for match in HEADER_LITERAL.finditer(text):
        if not safe_placeholder(match.group("value")):
            return True
    return any("${" not in match.group(0) for match in CREDENTIAL_URL.finditer(text))


def bytes_have_known_secret(data: bytes) -> bool:
    return any(pattern.search(data) for pattern in KNOWN_SECRET_MARKERS)


def scan_text(data: bytes) -> None:
    if bytes_have_known_secret(data):
        raise SecretError("input contains credential-like data")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SecretError("text input cannot be decoded safely") from exc
    if text_has_literal_secret(text):
        raise SecretError("input contains credential-like data")


def scan_csv(data: bytes) -> None:
    scan_text(data)
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
        for row in reader:
            for key, value in row.items():
                if key and sensitive_key(key) and not safe_placeholder(value):
                    raise SecretError("input contains credential-like data")
    except (UnicodeDecodeError, csv.Error) as exc:
        raise SecretError("text input cannot be decoded safely") from exc


def scan_archive(data: bytes, *, strict_binary: bool) -> None:
    members = safe_members(data)
    for name, member_data in members.items():
        if bytes_have_known_secret(member_data):
            raise SecretError("input contains credential-like data")
        suffix = PurePosixPath(name).suffix.lower()
        if suffix in TEXT_MEMBER_SUFFIXES:
            scan_text(member_data)
        elif strict_binary and suffix not in KNOWN_BINARY_SUFFIXES:
            raise SecretError("private archive contains an unsupported binary member")


def number(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"missing or invalid numeric field: {field}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"missing or invalid numeric field: {field}") from exc
    if not math.isfinite(result):
        raise ValueError(f"missing or invalid numeric field: {field}")
    return result


def integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"missing or invalid integer field: {field}")
    return value


def string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing or invalid field: {field}")
    return value


def list_of_strings(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"missing or invalid field: {field}")
    return value


def parse_artifact(data: bytes) -> dict:
    """Parse one native Freqtrade backtest export and its reproducibility identity."""
    members = safe_members(data)
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
        raise SecretError("exported config contains credential-like data")
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
        "max_open_trades": integer(summary.get("max_open_trades"), "summary.max_open_trades"),
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
    total_trades = integer(summary.get("total_trades"), "total_trades")
    require_nonnegative = {
        "total_trades": total_trades,
        "profit_factor": number(summary.get("profit_factor"), "profit_factor"),
        "max_drawdown_account": number(summary.get("max_drawdown_account"), "max_drawdown_account"),
    }
    if require_nonnegative["total_trades"] < 0 or require_nonnegative["profit_factor"] < 0:
        raise ValueError("trade count and profit factor must be nonnegative")
    if not 0 <= require_nonnegative["max_drawdown_account"] <= 1:
        raise ValueError("max_drawdown_account must be between 0 and 1")
    return {
        "source": {"sha256": sha256_bytes(data), "size_bytes": len(data)},
        "identity": identity,
        "pairlist_methods": methods,
        "metrics": {
            "total_trades": total_trades,
            "total_return": number(summary.get("profit_total"), "profit_total"),
            "profit_factor": require_nonnegative["profit_factor"],
            "max_drawdown_account": require_nonnegative["max_drawdown_account"],
            "positive_pair_profit": positive_pair_profit,
        },
    }


def parse_lookahead(data: bytes) -> dict:
    try:
        with io.StringIO(data.decode("utf-8-sig"), newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fieldnames = reader.fieldnames
    except (UnicodeDecodeError, csv.Error) as exc:
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
            row_checked = int(row["total_signals"])
            row_entry = int(row["biased_entry_signals"])
            row_exit = int(row["biased_exit_signals"])
        except (TypeError, ValueError) as exc:
            raise ValueError("malformed lookahead CSV") from exc
        if row_checked < 0 or row_entry < 0 or row_exit < 0:
            raise ValueError("malformed lookahead CSV")
        checked += row_checked
        entry += row_entry
        exit_signals += row_exit
        value = str(row["has_bias"]).strip().lower()
        if value not in {"true", "false"}:
            raise ValueError("malformed lookahead CSV")
        has_bias = has_bias or value == "true"
    return {
        "sha256": sha256_bytes(data),
        "size_bytes": len(data),
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
        "**PRIVATE ARTIFACT: DO NOT UPLOAD OR SHARE THIS DIRECTORY.**",
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


def public_summary(verdict: str, checks: list[dict], *, stress: bool, lookahead: bool) -> dict:
    return {
        "checks": [{"name": item["name"], "status": item["status"]} for item in checks],
        "claims": {"alpha": False, "live_ready": False, "profitability": False, "safe_to_trade": False},
        "evidence": {"base": True, "lookahead": lookahead, "stress": stress},
        "format": PUBLIC_FORMAT,
        "verdict": verdict,
    }


def build_pack(
    base_path: Path,
    output: Path,
    stress_path: Path | None = None,
    lookahead_path: Path | None = None,
    base_fee: float | None = None,
    stress_fee: float | None = None,
    include_private_artifacts: bool = False,
) -> str:
    """Build a public summary, plus a full private pack only when explicitly requested."""
    paths = {"base.zip": base_path}
    if stress_path:
        paths["stress.zip"] = stress_path
    if lookahead_path:
        paths["lookahead.csv"] = lookahead_path
    if output.exists():
        raise ValueError("output directory must be new")
    snapshots = {name: read_snapshot(path) for name, path in paths.items()}

    # All parsing and scanning uses the same byte snapshots. No output exists yet.
    errors: list[str] = []
    base = stress = lookahead = None
    try:
        base = parse_artifact(snapshots["base.zip"])
    except SecretError:
        raise
    except ValueError as exc:
        errors.append(f"base: {exc}")
    if stress_path:
        try:
            stress = parse_artifact(snapshots["stress.zip"])
        except SecretError:
            raise
        except ValueError as exc:
            errors.append(f"stress: {exc}")
    if lookahead_path:
        try:
            lookahead = parse_lookahead(snapshots["lookahead.csv"])
        except ValueError as exc:
            errors.append(f"lookahead: {exc}")

    for name in ("base.zip", "stress.zip"):
        if name not in snapshots:
            continue
        try:
            scan_archive(snapshots[name], strict_binary=include_private_artifacts)
        except SecretError:
            raise
        except ValueError:
            if include_private_artifacts:
                raise ValueError("private artifacts require a fully inspectable archive") from None
    if "lookahead.csv" in snapshots:
        scan_csv(snapshots["lookahead.csv"])
    if include_private_artifacts and errors:
        raise ValueError("private artifacts require all supplied inputs to parse safely")

    verdict, checks = evaluate(base, stress, lookahead, errors, base_fee, stress_fee)

    output.mkdir()
    write_json(
        output / "public-summary.json",
        public_summary(verdict, checks, stress=stress_path is not None, lookahead=lookahead_path is not None),
    )
    if not include_private_artifacts:
        return verdict

    inputs = output / "inputs"
    inputs.mkdir()
    for name, data in snapshots.items():
        (inputs / name).write_bytes(data)
    manifest = {
        "base": base,
        "claims": {"alpha": False, "live_ready": False, "profitability": False, "safe_to_trade": False},
        "declared_fees": {"base": base_fee, "stress": stress_fee, "verification": "DECLARED_NOT_EMBEDDED_IN_FREQTRADE_EXPORT"},
        "format": "gmaq-evidence-pack-v0.1",
        "inputs": {name: {"sha256": sha256_bytes(data), "size_bytes": len(data)} for name, data in sorted(snapshots.items())},
        "lookahead": lookahead,
        "privacy": "PRIVATE_DO_NOT_UPLOAD",
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
    checksum_lines = [f"{sha256_bytes(path.read_bytes())}  {path.relative_to(output).as_posix()}" for path in files]
    (output / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="ascii")
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an offline public evidence summary from native Freqtrade exports.")
    parser.add_argument("--base", required=True, type=Path, help="base-fee Freqtrade backtest ZIP")
    parser.add_argument("--stress", type=Path, help="higher-fee Freqtrade backtest ZIP")
    parser.add_argument("--lookahead", type=Path, help="Freqtrade lookahead-analysis CSV")
    parser.add_argument("--base-fee", type=float, help="declared base fee; required with --stress")
    parser.add_argument("--stress-fee", type=float, help="declared stress fee; required with --stress")
    parser.add_argument("--output", required=True, type=Path, help="new public-summary directory")
    parser.add_argument(
        "--include-private-artifacts",
        action="store_true",
        help="also write raw inputs and detailed private evidence; never upload or share this output directory",
    )
    args = parser.parse_args(argv)
    if args.stress and (args.base_fee is None or args.stress_fee is None):
        parser.error("--base-fee and --stress-fee are required with --stress")
    if not args.stress and (args.base_fee is not None or args.stress_fee is not None):
        parser.error("fee flags are only valid with --stress")
    try:
        verdict = build_pack(
            args.base,
            args.output,
            args.stress,
            args.lookahead,
            args.base_fee,
            args.stress_fee,
            args.include_private_artifacts,
        )
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
