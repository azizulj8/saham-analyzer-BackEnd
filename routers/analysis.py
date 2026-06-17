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
    latest_dps = dividends[-1]["amount"] if dividends else 0
    historical_per, historical_pbv = await market_data_svc.get_historical_per_pbv(ticker)

    # 4. Coba fetch PDF dari IDX dan parse
    financial_data_list = []
    pdf_text_latest = ""
    data_source_label = "Yahoo Finance (harga pasar)"

    # Coba cari PDF di cache dulu (termasuk manual upload)
    for yr in range(datetime.now().year, datetime.now().year - 3, -1):
        cached_pdf = idx_fetcher.get_cached_pdf(ticker, yr)
        if cached_pdf:
            logger.info(f"PDF ditemukan di cache untuk {ticker} {yr}: {cached_pdf}")
            fd = pdf_parser.parse(
                pdf_path=cached_pdf,
                ticker=ticker,
                year=yr,
                company_name=company_name,
                sector=sector,
                shares_outstanding=shares_outstanding,
            )
            if fd:
                financial_data_list.append(fd)
                if not pdf_text_latest:
                    pdf_text_latest = pdf_parser.extract_text_for_llm(cached_pdf)

    # Jika tidak ada di cache, coba download dari IDX
    if not financial_data_list:
        logger.info(f"Mencoba download PDF dari IDX untuk {ticker}...")
        try:
            pdf_reports = await idx_fetcher.get_multi_year_reports(ticker, years=min(years, 3))

            for pdf_path, year in pdf_reports:
                fd = pdf_parser.parse(
                    pdf_path=pdf_path,
                    ticker=ticker,
                    year=year,
                    company_name=company_name,
                    sector=sector,
                    shares_outstanding=shares_outstanding,
                )
                if fd:
                    financial_data_list.append(fd)
                    if not pdf_text_latest:
                        pdf_text_latest = pdf_parser.extract_text_for_llm(pdf_path)

        except Exception as e:
            logger.warning(f"IDX PDF fetch gagal untuk {ticker}: {e}")

    # Tentukan mode analisis berdasarkan data yang tersedia
    has_pdf_data = len(financial_data_list) > 0
    if has_pdf_data:
        data_source_label = "IDX (Laporan Keuangan Resmi) + Yahoo Finance"
        logger.info(f"✅ Data PDF tersedia untuk {ticker}: {len(financial_data_list)} tahun")
    else:
        logger.warning(
            f"⚠️ PDF IDX tidak tersedia untuk {ticker}. "
            f"Menggunakan data Yahoo Finance sebagai fallback."
        )

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

    # 6. Hitung metrik valuasi
    valuasi = calculator.calculate_valuasi(
        metrics=metrics,
        current_price=current_price,
        shares_outstanding=shares_outstanding,
        historical_per=historical_per,
        historical_pbv=historical_pbv,
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
