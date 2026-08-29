# GMAQ Evidence Pack

Offline, standard-library audit packs for native Freqtrade backtest exports. It reads supplied artifacts only: it never executes Freqtrade, imports a strategy, contacts an exchange, reads credentials, or submits orders.

Freqtrade warns that dynamic pairlists can break historical reproducibility and that backtests can differ from dry/live behavior because of lookahead, recursive indicators, fills, and other assumptions. Evidence Pack turns the native exports into a bounded, shareable review artifact without replacing Freqtrade's engine.

```bash
scripts/gmaq-evidence-pack \
  --base base.zip --stress stress.zip \
  --base-fee 0.001 --stress-fee 0.002 \
  --lookahead lookahead.csv --output evidence-pack
```

The new output directory contains copied inputs, identities, a verdict, a readable report, and SHA256 checksums. The only verdicts are `PASS_FOR_REVIEW`, `REVIEW_REQUIRED`, and `BLOCKED`. A pass is a screening result for human review; it is never an Alpha, profitability, live-readiness, or trading-safety claim.

The v0.1 acceptance run used real E0 native exports and returned `PASS_FOR_REVIEW`; the input and core-output hashes are recorded in [`results/e0-v0.1-acceptance.json`](results/e0-v0.1-acceptance.json). The source ZIP files remain outside Git.

## What it checks

- safe, bounded ZIP structure and redacted exchange credentials;
- static pairlist plus exact base/stress strategy identity;
- base return, profit factor, drawdown, trade count, and pair concentration;
- higher-fee stress return and profit factor;
- lookahead strategy identity, signal count, and biased entry/exit results.

The declared fee values are recorded as `DECLARED_NOT_EMBEDDED_IN_FREQTRADE_EXPORT`. Freqtrade's ZIP does not prove the CLI fee arguments, so later confirmation must bind the original run command or rerun the native engine.

Run the focused tests with:

```bash
python3 -m unittest discover -s tests -v
```

Primary references: [Freqtrade backtesting](https://docs.freqtrade.io/en/stable/backtesting/), [lookahead analysis](https://docs.freqtrade.io/en/stable/lookahead-analysis/), and [recursive analysis](https://docs.freqtrade.io/en/stable/recursive-analysis/).
