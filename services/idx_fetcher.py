"""
IDX Fetcher Service — Fixed Version
Mengambil laporan keuangan dari IDX (Bursa Efek Indonesia)

IDX menyediakan data melalui beberapa endpoint:
1. Primary API: https://idx.co.id/primary/TbEmiten/GetFinancialReport
2. Open API: https://idx.co.id/api/download/FT-LAPKEU-TRIWULAN_{ticker}_{year}Q{quarter}.pdf
3. Fallback: Scraping HTML IDX
"""
import os
import re
import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Tuple
from pathlib import Path

import aiohttp
import aiofiles
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# IDX Endpoints
IDX_BASE = "https://idx.co.id"
IDX_FINANCIAL_REPORT_API = "https://idx.co.id/primary/TbEmiten/GetFinancialReport"
IDX_OPEN_API_BASE = "https://idx.co.id/api"


class IDXFetcher:
    """
    Service untuk fetch laporan keuangan dari IDX.
    """

    def __init__(self, cache_dir: str = "./cache"):
        self.cache_dir = Path(cache_dir)
        self.pdf_dir = self.cache_dir / "pdfs"
        self.pdf_dir.mkdir(parents=True, exist_ok=True)

        self.session: Optional[aiohttp.ClientSession] = None

        # Headers yang menyerupai browser agar IDX tidak memblokir
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/html, */*",
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": "https://idx.co.id/",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self.session = aiohttp.ClientSession(
                headers=self.headers,
                connector=connector,
            )
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def get_financial_reports_list(
        self,
        ticker: str,
        year: Optional[int] = None,
        report_type: str = "annual",
    ) -> List[dict]:
        """
        Ambil daftar laporan keuangan dari IDX.
        Coba beberapa endpoint, dari yang paling reliable.
        """
        ticker = ticker.upper()
        target_year = year or datetime.now().year

        # Strategi 1: IDX Primary API (paling reliable)
        reports = await self._try_idx_primary_api(ticker, target_year, report_type)
        if reports:
            logger.info(f"IDX Primary API berhasil untuk {ticker}")
            return reports

        # Strategi 2: IDX dengan periode berbeda
        reports = await self._try_idx_primary_api(ticker, target_year - 1, report_type)
        if reports:
            logger.info(f"IDX Primary API berhasil untuk {ticker} tahun {target_year-1}")
            return reports

        # Strategi 3: Construct URL langsung dari pola IDX
        reports = self._construct_direct_urls(ticker, target_year)
        if reports:
            logger.info(f"Menggunakan constructed URL untuk {ticker}")
            return reports

        logger.warning(f"Semua strategi IDX gagal untuk {ticker}")
        return []

    async def _try_idx_primary_api(
        self,
        ticker: str,
        year: int,
        report_type: str,
    ) -> List[dict]:
        """Coba IDX Primary API endpoint"""
        session = await self._get_session()

        # Parameter sesuai IDX API
        params = {
            "emiten_code": ticker,
            "periode": "Tahunan" if report_type == "annual" else "Triwulan",
            "year": str(year),
            "start": "0",
            "length": "10",
        }

        endpoints = [
            IDX_FINANCIAL_REPORT_API,
            f"{IDX_BASE}/primary/TbEmiten/GetFinancialReport",
            f"{IDX_BASE}/primary/ListedCompany/GetFinancialReport",
        ]

        for endpoint in endpoints:
            try:
                async with session.get(
                    endpoint,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 200:
                        try:
                            data = await resp.json(content_type=None)
                            reports = self._parse_idx_api_response(data, ticker, year)
                            if reports:
                                return reports
                        except Exception as e:
                            logger.debug(f"Parse JSON error dari {endpoint}: {e}")

            except asyncio.TimeoutError:
                logger.debug(f"Timeout di {endpoint}")
            except Exception as e:
                logger.debug(f"Error di {endpoint}: {e}")

        return []

    def _parse_idx_api_response(self, data: dict, ticker: str, year: int) -> List[dict]:
        """Parse berbagai format response dari IDX API"""
        reports = []

        # Coba berbagai key yang mungkin digunakan IDX
        possible_data_keys = ["data", "Data", "result", "Result", "records", "Records"]
        items = []

        for key in possible_data_keys:
            if key in data and isinstance(data[key], list):
                items = data[key]
                break

        # Kalau langsung list
        if isinstance(data, list):
            items = data

        for item in items:
            # Cari URL attachment PDF
            pdf_url = (
                item.get("Attachment") or
                item.get("attachment") or
                item.get("File") or
                item.get("file") or
                item.get("url") or
                item.get("URL") or
                ""
            )

            if pdf_url and ".pdf" in pdf_url.lower():
                # Pastikan URL absolut
                if pdf_url.startswith("/"):
                    pdf_url = f"{IDX_BASE}{pdf_url}"
                elif not pdf_url.startswith("http"):
                    pdf_url = f"{IDX_BASE}/{pdf_url}"

                reports.append({
                    "ticker": ticker,
                    "year": item.get("Tahun", item.get("year", year)),
                    "period": "annual",
                    "pdf_url": pdf_url,
                    "title": item.get("Judul", item.get("title", f"Laporan Keuangan {ticker} {year}")),
                })

        return reports

    def _construct_direct_urls(self, ticker: str, year: int) -> List[dict]:
        """
        Construct URL langsung berdasarkan pola URL IDX yang diketahui.
        IDX menyimpan laporan keuangan dengan pola URL yang konsisten.
        """
        reports = []

        # Pola URL IDX untuk laporan keuangan tahunan
        url_patterns = [
            # Pola 1: Annual Report di Portals
            f"https://idx.co.id/Portals/0/StaticData/ListedCompanies/Corporate_Actions/New_Info_JSX/Jenis_Informasi/01_Laporan_Keuangan/04_Annual_Report/{year}/{ticker}/{ticker}_Annual_Report_{year}.pdf",
            # Pola 2: Laporan Keuangan Tahunan
            f"https://idx.co.id/Portals/0/StaticData/ListedCompanies/Corporate_Actions/New_Info_JSX/Jenis_Informasi/01_Laporan_Keuangan/02_Soft_Copy_Laporan_Tahunan/Laporan_Tahunan_{year}/{ticker}/LAPKEU_{ticker}_{year}.pdf",
        ]

        for url in url_patterns:
            reports.append({
                "ticker": ticker,
                "year": year,
                "period": "annual",
                "pdf_url": url,
                "title": f"Laporan Keuangan Tahunan {ticker} {year}",
            })

        return reports

    async def download_pdf(
        self,
        pdf_url: str,
        ticker: str,
        year: int,
        report_type: str = "annual",
    ) -> Optional[str]:
        """
        Download PDF laporan keuangan dan simpan ke cache.
        """
        filename = f"{ticker.upper()}_{year}_{report_type}.pdf"
        pdf_path = self.pdf_dir / filename

        # Cek cache
        if pdf_path.exists() and pdf_path.stat().st_size > 50000:
            logger.info(f"PDF dari cache: {pdf_path}")
            return str(pdf_path)

        session = await self._get_session()
        logger.info(f"Downloading PDF: {pdf_url}")

        try:
            async with session.get(
                pdf_url,
                timeout=aiohttp.ClientTimeout(total=120),
                allow_redirects=True,
            ) as resp:
                if resp.status == 200:
                    content = await resp.read()

                    # Validasi: harus PDF (cek magic bytes)
                    if not content.startswith(b"%PDF"):
                        logger.warning(f"Response bukan PDF dari {pdf_url}")
                        return None

                    # Validasi ukuran minimal (laporan keuangan minimal ~100KB)
                    if len(content) < 50000:
                        logger.warning(f"PDF terlalu kecil ({len(content)} bytes): {pdf_url}")
                        return None

                    async with aiofiles.open(pdf_path, "wb") as f:
                        await f.write(content)

                    logger.info(f"PDF disimpan: {pdf_path} ({len(content)/1024:.0f}KB)")
                    return str(pdf_path)

                elif resp.status in (301, 302, 303, 307, 308):
                    redirect_url = resp.headers.get("Location")
                    if redirect_url:
                        logger.info(f"Redirect ke: {redirect_url}")
                        return await self.download_pdf(redirect_url, ticker, year, report_type)
                else:
                    logger.warning(f"HTTP {resp.status} dari {pdf_url}")
                    return None

        except asyncio.TimeoutError:
            logger.error(f"Timeout download: {pdf_url}")
            return None
        except Exception as e:
            logger.error(f"Error download PDF: {e}")
            return None

    async def get_latest_annual_report(
        self,
        ticker: str,
    ) -> Tuple[Optional[str], Optional[int]]:
        """
        Ambil laporan keuangan tahunan terbaru.
        Coba dari tahun sekarang mundur ke 3 tahun lalu.
        """
        current_year = datetime.now().year

        for year in range(current_year, current_year - 4, -1):
            reports = await self.get_financial_reports_list(ticker, year, "annual")

            for report in reports:
                pdf_url = report.get("pdf_url")
                if pdf_url:
                    pdf_path = await self.download_pdf(pdf_url, ticker, year, "annual")
                    if pdf_path:
                        return pdf_path, year

            # Rate limiting
            await asyncio.sleep(0.5)

        return None, None

    async def get_multi_year_reports(
        self,
        ticker: str,
        years: int = 5,
    ) -> List[Tuple[str, int]]:
        """
        Ambil laporan keuangan untuk beberapa tahun (untuk CAGR).
        """
        current_year = datetime.now().year
        results = []

        for year in range(current_year, current_year - years, -1):
            reports = await self.get_financial_reports_list(ticker, year, "annual")

            downloaded = False
            for report in reports:
                pdf_url = report.get("pdf_url")
                if pdf_url:
                    pdf_path = await self.download_pdf(pdf_url, ticker, year, "annual")
                    if pdf_path:
                        results.append((pdf_path, year))
                        downloaded = True
                        break

            if not downloaded:
                logger.debug(f"Tidak ada laporan untuk {ticker} tahun {year}")

            await asyncio.sleep(0.5)

        return results

    async def get_company_info(self, ticker: str) -> dict:
        """Ambil info perusahaan: nama, sektor, jumlah saham beredar."""
        # Coba dari yfinance (paling reliable)
        try:
            import yfinance as yf
            stock = yf.Ticker(f"{ticker}.JK")
            info = stock.info

            if info and info.get("longName"):
                result = {
                    "company_name": info.get("longName", ticker),
                    "sector": info.get("sector", ""),
                    "shares_outstanding": info.get("sharesOutstanding", 0),
                    "listing_date": "",
                }
                logger.info(f"Company info dari yfinance: {result['company_name']}")
                return result
        except Exception as e:
            logger.warning(f"yfinance company info gagal untuk {ticker}: {e}")

        # Fallback: coba IDX API
        session = await self._get_session()
        try:
            url = f"{IDX_BASE}/primary/TbEmiten/GetEmiten?kodeEmiten={ticker}&start=0&length=1"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    items = data.get("data", data.get("Data", []))
                    if items:
                        item = items[0]
                        return {
                            "company_name": item.get("Name", item.get("name", ticker)),
                            "sector": item.get("Sector", item.get("sector", "")),
                            "shares_outstanding": item.get("SharesListed", 0),
                            "listing_date": item.get("ListingDate", ""),
                        }
        except Exception as e:
            logger.warning(f"IDX company info gagal untuk {ticker}: {e}")

        return {
            "company_name": ticker,
            "sector": "",
            "shares_outstanding": 0,
            "listing_date": "",
        }

    def save_manual_upload(self, ticker: str, year: int, pdf_content: bytes) -> str:
        """Simpan PDF upload manual."""
        filename = f"{ticker.upper()}_{year}_annual_manual.pdf"
        pdf_path = self.pdf_dir / filename
        pdf_path.write_bytes(pdf_content)
        logger.info(f"PDF manual tersimpan: {pdf_path} ({len(pdf_content)/1024:.0f}KB)")
        return str(pdf_path)

    def get_cached_pdf(self, ticker: str, year: int, report_type: str = "annual") -> Optional[str]:
        """Cek apakah PDF sudah ada di cache (termasuk manual upload)."""
        # Cek semua kemungkinan nama file
        patterns = [
            f"{ticker.upper()}_{year}_{report_type}.pdf",
            f"{ticker.upper()}_{year}_{report_type}_manual.pdf",
        ]

        for pattern in patterns:
            pdf_path = self.pdf_dir / pattern
            if pdf_path.exists() and pdf_path.stat().st_size > 50000:
                return str(pdf_path)

        return None
