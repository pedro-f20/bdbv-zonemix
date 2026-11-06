# pedro-zonemix for the BDBV Modeling Hub

Weekly nowcasts and forecasts of confirmed Bundibugyo Ebola cases by onset week, for every active health zone, province and the country, submitted to the [BDBV Modeling Hub](https://github.com/InsightNet-US/BDBV-Modeling-Hub) as `pedro-zonemix`.

## Model

- **Nowcast.** Recent onset weeks in each health zone are scaled up by how complete weeks of that age have been. The completeness comes from how each onset week grew between consecutive hub releases (chain ladder, median over release pairs, adjusted when a release is dated after the week ends). The hub's delay file sets the differences between provinces. One shock per trajectory, shared by all weeks and zones, carries the uncertainty in that level.
- **Forecast.** Each zone's trajectories come a third each from a weekly renewal model (R pooled toward province and country, damped toward 1), an endemic-epidemic spatial model where zones import cases through road travel time (also damped), and persistence, plus a trend shared by all zones. Provinces and the country are sums of zone trajectories.
- **Testing.** The forecast part was tested on the 2018-20 North Kivu/Ituri outbreak using only data available at each date. Log-scale CRPS was 0.86 of persistence for health zones with recent cases (95% CI 0.84-0.88), 0.91 for provinces (0.87-0.96) and 0.96 for the total (0.89-1.02, so no clear gain there). Live performance is scored on this hub.

Inputs: the hub's target data and delay file, plus INRB's road travel times and WorldPop populations by health zone (`data/`, from [inrb-umie/BDBV2026-Data](https://github.com/inrb-umie/BDBV2026-Data), MIT).

## Running

A scheduled GitHub Action runs `submit.sh` from Sunday to Tuesday. Each run forecasts the coming Saturday's round. It does nothing while the hub hasn't opened the round or published the week's release, or once the round is submitted. Otherwise it opens a PR from the `pedro-f20` fork and waits for the hub's validation. From Tuesday morning, if the release isn't complete, it forecasts from the previous one so that no round is skipped, and a later run replaces that PR if the release lands before it is merged. A PR that fails validation is rebuilt. `check.sh` runs on Wednesday and fails if the round isn't in the hub or in an open PR that passed validation. `submissions.csv` lists the rounds.

By hand:

```
uv venv --python 3.12 && uv pip install -r requirements.lock
REF=2026-11-14 DRY_RUN=1 bash submit.sh
```

Disclosure: I used AI to assist in this analysis. I validated every finding.
