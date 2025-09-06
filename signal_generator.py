# signal_generator.py - improved analyze & label logic using features + learner
from features import candles_to_df, make_features
from coinbase_fetcher import fetch_candles, list_usd_products
from learner import OnlineLearner
import os, math, time

SHORT_MODEL = OnlineLearner('short')
LONG_MODEL = OnlineLearner('long')

def analyze_coin(product_id, granularity=300):
    candles = fetch_candles(product_id, granularity=granularity, limit=400)
    if not candles:
        return {'error':'no_candles'}
    df = candles_to_df(candles)
    feats = make_features(df)
    # heuristic: macd_hist + ema diff
    h = 'HOLD'
    score = 0.0
    if feats['macd_hist'] > 0 and feats['ema9_ema21_diff'] > 0:
        h = 'BUY'
        score += 1.2
    elif feats['macd_hist'] < 0 and feats['ema9_ema21_diff'] < 0:
        h = 'SELL'
        score += 1.0
    # model probabilities
    p_s = SHORT_MODEL.predict_proba(feats)
    p_l = LONG_MODEL.predict_proba(feats)
    # combined confidence (simple blend)
    conf_short = min(0.99, 0.6 * p_s + 0.4 * min(1, score/2))
    conf_long = min(0.99, 0.6 * p_l + 0.4 * min(1, score/2))
    return {
        'product_id': product_id,
        'price': feats['close'],
        'features': feats,
        'heuristic': h,
        'conf_short': conf_short,
        'conf_long': conf_long
    }

def label_and_update(product_id, lookahead_short=3, lookahead_long=288, granularity=300):
    # improved labeling using percent thresholds based on volatility (ATR)
    candles = fetch_candles(product_id, granularity=granularity, limit=lookahead_long+50)
    if len(candles) < lookahead_long+10:
        return {'error':'not_enough_data'}
    df = candles_to_df(candles)
    feats_series = []
    y_short = []
    y_long = []
    closes = df['close'].values
    atr_series = (df['high'] - df['low']).rolling(window=14).mean().fillna(method='bfill').values
    for i in range(len(df)-lookahead_long-1):
        window = df.iloc[:i+1]
        feats = make_features(window)
        future_short = df['close'].iloc[i+1:i+1+lookahead_short]
        if len(future_short) < lookahead_short:
            continue
        fut_ret = (future_short.iloc[-1] - window['close'].iloc[-1]) / window['close'].iloc[-1]
        # short threshold proportional to ATR
        thr_short = 0.002 + 0.5 * (atr_series[i] / window['close'].iloc[-1])
        label_short = 1 if fut_ret > thr_short else 0
        # long label
        future_long = df['close'].iloc[i+1:i+1+lookahead_long]
        if len(future_long) < lookahead_long:
            continue
        fut_ret_long = (future_long.iloc[-1] - window['close'].iloc[-1]) / window['close'].iloc[-1]
        thr_long = 0.01 + 0.5 * (atr_series[i] / window['close'].iloc[-1])
        label_long = 1 if fut_ret_long > thr_long else 0
        feats_series.append(feats)
        y_short.append(label_short)
        y_long.append(label_long)
    if feats_series:
        SHORT_MODEL.partial_update(feats_series, y_short)
        LONG_MODEL.partial_update(feats_series, y_long)
        return {'updated': len(feats_series)}
    return {'error':'no_labels'}
