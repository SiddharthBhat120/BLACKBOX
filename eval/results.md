# BlackBox evaluation results

50 injected incidents (5 scenarios x 10 seeds), run in 121s with the offline engine. Each run has two decoy changes (random services, random times, sometimes on the failing service) next to the real trigger.

## Overall

| Metric | Result |
|---|---|
| True trigger ranked first | **49 of 50** (98%) |
| True trigger in top 3 | 50 of 50 |
| First failing service correct | 50 of 50 |
| Mean error in detected start | 0.24 min |
| Mean error in detection time | 0.00 min |
| Mean error in mitigation time | 0.69 min |
| Mean error in recovery time | 0.50 min |
| Log lines in, timeline items out (mean) | 34,033 lines -> 23 items (20 key moments) |
| Postmortem sentences with valid evidence | 100% |

Held-out runs only (seeds 5 to 10, never looked at while the rules were tuned): trigger ranked first in **29 of 30**, first failing service correct in 30 of 30, mean recovery-time error 0.53 min.

## Per scenario

| Scenario | Runs | Trigger #1 | Top 3 | Root service | Start err (min) | Recovery err (min) | Lines -> items |
|---|---|---|---|---|---|---|---|
| `bad_deploy` | 10 | 10/10 | 10/10 | 10/10 | 0.19 | 0.60 | 32,791 -> 17 |
| `db_pool_exhausted` | 10 | 9/10 | 10/10 | 10/10 | 0.24 | 0.27 | 32,654 -> 25 |
| `slow_dependency` | 10 | 10/10 | 10/10 | 10/10 | 0.35 | 1.19 | 33,734 -> 31 |
| `config_change` | 10 | 10/10 | 10/10 | 10/10 | 0.31 | 0.21 | 33,404 -> 22 |
| `disk_full` | 10 | 10/10 | 10/10 | 10/10 | 0.13 | 0.21 | 37,585 -> 22 |

Scenario notes:

- `bad_deploy`: A new orders release turns on a feature flag with a bug in the promotions code.
- `db_pool_exhausted`: A config change shrinks the orders database connection pool; requests queue and fail.
- `slow_dependency`: The external payment provider slows down. Nothing inside the company changed.
- `config_change`: A payments config change lowers the provider timeout from 3000 ms to 250 ms.
- `disk_full`: A payments release turns on DEBUG logging; twenty minutes later the disk fills up.

## Failure cases

- `db_pool_exhausted` seed 7: true trigger ranked 2; top was "Deploy orders v1.7.6: Refactor order listing query (no behaviour change)" (score 1.43: 0.7 min before the first errors; orders is the service where errors started)

## Honest notes

- These are simulated incidents. The simulator and the analyzer were written by the same person, so real
  incidents will be messier (clock skew between hosts, missing logs, several changes at once).
- Timing errors are measured against the simulator's exact fault times; BlackBox works at one-minute
  resolution for spikes, so errors under one minute are expected.
- The `slow_dependency` scenario has no internal trigger: a correct answer is the "outside cause" candidate.
- Detection error is zero by construction: both the simulator and BlackBox define detection as the first
  firing alert. It checks that alerts are ingested and timed correctly, not that detection is clever.
