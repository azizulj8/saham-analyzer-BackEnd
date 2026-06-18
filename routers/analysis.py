"""
Analysis Router — Endpoint utama analisis saham
GET /api/analyze/{ticker}

Flow:
1. Cek cache
2. Ambil info perusahaan (yfinance)
3. Ambil harga pasar (yfinance) - selalu berhasil
4. Coba fetch PDF dari IDX → parse → hitung rasio
   jika gagal → fallback ke data yfinance
5. Scoring + LLM
6. Return hasil
"""
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query

from models.schemas import AnalysisResult, KeputusanEnum, RisikoLevel
from services.idx_fetcher import IDXFetcher
from services.pdf_parser import PDFParser
from services.market_data import MarketDataService
from services.calculator import CalculatorEngine
from services.scorer import ScorerEngine
from services.llm_analyzer import LLMAnalyzer
from db.database import get_cached_analysis, save_analysis_cache

router = APIRouter()
logger = logging.getLogger(__name__)

# Initialize services
idx_fetcher = IDXFetcher()
pdf_parser = PDFParser()
market_data_svc = MarketDataService()
calculator = CalculatorEngine()
scorer = ScorerEngine()
llm_analyzer = LLMAnalyzer()


@router.get(
    "/analyze/{ticker}",
    summary="Analisis saham lengkap",
    description="Analisis saham IDX menggunakan laporan keuangan IDX + harga dari Yahoo Finance",
)
async def analyze_stock(
    ticker: str,
    force_refresh: bool = Query(False, description="Paksa refresh data baru"),
    years: int = Query(3, description="Berapa tahun data historis", ge=1, le=10),
):
    ticker = ticker.upper().strip()

    # 1. Cek cache
    if not force_refresh:
        cached = await get_cached_analysis(ticker)
        if cached:
            cached["from_cache"] = True
            return cached

    logger.info(f"Memulai analisis untuk {ticker}...")

    # 2. Ambil info perusahaan (dari yfinance, hampir pasti berhasil)
    company_info = await idx_fetcher.get_company_info(ticker)
    company_name = company_info.get("company_name", ticker)
    sector = company_info.get("sector", "")
    shares_outstanding = company_info.get("shares_outstanding", 0)

    # 3. Ambil data pasar (SELALU dari Yahoo Finance)
    market = await market_data_svc.get_market_data(ticker)
    current_price = market.get("current_price") or 0

    if not current_price:
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Saham {ticker} tidak ditemukan di Yahoo Finance (.JK)",
                "suggestion": (
                    f"Pastikan kode saham benar. "
                    f"Contoh: BBCA, BBRI, TLKM. "
                    f"Yahoo Finance mencari {ticker}.JK"
                ),
                "ticker": ticker,
            }
        )

    # Update shares dari yfinance jika lebih lengkap
    if market.get("shares_outstanding"):
        shares_outstanding = market["shares_outstanding"]

    corporate_actions = await market_data_svc.get_corporate_actions(ticker)
    dividends = corporate_actions.get("dividends", [])
    # DPS = TOTAL dividen 12 bulan terakhir (interim + final), bukan satu pembayaran saja.
    # Banyak emiten (mis. BBCA) bayar dua kali setahun; ambil yang terakhir saja
    # akan membuat DPS & dividend yield terlalu kecil.
    _cutoff = datetime.now() - __import__("datetime").timedelta(days=365)
    _ttm = [
        d["amount"] for d in dividends
        if datetime.fromisoformat(d["date"]) >= _cutoff
    ]
    latest_dps = sum(_ttm) if _ttm else (dividends[-1]["amount"] if dividends else 0)
    historical_per, historical_pbv = await market_data_svc.get_historical_per_pbv(ticker)

    # 4. Coba fetch PDF dari IDX dan parse
    financial_data_list = []
    pdf_text_latest = ""
    data_source_label = "Yahoo Finance (harga pasar)"
    extraction_method = "none"  # "llm" | "regex" | "none"

    def _parse_pdf(pdf_path: str, yr: int):
        """Ekstrak data keuangan dari 1 PDF.
        Prioritas: ekstraksi via Claude (lebih andal) → fallback parser regex.
        Mengembalikan (FinancialData|None, pdf_text, method)."""
        pdf_text = pdf_parser.extract_text_for_llm(pdf_path)
        # Coba LLM dulu
        if llm_analyzer.is_available() and pdf_text:
            fd_llm = llm_analyzer.extract_financial_data(
                pdf_text=pdf_text,
                ticker=ticker,
                company_name=company_name,
                sector=sector,
                year=yr,
                shares_outstanding=shares_outstanding,
            )
            if fd_llm and fd_llm.laba_rugi.net_profit and fd_llm.neraca.total_assets:
                return fd_llm, pdf_text, "llm"
            logger.warning(f"Ekstraksi LLM gagal untuk {ticker} {yr}, fallback ke parser regex.")
        # Fallback parser regex
        fd_regex = pdf_parser.parse(
            pdf_path=pdf_path,
            ticker=ticker,
            year=yr,
            company_name=company_name,
            sector=sector,
            shares_outstanding=shares_outstanding,
        )
        return fd_regex, pdf_text, ("regex" if fd_regex else "none")

    # Coba cari PDF di cache dulu (termasuk manual upload)
    for yr in range(datetime.now().year, datetime.now().year - 3, -1):
        cached_pdf = idx_fetcher.get_cached_pdf(ticker, yr)
        if cached_pdf:
            logger.info(f"PDF ditemukan di cache untuk {ticker} {yr}: {cached_pdf}")
            fd, pdf_text, method = _parse_pdf(cached_pdf, yr)
            if fd:
                financial_data_list.append(fd)
                if method == "llm" or extraction_method == "none":
                    extraction_method = method
                if not pdf_text_latest:
                    pdf_text_latest = pdf_text

    # Jika tidak ada di cache, coba download dari IDX
    if not financial_data_list:
        logger.info(f"Mencoba download PDF dari IDX untuk {ticker}...")
        try:
            pdf_reports = await idx_fetcher.get_multi_year_reports(ticker, years=min(years, 3))

            for pdf_path, year in pdf_reports:
                fd, pdf_text, method = _parse_pdf(pdf_path, year)
                if fd:
                    if method == "llm" or extraction_method == "none":
                        extraction_method = method
                    if not pdf_text_latest:
                        pdf_text_latest = pdf_text
                    financial_data_list.append(fd)

        except Exception as e:
            logger.warning(f"IDX PDF fetch gagal untuk {ticker}: {e}")

    # Tentukan mode analisis berdasarkan data yang tersedia
    has_pdf_data = len(financial_data_list) > 0
    if has_pdf_data:
        _method_label = "ekstraksi Claude" if extraction_method == "llm" else "parser regex"
        data_source_label = f"IDX (Laporan Keuangan Resmi, {_method_label}) + Yahoo Finance"
        logger.info(f"✅ Data PDF tersedia untuk {ticker}: {len(financial_data_list)} tahun")
    else:
        logger.warning(
            f"⚠️ PDF IDX tidak tersedia untuk {ticker}. "
            f"Menggunakan data Yahoo Finance sebagai fallback."
        )

    # Snapshot data PDF mentah SEBELUM safety-net reset (untuk pemetaan/diagnosa)
    raw_pdf_snapshot = list(financial_data_list)
    fundamental_fallback_used = False

    # 5. Hitung metrik fundamental
    if has_pdf_data:
        metrics = calculator.calculate_fundamental(
            financial_data_list=financial_data_list,
            shares_outstanding=shares_outstanding,
            dividends_per_share=latest_dps,
        )

        # SAFETY NET: Jika ekstraksi PDF gagal murni (seperti saat baca interim report atau format tidak standar),
        # EPS dan BVPS biasanya bernilai 0. Kita tambal metrik kunci menggunakan data Yahoo Finance agar valuasi tidak rusak.
        if (metrics.eps is None or metrics.eps == 0) and (metrics.bvps is None or metrics.bvps == 0):
            logger.warning(f"Ekstraksi PDF gagal untuk {ticker} (EPS=0). Menimpa dengan data Yahoo Finance!")
            fundamental_fallback_used = True
            yfinance_metrics = _build_metrics_from_yfinance(market, latest_dps, current_price)
            metrics.eps = yfinance_metrics.eps
            metrics.bvps = yfinance_metrics.bvps
            metrics.roe = yfinance_metrics.roe or metrics.roe
            metrics.roa = yfinance_metrics.roa or metrics.roa
            metrics.npm = yfinance_metrics.npm or metrics.npm
            metrics.debt_to_equity = yfinance_metrics.debt_to_equity or metrics.debt_to_equity
            metrics.revenue_growth_yoy = yfinance_metrics.revenue_growth_yoy or metrics.revenue_growth_yoy
            metrics.profit_growth_yoy = yfinance_metrics.profit_growth_yoy or metrics.profit_growth_yoy
            
            # Reset financial_data_list jika kita mengandalkan yfinance (agar scorer fundamental tidak terkecoh)
            financial_data_list = []
            
    else:
        # FALLBACK: gunakan data dari Yahoo Finance
        metrics = _build_metrics_from_yfinance(market, latest_dps, current_price)

    # 6. Tentukan baseline PER/PBV historis (opsi C: historis emiten → ROE-justified → sektor)
    year_end_prices = {}
    try:
        hist_prices = await market_data_svc.get_historical_prices(ticker, period="5y")
        if hist_prices and hist_prices.get("dates"):
            for dstr, close in zip(hist_prices["dates"], hist_prices["closes"]):
                yr = int(dstr[:4])
                year_end_prices[yr] = close  # close terakhir per tahun (data terurut menaik)
    except Exception as e:
        logger.warning(f"Gagal ambil harga historis untuk baseline {ticker}: {e}")

    baseline = calculator.compute_valuation_baseline(
        financial_data_list=raw_pdf_snapshot,
        year_end_prices=year_end_prices,
        metrics=metrics,
        shares_outstanding=shares_outstanding,
        sector_per=historical_per,
    )
    logger.info(
        f"Baseline valuasi {ticker}: PER={baseline['per']}, PBV={baseline['pbv']}, "
        f"metode={baseline['method']} ({baseline['years_used']} tahun)"
    )

    # 7. Hitung metrik valuasi
    valuasi = calculator.calculate_valuasi(
        metrics=metrics,
        current_price=current_price,
        shares_outstanding=shares_outstanding,
        historical_per=baseline["per"],
        historical_pbv=baseline["pbv"],
        price_52w_high=market.get("price_52w_high"),
        price_52w_low=market.get("price_52w_low"),
        market_cap=market.get("market_cap"),
        dividend_yield=market.get("dividend_yield"),
    )

    # 7. Scoring
    fundamental_score = scorer.score_fundamental(metrics, financial_data_list)
    valuasi_score = scorer.score_valuasi(valuasi)
    risiko = scorer.assess_risiko(metrics, valuasi, fundamental_score)
    keputusan, alasan_keputusan = scorer.determine_keputusan(
        fundamental_score, valuasi_score, valuasi, risiko
    )
    area_beli_bawah, area_beli_atas, area_jual = scorer.calculate_buy_zones(valuasi)

    # 8. LLM Analysis (Claude Sonnet)
    llm_result = await llm_analyzer.analyze(
        ticker=ticker,
        company_name=company_name,
        sector=sector,
        pdf_text=pdf_text_latest,
        metrics=metrics,
        valuasi=valuasi,
        risiko=risiko,
        fundamental_score=fundamental_score.total,
        valuasi_score=valuasi_score.total,
        keputusan=keputusan.value,
        margin_of_safety=valuasi.margin_of_safety,
    )

    # 9. Susun peringatan jika data tidak lengkap
    red_flags = llm_result.get("red_flags", [])
    if not has_pdf_data:
        red_flags = [
            "⚠️ Laporan keuangan IDX belum tersedia — analisis berdasarkan data Yahoo Finance. "
            "Upload PDF laporan keuangan untuk analisis lebih akurat.",
        ] + red_flags

    # 10. Build result
    result = AnalysisResult(
        ticker=ticker,
        company_name=company_name,
        sector=sector,
        analysis_date=datetime.now(),
        latest_financial_year=(
            financial_data_list[0].year if len(financial_data_list) > 0 else datetime.now().year - 1
        ),
        financial_data_years=[fd.year for fd in financial_data_list],
        fundamental=metrics,
        valuasi=valuasi,
        fundamental_score=fundamental_score,
        valuasi_score=valuasi_score,
        risiko=risiko,
        keputusan=keputusan,
        area_beli_bawah=area_beli_bawah,
        area_beli_atas=area_beli_atas,
        area_jual=area_jual,
        ringkasan_fundamental=llm_result.get("ringkasan_fundamental", ""),
        ringkasan_valuasi=llm_result.get("ringkasan_valuasi", ""),
        alasan_keputusan="\n".join(alasan_keputusan) + "\n\n" + llm_result.get("alasan_keputusan", ""),
        red_flags=red_flags,
        highlights=llm_result.get("highlights", []),
        data_source=data_source_label,
        llm_analysis=llm_analyzer.is_available(),
    )

    result_dict = result.model_dump(mode="json")
    result_dict["has_pdf_data"] = has_pdf_data
    result_dict["pdf_years"] = [fd.year for fd in financial_data_list]
    result_dict["data_trace"] = _build_data_trace(
        market=market,
        raw_pdf_list=raw_pdf_snapshot,
        metrics=metrics,
        valuasi=valuasi,
        latest_dps=latest_dps,
        current_price=current_price,
        fundamental_fallback_used=fundamental_fallback_used,
        extraction_method=extraction_method,
        baseline=baseline,
    )

    # Cache hasil
    await save_analysis_cache(ticker, result_dict)

    logger.info(
        f"✅ Analisis selesai untuk {ticker}: "
        f"Fundamental={fundamental_score.total}/10, "
        f"Valuasi={valuasi_score.total}/10, "
        f"Keputusan={keputusan.value}, "
        f"PDF={'Ya' if has_pdf_data else 'Tidak'}"
    )

    return result_dict


def _build_metrics_from_yfinance(market: dict, dps: float, current_price: float):
    """
    Build FundamentalMetrics dari data Yahoo Finance ketika PDF IDX tidak tersedia.
    Data Yahoo Finance adalah ESTIMASI — tidak seakurat laporan keuangan langsung.
    """
    from models.schemas import FundamentalMetrics

    metrics = FundamentalMetrics()

    # Yahoo Finance menyediakan beberapa data fundamental
    trailing_per = market.get("per_trailing")
    forward_per = market.get("per_forward")
    pbv = market.get("pbv")

    # Estimasi EPS dari PER dan harga
    if trailing_per and trailing_per > 0 and current_price > 0:
        metrics.eps = round(current_price / trailing_per, 2)

    # Estimasi BVPS dari PBV dan harga
    if pbv and pbv > 0 and current_price > 0:
        metrics.bvps = round(current_price / pbv, 2)
        
    # Data Fundamental dari yfinance
    if market.get("roe") is not None:
        metrics.roe = round(market["roe"] * 100, 2)
    if market.get("roa") is not None:
        metrics.roa = round(market["roa"] * 100, 2)
    if market.get("npm") is not None:
        metrics.npm = round(market["npm"] * 100, 2)
    if market.get("debt_to_equity") is not None:
        # yfinance D/E is often in percentage, e.g., 20.5 means 0.205
        # but let's safely handle it. Usually < 1000.
        metrics.debt_to_equity = round(market["debt_to_equity"] / 100, 2)
    if market.get("revenue_growth") is not None:
        metrics.revenue_growth_yoy = round(market["revenue_growth"] * 100, 2)
    if market.get("earnings_growth") is not None:
        metrics.profit_growth_yoy = round(market["earnings_growth"] * 100, 2)

    # DPS dari corporate actions
    metrics.dps = dps

    logger.info(
        f"Metrics dari Yahoo Finance: "
        f"EPS={metrics.eps}, BVPS={metrics.bvps}, "
        f"ROE={metrics.roe}%, ROA={metrics.roa}%, "
        f"PER={trailing_per}, PBV={pbv}"
    )

    return metrics


def _build_data_trace(
    market: dict,
    raw_pdf_list: list,
    metrics,
    valuasi,
    latest_dps: float,
    current_price: float,
    fundamental_fallback_used: bool,
    extraction_method: str = "none",
    baseline: dict = None,
) -> dict:
    """
    Bangun pemetaan data per-metrik untuk diagnosa:
    untuk tiap angka penting, tampilkan nilai MENTAH dari Yahoo Finance,
    nilai MENTAH dari PDF upload (laporan keuangan), formula, hasil akhir,
    sumber yang benar-benar dipakai, dan catatan anomali jika ada.

    Tujuan: pengguna bisa langsung tahu apakah angka aneh berasal dari
    data Yahoo Finance, dari ekstraksi PDF, atau dari rumus perhitungan.
    """
    # Data PDF mentah tahun terbaru (jika ada)
    latest_pdf = raw_pdf_list[0] if raw_pdf_list else None
    lr = latest_pdf.laba_rugi if latest_pdf else None
    nr = latest_pdf.neraca if latest_pdf else None
    pdf_year = latest_pdf.year if latest_pdf else None
    pdf_ok = bool(raw_pdf_list)

    def g(obj, attr):
        return getattr(obj, attr, None) if obj else None

    rows = [
        {
            "metric": "Harga Pasar",
            "yfinance": current_price,
            "pdf": None,
            "formula": "current_price (fast_info / .info)",
            "final": valuasi.current_price,
            "source_used": "Yahoo Finance",
            "note": None,
        },
        {
            "metric": "EPS (Laba per Saham)",
            "yfinance": round(current_price / market["per_trailing"], 2) if market.get("per_trailing") else None,
            "pdf": f"net_profit={g(lr,'net_profit')} ÷ shares" if pdf_ok else None,
            "formula": "PDF: Laba Bersih ÷ Saham Beredar | yfinance: Harga ÷ trailingPE",
            "final": metrics.eps,
            "source_used": "Yahoo Finance (PDF gagal)" if fundamental_fallback_used else "PDF",
            "note": "EPS hasil ekstraksi PDF = 0 → ditimpa estimasi yfinance" if fundamental_fallback_used else None,
        },
        {
            "metric": "BVPS (Nilai Buku per Saham)",
            "yfinance": round(current_price / market["pbv"], 2) if market.get("pbv") else None,
            "pdf": f"total_equity={g(nr,'total_equity')} ÷ shares" if pdf_ok else None,
            "formula": "PDF: Ekuitas ÷ Saham Beredar | yfinance: Harga ÷ priceToBook",
            "final": metrics.bvps,
            "source_used": "Yahoo Finance (PDF gagal)" if fundamental_fallback_used else "PDF",
            "note": None,
        },
        {
            "metric": "PER",
            "yfinance": market.get("per_trailing"),
            "pdf": None,
            "formula": "Harga ÷ EPS",
            "final": valuasi.per,
            "source_used": "Perhitungan (Harga ÷ EPS)",
            "note": None,
        },
        {
            "metric": "PBV",
            "yfinance": market.get("pbv"),
            "pdf": None,
            "formula": "Harga ÷ BVPS",
            "final": valuasi.pbv,
            "source_used": "Perhitungan (Harga ÷ BVPS)",
            "note": None,
        },
        {
            "metric": "DPS (Dividen per Saham)",
            "yfinance": latest_dps,
            "pdf": None,
            "formula": "Dividen terakhir dari corporate actions",
            "final": metrics.dps,
            "source_used": "Yahoo Finance (corporate actions)",
            "note": None,
        },
        {
            "metric": "Dividend Yield",
            "yfinance": market.get("dividend_yield"),
            "pdf": None,
            "formula": "Dihitung sendiri: DPS ÷ Harga × 100 (yfinance hanya fallback)",
            "final": valuasi.dividend_yield,
            "source_used": (
                "Perhitungan (DPS÷Harga)" if (latest_dps and current_price) else "Yahoo Finance"
            ),
            "note": (
                f"⚠️ ANOMALI: nilai akhir ({valuasi.dividend_yield}%) terlalu besar — periksa konversi yfinance."
                if (valuasi.dividend_yield or 0) > 100 else None
            ),
        },
        {
            "metric": "ROE",
            "yfinance": round(market["roe"] * 100, 2) if market.get("roe") is not None else None,
            "pdf": f"net_profit ÷ total_equity ({g(lr,'net_profit')} ÷ {g(nr,'total_equity')})" if pdf_ok else None,
            "formula": "Laba Bersih ÷ Ekuitas × 100",
            "final": metrics.roe,
            "source_used": "Yahoo Finance (PDF gagal)" if fundamental_fallback_used else "PDF",
            "note": None,
        },
        {
            "metric": "DER (Debt-to-Equity)",
            "yfinance": round(market["debt_to_equity"] / 100, 2) if market.get("debt_to_equity") is not None else None,
            "pdf": f"total_liabilities ÷ total_equity ({g(nr,'total_liabilities')} ÷ {g(nr,'total_equity')})" if pdf_ok else None,
            "formula": "Total Liabilitas ÷ Ekuitas",
            "final": metrics.debt_to_equity,
            "source_used": "Yahoo Finance (PDF gagal)" if fundamental_fallback_used else "PDF",
            "note": None,
        },
        {
            "metric": "CASA Ratio",
            "yfinance": None,
            "pdf": f"casa={g(nr,'casa')}, casa_ratio={g(nr,'casa_ratio')}, total_assets={g(nr,'total_assets')}" if pdf_ok else None,
            "formula": "CASA ÷ Total Aset × 100",
            "final": metrics.casa_ratio,
            "source_used": "PDF (laporan keuangan)",
            "note": "⚠️ ANOMALI: nilai negatif — kemungkinan salah ekstraksi pos CASA dari PDF" if (metrics.casa_ratio or 0) < 0 else None,
        },
        {
            "metric": "LDR (Loan-to-Deposit)",
            "yfinance": None,
            "pdf": f"total_loans={g(nr,'total_loans')}, total_liabilities={g(nr,'total_liabilities')}" if pdf_ok else None,
            "formula": "Total Kredit ÷ Total Liabilitas × 100",
            "final": metrics.loan_to_deposit,
            "source_used": "PDF (laporan keuangan)",
            "note": "⚠️ ANOMALI: 0 — pos Total Kredit gagal terbaca dari PDF" if (metrics.loan_to_deposit == 0) else None,
        },
        {
            "metric": "Market Cap",
            "yfinance": market.get("market_cap"),
            "pdf": None,
            "formula": "market_cap (yfinance)",
            "final": valuasi.market_cap,
            "source_used": "Yahoo Finance",
            "note": None,
        },
        {
            "metric": "52W High / Low",
            "yfinance": f"{market.get('price_52w_high')} / {market.get('price_52w_low')}",
            "pdf": None,
            "formula": "year_high / year_low (yfinance)",
            "final": f"{valuasi.price_52w_high} / {valuasi.price_52w_low}",
            "source_used": "Yahoo Finance",
            "note": None,
        },
    ]

    # ── Guard konsistensi (deteksi salah baris / inkonsistensi antar tahun) ──
    consistency_warnings = []

    # 1. Identitas neraca: Total Aset ≈ Total Liabilitas + Total Ekuitas
    if pdf_ok and nr and nr.total_assets and nr.total_liabilities and nr.total_equity:
        diff = abs(nr.total_assets - (nr.total_liabilities + nr.total_equity))
        if nr.total_assets and (diff / nr.total_assets) > 0.05:
            consistency_warnings.append(
                f"⚠️ Neraca tidak balance: Total Aset ({nr.total_assets:,.0f}) ≠ "
                f"Liabilitas + Ekuitas ({nr.total_liabilities + nr.total_equity:,.0f}) — "
                f"selisih {diff / nr.total_assets * 100:.1f}%. Kemungkinan salah ambil baris."
            )

    # 2. Divergensi YoY: laba & pendapatan bunga seharusnya searah.
    if len(raw_pdf_list) >= 2:
        cur, prev = raw_pdf_list[0], raw_pdf_list[1]

        def yoy(c, p):
            if c is None or p is None or p == 0:
                return None
            return (c - p) / abs(p) * 100

        np_yoy = yoy(cur.laba_rugi.net_profit, prev.laba_rugi.net_profit)
        ii_yoy = yoy(cur.laba_rugi.interest_income, prev.laba_rugi.interest_income)
        if np_yoy is not None and ii_yoy is not None:
            if (np_yoy > 0) != (ii_yoy > 0) and abs(np_yoy - ii_yoy) > 10:
                consistency_warnings.append(
                    f"⚠️ Arah laba ({np_yoy:+.1f}% YoY) dan pendapatan bunga ({ii_yoy:+.1f}% YoY) "
                    f"berlawanan — kemungkinan angka pendapatan bunga antar tahun tidak konsisten "
                    f"(bruto vs bersih). Periksa sebelum mengandalkan skor pertumbuhan."
                )

    return {
        "pdf_extraction_ok": pdf_ok and not fundamental_fallback_used,
        "fundamental_fallback_used": fundamental_fallback_used,
        "extraction_method": extraction_method,
        "consistency_warnings": consistency_warnings,
        "valuation_baseline": baseline,
        "pdf_year": pdf_year,
        "raw_yfinance": {
            "current_price": market.get("current_price"),
            "per_trailing": market.get("per_trailing"),
            "per_forward": market.get("per_forward"),
            "pbv": market.get("pbv"),
            "dividend_yield": market.get("dividend_yield"),
            "roe": market.get("roe"),
            "roa": market.get("roa"),
            "debt_to_equity": market.get("debt_to_equity"),
            "market_cap": market.get("market_cap"),
            "shares_outstanding": market.get("shares_outstanding"),
        },
        "raw_pdf_latest": {
            "year": pdf_year,
            "net_profit": g(lr, "net_profit"),
            "revenue": g(lr, "revenue"),
            "interest_income": g(lr, "interest_income"),
            "total_assets": g(nr, "total_assets"),
            "total_equity": g(nr, "total_equity"),
            "total_liabilities": g(nr, "total_liabilities"),
            "total_loans": g(nr, "total_loans"),
            "casa": g(nr, "casa"),
        } if pdf_ok else None,
        "rows": rows,
    }


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "services": {
            "llm": llm_analyzer.is_available(),
            "idx_fetcher": True,
            "pdf_parser": True,
            "market_data": True,
        }
    }
