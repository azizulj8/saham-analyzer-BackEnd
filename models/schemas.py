"""
Pydantic models / schemas untuk API response dan request
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class KeputusanEnum(str, Enum):
    BELI = "BELI"
    CICIL_BELI = "CICIL_BELI"
    TUNGGU = "TUNGGU"
    LEWATI = "LEWATI"


class RisikoLevel(str, Enum):
    RENDAH = "RENDAH"
    SEDANG = "SEDANG"
    TINGGI = "TINGGI"


# ─── Raw Financial Data ────────────────────────────────────────────────────────

class LabaRugi(BaseModel):
    """Data dari Laporan Laba Rugi"""
    revenue: Optional[float] = None                    # Pendapatan/Revenue
    gross_profit: Optional[float] = None               # Laba Kotor
    operating_profit: Optional[float] = None           # Laba Operasional
    net_profit: Optional[float] = None                 # Laba Bersih
    interest_income: Optional[float] = None            # Pendapatan Bunga (bank)
    interest_expense: Optional[float] = None           # Beban Bunga
    year: int = 0


class Neraca(BaseModel):
    """Data dari Neraca / Balance Sheet"""
    total_assets: Optional[float] = None               # Total Aset
    total_equity: Optional[float] = None               # Ekuitas / Modal
    total_liabilities: Optional[float] = None          # Total Utang/Kewajiban
    total_loans: Optional[float] = None                # Total Kredit (bank)
    npl_gross: Optional[float] = None                  # NPL Gross % (bank)
    casa: Optional[float] = None                       # CASA (bank)
    casa_ratio: Optional[float] = None                 # CASA Ratio % (bank)
    current_assets: Optional[float] = None             # Aset Lancar
    current_liabilities: Optional[float] = None        # Kewajiban Lancar
    cash_and_equiv: Optional[float] = None             # Kas & Setara Kas
    year: int = 0


class ArusKas(BaseModel):
    """Data dari Laporan Arus Kas"""
    operating_cf: Optional[float] = None               # Arus Kas Operasional
    investing_cf: Optional[float] = None               # Arus Kas Investasi
    financing_cf: Optional[float] = None               # Arus Kas Pendanaan
    free_cash_flow: Optional[float] = None             # FCF = Operating - Capex
    capex: Optional[float] = None                      # Capital Expenditure
    year: int = 0


class FinancialData(BaseModel):
    """Data keuangan lengkap 1 tahun"""
    ticker: str
    company_name: str
    sector: str = ""
    year: int
    laba_rugi: LabaRugi
    neraca: Neraca
    arus_kas: ArusKas
    shares_outstanding: Optional[float] = None         # Jumlah saham beredar
    source_pdf_url: Optional[str] = None
    extracted_at: Optional[datetime] = None


# ─── Calculated Metrics ───────────────────────────────────────────────────────

class FundamentalMetrics(BaseModel):
    """Semua rasio fundamental yang sudah dihitung"""
    # Profitabilitas
    roe: Optional[float] = None                        # Return on Equity %
    roa: Optional[float] = None                        # Return on Assets %
    npm: Optional[float] = None                        # Net Profit Margin %
    gross_margin: Optional[float] = None               # Gross Profit Margin %

    # Per Saham
    eps: Optional[float] = None                        # Earnings Per Share (Rp)
    bvps: Optional[float] = None                       # Book Value Per Share (Rp)
    dps: Optional[float] = None                        # Dividend Per Share (Rp)

    # Pertumbuhan (YoY)
    revenue_growth_yoy: Optional[float] = None         # Revenue growth % YoY
    profit_growth_yoy: Optional[float] = None          # Laba growth % YoY
    revenue_cagr_5y: Optional[float] = None            # CAGR Revenue 5 tahun
    profit_cagr_5y: Optional[float] = None             # CAGR Laba 5 tahun

    # Leverage
    debt_to_equity: Optional[float] = None             # DER
    debt_to_assets: Optional[float] = None             # DAR
    current_ratio: Optional[float] = None              # Rasio Lancar

    # Bank-specific
    nim: Optional[float] = None                        # Net Interest Margin %
    npl_gross: Optional[float] = None                  # NPL Gross %
    casa_ratio: Optional[float] = None                 # CASA Ratio %
    car: Optional[float] = None                        # Capital Adequacy Ratio %
    loan_to_deposit: Optional[float] = None            # LDR %


class ValuasiMetrics(BaseModel):
    """Metrik valuasi pasar"""
    current_price: float                               # Harga pasar saat ini (Rp)
    per: Optional[float] = None                        # Price-to-Earnings Ratio
    pbv: Optional[float] = None                        # Price-to-Book Value
    psr: Optional[float] = None                        # Price-to-Sales Ratio
    dividend_yield: Optional[float] = None             # Dividend Yield %
    market_cap: Optional[float] = None                 # Market Capitalization (Rp)

    # Harga wajar hasil perhitungan
    fair_value_conservative: Optional[float] = None   # PER × 15 (konservatif)
    fair_value_normal: Optional[float] = None          # PER × 18 (normal)
    fair_value_graham: Optional[float] = None          # Formula Graham
    margin_of_safety: Optional[float] = None           # MoS % terhadap fair value konservatif

    # Historis (untuk perbandingan)
    per_historical_avg: Optional[float] = None         # Rata-rata PER historis
    pbv_historical_avg: Optional[float] = None         # Rata-rata PBV historis
    price_52w_high: Optional[float] = None
    price_52w_low: Optional[float] = None


# ─── Scoring ──────────────────────────────────────────────────────────────────

class FundamentalScore(BaseModel):
    """Skor fundamental ala Buffett (0-10)"""
    total: float = 0
    profit_growth: float = 0       # Laba tumbuh konsisten 5 tahun (max 2)
    revenue_growth: float = 0      # Revenue tumbuh (max 1.5)
    roe_score: float = 0           # ROE > 15% (max 2)
    debt_score: float = 0          # Utang terkendali (max 2)
    fcf_score: float = 0           # Free Cash Flow positif (max 1.5)
    moat_score: float = 0          # Keunggulan kompetitif / moat (max 1)
    details: dict = {}


class ValuasiScore(BaseModel):
    """Skor valuasi ala Graham (0-10)"""
    total: float = 0
    per_score: float = 0           # PER vs historis (max 4)
    pbv_score: float = 0           # PBV vs historis (max 3)
    mos_score: float = 0           # Margin of Safety (max 3)
    details: dict = {}


class RisikoAssessment(BaseModel):
    """Penilaian risiko"""
    bisnis: RisikoLevel = RisikoLevel.SEDANG
    keuangan: RisikoLevel = RisikoLevel.SEDANG
    harga: RisikoLevel = RisikoLevel.SEDANG
    overall: RisikoLevel = RisikoLevel.SEDANG
    catatan_bisnis: List[str] = []
    catatan_keuangan: List[str] = []
    catatan_harga: List[str] = []


# ─── Analysis Result ──────────────────────────────────────────────────────────

class AnalysisResult(BaseModel):
    """Hasil analisis lengkap 1 saham"""
    # Identitas
    ticker: str
    company_name: str
    sector: str = ""
    analysis_date: datetime

    # Data keuangan
    latest_financial_year: int
    financial_data_years: List[int] = []

    # Metrik
    fundamental: FundamentalMetrics
    valuasi: ValuasiMetrics

    # Scoring
    fundamental_score: FundamentalScore
    valuasi_score: ValuasiScore
    risiko: RisikoAssessment

    # Keputusan
    keputusan: KeputusanEnum
    area_beli_bawah: Optional[float] = None
    area_beli_atas: Optional[float] = None
    area_jual: Optional[float] = None

    # Narasi LLM
    ringkasan_fundamental: str = ""
    ringkasan_valuasi: str = ""
    alasan_keputusan: str = ""
    red_flags: List[str] = []
    highlights: List[str] = []

    # Metadata
    data_source: str = "IDX + Yahoo Finance"
    llm_analysis: bool = True


# ─── API Request/Response ─────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    ticker: str = Field(..., description="Kode saham IDX, contoh: BBCA, BBRI, TLKM")
    force_refresh: bool = Field(False, description="Paksa refresh data dari IDX")


class WatchlistItem(BaseModel):
    ticker: str
    added_at: datetime
    target_buy_price: Optional[float] = None
    notes: Optional[str] = None
    latest_analysis: Optional[AnalysisResult] = None


class UploadResponse(BaseModel):
    ticker: str
    message: str
    pdf_path: str
    year: int


class ErrorResponse(BaseModel):
    error: str
    detail: str = ""
    ticker: Optional[str] = None
