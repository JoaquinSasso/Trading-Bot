import yfinance as yf
import pandas as pd
from pathlib import Path
import time

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "data" / "historical_2010_2026"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

UNIVERSE_100 = [
    # Top Mega Caps & Tech
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "COST", "PEP", 
    "CSCO", "TMUS", "ADBE", "TXN", "NFLX", "AMD", "INTC", "INTU", "CMCSA", "QCOM", 
    "AMGN", "HON", "AMAT", "SBUX", "ISRG", "BKNG", "MDLZ", "GILD", "ADP", "VRTX", 
    "REGN", "PANW", "LRCX", "SNPS", "CDNS", "KLAC", "CHTR", "MAR", "CRWD", "ORLY", 
    "ABNB", "CTAS", "MELI", "MNST", "PYPL", "DXCM", "WDAY", "FTNT", "MRVL", "KDP", 
    "KHC", "PAYX", "LULU", "ADSK", "PCAR", "EXC", "CTSH", "EA", "ROST", "BIIB", 
    "VRSK", "ODFL", "IDXX", "FAST", "WBD", "ILMN", "EBAY", "ZM", "CRM",
    # Finance, Health, Industrials
    "JPM", "BAC", "V", "MA", "UNH", "JNJ", "PG", "HD", "CVX", "ABBV", "MRK", "KO", 
    "TMO", "MCD", "ACN", "ABT", "LIN", "DIS", "DHR", "NEM", "LLY", "XOM",
    # Commodities & Bonds
    "GLD", "SLV", "SPY", "QQQ", "TLT", "IEF", "BIL", "SGOV"
]

print(f"Descargando {len(UNIVERSE_100)} activos desde 2010...")

data = yf.download(
    tickers=UNIVERSE_100,
    start="2010-01-01",
    end="2026-03-01",
    group_by="ticker",
    auto_adjust=False, # We want raw close + adj close to standardise
    threads=True
)

for ticker in UNIVERSE_100:
    try:
        # yfinance returns multi-index columns if multiple tickers are passed
        if len(UNIVERSE_100) > 1:
            df = data[ticker].copy()
        else:
            df = data.copy()
            
        df = df.dropna(subset=['Close'])
        
        if df.empty:
            print(f"Advertencia: No hay datos para {ticker}")
            continue
            
        # Standardize columns: date, open, high, low, close, volume
        df = df.reset_index()
        df.rename(columns={
            "Date": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume"
        }, inplace=True)
        
        # Calculate split-adjusted prices if 'Adj Close' exists
        if 'Adj Close' in df.columns:
            ratio = df['Adj Close'] / df['close']
            df['open'] = round(df['open'] * ratio, 4)
            df['high'] = round(df['high'] * ratio, 4)
            df['low'] = round(df['low'] * ratio, 4)
            df['close'] = round(df['Adj Close'], 4)
            
        df['date'] = df['date'].dt.strftime('%Y-%m-%d')
        
        output_path = OUTPUT_DIR / f"{ticker}_daily.csv"
        df[['date', 'open', 'high', 'low', 'close', 'volume']].to_csv(output_path, index=False)
        
    except Exception as e:
        print(f"Error procesando {ticker}: {e}")

print("Descarga completada!")
