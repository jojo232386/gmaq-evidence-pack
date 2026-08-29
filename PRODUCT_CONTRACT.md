# GMAQ Evidence Pack v0.1.0 contract

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
- an explicit `--include-private-artifacts` option for a local archival pack.

The CLI reads existing artifacts. It must not execute Freqtrade, download data, import or execute strategy code, contact an exchange, use credentials, or submit orders.

## Output contract

The default output is public-safe and contains exactly one fixed-whitelist file:

```text
evidence-pack/
  public-summary.json
```

`public-summary.json` may contain only its format version, evidence-presence booleans, check names and statuses, four permanently false claim flags, and exactly one verdict. It must not contain check details, hashes, strategy names, pairs, metrics, fees, paths, filenames, or raw errors.

Only the explicit `--include-private-artifacts` option may add the original byte snapshots and detailed evidence:

```text
private-evidence-pack/
  inputs/
  public-summary.json
  manifest.json
  verdict.json
  report.md
  checksums.sha256
```

That private directory is an archival artifact and must never be uploaded or represented as secret-free. Secret scanning is defense in depth, not proof that a private pack is safe to disclose. Both output profiles emit exactly one verdict:

- `PASS_FOR_REVIEW`: complete screening evidence passed; human confirmation is still required.
- `REVIEW_REQUIRED`: the base export is valid, but stress or lookahead evidence is missing.
- `BLOCKED`: artifact safety, identity, bias, reproducibility, or performance gates failed.

No verdict may say Alpha, profitable, live-ready, or safe to trade.

## Safety and identity gates

The tool must fail closed on:

- ZIP path traversal, symlinks, duplicate names, more than 32 members, more than 128 MiB uncompressed, or a member larger than 64 MiB;
- missing or ambiguous report/config/strategy members;
- malformed JSON or CSV;
- credential-like structured config values after case/separator normalization, including key, secret, password, passphrase, token, authorization, cookie, UID/account identifiers, chat ID, webhook credentials, and client/access/private keys;
- obvious literal credentials in any supplied textual ZIP member or lookahead CSV, credential-bearing URLs, authorization or cookie headers, private-key markers, and common provider-token formats;
- an uninspectable extra archive member when private artifacts were requested;
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

All input files must be read into bounded byte snapshots before parsing or scanning. Any private copy must use those same snapshots. Credential rejection occurs before output creation and must not echo the detected value or leave a partial output directory.

## Determinism and acceptance

The implementation succeeds only if:

1. a synthetic complete summary returns `PASS_FOR_REVIEW`;
2. missing optional evidence returns `REVIEW_REQUIRED`;
3. a biased lookahead file and a dynamic pairlist each return `BLOCKED`;
4. the public summary has an exact allowlisted schema for all three verdicts and default output contains no private files;
5. nested, mixed-case, source-code, unused-member, CSV, header, URL, and known-token credential canaries are rejected without output or secret echo;
6. explicit private mode alone creates byte-snapshot inputs and detailed files;
7. a traversal archive is rejected without writing outside the output directory;
8. two runs over identical inputs produce byte-identical outputs in both profiles;
9. the real local E0 base/stress/lookahead artifacts produce deterministic public and private profiles without executing Freqtrade or contacting an exchange;
10. all tests pass in a clean Python 3.12 environment.

Observed results must not change these thresholds. A defect may be fixed; a failed market or screening gate must remain failed.
