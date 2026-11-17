# Live scoreboard

Score: CRPS of the samples on log(x+1) (as the backtest), truth = hub release of 2026-11-15, onset weeks ending 14+ days before it. Ratio = pedro-zonemix mean CRPS / comparator's on shared cells, below 1 means we are better; geo = respicast's geometric mean over locations.

| comparator | ratio | geo | shared cells | locations | rounds |
|---|---|---|---|---|---|
| BDBVHub-baseline | 0.56 | 1.04 | 187 | 63 | 2 |
| ACCIDDA-Chromantis | 1.08 | 1.20 | 562 | 63 | 2 |
| ACCIDDA-insightcast | 1.10 | 1.55 | 562 | 63 | 2 |
| CEPH-Rtrend_BDBV | 2.28 | 2.21 | 9 | 3 | 2 |
| MOBS-GLEAM_BDBV | 1.61 | 1.60 | 28 | 4 | 2 |
| NTUCPH-LLM | 1.04 | 1.06 | 25 | 5 | 2 |
| OHTJHU-BM | 0.67 | 0.66 | 562 | 63 | 2 |
| UQPSU-SEIHRD_CTMC | 0.94 | 1.10 | 20 | 5 | 1 |
| UQPSU-national_level | 2.92 | 2.92 | 5 | 1 | 1 |
| seabbs-renewal | 1.09 | 1.40 | 562 | 63 | 2 |
| seabbs-rwnowcast | 0.93 | 1.80 | 562 | 63 | 2 |

Vs BDBVHub-baseline:

| by | ratio | cells |
|---|---|---|
| round 2026-11-07 | 0.60 | 124 |
| round 2026-11-14 | 0.48 | 63 |
| country | 2.36 | 3 |
| province | 1.23 | 21 |
| zone | 0.51 | 163 |
