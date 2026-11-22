"""Compute the daily S&P 500 speculation gauge.

The gauge follows Marx's account of fictitious capital:

    P = (D + B) / r + X

where P is the market value of US corporate equities, D is dividends
paid over 12 months, B is net buybacks, r is the rate of interest and
X is the speculative component of the price.

r is the 20-year High Quality Market (HQM) corporate bond spot rate
published monthly by the US Department of the Treasury. It moves with
Moody's Baa corporate bond yield (the gauge built on it correlates
0.99 with the Baa version from 1986) and, unlike Moody's data, it is
public domain, so every column can be shared freely. Each month's
rate is used from the first day of the next month.

Values are in trillions of US dollars for all US corporate equities.
The S&P 500's daily changes move the Federal Reserve's quarterly
market value between releases; no S&P 500 index levels are written.

The script writes one JSON file holding the column names, one list of
values per trading day, and metadata: the data dictionary and this
source code.

Usage:
    python3 compute.py OUTPUT_PATH
"""

import datetime
import io
import json
import sys

import pandas as pd
import requests
import yfinance as yf

FRED_CSV_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id={}'

# Days from the end of a period until the Federal Reserve publishes it.
PUBLICATION_LAG_DAYS = 75

# FRED series identifiers.
MARKET_VALUE_SERIES = 'BOGZ1LM893064105Q'
DIVIDENDS_SERIES = 'ASDIPAA027N'
NET_ISSUANCE_SERIES = 'NCBCEBQ027S'
CORPORATE_RATE_SERIES = 'HQMCB20YR'

# Days from the end of a month until its corporate bond rate is used.
RATE_LAG_DAYS = 1

# Yahoo Finance tickers.
INDEX_TICKER = '^GSPC'
DIVIDEND_TICKER = 'SPY'

# Data dictionary, following ISO/IEC 11179-4 and 11179-5:
# (name, definition, unit of measure, source).
DATA_DICTIONARY = [
    ('trading_day_date',
     'The calendar date of a New York Stock Exchange trading session.',
     'date, ISO 8601 (YYYY-MM-DD)',
     'Yahoo Finance'),
    ('us_equity_market_value_amount',
     'The estimated market value of all US corporate equities at the '
     'close of the trading day: the latest quarterly value published by '
     'the Federal Reserve, moved by the change in the S&P 500 since the '
     'end of that quarter.',
     'trillions of US dollars',
     'Federal Reserve Financial Accounts via FRED; S&P 500 daily changes'),
    ('hqm_20y_corporate_bond_spot_rate',
     'The 20-year High Quality Market corporate bond spot rate for the '
     'latest month completed before the trading day.',
     'percent per year',
     'US Department of the Treasury via FRED (HQMCB20YR)'),
    ('us_equity_12m_buyback_payout_yield_rate',
     'Dividends paid plus net buybacks over the past 12 months, as a '
     'percentage of the market value of US corporate equities.',
     'percent per year',
     'Federal Reserve Financial Accounts via FRED; SPY dividends'),
    ('us_equity_12m_buyback_capitalized_value_amount',
     'Dividends paid plus net buybacks over the past 12 months, divided '
     'by the corporate bond spot rate.',
     'trillions of US dollars',
     'Calculated'),
    ('us_equity_36m_buyback_capitalized_value_amount',
     'Dividends paid over the past 12 months plus net buybacks averaged '
     'over the past 36 months, divided by the corporate bond spot rate.',
     'trillions of US dollars',
     'Calculated'),
    ('us_equity_12m_buyback_speculative_component_amount',
     'The US equity market value amount minus the 12-month-buyback '
     'capitalized value amount.',
     'trillions of US dollars',
     'Calculated'),
    ('us_equity_12m_buyback_speculative_component_percent',
     'The 12-month-buyback speculative component amount as a percentage '
     'of the US equity market value amount.',
     'percent',
     'Calculated'),
    ('us_equity_12m_buyback_market_to_capitalized_value_percent',
     'The US equity market value amount as a percentage of the '
     '12-month-buyback capitalized value amount.',
     'percent',
     'Calculated'),
    ('us_equity_36m_buyback_market_to_capitalized_value_percent',
     'The US equity market value amount as a percentage of the '
     '36-month-buyback capitalized value amount.',
     'percent',
     'Calculated'),
]
COLUMNS = [entry[0] for entry in DATA_DICTIONARY]


def fetch_fred_series(series_id):
    """Return a FRED series as floats indexed by date."""
    response = requests.get(FRED_CSV_URL.format(series_id), timeout=120)
    if not response.text.startswith('observation_date'):
        raise RuntimeError(f'FRED did not return CSV for {series_id}')
    frame = pd.read_csv(io.StringIO(response.text), index_col=0,
                        parse_dates=True, na_values='.')
    return frame.iloc[:, 0].astype(float).dropna()


def drop_repeated_tail(series):
    """Drop trailing values that repeat the value before them.

    FRED repeats the latest annual estimate for a year that has not
    been estimated yet; only the first of such a run is kept.
    """
    while len(series) > 1 and series.iloc[-1] == series.iloc[-2]:
        series = series.iloc[:-1]
    return series


def period_end(index, frequency):
    """Return the last calendar day of each period in the index."""
    return index.to_period(frequency).end_time.normalize()


def fetch_yahoo_history(ticker):
    """Return the full daily price and dividend history of a ticker."""
    history = yf.Ticker(ticker).history(period='max', auto_adjust=False,
                                        actions=True)
    history.index = (pd.to_datetime(history.index)
                     .tz_localize(None).normalize())
    return history


def value_as_of(anchor, driver, days, lag_days=PUBLICATION_LAG_DAYS):
    """Return the anchor value published by each day.

    Each anchor value becomes available lag_days after the end of its
    period. When a driver series is given, the value is scaled by the
    driver's change since the anchor date.
    """
    lag = pd.Timedelta(days=lag_days)
    anchors = pd.DataFrame({
        'value': anchor.values,
        'anchor_date': anchor.index.astype('datetime64[ns]'),
        'available_date': (anchor.index + lag).astype('datetime64[ns]'),
    }).sort_values('available_date')
    calendar = pd.DataFrame({'date': days.astype('datetime64[ns]')})
    result = pd.merge_asof(calendar, anchors, left_on='date',
                           right_on='available_date').set_index('date')
    if driver is not None:
        now = driver.reindex(days, method='ffill').values
        then = driver.reindex(result['anchor_date'],
                              method='ffill').values
        growth = now / then
        growth[pd.isna(growth)] = 1.0
        result['value'] = result['value'] * growth
    return result


def build_daily_frame():
    """Return one row per trading day with every gauge column."""
    market_value = fetch_fred_series(MARKET_VALUE_SERIES)
    dividends = drop_repeated_tail(fetch_fred_series(DIVIDENDS_SERIES))
    net_issuance = fetch_fred_series(NET_ISSUANCE_SERIES)
    corporate_rate = fetch_fred_series(CORPORATE_RATE_SERIES)

    index_close = fetch_yahoo_history(INDEX_TICKER)['Close']
    etf = fetch_yahoo_history(DIVIDEND_TICKER)
    payments = etf.loc[etf['Dividends'] > 0, 'Dividends']
    etf_dividends_12m = (payments.rolling(4).sum()
                         .reindex(etf.index).ffill().dropna())

    market_value_q = pd.Series(market_value.values,
                               period_end(market_value.index, 'Q'))
    dividends_a = pd.Series(dividends.values,
                            period_end(dividends.index, 'Y'))
    buybacks = {
        quarters: pd.Series(
            (-net_issuance).rolling(quarters).mean().values,
            period_end(net_issuance.index, 'Q')).dropna()
        for quarters in (4, 12)
    }

    rate_m = pd.Series(corporate_rate.values,
                       period_end(corporate_rate.index, 'M'))

    lag = pd.Timedelta(days=PUBLICATION_LAG_DAYS)
    first_day = max(market_value_q.index[0] + lag,
                    dividends_a.index[0] + lag,
                    buybacks[12].index[0] + lag,
                    rate_m.index[0] + pd.Timedelta(days=RATE_LAG_DAYS))
    days = index_close.index[index_close.index >= first_day]

    frame = pd.DataFrame({
        'price': index_close.reindex(days),
        'rate': value_as_of(rate_m, None, days, RATE_LAG_DAYS)['value'],
        'market_value': value_as_of(market_value_q, index_close,
                                    days)['value'],
        'dividends': value_as_of(dividends_a, etf_dividends_12m,
                                 days)['value'],
        'buybacks_12m': value_as_of(buybacks[4], None, days)['value'],
        'buybacks_36m': value_as_of(buybacks[12], None, days)['value'],
    }).dropna()

    for months in (12, 36):
        payout = frame['dividends'] + frame[f'buybacks_{months}m']
        # Both are in millions of dollars and the rate is in percent.
        frame[f'capitalized_{months}m'] = (
            100 * payout / frame['rate']).where(payout > 0)
        frame[f'ratio_{months}m'] = (
            100 * frame['market_value'] / frame[f'capitalized_{months}m'])
    frame['payout_yield_12m'] = (
        100 * (frame['dividends'] + frame['buybacks_12m'])
        / frame['market_value'])
    return frame.dropna()


def build_output(frame):
    """Return the gauge as columns, rows of values and metadata."""
    trillion = 1e6    # source values are in millions of dollars
    rows = []
    for day, row in frame.iterrows():
        speculative = row['market_value'] - row['capitalized_12m']
        rows.append([
            day.strftime('%Y-%m-%d'),
            round(row['market_value'] / trillion, 3),
            round(row['rate'], 2),
            round(row['payout_yield_12m'], 3),
            round(row['capitalized_12m'] / trillion, 3),
            round(row['capitalized_36m'] / trillion, 3),
            round(speculative / trillion, 3),
            round(100 * speculative / row['market_value'], 1),
            round(row['ratio_12m'], 1),
            round(row['ratio_36m'], 1),
        ])
    computed_at = datetime.datetime.now(datetime.timezone.utc)
    meta = [
        {'label': 'compute.py', 'value': open(__file__).read(),
         'type': 'code', 'lang': 'python'},
        {'label': 'Data through', 'value': frame.index[-1].strftime(
            '%Y-%m-%d')},
        {'label': 'Computed at',
         'value': computed_at.strftime('%Y-%m-%d %H:%M UTC')},
    ]
    for name, definition, unit, source in DATA_DICTIONARY:
        meta.append({'label': name,
                     'value': f'{definition} Unit: {unit}. '
                              f'Source: {source}.'})
    return {'columns': COLUMNS, 'data': rows, 'meta': meta}


def main(argv):
    """Compute the gauge and write it to the path given."""
    output_path = argv[1] if len(argv) > 1 else 'speculation.json'
    frame = build_daily_frame()
    with open(output_path, 'w') as handle:
        json.dump(build_output(frame), handle, separators=(',', ':'))
    latest = frame.iloc[-1]
    print(f'{len(frame)} rows through {frame.index[-1]:%Y-%m-%d}; '
          f'market to capitalized value {latest["ratio_12m"]:.1f}%, '
          f'capitalized value ${latest["capitalized_12m"] / 1e6:,.1f}T, '
          f'market value ${latest["market_value"] / 1e6:,.1f}T, '
          f'rate {latest["rate"]:.2f}%')


if __name__ == '__main__':
    main(sys.argv)
