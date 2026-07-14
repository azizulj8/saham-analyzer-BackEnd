"""
PDF Parser Service
Mengekstrak data keuangan dari PDF laporan keuangan IDX menggunakan pdfplumber.
Mendukung format laporan keuangan standar IDX: Perbankan, Non-perbankan.
"""
import re
import logging
from typing import Optional, List, Tuple
from pathlib import Path

import pdfplumber

import yfinance as yf
from datetime import datetime

from models.schemas import LabaRugi, Neraca, ArusKas, FinancialData

logger = logging.getLogger(__name__)

# ─── Keyword Patterns untuk Cari Data ────────────────────────────────────────

PATTERNS = {
    # Laba Rugi
    "revenue": [
        r"pendapatan\s+neto\b",
        r"pendapatan\s+usaha\b",
        r"pendapatan\s+bunga\s+(?:dan\s+syariah\s+)?(?:bersih)?",
        r"net\s+revenues?\b",
        r"penjualan\s+neto\b",
        r"jumlah\s+pendapatan\b",
    ],
    "net_profit": [
        r"laba\s+(?:\(rugi\)\s+)?yang\s+dapat",
        r"jumlah\s+laba\s+(?:\(rugi\))?(?!\s+(?:operasional|bruto))",
        r"total\s+profit\s+(?:\(loss\))?",
        r"laba\s+(?:\(rugi\)\s+)?bersih\s+tahun\s+berjalan",
        r"profit\s+(?:\(loss\)\s+)?for\s+the\s+year",
        r"laba\s+(?:\(rugi\)\s+)?tahun\s+berjalan",
        r"net\s+income",
        r"laba\s+(?:\(rugi\)\s+)?bersih",
    ],
    "eps": [
        r"(?:basic\s+)?earnings?\s+(?:\(loss\)\s+)?per\s+share",
        r"laba\s+(?:\(rugi\)\s+)?per\s+saham",
        r"eps\b",
    ],
    "gross_profit": [
        r"laba\s+kotor\b",
        r"gross\s+profit\b",
    ],
    "operating_profit": [
        r"laba\s+(?:usaha|operasional)\b",
        r"operating\s+(?:income|profit)\b",
    ],

    # Neraca
    "total_assets": [
        r"jumlah\s+aset\b",
        r"total\s+aset\b",
        r"total\s+assets\b",
    ],
    "total_equity": [
        r"jumlah\s+ekuitas\b",
        r"total\s+ekuitas\b",
        r"total\s+equity\b",
        r"ekuitas\s+yang\s+dapat\s+diatribusikan",
    ],
    "total_liabilities": [
        r"jumlah\s+liabilitas\b",
        r"total\s+liabilitas\b",
        r"total\s+(?:kewajiban|utang)\b",
        r"total\s+liabilities\b",
    ],
    "total_loans": [
        r"kredit\s+yang\s+diberikan",
        r"pinjaman\s+yang\s+diberikan",
        r"total\s+kredit\b",
        r"loans?\s+and\s+financing",
    ],
    "cash": [
        r"kas\s+dan\s+setara\s+kas\b",
        r"cash\s+and\s+cash\s+equivalents?\b",
    ],
    "current_assets": [
        r"jumlah\s+aset\s+(?:lancar|tidak\s+tetap)\b",
        r"total\s+aset\s+lancar\b",
        r"total\s+current\s+assets?\b",
    ],
    "current_liabilities": [
        r"jumlah\s+liabilitas\s+jangka\s+pendek\b",
        r"total\s+liabilitas\s+lancar\b",
        r"total\s+current\s+liabilities?\b",
    ],

    # Arus Kas
    "operating_cf": [
        r"arus\s+kas\s+(?:bersih\s+)?(?:dari|untuk)\s+(?:aktivitas\s+)?operasi",
        r"net\s+cash\s+(?:from|used\s+in)\s+operating\s+activities?",
    ],
    "investing_cf": [
        r"arus\s+kas\s+(?:bersih\s+)?(?:dari|untuk)\s+(?:aktivitas\s+)?investasi",
        r"net\s+cash\s+(?:from|used\s+in)\s+investing\s+activities?",
    ],
    "financing_cf": [
        r"arus\s+kas\s+(?:bersih\s+)?(?:dari|untuk)\s+(?:aktivitas\s+)?pendanaan",
        r"net\s+cash\s+(?:from|used\s+in)\s+financing\s+activities?",
    ],
    "capex": [
        r"pembelian\s+(?:aset\s+tetap|properti|peralatan)",
        r"capital\s+expenditures?\b",
        r"acquisition\s+of\s+fixed\s+assets?\b",
    ],

    # Bank-specific
    "npl": [
        r"non[\s-]?performing\s+(?:loan|credit)",
        r"kredit\s+(?:bermasalah|macet)",
        r"npl\b",
    ],
    "casa": [
        r"giro\s+dan\s+tabungan\b",
        r"current\s+accounts?\s+and\s+savings?\b",
        r"casa\b",
        r"tabungan\s+dan\s+giro\b",
    ],
    "shares": [
        r"(?:jumlah\s+)?saham\s+(?:yang\s+)?beredar",
        r"shares?\s+outstanding",
        r"shares?\s+issued\s+and\s+outstanding",
    ],
}


class PDFParser:
    """
    Parser laporan keuangan IDX dari file PDF.
    Menggunakan pdfplumber untuk ekstraksi teks dan tabel.
    """

    def __init__(self):
        self.number_pattern = re.compile(
            r"(?<![a-zA-Z])"
            r"([-]?\s*(?:\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?))"
            r"(?!\s*[a-zA-Z%])"
        )

    def _detect_currency(self, text: str) -> bool:
        """Deteksi apakah laporan menggunakan USD."""
        # Ambil 5000 karakter pertama saja untuk efisiensi
        header_text = text[:5000].lower()
        patterns = [
            r"dolar\s+amerika\s+serikat",
            r"united\s+states\s+dollar",
            r"disajikan\s+dalam\s+usd",
            r"dalam\s+dolar\s+as\b"
        ]
        for p in patterns:
            if re.search(p, header_text):
                return True
        return False

    def _get_dynamic_exchange_rate(self, year: int) -> float:
        """Dapatkan kurs USDIDR pada akhir tahun laporan."""
        try:
            logger.info(f"Mencari kurs historis USDIDR untuk akhir tahun {year}...")
            ticker = yf.Ticker("USDIDR=X")
            # Cari data sekitar akhir Desember tahun tersebut
            start_date = f"{year}-12-15"
            end_date = f"{year + 1}-01-10"
            hist = ticker.history(start=start_date, end=end_date)
            
            if not hist.empty:
                # Cari kurs terakhir di tahun tersebut
                for date, row in hist.iterrows():
                    if date.year == year:
                        rate = float(row['Close'])
                        logger.info(f"Kurs historis ditemukan: {rate} (pada {date.strftime('%Y-%m-%d')})")
                        return rate
                
                # Jika tidak ada yang pas tahunnya, pakai yang terakhir di history itu
                rate = float(hist.iloc[-1]['Close'])
                logger.info(f"Kurs historis (fallback rentang) ditemukan: {rate}")
                return rate
        except Exception as e:
            logger.warning(f"Gagal mengambil kurs historis dari yfinance: {e}")
            
        # Fallback statis jika gagal
        fallback = 15500.0
        logger.warning(f"Menggunakan kurs statis fallback: {fallback}")
        return fallback

    def parse(
        self,
        pdf_path: str,
        ticker: str,
        year: int,
        company_name: str = "",
        sector: str = "",
        shares_outstanding: float = 0
    ) -> Optional[FinancialData]:
        """
        Parse PDF laporan keuangan dan ekstrak semua data keuangan.
        
        Returns:
            FinancialData atau None jika parsing gagal
        """
        try:
            logger.info(f"Parsing PDF: {pdf_path} untuk {ticker} tahun {year}")

            with pdfplumber.open(pdf_path) as pdf:
                # Ekstrak semua teks dari seluruh halaman
                all_text = ""
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        all_text += text + "\n"

                # Ekstrak tabel dari semua halaman
                all_tables = []
                for page in pdf.pages:
                    tables = page.extract_tables()
                    if tables:
                        all_tables.extend(tables)

            if not all_text:
                logger.error(f"Tidak bisa ekstrak teks dari PDF: {pdf_path}")
                return None

            # Parse masing-masing laporan
            laba_rugi = self._extract_laba_rugi(all_text, all_tables, year)
            neraca = self._extract_neraca(all_text, all_tables, year)
            arus_kas = self._extract_arus_kas(all_text, all_tables, year)

            # Deteksi mata uang
            is_usd = self._detect_currency(all_text)
            exchange_rate = 1.0
            if is_usd:
                exchange_rate = self._get_dynamic_exchange_rate(year)
                logger.info(f"PDF terdeteksi menggunakan USD. Menggunakan kurs: {exchange_rate}")

            # Coba ekstrak jumlah saham dari teks jika belum ada
            if shares_outstanding == 0:
                shares_outstanding = self._extract_shares(all_text) or 0

            # Jika USD, kalikan semua nilai finansial dengan kurs (kecuali jumlah saham)
            if is_usd and exchange_rate > 1.0:
                def convert(val):
                    return val * exchange_rate if val is not None else None

                laba_rugi.revenue = convert(laba_rugi.revenue)
                laba_rugi.net_profit = convert(laba_rugi.net_profit)
                laba_rugi.gross_profit = convert(laba_rugi.gross_profit)
                laba_rugi.operating_profit = convert(laba_rugi.operating_profit)

                neraca.total_assets = convert(neraca.total_assets)
                neraca.total_equity = convert(neraca.total_equity)
                neraca.total_liabilities = convert(neraca.total_liabilities)
                neraca.total_loans = convert(neraca.total_loans)
                neraca.cash_and_equiv = convert(neraca.cash_and_equiv)
                neraca.current_assets = convert(neraca.current_assets)
                neraca.current_liabilities = convert(neraca.current_liabilities)
                neraca.casa = convert(neraca.casa)

                arus_kas.operating_cf = convert(arus_kas.operating_cf)
                arus_kas.investing_cf = convert(arus_kas.investing_cf)
                arus_kas.financing_cf = convert(arus_kas.financing_cf)
                arus_kas.capex = convert(arus_kas.capex)
                arus_kas.free_cash_flow = convert(arus_kas.free_cash_flow)

            logger.info(
                f"Parsing selesai untuk {ticker} {year}: "
                f"Revenue={laba_rugi.revenue}, NetProfit={laba_rugi.net_profit}, "
                f"TotalAssets={neraca.total_assets}, TotalEquity={neraca.total_equity}"
            )

            return FinancialData(
                ticker=ticker,
                company_name=company_name or ticker,
                sector=sector,
                year=year,
                laba_rugi=laba_rugi,
                neraca=neraca,
                arus_kas=arus_kas,
                shares_outstanding=shares_outstanding,
                source_pdf_url=pdf_path,
            )

        except Exception as e:
            logger.error(f"Error parsing PDF {pdf_path}: {e}", exc_info=True)
            return None

    def _normalize_number(self, text: str) -> Optional[float]:
        """
        Konversi teks angka IDX (misal: '1.234.567' atau '1,234,567') ke float.
        IDX menggunakan format: 1.234.567 (titik sebagai ribuan, koma sebagai desimal)
        """
        if not text:
            return None

        text = text.strip().replace(" ", "")

        # Handle tanda kurung sebagai negatif: (1.234) = -1234
        is_negative = text.startswith("(") and text.endswith(")")
        if is_negative:
            text = text[1:-1]

        # Remove currency symbols
        text = re.sub(r"[RpIDR$€£¥]", "", text, flags=re.IGNORECASE)

        # Format IDX: titik untuk ribuan, koma untuk desimal
        # Deteksi format berdasarkan posisi titik/koma
        dots = text.count(".")
        commas = text.count(",")

        if dots > 0 and commas > 0:
            # Ada keduanya: format 1.234.567,89 (IDX standard)
            text = text.replace(".", "").replace(",", ".")
        elif dots > 1:
            # Multiple dots: format 1.234.567 (ribuan pakai titik)
            text = text.replace(".", "")
        elif commas > 1:
            # Multiple commas: format 1,234,567 (US format)
            text = text.replace(",", "")
        elif dots == 1:
            # Satu titik: bisa desimal (1.5) atau ribuan (1.000)
            parts = text.split(".")
            if len(parts[1]) == 3:
                text = text.replace(".", "")  # ribuan: 1.000 → 1000
            # else: desimal, biarkan
        elif commas == 1:
            # Satu koma: IDX desimal (1234,5)
            text = text.replace(",", ".")

        try:
            val = float(text)
            return -val if is_negative else val
        except ValueError:
            return None

    def _find_value_in_text(
        self,
        text: str,
        patterns: List[str],
        search_radius: int = 200,
        prefer_largest: bool = False
    ) -> Optional[float]:
        """
        Cari nilai numerik di dekat keyword dalam teks.
        
        Args:
            text: Teks lengkap laporan keuangan
            patterns: List regex patterns untuk dicari
            search_radius: Berapa karakter setelah keyword dicari
            prefer_largest: Pilih angka terbesar (biasanya total, bukan subtotal)
        """
        text_lower = text.lower()

        for pattern in patterns:
            # Cari semua kemunculan pattern
            for match in re.finditer(pattern, text_lower, re.IGNORECASE):
                start = match.start()
                end = match.end()

                # Ambil teks di sekitar keyword
                context = text[end:end + search_radius]

                # Cari semua angka di context
                numbers = []
                for num_match in re.finditer(
                    r"(?<![\d.,])([-]?\(?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?\s*\)?)(?![\d.,])",
                    context
                ):
                    val = self._normalize_number(num_match.group(1))
                    if val is not None and abs(val) > 0:
                        # Ignore common years that might be matched as values
                        if abs(val) in [2021, 2022, 2023, 2024, 2025, 2026]:
                            continue
                        numbers.append(val)

                if numbers:
                    # Filter: abaikan angka yang terlalu kecil (< 100)
                    # karena laporan keuangan IDX biasanya dalam jutaan/miliar
                    significant = [n for n in numbers if abs(n) >= 100]
                    if significant:
                        return max(significant, key=abs) if prefer_largest else significant[0]
                    elif numbers:
                        return numbers[0]

        return None

    def _find_value_in_tables(
        self,
        tables: List[List[List]],
        patterns: List[str]
    ) -> Optional[float]:
        """
        Cari nilai di tabel-tabel PDF.
        Cocok untuk laporan keuangan yang terformat sebagai tabel.
        """
        for table in tables:
            for row in table:
                if not row:
                    continue

                # Kolom pertama biasanya label, kolom berikutnya nilai
                label = str(row[0] or "").lower().strip()

                for pattern in patterns:
                    if re.search(pattern, label, re.IGNORECASE):
                        # Ambil nilai dari kolom terakhir yang berisi angka
                        for cell in reversed(row[1:]):
                            if cell:
                                val = self._normalize_number(str(cell))
                                if val is not None and abs(val) > 0:
                                    return val

        return None

    def _extract_laba_rugi(
        self, text: str, tables: List, year: int
    ) -> LabaRugi:
        """Ekstrak data Laporan Laba Rugi"""
        result = LabaRugi(year=year)

        def find(field_patterns: List[str]) -> Optional[float]:
            val = self._find_value_in_tables(tables, field_patterns)
            if val is None:
                val = self._find_value_in_text(text, field_patterns)
            return val

        result.revenue = find(PATTERNS["revenue"])
        result.net_profit = find(PATTERNS["net_profit"])
        result.gross_profit = find(PATTERNS["gross_profit"])
        result.operating_profit = find(PATTERNS["operating_profit"])

        logger.debug(
            f"LabaRugi: revenue={result.revenue}, net_profit={result.net_profit}"
        )
        return result

    def _extract_neraca(
        self, text: str, tables: List, year: int
    ) -> Neraca:
        """Ekstrak data Neraca / Balance Sheet"""
        result = Neraca(year=year)

        def find(field_patterns: List[str]) -> Optional[float]:
            val = self._find_value_in_tables(tables, field_patterns)
            if val is None:
                val = self._find_value_in_text(text, field_patterns)
            return val

        result.total_assets = find(PATTERNS["total_assets"])
        result.total_equity = find(PATTERNS["total_equity"])
        result.total_liabilities = find(PATTERNS["total_liabilities"])
        result.total_loans = find(PATTERNS["total_loans"])
        result.cash_and_equiv = find(PATTERNS["cash"])
        result.current_assets = find(PATTERNS["current_assets"])
        result.current_liabilities = find(PATTERNS["current_liabilities"])
        result.casa = find(PATTERNS["casa"])

        # Cari NPL ratio (biasanya dalam %)
        npl_val = find(PATTERNS["npl"])
        if npl_val and npl_val < 100:  # NPL dalam persentase
            result.npl_gross = npl_val

        logger.debug(
            f"Neraca: total_assets={result.total_assets}, "
            f"total_equity={result.total_equity}"
        )
        return result

    def _extract_arus_kas(
        self, text: str, tables: List, year: int
    ) -> ArusKas:
        """Ekstrak data Laporan Arus Kas"""
        result = ArusKas(year=year)

        def find(field_patterns: List[str]) -> Optional[float]:
            val = self._find_value_in_tables(tables, field_patterns)
            if val is None:
                val = self._find_value_in_text(text, field_patterns)
            return val

        result.operating_cf = find(PATTERNS["operating_cf"])
        result.investing_cf = find(PATTERNS["investing_cf"])
        result.financing_cf = find(PATTERNS["financing_cf"])
        result.capex = find(PATTERNS["capex"])

        # Hitung Free Cash Flow
        if result.operating_cf and result.capex:
            result.free_cash_flow = result.operating_cf - abs(result.capex)
        elif result.operating_cf:
            result.free_cash_flow = result.operating_cf

        return result

    def _extract_shares(self, text: str) -> Optional[float]:
        """Ekstrak jumlah saham beredar dari teks"""
        return self._find_value_in_text(text, PATTERNS["shares"], prefer_largest=False)

    def extract_text_for_llm(self, pdf_path: str, max_chars: int = 50000) -> str:
        """
        Ekstrak teks dari PDF untuk dianalisis LLM.
        Fokus pada bagian yang relevan: pendahuluan, highlight, dan catatan penting.
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                text_parts = []
                total_chars = 0

                for i, page in enumerate(pdf.pages):
                    if total_chars >= max_chars:
                        break

                    text = page.extract_text()
                    if text:
                        text_parts.append(f"=== HALAMAN {i+1} ===\n{text}")
                        total_chars += len(text)

                full_text = "\n".join(text_parts)
                return full_text[:max_chars]

        except Exception as e:
            logger.error(f"Error extract text untuk LLM dari {pdf_path}: {e}")
            return ""
