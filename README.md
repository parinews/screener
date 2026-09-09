# Screener

A daily stock screener showing the **top 20 winners and losers by percentage** across five
timeframes, over a universe of the S&P 500 plus VGT (Vanguard Information Technology ETF).

It rebuilds itself once a day after the US close and serves as a static page, so it works on a
phone without anything running on your laptop. **No API keys are required anywhere.**

| Timeframe | Measured over |
|---|---|
| 1D | 1 trading day |
| 1W | 5 trading days |
| 2W | 10 trading days |
| 1M | 21 trading days |
| 3M | 63 trading days |

Trading-day offsets are used rather than calendar dates, so holidays and weekends can't quietly
shift a window. Prices are split-adjusted — without that, a 2-for-1 split reads as a 50% loss and
would top the losers list every time.

## The universe

**749 unique tickers**, from two issuer-published sources:

- **S&P 500** — State Street's daily [SPY holdings file][spy] (503 holdings, republished every
  trading day with weights).
- **VGT** — Vanguard's fund endpoints (319 holdings, 246 of which are not in the S&P 500).

### Why VGT needs two endpoints

Vanguard publishes holdings two different ways, and neither alone is right:

- **Month-end holdings** — complete, but published monthly and weeks stale.
- **The daily creation basket** — current to the trading day, but a strict *subset*. It omits names
  excluded from in-kind creation, typically recent IPOs and hard-to-borrow stocks.

On the day this was built the basket was missing 12 names the month-end list had, including
CoreWeave, Figma and IREN — precisely the volatile names that top a percentage-movers list. One of
them, TTAN, was the single worst 1D, 1W *and* 2W loser that day.

So the screener takes the **union**: month-end supplies complete coverage, the daily basket catches
new additions before the next month-end publish.

The basket URL isn't hardcoded — the fund response carries a `basketId` and `usageCode`, and the
URL is built from those at runtime.

## Filters

Stocks under **$5** or under **250,000** average daily volume (21-day) are excluded. Both indices
are already quality-screened, so this is a light safety net against the occasional illiquid small
cap rather than a primary filter. Thresholds are constants at the top of [`build.py`](build.py).

## How the daily refresh works

GitHub Actions does all the work; Vercel is a static host that reacts to a push.

```
GitHub Actions  (cron: 0 22 * * 1-5)
  └── build.py → public/data.json → commit → push
                                              ↓
Vercel  (auto-deploys on push) → static page
```

**22:00 UTC** is 6pm EDT / 5pm EST — after the 4pm US close in both halves of the year, since GitHub
cron has no daylight-saving awareness. Weekdays only.

Vercel runs no code here. Its free tier caps function execution at 10 seconds and the fetch takes
about 30, so the build belongs upstream.

To refresh immediately, use **Actions → Daily Screener Update → Run workflow**.

## Running locally

Double-click **`Start Screener.command`**. It refetches prices if they aren't already current for
the day, starts a local server on port 8770, and opens your browser. Closing the window quits it.
The window also prints a LAN address for viewing on a phone on the same wifi.

## Files

| Path | What it does |
|---|---|
| `build.py` | Fetches the universe and prices, computes the tables, writes `data.json` |
| `public/index.html` | The page — no framework, no build step |
| `public/data.json` | Generated output, ~33KB |
| `sectors.json` | Cached ticker → sector, so lookups happen once, not daily |
| `.github/workflows/daily.yml` | The cron |
| `vercel.json` | Serves `public/`, no caching on `data.json` |

Sector labels come from Yahoo, whose taxonomy is **not** GICS — it returns `Technology` rather than
Information Technology, `Financial Services` rather than Financials. They're only available from a
per-ticker call (~0.8s each, so ~10 minutes for the full universe), which is why they're cached and
only looked up for tickers new to the universe.

## Notes

- GitHub disables scheduled workflows after **60 days of repository inactivity**. It emails first,
  and re-enabling is one click.
- The Vanguard endpoints are undocumented. They power the public fund page, so they should be
  stable, but `build.py` fails loudly naming the broken source rather than publishing a partial
  universe.
- Prices come from Yahoo via `yfinance`, an unofficial client. Requests are batched and retried
  with backoff.

[spy]: https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx
