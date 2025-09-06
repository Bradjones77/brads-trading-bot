# features.py - expanded features: EMA, RSI, MACD, ATR, volume metrics
import pandas as pd, numpy as np

def candles_to_df(candles):
    df = pd.DataFrame(candles, columns=['time','low','high','open','close','volume'])
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
    df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
    return df

def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta>0,0)).rolling(window=period).mean()
    loss = (-delta.where(delta<0,0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)

def macd(series, short=12, long=26, signal=9):
    ema_short = ema(series, short)
    ema_long = ema(series, long)
    macd_line = ema_short - ema_long
    macd_signal = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - macd_signal
    return macd_line, macd_signal, hist

def atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean().fillna(method='bfill')

def make_features(df):
    close = df['close']
    f = {}
    f['close'] = close.iloc[-1]
    f['ret_1'] = close.pct_change().iloc[-1]
    f['ema9'] = ema(close,9).iloc[-1]
    f['ema21'] = ema(close,21).iloc[-1]
    f['rsi14'] = rsi(close,14).iloc[-1]
    m_line, m_signal, m_hist = macd(close)
    f['macd_hist'] = m_hist.iloc[-1]
    a = atr(df)
    f['atr'] = a.iloc[-1] if not a.empty else 0.0
    f['vol_mean_20'] = df['volume'].rolling(window=20).mean().iloc[-1]
    f['ema9_ema21_diff'] = f['ema9'] - f['ema21']
    return f
