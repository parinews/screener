"""Daily screener: top 20 winners/losers by % across 5 timeframes for S&P 500 + VGT."""

import io
import json
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
import yfinance as yf

ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
SECTOR_CACHE = ROOT / "sectors.json"

SPY_XLSX = "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
VGT_FUND = "https://investor.vanguard.com/irr/funds/profile/VGT-AdditionalFundData"
VANGUARD_GIST = "https://etfportal.vanguard.com/extrapi/v2/us/standard"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MIN_PRICE = 5.0
MIN_AVG_VOLUME = 250_000
VOLUME_WINDOW = 21
TOP_N = 20
CHUNK_SIZE = 250
TIMEFRAMES = {"1D": 1, "1W": 5, "2W": 10, "1M": 21, "3M": 63}

XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
TICKER_RE = re.compile(r"[A-Z]{1,5}([.\-][A-Z])?$")


class SourceError(RuntimeError):
    """A required upstream source failed; the run must not publish partial data."""


def get(url, as_json=False, referer=None):
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
    resp = requests.get(url, headers=headers, timeout=45)
    resp.raise_for_status()
    if as_json:
        # Vanguard returns an HTML app shell instead of 404 when a route moves.
        if resp.text.lstrip().startswith("<"):
            raise SourceError(f"{url} returned HTML, not JSON — the endpoint has moved.")
        return resp.json()
    return resp.content


def fetch_spy():
    """S&P 500 constituents from State Street's daily SPY holdings file."""
    try:
        book = zipfile.ZipFile(io.BytesIO(get(SPY_XLSX)))
        shared = ET.fromstring(book.read("xl/sharedStrings.xml"))
        strings = [
            "".join(t.text or "" for t in si.iter(XL_NS + "t"))
            for si in shared.findall(XL_NS + "si")
        ]
        sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
    except Exception as exc:
        raise SourceError(f"SPY holdings file unavailable or unreadable: {exc}") from exc

    rows = []
    for row in sheet.iter(XL_NS + "row"):
        values = []
        for cell in row.findall(XL_NS + "c"):
            node = cell.find(XL_NS + "v")
            text = "" if node is None else (node.text or "")
            if cell.get("t") == "s" and text:
                text = strings[int(text)]
            values.append(text)
        rows.append(values)

    header = next((r for r in rows if "Ticker" in r and "Name" in r), None)
    if header is None:
        raise SourceError("SPY holdings file has no recognisable header row.")
    t_idx, n_idx = header.index("Ticker"), header.index("Name")

    holdings = {}
    for row in rows[rows.index(header) + 1 :]:
        if len(row) <= max(t_idx, n_idx):
            continue
        ticker = row[t_idx].strip()
        if TICKER_RE.fullmatch(ticker):
            holdings[ticker] = row[n_idx].strip()
    if len(holdings) < 400:
        raise SourceError(f"SPY holdings parsed only {len(holdings)} rows; expected ~503.")
    return holdings


def fetch_vgt():
    """VGT constituents: union of Vanguard's month-end holdings and today's creation basket.

    The daily basket omits recent IPOs and other names excluded from in-kind creation,
    so month-end supplies complete coverage while the basket catches new additions.
    """
    try:
        fund = get(VGT_FUND, as_json=True, referer="https://investor.vanguard.com/")
        details = fund["holdingDetails"]
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError(f"Vanguard VGT fund data unavailable: {exc}") from exc

    holdings = {}
    for row in details.get("equityHoldings") or []:
        ticker = (row.get("ticker") or "").strip()
        if TICKER_RE.fullmatch(ticker):
            holdings[ticker] = (row.get("securityLongDescription") or ticker).strip()
    month_end = details.get("asOfDate")

    basket_date = None
    pcf = details.get("portfolioCompositionFile") or {}
    if pcf.get("basketId") and pcf.get("usageCode"):
        url = f"{VANGUARD_GIST}/{pcf['basketId']}/{pcf['usageCode']}/holdings.json"
        try:
            basket = get(url, as_json=True, referer="https://investor.vanguard.com/")
            for row in basket.get("holding") or []:
                ticker = (row.get("ticker") or "").strip()
                if TICKER_RE.fullmatch(ticker):
                    holdings.setdefault(ticker, (row.get("shrtName") or ticker).strip())
            basket_date = basket.get("trdDateStr", "").split()[0] or None
        except Exception as exc:
            # Month-end alone is a complete list, so a stale basket is survivable.
            print(f"  warning: daily basket unavailable ({exc}); using month-end only")

    if len(holdings) < 200:
        raise SourceError(f"VGT holdings parsed only {len(holdings)} rows; expected ~319.")
    return holdings, month_end, basket_date


def fetch_prices(tickers):
    closes, volumes = [], []
    for start in range(0, len(tickers), CHUNK_SIZE):
        chunk = tickers[start : start + CHUNK_SIZE]
        frame = None
        for attempt in range(3):
            try:
                frame = yf.download(
                    chunk,
                    period="6mo",
                    interval="1d",
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                    group_by="column",
                )
                break
            except Exception as exc:
                if attempt == 2:
                    raise SourceError(f"Yahoo price download failed: {exc}") from exc
                time.sleep(2**attempt)
        if frame is None or frame.empty:
            raise SourceError("Yahoo returned no price data.")
        closes.append(frame["Close"])
        volumes.append(frame["Volume"])
        print(f"  {min(start + CHUNK_SIZE, len(tickers))}/{len(tickers)} tickers")

    import pandas as pd

    return pd.concat(closes, axis=1), pd.concat(volumes, axis=1)


def load_sectors(tickers):
    cache = json.loads(SECTOR_CACHE.read_text()) if SECTOR_CACHE.exists() else {}
    missing = [t for t in tickers if t not in cache]
    if missing:
        print(f"Looking up {len(missing)} new sectors (cached: {len(cache)})")
    for i, ticker in enumerate(missing, 1):
        try:
            cache[ticker] = yf.Ticker(ticker).info.get("sector") or "—"
        except Exception:
            cache[ticker] = "—"
        if i % 50 == 0:
            print(f"  {i}/{len(missing)}")
    if missing:
        SECTOR_CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n")
    return cache


def build_tables(closes, volumes, names, sectors):
    results = {}
    for label, offset in TIMEFRAMES.items():
        rows = []
        for ticker in closes.columns:
            series = closes[ticker].dropna()
            if len(series) <= offset:
                continue
            latest, prior = series.iloc[-1], series.iloc[-1 - offset]
            if not (prior > 0 and latest > 0):
                continue
            if latest < MIN_PRICE:
                continue
            avg_volume = volumes[ticker].dropna().tail(VOLUME_WINDOW).mean()
            if not (avg_volume >= MIN_AVG_VOLUME):
                continue
            rows.append(
                {
                    "ticker": ticker,
                    "name": names.get(ticker, ticker),
                    "sector": sectors.get(ticker, "—"),
                    "price": round(float(latest), 2),
                    "volume": int(avg_volume),
                    "pct": round(float(latest / prior - 1) * 100, 2),
                }
            )
        rows.sort(key=lambda r: r["pct"], reverse=True)
        results[label] = {"winners": rows[:TOP_N], "losers": rows[::-1][:TOP_N]}
        print(f"  {label}: {len(rows)} eligible")
    return results


def main():
    print("Fetching S&P 500 (State Street SPY)…")
    spy = fetch_spy()
    print(f"  {len(spy)} holdings")

    print("Fetching VGT (Vanguard)…")
    vgt, month_end, basket_date = fetch_vgt()
    print(f"  {len(vgt)} holdings (month-end {month_end}, basket {basket_date})")

    names = {}
    for ticker, name in {**vgt, **spy}.items():
        names[ticker.replace(".", "-")] = name
    universe = sorted(names)
    print(f"Universe: {len(universe)} unique tickers")

    print("Downloading prices…")
    closes, volumes = fetch_prices(universe)
    closes = closes.loc[:, ~closes.columns.duplicated()]
    volumes = volumes.loc[:, ~volumes.columns.duplicated()]

    sectors = load_sectors(universe)

    print("Computing tables…")
    tables = build_tables(closes, volumes, names, sectors)

    as_of = closes.dropna(how="all").index[-1]
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of.strftime("%Y-%m-%d"),
        "universe": len(universe),
        "sources": {
            "sp500": "State Street SPY daily holdings",
            "vgt_month_end": month_end,
            "vgt_basket": basket_date,
        },
        "filters": {"min_price": MIN_PRICE, "min_avg_volume": MIN_AVG_VOLUME},
        "timeframes": tables,
    }

    PUBLIC.mkdir(exist_ok=True)
    (PUBLIC / "data.json").write_text(json.dumps(payload, indent=1) + "\n")
    print(f"Wrote public/data.json — as of {payload['as_of']}")


if __name__ == "__main__":
    try:
        main()
    except SourceError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
