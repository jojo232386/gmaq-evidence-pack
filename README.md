<p align="center">
  <img src="docs/assets/evidence-pack-hero.svg" alt="GMAQ Evidence Pack, offline Freqtrade backtest evidence audit" width="100%">
</p>

<p align="center">
  <a href="https://github.com/jojo232386/gmaq-evidence-pack/actions/workflows/test.yml"><img src="https://github.com/jojo232386/gmaq-evidence-pack/actions/workflows/test.yml/badge.svg" alt="CI status"></a>
  <img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white" alt="Python 3.12 or newer">
  <img src="https://img.shields.io/badge/dependencies-standard%20library-0F766E" alt="Python standard library only">
  <img src="https://img.shields.io/badge/v0.1-ACCEPTANCE%20PASS-16A34A" alt="v0.1 acceptance passed">
</p>

<p align="center"><strong>Turn native Freqtrade exports into a bounded, shareable evidence review.</strong></p>

GMAQ Evidence Pack audits existing Freqtrade base/stress backtest ZIPs and lookahead CSVs. It records artifact identities, applies frozen screening gates, copies the inputs, and produces a deterministic review pack. The tool runs offline with the Python standard library.

Freqtrade warns that dynamic pairlists can break historical reproducibility and that backtests can diverge from dry/live behavior because of lookahead, recursive indicators, fills, and other assumptions. See the official [backtesting](https://docs.freqtrade.io/en/stable/backtesting/), [lookahead analysis](https://docs.freqtrade.io/en/stable/lookahead-analysis/), and [recursive analysis](https://docs.freqtrade.io/en/stable/recursive-analysis/) documentation.

## Quick start

```sh
scripts/gmaq-evidence-pack \
  --base backtest-base.zip \
  --stress backtest-stress.zip \
  --base-fee 0.001 \
  --stress-fee 0.002 \
  --lookahead lookahead.csv \
  --output evidence-pack
```

The command reads supplied files only. It does not execute Freqtrade, import strategy code, contact an exchange, load account credentials, or submit orders.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `PASS_FOR_REVIEW` | Complete screening evidence passed; a person must still review and confirm it |
| `REVIEW_REQUIRED` | The base export parsed, but stress or lookahead evidence is missing |
| `BLOCKED` | Artifact safety, identity, bias, reproducibility, or a frozen screening gate failed |

No verdict claims Alpha, profitability, live readiness, or safety to trade.

## Evidence pack

```text
evidence-pack/
  inputs/
    base.zip
    stress.zip
    lookahead.csv
  manifest.json
  verdict.json
  report.md
  checksums.sha256
```

The manifest binds input SHA256 values, strategy source, timeframe, timerange, pairlist, trading mode, portfolio limits, declared fees, base/stress metrics, and lookahead identity. Declared fee values carry `DECLARED_NOT_EMBEDDED_IN_FREQTRADE_EXPORT`, because a native ZIP does not prove the original CLI fee arguments.

## Frozen checks

- bounded ZIP structure, path safety, CRC, duplicate names, and redacted exchange credentials;
- static pairlist plus exact base/stress strategy, source, timeframe, timerange, and portfolio identity;
- base return, profit factor, drawdown, trade count, and positive-profit concentration;
- stress return and profit factor with a declared fee at least twice the base fee;
- lookahead strategy identity, at least 20 checked signals, and zero biased entry/exit signals.

Malformed JSON/CSV, duplicate keys, negative signal counts, fractional trade counts, invalid drawdown values, identity drift, and dynamic pairlists fail closed.

## Real acceptance result

v0.1 audited real native E0 artifacts from `E0V1E_53_Sharpe`:

| Evidence | Result |
| --- | --- |
| Base export | 227 trades, return `+66.47%`, PF `1.934`, account DD `16.60%` |
| 2× declared-fee stress | Return `+47.58%`, PF `1.701` |
| Lookahead | 20 checked signals, 0 biased entry/exit signals |
| Pack determinism | Two runs produced byte-identical core files |
| Verdict | `PASS_FOR_REVIEW` |

The committed [acceptance record](results/e0-v0.1-acceptance.json) binds the input and output hashes while keeping the large source ZIPs outside Git. The result remains a screening decision for human review.

## Verify

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile gmaq_evidence_pack.py scripts/gmaq-evidence-pack
```

The frozen product boundary and acceptance rules live in [PRODUCT_CONTRACT.md](PRODUCT_CONTRACT.md).
