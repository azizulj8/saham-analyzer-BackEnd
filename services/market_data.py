"""
Market Data Service
Mengambil harga saham terkini dan data market lainnya.
Sumber: Yahoo Finance (via yfinance) untuk saham IDX (.JK suffix)
"""
import logging
from typing import Optional, Tuple
from datetime import datetime, timedelta

import yfinance as yf

logger = logging.getLogger(__name__)


class MarketDataService:
    """Service untuk ambil data pasar dari Yahoo Finance"""

    @staticmethod
    def _get_ticker_symbol(ticker: str) -> str:
        """Konversi kode IDX ke format Yahoo Finance (tambah .JK)"""
        ticker = ticker.upper().strip()
        if not ticker.endswith(".JK"):
            ticker = f"{ticker}.JK"
        return ticker

    async def get_current_price(self, ticker: str) -> Optional[float]:
        """
        Ambil harga saham terkini dari Yahoo Finance.
        
        Returns:
            Harga terkini dalam Rupiah, atau None jika gagal
        """
        symbol = self._get_ticker_symbol(ticker)
        try:
            stock = yf.Ticker(symbol)
            hist = stock.history(period="5d")

            if hist.empty:
                logger.warning(f"Tidak ada data harga untuk {symbol}")
                return None

            # Ambil harga penutupan terbaru
            price = float(hist["Close"].iloc[-1])
            logger.info(f"Harga terkini {symbol}: Rp{price:,.0f}")
            return price

        except Exception as e:
            logger.error(f"Error ambil harga untuk {ticker}: {e}")
            return None

    async def get_market_data(self, ticker: str) -> dict:
        """
        Ambil data market lengkap: harga, 52w high/low, market cap, volume, dll.
        
        Returns:
            Dict berisi semua data market yang tersedia
        """
        symbol = self._get_ticker_symbol(ticker)
        result = {
            "ticker": ticker,
            "symbol": symbol,
            "current_price": None,
            "price_52w_high": None,
            "price_52w_low": None,
            "market_cap": None,
            "volume": None,
            "avg_volume": None,
            "dividend_yield": None,
            "per_trailing": None,
            "per_forward": None,
            "pbv": None,
            "shares_outstanding": None,
            "beta": None,
            "roe": None,
            "roa": None,
            "npm": None,
            "debt_to_equity": None,
            "revenue_growth": None,
            "earnings_growth": None,
            "currency": "IDR",
            "exchange": "IDX",
            "retrieved_at": datetime.now().isoformat(),
        }

        try:
            stock = yf.Ticker(symbol)
            info = stock.info

            # Harga
            result["current_price"] = (
                info.get("currentPrice") or
                info.get("regularMarketPrice") or
                info.get("previousClose")
            )
            result["price_52w_high"] = info.get("fiftyTwoWeekHigh")
            result["price_52w_low"] = info.get("fiftyTwoWeekLow")

            # Market data
            result["market_cap"] = info.get("marketCap")
            result["volume"] = info.get("regularMarketVolume")
            result["avg_volume"] = info.get("averageVolume")

            # Valuasi dari Yahoo (sebagai cross-check, bukan data utama)
            result["per_trailing"] = info.get("trailingPE")
            result["per_forward"] = info.get("forwardPE")
            result["pbv"] = info.get("priceToBook")

            # Dividen
            div_yield = info.get("dividendYield")
            if div_yield:
                result["dividend_yield"] = div_yield * 100  # Convert to %

            # Saham beredar
            result["shares_outstanding"] = info.get("sharesOutstanding")

            # Fundamental Data
            result["roe"] = info.get("returnOnEquity")
            result["roa"] = info.get("returnOnAssets")
            result["npm"] = info.get("profitMargins")
            result["debt_to_equity"] = info.get("debtToEquity")
            result["revenue_growth"] = info.get("revenueGrowth")
            result["earnings_growth"] = info.get("earningsGrowth")

            # Beta
            result["beta"] = info.get("beta")

            logger.info(
                f"Market data {symbol}: "
                f"Price=Rp{result['current_price']:,.0f}, "
                f"MarketCap={result['market_cap']}"
            )

        except Exception as e:
            logger.error(f"Error ambil market data untuk {ticker}: {e}", exc_info=True)

        return result

    async def get_historical_prices(
        self,
        ticker: str,
        period: str = "5y"
    ) -> Optional[dict]:
        """
        Ambil data harga historis untuk chart.
        
        Args:
            ticker: Kode saham IDX
            period: Periode data ('1y', '3y', '5y', '10y')
        
        Returns:
            Dict berisi dates dan prices
        """
        symbol = self._get_ticker_symbol(ticker)
        try:
            stock = yf.Ticker(symbol)
            hist = stock.history(period=period)

            if hist.empty:
                return None

            return {
                "dates": [d.strftime("%Y-%m-%d") for d in hist.index],
                "closes": [round(p, 2) for p in hist["Close"].tolist()],
                "volumes": hist["Volume"].tolist(),
                "period": period,
            }

        except Exception as e:
            logger.error(f"Error ambil harga historis untuk {ticker}: {e}")
            return None

    async def get_corporate_actions(self, ticker: str) -> dict:
        """
        Ambil corporate actions: dividen, stock split.
        """
        symbol = self._get_ticker_symbol(ticker)
        result = {
            "dividends": [],
            "splits": [],
        }

        try:
            stock = yf.Ticker(symbol)

            # Dividen
            dividends = stock.dividends
            if not dividends.empty:
                result["dividends"] = [
                    {
                        "date": date.strftime("%Y-%m-%d"),
                        "amount": round(float(amount), 2),
                    }
                    for date, amount in dividends.tail(10).items()
                ]

            # Stock splits
            splits = stock.splits
            if not splits.empty:
                result["splits"] = [
                    {
                        "date": date.strftime("%Y-%m-%d"),
                        "ratio": float(ratio),
                    }
                    for date, ratio in splits.items()
                ]

        except Exception as e:
            logger.error(f"Error ambil corporate actions untuk {ticker}: {e}")

        return result

    async def get_historical_per_pbv(
        self,
        ticker: str,
        years: int = 5
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Estimasi rata-rata PER dan PBV historis.
        Digunakan untuk menilai apakah valuasi saat ini murah/mahal vs historis.
        
        Returns:
            Tuple (avg_per_historical, avg_pbv_historical)
        """
        symbol = self._get_ticker_symbol(ticker)
        try:
            stock = yf.Ticker(symbol)
            info = stock.info

            # yfinance tidak selalu menyediakan historis PER/PBV
            # Gunakan data yang tersedia sebagai approximasi
            trailing_per = info.get("trailingPE")
            pbv = info.get("priceToBook")

            # Rata-rata historis berdasarkan sektor IDX (hardcoded reference)
            # Ini akan diupdate secara berkala
            SECTOR_PER_AVERAGES = {
                "Financial Services": 12.0,
                "Perbankan": 12.0,
                "Technology": 25.0,
                "Energy": 10.0,
                "Consumer Defensive": 18.0,
                "Consumer Cyclical": 15.0,
                "Basic Materials": 8.0,
                "Healthcare": 20.0,
                "Telecommunication": 16.0,
                "Infrastructure": 14.0,
                "default": 14.0,
            }

            sector = info.get("sector", "default")
            historical_per = SECTOR_PER_AVERAGES.get(
                sector, SECTOR_PER_AVERAGES["default"]
            )

            return historical_per, 2.0  # Default historical PBV

        except Exception as e:
            logger.error(f"Error estimasi historical PER/PBV untuk {ticker}: {e}")
            return 14.0, 2.0  # Default fallback
