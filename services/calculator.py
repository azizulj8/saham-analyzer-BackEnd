"""
Calculator Engine
Menghitung semua rasio keuangan dari data raw laporan keuangan.
SEMUA ANGKA DIHITUNG SENDIRI — tidak mengandalkan sumber sekunder.
"""
import logging
from typing import Optional, List
from models.schemas import (
    FinancialData, FundamentalMetrics, ValuasiMetrics
)

logger = logging.getLogger(__name__)


class CalculatorEngine:
    """
    Engine untuk menghitung semua rasio keuangan.
    
    Filosofi: semua angka dihitung dari data raw laporan keuangan IDX.
    Tidak ada angka yang diambil dari sumber sekunder (Bloomberg, Kontan, dll.)
    """

    @staticmethod
    def safe_divide(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
        """Safe division dengan handle None dan zero division"""
        if numerator is None or denominator is None:
            return None
        if denominator == 0:
            return None
        return numerator / denominator

    @staticmethod
    def safe_growth(current: Optional[float], previous: Optional[float]) -> Optional[float]:
        """Hitung pertumbuhan % antara dua periode"""
        if current is None or previous is None:
            return None
        if previous == 0 or previous is None:
            return None
        return ((current - previous) / abs(previous)) * 100

    @staticmethod
    def cagr(start_value: float, end_value: float, years: int) -> Optional[float]:
        """
        Compound Annual Growth Rate.
        CAGR = (end/start)^(1/years) - 1
        """
        if start_value <= 0 or end_value <= 0 or years <= 0:
            return None
        return ((end_value / start_value) ** (1 / years) - 1) * 100

    def compute_valuation_baseline(
        self,
        financial_data_list: List[FinancialData],
        year_end_prices: dict,
        metrics: "FundamentalMetrics",
        shares_outstanding: float = 0,
        sector_per: float = 14.0,
        min_years: int = 3,
        cost_of_equity: float = 0.11,
        long_term_growth: float = 0.04,
    ) -> dict:
        """
        Tentukan baseline PER/PBV historis untuk penilaian valuasi (opsi C).

        Hierarki:
        1. HISTORIS EMITEN — jika tersedia >= min_years tahun: rata-rata PER/PBV
           aktual per tahun = harga akhir tahun ÷ EPS/BVPS tahun tsb.
        2. ROE-JUSTIFIED — anchor fundamental: PBV* = (ROE - g)/(CoE - g),
           PER* = PBV* / ROE. Dipakai jika data historis kurang.
        3. SEKTOR DEFAULT — rata-rata sektor (PER) + PBV 2.0 sebagai fallback akhir.

        Returns dict: {per, pbv, method, years_used, assumptions}
        """
        pers, pbvs = [], []
        for fd in financial_data_list:
            sh = fd.shares_outstanding or shares_outstanding
            price = year_end_prices.get(fd.year)
            if not sh or not price:
                continue
            np_ = fd.laba_rugi.net_profit
            eq = fd.neraca.total_equity
            if np_ and np_ > 0:
                eps = np_ / sh
                if eps > 0:
                    pers.append(price / eps)
            if eq and eq > 0:
                bvps = eq / sh
                if bvps > 0:
                    pbvs.append(price / bvps)

        # 1. Historis emiten (paling kontekstual) — butuh cukup banyak tahun
        if len(pers) >= min_years and len(pbvs) >= min_years:
            return {
                "per": round(sum(pers) / len(pers), 2),
                "pbv": round(sum(pbvs) / len(pbvs), 2),
                "method": "historis_emiten",
                "years_used": len(pbvs),
                "assumptions": None,
            }

        # 2. ROE-justified anchor (Gordon/residual income)
        roe = metrics.roe
        if roe:
            roe_dec = abs(roe)
            if roe_dec > 1:
                roe_dec /= 100.0
            if cost_of_equity > long_term_growth and roe_dec > 0:
                justified_pbv = (roe_dec - long_term_growth) / (cost_of_equity - long_term_growth)
                if justified_pbv > 0:
                    justified_per = justified_pbv / roe_dec
                    return {
                        "per": round(justified_per, 2),
                        "pbv": round(justified_pbv, 2),
                        "method": "roe_justified",
                        "years_used": len(pbvs),
                        "assumptions": f"CoE={cost_of_equity:.0%}, g={long_term_growth:.0%}, ROE={roe_dec:.1%}",
                    }

        # 3. Sektor default (fallback akhir)
        return {
            "per": sector_per,
            "pbv": 2.0,
            "method": "sektor_default",
            "years_used": len(pbvs),
            "assumptions": None,
        }

    def calculate_fundamental(
        self,
        financial_data_list: List[FinancialData],
        shares_outstanding: float = 0,
        dividends_per_share: float = 0,
    ) -> FundamentalMetrics:
        """
        Hitung semua metrik fundamental dari data keuangan multi-tahun.
        
        Args:
            financial_data_list: Data keuangan dari beberapa tahun (terbaru dahulu)
            shares_outstanding: Jumlah saham beredar
            dividends_per_share: Dividen per saham (dari corporate actions)
        
        Returns:
            FundamentalMetrics berisi semua rasio yang sudah dihitung
        """
        if not financial_data_list:
            return FundamentalMetrics()

        # Data tahun terbaru
        latest = financial_data_list[0]
        lr = latest.laba_rugi
        neraca = latest.neraca
        kas = latest.arus_kas

        # Jumlah saham beredar (prioritas: dari parameter, lalu dari PDF, lalu dari neraca)
        shares = shares_outstanding or latest.shares_outstanding or 0

        metrics = FundamentalMetrics()

        # ─── PROFITABILITAS ────────────────────────────────────────────────────

        # ROE = Laba Bersih / Ekuitas × 100
        metrics.roe = self.safe_divide(lr.net_profit, neraca.total_equity)
        if metrics.roe:
            metrics.roe = round(metrics.roe * 100, 2) if abs(metrics.roe) < 10 else round(metrics.roe, 2)

        # ROA = Laba Bersih / Total Aset × 100
        metrics.roa = self.safe_divide(lr.net_profit, neraca.total_assets)
        if metrics.roa:
            metrics.roa = round(metrics.roa * 100 if abs(metrics.roa) < 1 else metrics.roa, 2)

        # Net Profit Margin = Laba Bersih / Revenue × 100
        revenue = lr.revenue or lr.interest_income
        metrics.npm = self.safe_divide(lr.net_profit, revenue)
        if metrics.npm:
            metrics.npm = round(metrics.npm * 100 if abs(metrics.npm) < 1 else metrics.npm, 2)

        # Gross Profit Margin
        metrics.gross_margin = self.safe_divide(lr.gross_profit, revenue)
        if metrics.gross_margin and abs(metrics.gross_margin) < 1:
            metrics.gross_margin = round(metrics.gross_margin * 100, 2)

        # ─── PER SAHAM ────────────────────────────────────────────────────────

        if shares and shares > 0:
            # EPS = Laba Bersih / Jumlah Saham Beredar
            if lr.net_profit:
                raw_eps = lr.net_profit / shares
                # Jika raw_eps sangat kecil (< 0.5), kemungkinan besar laba dalam jutaan Rupiah
                if abs(raw_eps) > 0 and abs(raw_eps) < 0.5:
                    metrics.eps = round(raw_eps * 1_000_000, 2)
                else:
                    metrics.eps = round(raw_eps, 2)

            # BVPS = Ekuitas / Jumlah Saham Beredar
            if neraca.total_equity:
                raw_bvps = neraca.total_equity / shares
                if abs(raw_bvps) > 0 and abs(raw_bvps) < 5:
                    metrics.bvps = round(raw_bvps * 1_000_000, 2)
                else:
                    metrics.bvps = round(raw_bvps, 2)

        # DPS dari corporate actions
        metrics.dps = dividends_per_share

        # ─── PERTUMBUHAN ──────────────────────────────────────────────────────

        if len(financial_data_list) >= 2:
            prev = financial_data_list[1]

            # Revenue Growth YoY
            curr_rev = lr.revenue or lr.interest_income
            prev_rev = prev.laba_rugi.revenue or prev.laba_rugi.interest_income
            metrics.revenue_growth_yoy = self.safe_growth(curr_rev, prev_rev)

            # Profit Growth YoY
            metrics.profit_growth_yoy = self.safe_growth(
                lr.net_profit, prev.laba_rugi.net_profit
            )

        # CAGR 5 tahun
        if len(financial_data_list) >= 5:
            oldest = financial_data_list[-1]
            years = latest.year - oldest.year

            if years > 0:
                curr_rev = lr.revenue or lr.interest_income
                old_rev = oldest.laba_rugi.revenue or oldest.laba_rugi.interest_income

                if curr_rev and old_rev and old_rev > 0:
                    metrics.revenue_cagr_5y = round(
                        self.cagr(old_rev, curr_rev, years), 2
                    )

                if lr.net_profit and oldest.laba_rugi.net_profit and oldest.laba_rugi.net_profit > 0:
                    metrics.profit_cagr_5y = round(
                        self.cagr(oldest.laba_rugi.net_profit, lr.net_profit, years), 2
                    )

        # ─── LEVERAGE ─────────────────────────────────────────────────────────

        # Debt-to-Equity Ratio = Total Utang / Ekuitas
        metrics.debt_to_equity = self.safe_divide(
            neraca.total_liabilities, neraca.total_equity
        )
        if metrics.debt_to_equity:
            metrics.debt_to_equity = round(metrics.debt_to_equity, 2)

        # Debt-to-Assets Ratio = Total Utang / Total Aset
        metrics.debt_to_assets = self.safe_divide(
            neraca.total_liabilities, neraca.total_assets
        )
        if metrics.debt_to_assets:
            metrics.debt_to_assets = round(metrics.debt_to_assets, 2)

        # Current Ratio = Aset Lancar / Kewajiban Lancar
        metrics.current_ratio = self.safe_divide(
            neraca.current_assets, neraca.current_liabilities
        )
        if metrics.current_ratio:
            metrics.current_ratio = round(metrics.current_ratio, 2)

        # ─── BANK-SPECIFIC ────────────────────────────────────────────────────

        # NIM = Net Interest Income / Earning Assets (approximasi)
        if lr.interest_income and lr.interest_expense and neraca.total_assets:
            nim_amount = lr.interest_income - lr.interest_expense
            metrics.nim = round(self.safe_divide(nim_amount, neraca.total_assets) * 100, 2)

        # NPL dari neraca
        metrics.npl_gross = neraca.npl_gross

        # CASA Ratio
        if neraca.casa and neraca.total_assets:
            metrics.casa_ratio = round(
                self.safe_divide(neraca.casa, neraca.total_assets) * 100, 2
            )
        elif neraca.casa_ratio:
            metrics.casa_ratio = neraca.casa_ratio

        # LDR = Total Kredit / Total DPK (Dana Pihak Ketiga ≈ Total Liabilitas untuk bank)
        if neraca.total_loans and neraca.total_liabilities:
            metrics.loan_to_deposit = round(
                self.safe_divide(neraca.total_loans, neraca.total_liabilities) * 100, 2
            )

        # Normalisasi ROE (pastikan dalam %)
        if metrics.roe and abs(metrics.roe) < 1:
            metrics.roe = round(metrics.roe * 100, 2)
        if metrics.roa and abs(metrics.roa) < 1:
            metrics.roa = round(metrics.roa * 100, 2)

        logger.info(
            f"Metrics calculated: ROE={metrics.roe}%, EPS={metrics.eps}, "
            f"BVPS={metrics.bvps}, Revenue Growth={metrics.revenue_growth_yoy}%"
        )

        return metrics

    def calculate_valuasi(
        self,
        metrics: FundamentalMetrics,
        current_price: float,
        shares_outstanding: float = 0,
        historical_per: float = 14.0,
        historical_pbv: float = 2.0,
        price_52w_high: Optional[float] = None,
        price_52w_low: Optional[float] = None,
        market_cap: Optional[float] = None,
        dividend_yield: Optional[float] = None,
    ) -> ValuasiMetrics:
        """
        Hitung semua metrik valuasi berdasarkan harga pasar dan metrik fundamental.
        
        Args:
            metrics: Metrik fundamental yang sudah dihitung
            current_price: Harga pasar terkini (Rupiah)
            historical_per: Rata-rata PER historis perusahaan
            historical_pbv: Rata-rata PBV historis
        
        Returns:
            ValuasiMetrics dengan semua rasio valuasi
        """
        v = ValuasiMetrics(current_price=current_price)

        # ─── VALUASI DASAR ────────────────────────────────────────────────────

        # PER = Harga / EPS
        if metrics.eps and metrics.eps > 0:
            v.per = round(current_price / metrics.eps, 2)

        # PBV = Harga / BVPS (Book Value Per Share)
        if metrics.bvps and metrics.bvps > 0:
            v.pbv = round(current_price / metrics.bvps, 2)

        # Dividend Yield
        # Prioritas: hitung sendiri dari DPS ÷ Harga (paling andal & konsisten).
        # Nilai yfinance hanya dipakai sebagai fallback jika DPS tidak tersedia.
        if metrics.dps and current_price > 0:
            v.dividend_yield = round((metrics.dps / current_price) * 100, 2)
        elif dividend_yield:
            v.dividend_yield = round(dividend_yield, 2)

        # Market Cap
        if market_cap:
            v.market_cap = market_cap

        # 52W range
        v.price_52w_high = price_52w_high
        v.price_52w_low = price_52w_low

        # ─── HARGA WAJAR ─────────────────────────────────────────────────────

        if metrics.eps and metrics.eps > 0:
            # Metode 1: Graham Number
            # Fair Value = √(22.5 × EPS × BVPS)
            if metrics.bvps and metrics.bvps > 0:
                graham_number = (22.5 * metrics.eps * metrics.bvps) ** 0.5
                v.fair_value_graham = round(graham_number, 0)

            # Metode 2: PER × 15 (Konservatif — Buffett style)
            # Asumsi: perusahaan bagus layak dihargai PER 15x
            v.fair_value_conservative = round(metrics.eps * 15, 0)

            # Metode 3: PER × 18 (Normal)
            # Asumsi: rata-rata pasar PER 18x untuk perusahaan berkualitas
            v.fair_value_normal = round(metrics.eps * 18, 0)

        # ─── MARGIN OF SAFETY ─────────────────────────────────────────────────

        # MoS = (Harga Wajar - Harga Pasar) / Harga Wajar × 100
        if v.fair_value_conservative and v.fair_value_conservative > 0:
            mos = (v.fair_value_conservative - current_price) / v.fair_value_conservative * 100
            v.margin_of_safety = round(mos, 2)

        # ─── HISTORIS ─────────────────────────────────────────────────────────

        v.per_historical_avg = historical_per
        v.pbv_historical_avg = historical_pbv

        fv_cons = v.fair_value_conservative or 0
        logger.info(
            f"Valuasi: PER={v.per}x, PBV={v.pbv}x, "
            f"FV_Conservative=Rp{fv_cons:,.0f}, "
            f"MoS={v.margin_of_safety}%"
        )

        return v
