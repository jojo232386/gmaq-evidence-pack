# Public sharing profile

The default output contains only a fixed-whitelist `public-summary.json`. A private archival pack is created only with `--include-private-artifacts`; it can contain copied Freqtrade exports, configuration, strategy source, identity, pairlist, metrics, and hashes.

## Safe default

For a public review request, share only:

- Evidence Pack version and Freqtrade version;
- the generated `public-summary.json` from the default mode;
- a short question that does not describe private strategy logic.

Do not upload private-artifact output, `inputs/`, native ZIPs, strategy files, configs, manifests, detailed reports, hashes, databases, logs, account balances, orders, wallet information, API keys, or secrets. Review every copied field before posting. Maintainers will never ask for credentials or funds.

## Sanitized example

```json
{
  "checks": [
    {
      "name": "input_parsing",
      "status": "FAIL"
    }
  ],
  "claims": {
    "alpha": false,
    "live_ready": false,
    "profitability": false,
    "safe_to_trade": false
  },
  "evidence": {
    "base": true,
    "lookahead": false,
    "stress": false
  },
  "format": "gmaq-evidence-public-summary-v0.1",
  "verdict": "BLOCKED"
}
```

This is enough to identify the failed stage without publishing its raw error, strategy, market, metric, path, or account. Use the repository's [Evidence audit request](https://github.com/jojo232386/gmaq-evidence-pack/issues/new?template=audit-request.yml) form.

Secret scanning in private mode is defense in depth, not proof that a private pack is safe to disclose. The only file approved for direct public sharing is the default mode's `public-summary.json`.
