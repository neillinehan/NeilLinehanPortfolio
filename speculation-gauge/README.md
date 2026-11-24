# US Stock Speculation Gauge

How much of the value of US company stock is speculation? This page compares the value of all US company stock with the value of the cash companies pay their shareholders, and calls the difference speculation. It follows Karl Marx's idea of "fictitious capital":

    P = (D + B) / r + X

- **P**: value of all US company stock (Federal Reserve Financial Accounts, moved day to day by the S&P 500's daily changes)
- **D**: dividends paid over the past 12 months
- **B**: net buybacks over the past 12 months (nonfinancial companies)
- **r**: 20-year High Quality Market corporate bond rate (US Treasury)
- **X**: speculation

Live page: https://neillinehan.github.io/NeilLinehanPortfolio/speculation-gauge/

## Files

- `index.html` — the page (plain HTML and JavaScript; loads `d3` from cdnjs)
- `data.json` — one row per trading day since February 1984; column definitions are in its `meta`
- `compute.py` — builds `data.json` from public sources (FRED and Yahoo Finance)

## Updating

`.github/workflows/speculation-gauge.yml` runs `compute.py` every weekday at about 5:15 p.m. US Eastern time, after the market closes, with backup runs that evening and the next morning in case GitHub skips one, and publishes the site to GitHub Pages. To run it by hand: **Actions → Speculation gauge → Run workflow**.

To build the data locally:

    pip install pandas requests yfinance
    python compute.py data.json

Then serve the folder, for example with `python -m http.server`, and open `http://localhost:8000/`.
