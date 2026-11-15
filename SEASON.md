# Live scoreboard

Score: CRPS of the samples on log(x+1) (as the backtest), truth = hub release of 2026-11-08, onset weeks ending 14+ days before it. Ratio = pedro-zonemix mean CRPS / comparator's on shared cells, below 1 means we are better; geo = respicast's geometric mean over locations.

| comparator | ratio | geo | shared cells | locations | rounds |
|---|---|---|---|---|---|
| BDBVHub-baseline | 0.37 | 0.87 | 62 | 62 | 1 |
| ACCIDDA-Chromantis | 0.91 | 0.91 | 248 | 62 | 1 |
| ACCIDDA-insightcast | 1.03 | 1.53 | 248 | 62 | 1 |
| CEPH-Rtrend_BDBV | 1.73 | 2.15 | 3 | 3 | 1 |
| MOBS-GLEAM_BDBV | 1.31 | 1.31 | 12 | 4 | 1 |
| NTUCPH-LLM | 0.87 | 0.86 | 10 | 5 | 1 |
| OHTJHU-BM | 0.62 | 0.53 | 248 | 62 | 1 |
| UQPSU-national_level | 2.04 | 2.04 | 4 | 1 | 1 |
| seabbs-renewal | 1.11 | 1.63 | 248 | 62 | 1 |
| seabbs-rwnowcast | 1.11 | 1.81 | 248 | 62 | 1 |

Vs BDBVHub-baseline:

| by | ratio | cells |
|---|---|---|
| round 2026-11-07 | 0.37 | 62 |
| country | 1.28 | 1 |
| province | 1.10 | 7 |
| zone | 0.33 | 54 |
