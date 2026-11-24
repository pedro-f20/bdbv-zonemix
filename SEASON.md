# Live scoreboard

Score: CRPS of the samples on log(x+1) (as the backtest), truth = hub release of 2026-11-22, onset weeks ending 14+ days before it. Ratio = pedro-zonemix mean CRPS / comparator's on shared cells, below 1 means we are better; geo = respicast's geometric mean over locations.

| comparator | ratio | geo | shared cells | locations | rounds |
|---|---|---|---|---|---|
| BDBVHub-baseline | 0.60 | 0.96 | 374 | 66 | 3 |
| ACCIDDA-Chromantis | 1.06 | 1.27 | 931 | 65 | 3 |
| ACCIDDA-insightcast | 1.09 | 1.47 | 935 | 66 | 3 |
| CEPH-Rtrend_BDBV | 1.76 | 1.79 | 21 | 3 | 3 |
| MOBS-GLEAM_BDBV | 0.99 | 1.01 | 48 | 4 | 3 |
| NAU-EpiPathsty | 1.09 | 1.47 | 228 | 57 | 1 |
| NTUCPH-LLM | 0.96 | 1.01 | 45 | 5 | 3 |
| OHTJHU-BM | 0.63 | 0.58 | 935 | 66 | 3 |
| UQPSU-SEIHRD_CTMC | 0.48 | 0.41 | 85 | 15 | 2 |
| UQPSU-national_level | 0.49 | 0.40 | 66 | 15 | 2 |
| seabbs-renewal | 0.99 | 1.22 | 935 | 66 | 3 |
| seabbs-rwnowcast | 0.72 | 0.92 | 935 | 66 | 3 |

Vs BDBVHub-baseline:

| by | ratio | cells |
|---|---|---|
| round 2026-11-07 | 0.68 | 186 |
| round 2026-11-14 | 0.60 | 126 |
| round 2026-11-21 | 0.35 | 62 |
| country | 1.82 | 6 |
| province | 1.08 | 42 |
| zone | 0.56 | 326 |
