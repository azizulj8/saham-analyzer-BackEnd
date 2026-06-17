"""
Scorer Engine — Framework Buffett + Graham
Memberikan skor fundamental (0-10) dan valuasi (0-10) berdasarkan data keuangan.
"""
import logging
from typing import List
from models.schemas import (
    FinancialData, FundamentalMetrics, ValuasiMetrics,
    FundamentalScore, ValuasiScore, RisikoAssessment,
    RisikoLevel, KeputusanEnum
)

logger = logging.getLogger(__name__)


class ScorerEngine:
    """
    Engine untuk menghitung skor analisis saham menggunakan
    framework Buffett (fundamental) + Graham (valuasi).
    
    Skor Fundamental (max 10):
    - Laba tumbuh konsisten 5 tahun: max 2.0
    - Revenue bertumbuh: max 1.5
    - ROE > 15%: max 2.0
    - Utang terkendali: max 2.0
    - Free Cash Flow positif: max 1.5
    - Keunggulan kompetitif (moat): max 1.0
    
    Skor Valuasi (max 10):
    - PER vs historis: max 4.0
    - PBV vs historis: max 3.0
    - Margin of Safety: max 3.0
    """

    def score_fundamental(
        self,
        metrics: FundamentalMetrics,
        financial_data_list: List[FinancialData],
    ) -> FundamentalScore:
        """
        Hitung skor fundamental ala Buffett.
        
        Args:
            metrics: Metrik fundamental yang sudah dihitung
            financial_data_list: Data keuangan multi-tahun
        
        Returns:
            FundamentalScore dengan total dan detail per kategori
        """
        score = FundamentalScore()
        details = {}

        # ─── 1. LABA TUMBUH KONSISTEN (max 2.0) ──────────────────────────────
        # Idealnya: laba naik setiap tahun selama 5 tahun

        profit_score = 0.0
        profit_details = []

        if len(financial_data_list) >= 2:
            profits = [
                fd.laba_rugi.net_profit
                for fd in financial_data_list
                if fd.laba_rugi.net_profit
            ]

            if len(profits) >= 2:
                # Cek berapa tahun laba naik
                positive_growth_years = sum(
                    1 for i in range(len(profits) - 1)
                    if profits[i] > profits[i + 1]  # Data urutan terbaru-ke-lama
                )
                total_years = len(profits) - 1

                growth_rate = positive_growth_years / total_years if total_years > 0 else 0

                if growth_rate >= 0.8:
                    profit_score = 2.0
                    profit_details.append("✅ Laba tumbuh konsisten > 80% tahun")
                elif growth_rate >= 0.6:
                    profit_score = 1.5
                    profit_details.append("✅ Laba tumbuh konsisten > 60% tahun")
                elif growth_rate >= 0.4:
                    profit_score = 1.0
                    profit_details.append("⚠️ Laba tumbuh tapi tidak konsisten")
                else:
                    profit_score = 0.0
                    profit_details.append("❌ Laba tidak tumbuh atau turun")

            # Bonus: CAGR laba > 10%
            if metrics.profit_cagr_5y and metrics.profit_cagr_5y > 10:
                profit_score = min(profit_score + 0.3, 2.0)
                profit_details.append(f"✅ CAGR Laba 5 tahun: {metrics.profit_cagr_5y:.1f}%")

        score.profit_growth = round(profit_score, 2)
        details["laba_tumbuh"] = profit_details

        # ─── 2. REVENUE BERTUMBUH (max 1.5) ─────────────────────────────────

        rev_score = 0.0
        rev_details = []

        if metrics.revenue_growth_yoy is not None:
            if metrics.revenue_growth_yoy > 10:
                rev_score = 1.5
                rev_details.append(f"✅ Revenue tumbuh {metrics.revenue_growth_yoy:.1f}% YoY")
            elif metrics.revenue_growth_yoy > 5:
                rev_score = 1.2
                rev_details.append(f"✅ Revenue tumbuh {metrics.revenue_growth_yoy:.1f}% YoY")
            elif metrics.revenue_growth_yoy > 0:
                rev_score = 0.8
                rev_details.append(f"⚠️ Revenue tumbuh tipis {metrics.revenue_growth_yoy:.1f}% YoY")
            else:
                rev_score = 0.0
                rev_details.append(f"❌ Revenue turun {metrics.revenue_growth_yoy:.1f}% YoY")

        if metrics.revenue_cagr_5y:
            rev_details.append(f"CAGR Revenue 5 tahun: {metrics.revenue_cagr_5y:.1f}%")

        score.revenue_growth = round(rev_score, 2)
        details["revenue"] = rev_details

        # ─── 3. ROE > 15% (max 2.0) ──────────────────────────────────────────

        roe_score = 0.0
        roe_details = []

        if metrics.roe:
            roe = abs(metrics.roe)  # Handle jika sudah dalam desimal
            if roe < 1:  # Konversi ke %
                roe = roe * 100

            if roe > 20:
                roe_score = 2.0
                roe_details.append(f"✅ ROE sangat tinggi: {roe:.1f}% (> 20%)")
            elif roe > 15:
                roe_score = 1.7
                roe_details.append(f"✅ ROE baik: {roe:.1f}% (> 15%)")
            elif roe > 10:
                roe_score = 1.2
                roe_details.append(f"⚠️ ROE sedang: {roe:.1f}% (10-15%)")
            elif roe > 5:
                roe_score = 0.7
                roe_details.append(f"⚠️ ROE rendah: {roe:.1f}% (5-10%)")
            else:
                roe_score = 0.0
                roe_details.append(f"❌ ROE sangat rendah: {roe:.1f}% (< 5%)")

        score.roe_score = round(roe_score, 2)
        details["roe"] = roe_details

        # ─── 4. UTANG TERKENDALI (max 2.0) ────────────────────────────────────

        debt_score = 0.0
        debt_details = []

        der = metrics.debt_to_equity

        if der is not None:
            # Untuk bank, DER tinggi adalah normal (leverage bisnis)
            # Cek apakah bank dari NPL/CASA
            is_bank = (
                metrics.npl_gross is not None or
                metrics.casa_ratio is not None or
                metrics.nim is not None
            )

            if is_bank:
                # Bank: DER > 10 adalah normal, kita pakai NPL sebagai indikator utama
                if metrics.npl_gross and metrics.npl_gross < 2:
                    debt_score = 2.0
                    debt_details.append(f"✅ NPL sangat rendah: {metrics.npl_gross:.2f}%")
                elif metrics.npl_gross and metrics.npl_gross < 3:
                    debt_score = 1.5
                    debt_details.append(f"✅ NPL terkendali: {metrics.npl_gross:.2f}%")
                elif metrics.npl_gross and metrics.npl_gross < 5:
                    debt_score = 1.0
                    debt_details.append(f"⚠️ NPL mulai tinggi: {metrics.npl_gross:.2f}%")
                else:
                    debt_score = 0.5
                    debt_details.append(f"⚠️ NPL tinggi atau tidak diketahui")
                    if metrics.npl_gross:
                        debt_details[-1] = f"❌ NPL tinggi: {metrics.npl_gross:.2f}%"
            else:
                # Non-bank: DER < 1 adalah ideal
                if der < 0.5:
                    debt_score = 2.0
                    debt_details.append(f"✅ DER sangat rendah: {der:.2f}x")
                elif der < 1.0:
                    debt_score = 1.7
                    debt_details.append(f"✅ DER rendah: {der:.2f}x")
                elif der < 2.0:
                    debt_score = 1.2
                    debt_details.append(f"⚠️ DER sedang: {der:.2f}x")
                elif der < 3.0:
                    debt_score = 0.7
                    debt_details.append(f"⚠️ DER tinggi: {der:.2f}x")
                else:
                    debt_score = 0.0
                    debt_details.append(f"❌ DER sangat tinggi: {der:.2f}x")

        score.debt_score = round(debt_score, 2)
        details["utang"] = debt_details

        # ─── 5. FREE CASH FLOW POSITIF (max 1.5) ──────────────────────────────

        fcf_score = 0.0
        fcf_details = []

        if financial_data_list:
            latest_kas = financial_data_list[0].arus_kas

            # Cek FCF
            fcf = latest_kas.free_cash_flow
            operating_cf = latest_kas.operating_cf

            if fcf and fcf > 0:
                fcf_score = 1.5
                fcf_details.append(f"✅ Free Cash Flow positif")
            elif operating_cf and operating_cf > 0:
                fcf_score = 1.0
                fcf_details.append(f"✅ Operating Cash Flow positif")
            elif fcf is None and operating_cf is None:
                fcf_score = 0.75  # Tidak ada data, asumsi netral
                fcf_details.append("⚪ Data arus kas tidak tersedia")
            else:
                fcf_score = 0.0
                fcf_details.append(f"❌ Cash Flow negatif")

        score.fcf_score = round(fcf_score, 2)
        details["fcf"] = fcf_details

        # ─── 6. MOAT / KEUNGGULAN KOMPETITIF (max 1.0) ───────────────────────
        # Ini dinilai berdasarkan kombinasi metrik:
        # - NPM tinggi = pricing power
        # - ROE konsisten tinggi = efisiensi + keunggulan kompetitif
        # - CASA ratio tinggi (bank) = moat cost of funding

        moat_score = 0.0
        moat_details = []

        # ROE konsisten tinggi menunjukkan moat
        if metrics.roe:
            roe_val = abs(metrics.roe)
            if roe_val < 1:
                roe_val *= 100

            if roe_val > 20 and (metrics.profit_cagr_5y or 0) > 8:
                moat_score += 0.5
                moat_details.append("✅ ROE tinggi + pertumbuhan laba konsisten = moat kuat")

        # NPM tinggi = pricing power
        if metrics.npm:
            npm = abs(metrics.npm)
            if npm < 1:
                npm *= 100
            if npm > 20:
                moat_score += 0.3
                moat_details.append(f"✅ Net Profit Margin tinggi: {npm:.1f}%")

        # CASA ratio tinggi untuk bank = moat pendanaan murah
        if metrics.casa_ratio and metrics.casa_ratio > 50:
            moat_score += 0.2
            moat_details.append(f"✅ CASA ratio tinggi: {metrics.casa_ratio:.1f}% (pendanaan murah)")

        moat_score = min(moat_score, 1.0)

        if not moat_details:
            moat_details.append("⚪ Keunggulan kompetitif perlu analisis lebih lanjut")

        score.moat_score = round(moat_score, 2)
        details["moat"] = moat_details

        # ─── TOTAL SKOR FUNDAMENTAL ────────────────────────────────────────────

        score.total = round(
            score.profit_growth +
            score.revenue_growth +
            score.roe_score +
            score.debt_score +
            score.fcf_score +
            score.moat_score,
            2
        )
        score.details = details

        logger.info(
            f"Fundamental Score: {score.total}/10 "
            f"(Laba={score.profit_growth}, Revenue={score.revenue_growth}, "
            f"ROE={score.roe_score}, Utang={score.debt_score}, "
            f"FCF={score.fcf_score}, Moat={score.moat_score})"
        )

        return score

    def score_valuasi(
        self,
        valuasi: ValuasiMetrics,
    ) -> ValuasiScore:
        """
        Hitung skor valuasi ala Graham.
        
        Args:
            valuasi: Metrik valuasi yang sudah dihitung
        
        Returns:
            ValuasiScore dengan total dan detail per kategori
        """
        score = ValuasiScore()
        details = {}

        # ─── 1. PER vs HISTORIS (max 4.0) ────────────────────────────────────

        per_score = 0.0
        per_details = []

        if valuasi.per and valuasi.per_historical_avg:
            per = valuasi.per
            hist_per = valuasi.per_historical_avg

            discount = (hist_per - per) / hist_per * 100

            if per < 0:
                per_score = 0.0
                per_details.append("❌ PER negatif (perusahaan rugi)")
            elif per < hist_per * 0.5:
                per_score = 4.0
                per_details.append(f"✅ PER sangat murah: {per:.1f}x (diskon {discount:.0f}% vs historis {hist_per:.0f}x)")
            elif per < hist_per * 0.7:
                per_score = 3.5
                per_details.append(f"✅ PER murah: {per:.1f}x (diskon {discount:.0f}% vs historis {hist_per:.0f}x)")
            elif per < hist_per * 0.9:
                per_score = 2.5
                per_details.append(f"✅ PER di bawah rata-rata: {per:.1f}x vs historis {hist_per:.0f}x")
            elif per < hist_per * 1.1:
                per_score = 1.5
                per_details.append(f"⚠️ PER wajar: {per:.1f}x ≈ historis {hist_per:.0f}x")
            elif per < hist_per * 1.3:
                per_score = 0.8
                per_details.append(f"⚠️ PER di atas historis: {per:.1f}x vs {hist_per:.0f}x")
            else:
                per_score = 0.0
                per_details.append(f"❌ PER mahal: {per:.1f}x >> historis {hist_per:.0f}x")

        elif valuasi.per and not valuasi.per_historical_avg:
            per = valuasi.per
            if per < 8:
                per_score = 3.5
            elif per < 12:
                per_score = 2.5
            elif per < 15:
                per_score = 2.0
            elif per < 20:
                per_score = 1.5
            elif per < 25:
                per_score = 1.0
            else:
                per_score = 0.5
            per_details.append(f"PER: {per:.1f}x")

        score.per_score = round(per_score, 2)
        details["per"] = per_details

        # ─── 2. PBV vs HISTORIS (max 3.0) ────────────────────────────────────

        pbv_score = 0.0
        pbv_details = []

        if valuasi.pbv and valuasi.pbv > 0:
            pbv = valuasi.pbv
            hist_pbv = valuasi.pbv_historical_avg or 2.0

            if pbv < 1.0:
                pbv_score = 3.0
                pbv_details.append(f"✅ PBV < 1x — Harga di bawah nilai buku! ({pbv:.2f}x)")
            elif pbv < hist_pbv * 0.6:
                pbv_score = 2.5
                pbv_details.append(f"✅ PBV sangat murah vs historis: {pbv:.2f}x vs {hist_pbv:.1f}x")
            elif pbv < hist_pbv * 0.8:
                pbv_score = 2.0
                pbv_details.append(f"✅ PBV di bawah historis: {pbv:.2f}x vs {hist_pbv:.1f}x")
            elif pbv < hist_pbv * 1.0:
                pbv_score = 1.5
                pbv_details.append(f"⚠️ PBV mendekati historis: {pbv:.2f}x vs {hist_pbv:.1f}x")
            elif pbv < hist_pbv * 1.3:
                pbv_score = 1.0
                pbv_details.append(f"⚠️ PBV di atas historis: {pbv:.2f}x vs {hist_pbv:.1f}x")
            else:
                pbv_score = 0.0
                pbv_details.append(f"❌ PBV mahal: {pbv:.2f}x >> historis {hist_pbv:.1f}x")

        score.pbv_score = round(pbv_score, 2)
        details["pbv"] = pbv_details

        # ─── 3. MARGIN OF SAFETY (max 3.0) ────────────────────────────────────

        mos_score = 0.0
        mos_details = []

        if valuasi.margin_of_safety is not None:
            mos = valuasi.margin_of_safety

            if mos > 40:
                mos_score = 3.0
                mos_details.append(f"✅ Margin of Safety sangat besar: {mos:.1f}% (> 40%)")
            elif mos > 30:
                mos_score = 2.5
                mos_details.append(f"✅ Margin of Safety sangat menarik: {mos:.1f}% (30-40%)")
            elif mos > 20:
                mos_score = 2.0
                mos_details.append(f"✅ Margin of Safety menarik: {mos:.1f}% (20-30%)")
            elif mos > 10:
                mos_score = 1.5
                mos_details.append(f"⚠️ Margin of Safety cukup: {mos:.1f}% (10-20%)")
            elif mos > 0:
                mos_score = 1.0
                mos_details.append(f"⚠️ Margin of Safety tipis: {mos:.1f}% (< 10%)")
            else:
                mos_score = 0.0
                mos_details.append(f"❌ Harga di atas nilai wajar: MoS {mos:.1f}%")

            if valuasi.fair_value_conservative:
                mos_details.append(
                    f"Harga Wajar Konservatif: Rp{valuasi.fair_value_conservative:,.0f}"
                )
            if valuasi.fair_value_normal:
                mos_details.append(
                    f"Harga Wajar Normal: Rp{valuasi.fair_value_normal:,.0f}"
                )

        score.mos_score = round(mos_score, 2)
        details["margin_of_safety"] = mos_details

        # ─── TOTAL SKOR VALUASI ────────────────────────────────────────────────

        score.total = round(
            score.per_score + score.pbv_score + score.mos_score, 2
        )
        score.details = details

        logger.info(
            f"Valuasi Score: {score.total}/10 "
            f"(PER={score.per_score}, PBV={score.pbv_score}, MoS={score.mos_score})"
        )

        return score

    def assess_risiko(
        self,
        metrics: FundamentalMetrics,
        valuasi: ValuasiMetrics,
        fundamental_score: FundamentalScore,
    ) -> RisikoAssessment:
        """Penilaian risiko komprehensif"""
        risiko = RisikoAssessment()

        # ─── RISIKO BISNIS ────────────────────────────────────────────────────
        catatan_bisnis = []

        if metrics.roe:
            roe = abs(metrics.roe)
            if roe < 1:
                roe *= 100
            if roe < 10:
                risiko.bisnis = RisikoLevel.TINGGI
                catatan_bisnis.append("ROE rendah menunjukkan bisnis kurang efisien")
            elif roe < 15:
                catatan_bisnis.append("ROE perlu ditingkatkan")

        if metrics.profit_growth_yoy and metrics.profit_growth_yoy < -10:
            risiko.bisnis = RisikoLevel.TINGGI
            catatan_bisnis.append(f"Laba turun signifikan {metrics.profit_growth_yoy:.1f}% YoY")

        if not catatan_bisnis:
            if fundamental_score.total >= 7:
                risiko.bisnis = RisikoLevel.RENDAH
                catatan_bisnis.append("Bisnis fundamental kuat")
            elif fundamental_score.total >= 5:
                risiko.bisnis = RisikoLevel.SEDANG
                catatan_bisnis.append("Bisnis cukup baik dengan beberapa area perhatian")

        risiko.catatan_bisnis = catatan_bisnis

        # ─── RISIKO KEUANGAN ──────────────────────────────────────────────────
        catatan_keuangan = []

        der = metrics.debt_to_equity
        is_bank = metrics.npl_gross is not None or metrics.casa_ratio is not None

        if is_bank:
            if metrics.npl_gross and metrics.npl_gross > 5:
                risiko.keuangan = RisikoLevel.TINGGI
                catatan_keuangan.append(f"NPL tinggi: {metrics.npl_gross:.2f}%")
            elif metrics.npl_gross and metrics.npl_gross < 2:
                risiko.keuangan = RisikoLevel.RENDAH
                catatan_keuangan.append(f"NPL sangat terkendali: {metrics.npl_gross:.2f}%")
            else:
                catatan_keuangan.append(f"NPL dalam batas wajar")
        else:
            if der and der > 3:
                risiko.keuangan = RisikoLevel.TINGGI
                catatan_keuangan.append(f"DER sangat tinggi: {der:.2f}x")
            elif der and der > 1.5:
                risiko.keuangan = RisikoLevel.SEDANG
                catatan_keuangan.append(f"DER perlu diperhatikan: {der:.2f}x")
            elif der and der < 0.5:
                risiko.keuangan = RisikoLevel.RENDAH
                catatan_keuangan.append(f"Neraca sangat sehat, DER: {der:.2f}x")

        if metrics.current_ratio and metrics.current_ratio < 1.0:
            catatan_keuangan.append(f"⚠️ Current Ratio < 1x: {metrics.current_ratio:.2f}x")

        risiko.catatan_keuangan = catatan_keuangan

        # ─── RISIKO HARGA ─────────────────────────────────────────────────────
        catatan_harga = []

        mos = valuasi.margin_of_safety

        if mos is not None:
            if mos < -20:
                risiko.harga = RisikoLevel.TINGGI
                catatan_harga.append(f"Harga sangat mahal, {abs(mos):.1f}% di atas nilai wajar")
            elif mos < 0:
                risiko.harga = RisikoLevel.SEDANG
                catatan_harga.append(f"Harga sedikit di atas nilai wajar ({abs(mos):.1f}%)")
            elif mos < 15:
                risiko.harga = RisikoLevel.SEDANG
                catatan_harga.append(f"Harga wajar, margin keamanan tipis ({mos:.1f}%)")
            else:
                risiko.harga = RisikoLevel.RENDAH
                catatan_harga.append(f"Harga menarik dengan margin keamanan {mos:.1f}%")

        # Posisi vs 52W
        if valuasi.price_52w_high and valuasi.current_price:
            distance_from_high = (
                (valuasi.price_52w_high - valuasi.current_price) /
                valuasi.price_52w_high * 100
            )
            if distance_from_high < 5:
                catatan_harga.append(f"⚠️ Harga dekat puncak 52 minggu")
            elif distance_from_high > 30:
                catatan_harga.append(f"✅ Harga sudah jauh terkoreksi dari puncak ({distance_from_high:.0f}%)")

        risiko.catatan_harga = catatan_harga

        # ─── OVERALL RISK ──────────────────────────────────────────────────────

        risk_levels = [risiko.bisnis, risiko.keuangan, risiko.harga]
        if RisikoLevel.TINGGI in risk_levels:
            risiko.overall = RisikoLevel.TINGGI
        elif risk_levels.count(RisikoLevel.SEDANG) >= 2:
            risiko.overall = RisikoLevel.SEDANG
        elif all(r == RisikoLevel.RENDAH for r in risk_levels):
            risiko.overall = RisikoLevel.RENDAH
        else:
            risiko.overall = RisikoLevel.SEDANG

        return risiko

    def determine_keputusan(
        self,
        fundamental_score: FundamentalScore,
        valuasi_score: ValuasiScore,
        valuasi: ValuasiMetrics,
        risiko: RisikoAssessment,
    ) -> tuple[KeputusanEnum, list[str]]:
        """
        Tentukan keputusan akhir: BELI, CICIL_BELI, TUNGGU, atau LEWATI.
        
        Returns:
            Tuple (keputusan, alasan_list)
        """
        mos = valuasi.margin_of_safety or 0
        fund_score = fundamental_score.total
        val_score = valuasi_score.total

        alasan = []

        # LEWATI: Fundamental sangat buruk
        if fund_score < 4.0:
            alasan.append(f"Skor fundamental rendah ({fund_score}/10)")
            if risiko.overall == RisikoLevel.TINGGI:
                alasan.append("Risiko keseluruhan tinggi")
            return KeputusanEnum.LEWATI, alasan

        # LEWATI: Risiko sangat tinggi di semua dimensi
        if (risiko.bisnis == RisikoLevel.TINGGI and
                risiko.keuangan == RisikoLevel.TINGGI):
            alasan.append("Risiko bisnis dan keuangan sama-sama tinggi")
            return KeputusanEnum.LEWATI, alasan

        # TUNGGU: Fundamental bagus tapi harga mahal
        if fund_score >= 7.0 and mos < 5:
            alasan.append(f"Bisnis bagus (skor {fund_score}/10) tapi harga di atas nilai wajar")
            if valuasi.fair_value_conservative:
                alasan.append(
                    f"Target beli: Rp{valuasi.fair_value_conservative * 0.9:,.0f} "
                    f"atau lebih rendah"
                )
            return KeputusanEnum.TUNGGU, alasan

        # BELI: Fundamental kuat + harga sangat murah
        if fund_score >= 7.0 and mos >= 25 and risiko.overall != RisikoLevel.TINGGI:
            alasan.append(f"Fundamental sangat kuat ({fund_score}/10)")
            alasan.append(f"Margin of Safety besar: {mos:.1f}%")
            alasan.append(f"Harga jauh di bawah nilai wajar")
            return KeputusanEnum.BELI, alasan

        # CICIL BELI: Fundamental bagus + harga cukup menarik
        if fund_score >= 6.5 and mos >= 10 and risiko.overall != RisikoLevel.TINGGI:
            alasan.append(f"Fundamental baik ({fund_score}/10)")
            alasan.append(f"Margin of Safety: {mos:.1f}% — cukup menarik")
            alasan.append("Rekomendasi: cicil beli untuk rata-rata harga")
            return KeputusanEnum.CICIL_BELI, alasan

        # CICIL BELI: Fundamental sedang tapi harga sangat murah
        if fund_score >= 5.0 and mos >= 30:
            alasan.append(f"Harga sangat murah (MoS {mos:.1f}%)")
            alasan.append("Cicil dengan posisi kecil sambil pantau fundamental")
            return KeputusanEnum.CICIL_BELI, alasan

        # Default: TUNGGU
        if fund_score >= 5.0:
            alasan.append(f"Fundamental cukup ({fund_score}/10)")
            alasan.append(f"Margin of Safety belum cukup: {mos:.1f}%")
            alasan.append("Tunggu harga lebih menarik")
        else:
            alasan.append(f"Fundamental perlu diperhatikan ({fund_score}/10)")
            alasan.append("Lakukan riset lebih mendalam sebelum beli")

        return KeputusanEnum.TUNGGU, alasan

    def calculate_buy_zones(
        self, valuasi: ValuasiMetrics
    ) -> tuple[float | None, float | None, float | None]:
        """
        Hitung zona harga: area beli bawah, area beli atas, area ambil untung.
        
        Returns:
            Tuple (area_beli_bawah, area_beli_atas, area_jual)
        """
        fv_cons = valuasi.fair_value_conservative
        fv_norm = valuasi.fair_value_normal

        if not fv_cons:
            return None, None, None

        # Area beli: 85-90% dari fair value konservatif
        area_beli_bawah = round(fv_cons * 0.80, 0)
        area_beli_atas = round(fv_cons * 0.92, 0)

        # Area jual: di sekitar fair value normal
        area_jual = round(fv_norm * 0.95, 0) if fv_norm else round(fv_cons * 1.15, 0)

        return area_beli_bawah, area_beli_atas, area_jual
