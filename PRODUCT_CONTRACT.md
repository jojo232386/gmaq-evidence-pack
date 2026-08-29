# GMAQ Evidence Pack v0.1 contract

## User and problem

Freqtrade users can export attractive backtests, but the export alone does not answer whether the result used a reproducible pairlist, survived lookahead checks, held up under higher fees, or concentrated profit in one pair. GMAQ Evidence Pack audits those native artifacts without rerunning or translating the strategy.

## Frozen scope

The Git commit containing this contract is the v0.1 freeze. Implementation must remain offline and use the Python standard library.

The CLI accepts:

- one required Freqtrade backtest ZIP (`--base`);
- one optional higher-fee backtest ZIP (`--stress`);
- one optional Freqtrade lookahead-analysis CSV (`--lookahead`);
- a declared base fee and stress fee when the stress ZIP is present;
- a new output directory.

The CLI reads existing artifacts. It must not execute Freqtrade, download data, import strategy code, access an exchange, read credentials, or submit orders.

## Output contract

Each run creates one self-contained directory:

```text
evidence-pack/
  inputs/
  manifest.json
  verdict.json
  report.md
  checksums.sha256
```

The pack copies the supplied artifacts into `inputs/`, records SHA256 identities, extracts the native summary, lists every check, and emits exactly one verdict:

- `PASS_FOR_REVIEW`: complete screening evidence passed; human confirmation is still required.
- `REVIEW_REQUIRED`: the base export is valid, but stress or lookahead evidence is missing.
- `BLOCKED`: artifact safety, identity, bias, reproducibility, or performance gates failed.

No verdict may say Alpha, profitable, live-ready, or safe to trade.

## Safety and identity gates

The tool must fail closed on:

- ZIP path traversal, symlinks, duplicate names, more than 32 members, more than 128 MiB uncompressed, or a member larger than 64 MiB;
- missing or ambiguous report/config/strategy members;
- malformed JSON or CSV;
- unredacted exchange key or secret in the exported config;
- base/stress mismatch in strategy name, strategy source SHA256, timeframe, timerange, pairlist, trading mode, margin mode, stake currency, or max-open-trades;
- any pairlist method other than `StaticPairList`;
- stress fee below twice the declared base fee;
- lookahead bias, biased entry/exit signals, or fewer than 20 checked signals.

## Frozen screening gates

`PASS_FOR_REVIEW` requires all safety and identity gates plus:

- base total trades `>= 100`;
- base total return `> 0`;
- base profit factor `> 1.05`;
- base account maximum drawdown `< 35%`;
- largest positive non-TOTAL pair contribution `<= 60%` of all positive pair profit;
- stress total return `> 0`;
- stress profit factor `> 1.00`;
- lookahead `has_bias=false`, with zero biased entry and exit signals.

Any failed frozen screening gate returns `BLOCKED`. Missing optional evidence returns `REVIEW_REQUIRED`; the tool must not infer a pass.

## Determinism and acceptance

The implementation succeeds only if:

1. a synthetic complete pack returns `PASS_FOR_REVIEW`;
2. missing optional evidence returns `REVIEW_REQUIRED`;
3. a biased lookahead file and a dynamic pairlist each return `BLOCKED`;
4. a traversal archive is rejected without writing outside the output directory;
5. two runs over identical inputs produce byte-identical manifest, verdict, report, and checksum files;
6. the real local E0 base/stress/lookahead artifacts produce a pack without reading credentials or executing Freqtrade;
7. all tests pass in a clean Python 3.12 environment.

Observed results must not change these thresholds. A defect may be fixed; a failed market or screening gate must remain failed.
