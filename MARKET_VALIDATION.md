# v0.1 public validation

Window: **2026-08-30 through 2026-09-29**

This release tests whether Freqtrade users need a small, offline evidence preflight before manual strategy review. It does not test or claim strategy profitability.

## Provisional success measures

| Measure | Current | 30-day target |
| --- | ---: | ---: |
| Attributable external preflight cases | 0 | 3 |
| Contributors with actionable feedback | 2 | 2 |
| Users who return for a second preflight | 0 | 1 |

Only public GitHub issues whose authors consent to counting are attributable. There is no product telemetry, so zero means no attributable evidence, not proof of zero unseen use.

## Guardrails

- zero credentials, funds, account data, private code, raw ZIPs, databases, or order histories collected;
- zero claims of Alpha, profitability, live readiness, or safety to trade;
- no parser or workflow expansion before external evidence identifies a repeated problem.

## Decision on 2026-09-30

- If the three targets are met and at least two users report the same friction, v0.2 may address exactly that friction.
- With one or two attributable cases, improve onboarding only and extend validation by 14 days.
- With no attributable cases after documented outreach, pause product development and preserve v0.1 as a complete public artifact.
