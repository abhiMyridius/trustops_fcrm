# Load test results, 26 November 2026 (ADR-90)

Locust against the local stack (`make up-debug`), reads only (no agent runs, no Bedrock).
Script: `tests/load/locustfile.py`. Run: 30 users, started at 0.15 a second (the gateway allows
10 logins a minute per IP), 9 minutes.

| Criterion | Limit | Result |
|---|---|---|
| 5xx answers | none | **0** |
| Failures, excluding 429 | under 1% | **0%** (0 of 11,215 reads) |
| Aggregated p95 | under 1,500 ms | **610 ms** |
| Rate limited (429) | reported apart | 0 |

Median 44 ms, about 21 requests a second at 30 users. Individual requests reached up to 4.5 s
(`/cases/{id}/workbench` p95 1.0 s, the slowest p95); the stack shares a WSL machine with other
containers, so the numbers are indicative, not a capacity figure.

Files: `report.html` (charts), `run_stats.csv` (per endpoint), `run_stats_history.csv` (over time),
`summary.json` (the criteria), `run_failures.csv` and `run_exceptions.csv` (empty).

Rerun from `trustops/`, against the local stack only:

```bash
E2E_BASE_URL=http://localhost:8080 uvx locust -f tests/load/locustfile.py --host "$E2E_BASE_URL" \
  --headless -u 30 -r 0.15 -t 9m --csv ops/reports/locust/run --html ops/reports/locust/report.html
```
