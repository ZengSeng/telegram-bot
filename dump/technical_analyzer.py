"""
Technical analysis module.
Calculates indicators and trading signals from price data.
"""
import pandas as pd
import numpy as np
import ta


def run_analysis(group: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate technical indicators and trading signals for a single ticker.

    Args:
        group: DataFrame with OHLCV data for one ticker (grouped by ticker)

    Returns:
        DataFrame with added indicator and signal columns
    """
    df_analysis = group.copy()
    df_analysis['ticker'] = group.name  # restore ticker

    # -------------------------
    # Trend Indicators
    # -------------------------
    df_analysis['SMA_20'] = ta.trend.sma_indicator(df_analysis['close'], window=20)  # type: ignore
    df_analysis['SMA_50'] = ta.trend.sma_indicator(df_analysis['close'], window=50)  # type: ignore
    df_analysis['EMA_12'] = ta.trend.ema_indicator(df_analysis['close'], window=12)  # type: ignore
    df_analysis['EMA_26'] = ta.trend.ema_indicator(df_analysis['close'], window=26)  # type: ignore

    # -------------------------
    # MACD
    # -------------------------
    macd = ta.trend.MACD(df_analysis['close'])  # type: ignore
    df_analysis['MACD'] = macd.macd()
    df_analysis['MACD_Signal'] = macd.macd_signal()
    df_analysis['MACD_Hist'] = macd.macd_diff()

    # -------------------------
    # RSI
    # -------------------------
    df_analysis['RSI_14'] = ta.momentum.rsi(df_analysis['close'], window=14)  # type: ignore

    # -------------------------
    # Bollinger Bands
    # -------------------------
    bb = ta.volatility.BollingerBands(df_analysis['close'], window=20, window_dev=2)  # type: ignore
    df_analysis['BB_Upper'] = bb.bollinger_hband()
    df_analysis['BB_Middle'] = bb.bollinger_mavg()
    df_analysis['BB_Lower'] = bb.bollinger_lband()
    df_analysis['BB_Width'] = bb.bollinger_wband()

    # -------------------------
    # Volume Indicators
    # -------------------------
    df_analysis['Volume_SMA_20'] = ta.trend.sma_indicator(df_analysis['volume'], window=20)  # type: ignore
    df_analysis['OBV'] = ta.volume.on_balance_volume(df_analysis['close'], df_analysis['volume'])  # type: ignore

    df_analysis['Volume_Ratio'] = df_analysis['volume'] / df_analysis['Volume_SMA_20']

    # -------------------------
    # Signals
    # -------------------------
    df_analysis['Signal_RSI'] = np.where(df_analysis['RSI_14'] < 30, 1,
                                 np.where(df_analysis['RSI_14'] > 70, -1, 0))

    df_analysis['Signal_MACD'] = np.where(df_analysis['MACD'] > df_analysis['MACD_Signal'], 1,
                                  np.where(df_analysis['MACD'] < df_analysis['MACD_Signal'], -1, 0))

    df_analysis['Signal_Trend'] = np.where(df_analysis['close'] > df_analysis['SMA_20'], 1,
                                   np.where(df_analysis['close'] < df_analysis['SMA_20'], -1, 0))

    df_analysis['Signal_BB'] = np.where(df_analysis['close'] < df_analysis['BB_Lower'], 1,
                                np.where(df_analysis['close'] > df_analysis['BB_Upper'], -1, 0))

    df_analysis['Signal_Volume'] = np.where(df_analysis['Volume_Ratio'] > 1.5, 1,
                                    np.where(df_analysis['Volume_Ratio'] < 0.5, -1, 0))

    # -------------------------
    # Combined Signal
    # -------------------------
    df_analysis['Combined_Signal'] = (
        df_analysis['Signal_RSI'] * 0.25 +
        df_analysis['Signal_MACD'] * 0.25 +
        df_analysis['Signal_Trend'] * 0.20 +
        df_analysis['Signal_BB'] * 0.15 +
        df_analysis['Signal_Volume'] * 0.15
    )

    df_analysis['Trade_Signal'] = np.where(df_analysis['Combined_Signal'] > 0.3, 1,
                                   np.where(df_analysis['Combined_Signal'] < -0.3, -1, 0))

    return df_analysis
