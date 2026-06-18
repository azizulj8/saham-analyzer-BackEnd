"""
LLM Analyzer Service — Claude Sonnet Integration
Menggunakan Claude Sonnet untuk analisis naratif laporan keuangan.
"""
import logging
import os
from typing import Optional
from anthropic import Anthropic, APIStatusError
from dotenv import load_dotenv

from models.schemas import (
    FinancialData, FundamentalMetrics, ValuasiMetrics, RisikoAssessment,
    LabaRugi, Neraca, ArusKas,
)

logger = logging.getLogger(__name__)


class LLMAnalyzer:
    """
    Service untuk analisis naratif menggunakan Claude Sonnet.
    
    Claude membaca isi laporan keuangan (teks PDF) dan menghasilkan:
    1. Ringkasan kondisi fundamental bisnis
    2. Ringkasan kondisi valuasi
    3. Identifikasi red flag / risiko tersembunyi
    4. Highlight poin-poin penting
    """

    def __init__(self):
        # We will initialize the client lazily to ensure env vars are loaded
        self.client = None
        
    def _get_client(self):
        if not self.client:
            load_dotenv()
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if api_key:
                self.client = Anthropic(api_key=api_key)
            else:
                logger.warning("ANTHROPIC_API_KEY tidak ditemukan. LLM analysis dinonaktifkan.")
        return self.client

    def is_available(self) -> bool:
        client = self._get_client()
        return client is not None

    async def analyze(
        self,
        ticker: str,
        company_name: str,
        sector: str,
        pdf_text: str,
        metrics: FundamentalMetrics,
        valuasi: ValuasiMetrics,
        risiko: RisikoAssessment,
        fundamental_score: float,
        valuasi_score: float,
        keputusan: str,
        margin_of_safety: Optional[float],
    ) -> dict:
        """
        Analisis naratif lengkap menggunakan Claude Sonnet.
        
        Returns:
            Dict berisi ringkasan_fundamental, ringkasan_valuasi,
            alasan_keputusan, red_flags, highlights
        """
        client = self._get_client()
        if not client:
            return self._fallback_analysis(
                ticker, metrics, valuasi, fundamental_score,
                valuasi_score, keputusan, margin_of_safety
            )

        # Buat prompt yang komprehensif
        prompt = self._build_prompt(
            ticker=ticker,
            company_name=company_name,
            sector=sector,
            pdf_text=pdf_text,
            metrics=metrics,
            valuasi=valuasi,
            fundamental_score=fundamental_score,
            valuasi_score=valuasi_score,
            keputusan=keputusan,
            margin_of_safety=margin_of_safety,
        )

        try:
            logger.info(f"Meminta analisis Claude untuk {ticker}...")

            # System prompt dinamis
            base_system = (
                "Kamu adalah analis saham senior Indonesia dengan pengalaman 20 tahun. "
                "Kamu mengkhususkan diri dalam analisis fundamental menggunakan framework "
                "Warren Buffett dan Benjamin Graham. "
                "Analisismu tajam, jujur, dan berbasis data — tidak memberikan rekomendasi berlebihan. "
                "Gunakan bahasa Indonesia yang profesional namun mudah dipahami investor ritel."
            )

            if pdf_text and pdf_text.strip():
                system_prompt = base_system + " Kamu sedang menganalisis teks mentah dari file PDF laporan keuangan asli. Percayai data dari teks PDF tersebut."
            else:
                system_prompt = base_system + (
                    " PERINGATAN PENTING: Dokumen laporan keuangan saat ini TIDAK TERSEDIA. "
                    "Kamu HANYA diberikan beberapa angka rasio fundamental dari Yahoo Finance. "
                    "DILARANG KERAS menyebutkan atau mengarang/menebak angka absolut (seperti total aset dalam rupiah, total laba dalam rupiah) yang tidak ada di dalam prompt. "
                    "Analisis HANYA boleh merujuk pada rasio persentase dan kelipatan (PER/PBV) yang secara eksplisit diberikan di prompt."
                )

            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                system=system_prompt
            )

            response_text = message.content[0].text
            return self._parse_llm_response(response_text)

        except Exception as e:
            logger.error(f"Error Claude API untuk {ticker}: {e}")
            return self._fallback_analysis(
                ticker, metrics, valuasi, fundamental_score,
                valuasi_score, keputusan, margin_of_safety
            )

    def extract_financial_data(
        self,
        pdf_text: str,
        ticker: str,
        company_name: str,
        sector: str,
        year: int,
        shares_outstanding: float = 0,
    ) -> Optional[FinancialData]:
        """
        Ekstrak data keuangan TERSTRUKTUR dari teks PDF menggunakan Claude.
        Lebih andal daripada parser regex karena Claude memahami konteks tabel,
        skala satuan ('dalam jutaan/miliar Rupiah'), dan label yang bervariasi.

        Returns FinancialData (semua nilai dalam Rupiah absolut) atau None jika gagal.
        """
        client = self._get_client()
        if not client or not pdf_text or not pdf_text.strip():
            return None

        excerpt = pdf_text[:45000]
        prompt = f"""Kamu adalah ekstraktor data laporan keuangan IDX. Dari teks laporan keuangan {ticker} ({company_name}) tahun {year} di bawah ini, ekstrak angka-angka berikut.

ATURAN KETAT (ikuti definisi baris secara KONSISTEN, jangan berganti baris antar tahun):
- Kembalikan SEMUA nilai dalam RUPIAH ABSOLUT. Jika laporan disajikan 'dalam jutaan Rupiah', kalikan 1.000.000; jika 'dalam ribuan', kalikan 1.000; jika dalam USD, tetap dalam USD dan set "currency":"USD".
- Ambil nilai TAHUN BERJALAN ({year}), bukan tahun pembanding (kolom tahun sebelumnya).
- net_profit = laba bersih yang DAPAT DIATRIBUSIKAN KE PEMILIK ENTITAS INDUK (bukan termasuk kepentingan non-pengendali).
- interest_income: WAJIB pakai 'PENDAPATAN BUNGA BERSIH' / 'Pendapatan bunga dan syariah - bersih' (yaitu pendapatan bunga SETELAH dikurangi beban bunga). JANGAN gunakan pendapatan bunga BRUTO. Jika hanya tersedia bruto, hitung bersih = pendapatan bunga bruto - beban bunga.
- interest_expense = beban bunga (dan syariah).
- total_loans = kredit yang diberikan, nilai BRUTO (sebelum cadangan kerugian).
- casa = giro + tabungan (current account + savings).
- Nilai dalam tanda kurung = negatif.
- Jika suatu pos TIDAK ditemukan dengan pasti, isi null (JANGAN menebak/mengarang).

Kembalikan HANYA JSON valid (tanpa teks lain):
{{
  "currency": "IDR",
  "net_profit": <angka|null>,
  "revenue": <angka|null>,
  "interest_income": <angka|null>,
  "interest_expense": <angka|null>,
  "gross_profit": <angka|null>,
  "operating_profit": <angka|null>,
  "total_assets": <angka|null>,
  "total_equity": <angka|null>,
  "total_liabilities": <angka|null>,
  "total_loans": <angka|null>,
  "casa": <angka|null>,
  "current_assets": <angka|null>,
  "current_liabilities": <angka|null>,
  "cash_and_equiv": <angka|null>,
  "npl_gross": <persen|null>,
  "operating_cf": <angka|null>,
  "investing_cf": <angka|null>,
  "financing_cf": <angka|null>,
  "capex": <angka|null>,
  "shares_outstanding": <angka|null>
}}

TEKS LAPORAN KEUANGAN:
---
{excerpt}
---"""

        try:
            logger.info(f"Ekstraksi data keuangan via Claude untuk {ticker} {year}...")
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1500,
                temperature=0,  # deterministik — konsistensi antar tahun & antar run
                system="Kamu ekstraktor data presisi. Hanya keluarkan JSON valid sesuai skema, tanpa penjelasan.",
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
            data = self._parse_llm_response(raw)
            if not data or data.get("net_profit") is None and data.get("total_assets") is None:
                logger.warning(f"Ekstraksi LLM {ticker} {year} kosong/tidak valid.")
                return None

            # Konversi USD → IDR jika perlu (pakai kurs dari pdf_parser via yfinance)
            rate = 1.0
            if str(data.get("currency", "IDR")).upper() == "USD":
                try:
                    from services.pdf_parser import PDFParser
                    rate = PDFParser()._get_dynamic_exchange_rate(year)
                except Exception:
                    rate = 15500.0

            def num(key):
                v = data.get(key)
                if v is None:
                    return None
                try:
                    return float(v) * (rate if rate > 1.0 else 1.0)
                except (TypeError, ValueError):
                    return None

            laba_rugi = LabaRugi(
                year=year,
                revenue=num("revenue"),
                net_profit=num("net_profit"),
                gross_profit=num("gross_profit"),
                operating_profit=num("operating_profit"),
                interest_income=num("interest_income"),
                interest_expense=num("interest_expense"),
            )
            neraca = Neraca(
                year=year,
                total_assets=num("total_assets"),
                total_equity=num("total_equity"),
                total_liabilities=num("total_liabilities"),
                total_loans=num("total_loans"),
                casa=num("casa"),
                current_assets=num("current_assets"),
                current_liabilities=num("current_liabilities"),
                cash_and_equiv=num("cash_and_equiv"),
                npl_gross=data.get("npl_gross"),
            )
            arus_kas = ArusKas(
                year=year,
                operating_cf=num("operating_cf"),
                investing_cf=num("investing_cf"),
                financing_cf=num("financing_cf"),
                capex=num("capex"),
            )
            if arus_kas.operating_cf is not None and arus_kas.capex is not None:
                arus_kas.free_cash_flow = arus_kas.operating_cf - abs(arus_kas.capex)

            llm_shares = data.get("shares_outstanding")
            shares = shares_outstanding or (float(llm_shares) if llm_shares else 0) or None

            logger.info(
                f"Ekstraksi LLM {ticker} {year}: NetProfit={laba_rugi.net_profit}, "
                f"TotalAssets={neraca.total_assets}, Equity={neraca.total_equity}"
            )
            return FinancialData(
                ticker=ticker,
                company_name=company_name or ticker,
                sector=sector,
                year=year,
                laba_rugi=laba_rugi,
                neraca=neraca,
                arus_kas=arus_kas,
                shares_outstanding=shares,
                source_pdf_url="LLM extraction",
            )
        except Exception as e:
            logger.error(f"Error ekstraksi LLM untuk {ticker} {year}: {e}", exc_info=True)
            return None

    def _build_prompt(
        self,
        ticker: str,
        company_name: str,
        sector: str,
        pdf_text: str,
        metrics: FundamentalMetrics,
        valuasi: ValuasiMetrics,
        fundamental_score: float,
        valuasi_score: float,
        keputusan: str,
        margin_of_safety: Optional[float],
    ) -> str:
        """Buat prompt komprehensif untuk Claude"""

        # Format metrik untuk prompt
        metrics_text = f"""
METRIK FUNDAMENTAL (dihitung dari laporan keuangan):
- ROE: {metrics.roe}%
- ROA: {metrics.roa}%
- Net Profit Margin: {metrics.npm}%
- EPS: {f'Rp{metrics.eps:,.2f}' if metrics.eps is not None else 'N/A'}
- BVPS: {f'Rp{metrics.bvps:,.2f}' if metrics.bvps is not None else 'N/A'}
- Revenue Growth YoY: {metrics.revenue_growth_yoy}%
- Profit Growth YoY: {metrics.profit_growth_yoy}%
- Revenue CAGR 5 tahun: {metrics.revenue_cagr_5y}%
- Profit CAGR 5 tahun: {metrics.profit_cagr_5y}%
- Debt-to-Equity: {metrics.debt_to_equity}x
- Current Ratio: {metrics.current_ratio}x
"""
        if metrics.npl_gross:
            metrics_text += f"- NPL Gross: {metrics.npl_gross}%\n"
        if metrics.casa_ratio:
            metrics_text += f"- CASA Ratio: {metrics.casa_ratio}%\n"
        if metrics.nim:
            metrics_text += f"- Net Interest Margin (NIM): {metrics.nim}%\n"

        valuasi_text = f"""
METRIK VALUASI:
- Harga Saat Ini: Rp{valuasi.current_price:,.0f}
- PER: {valuasi.per}x (historis rata-rata: {valuasi.per_historical_avg}x)
- PBV: {valuasi.pbv}x (historis rata-rata: {valuasi.pbv_historical_avg}x)
- Harga Wajar Konservatif (PER 15x): {f'Rp{valuasi.fair_value_conservative:,.0f}' if valuasi.fair_value_conservative is not None else 'N/A'}
- Harga Wajar Normal (PER 18x): {f'Rp{valuasi.fair_value_normal:,.0f}' if valuasi.fair_value_normal is not None else 'N/A'}
- Graham Number: {f'Rp{valuasi.fair_value_graham:,.0f}' if valuasi.fair_value_graham is not None else 'N/A'}
- Margin of Safety: {margin_of_safety}%
- 52W High: {f'Rp{valuasi.price_52w_high:,.0f}' if valuasi.price_52w_high is not None else 'N/A'}
- 52W Low: {f'Rp{valuasi.price_52w_low:,.0f}' if valuasi.price_52w_low is not None else 'N/A'}
"""

        # Potong PDF text agar tidak terlalu panjang
        pdf_excerpt = pdf_text[:15000] if pdf_text else "(Teks laporan keuangan tidak tersedia)"

        return f"""
Saya sedang menganalisis saham {ticker} ({company_name}) di sektor {sector}.

Sistem analisis otomatis telah menghitung:
- Skor Fundamental: {fundamental_score}/10
- Skor Valuasi: {valuasi_score}/10
- Keputusan Sistem: {keputusan}

{metrics_text}

{valuasi_text}

KUTIPAN DARI LAPORAN KEUANGAN ASLI (IDX):
---
{pdf_excerpt}
---

Berdasarkan data di atas, berikan analisis dalam format JSON berikut:

{{
  "ringkasan_fundamental": "2-3 paragraf analisis kondisi fundamental bisnis. Fokus pada: kualitas laba, pertumbuhan, moat bisnis, dan hal-hal penting dari laporan keuangan.",
  "ringkasan_valuasi": "1-2 paragraf analisis valuasi. Bandingkan dengan historis dan kompetitor. Apakah murah atau mahal?",
  "alasan_keputusan": "1 paragraf alasan singkat mengapa keputusan {keputusan} tepat berdasarkan data.",
  "red_flags": ["Daftar 2-5 hal yang perlu diwaspadai investor, atau kosong jika tidak ada"],
  "highlights": ["Daftar 3-5 poin positif utama perusahaan"]
}}

PENTING: 
- Hanya kembalikan JSON yang valid, tidak ada teks lain
- DILARANG KERAS menggunakan tanda kutip dua (") di dalam isi teks/kalimat. Jika ingin mengutip, gunakan tanda kutip tunggal ('). Ini untuk mencegah error JSON!
- Jujur tentang risiko, jangan berlebihan dalam memuji
- Basis analisis dari angka yang sudah dihitung, bukan opini umum
- Jika ada informasi dari laporan keuangan yang penting tapi tidak tercermin di metrik, sebutkan
"""

    def _parse_llm_response(self, response_text: str) -> dict:
        """Parse response JSON dari Claude"""
        import json
        import re

        # Bersihkan markdown backticks
        clean_text = response_text.replace("```json", "").replace("```", "").strip()
        
        # Cari JSON dalam response
        json_match = re.search(r'\{.*\}', clean_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError as e:
                logger.warning(f"Gagal parse JSON dari Claude: {e}. Raw: {clean_text[:100]}")

        # Jika tidak berhasil parse JSON, coba bersihkan syntax JSON-nya agar sedap dipandang
        plain = clean_text
        # Hapus kurung kurawal awal/akhir dan key json
        plain = re.sub(r'^\{', '', plain)
        plain = re.sub(r'\}$', '', plain)
        plain = re.sub(r'"ringkasan_fundamental"\s*:\s*', '', plain)
        plain = re.sub(r'"ringkasan_valuasi"\s*:\s*', '\n\nValuasi:\n', plain)
        plain = re.sub(r'"alasan_keputusan"\s*:\s*', '\n\nKeputusan:\n', plain)
        plain = re.sub(r'"red_flags"\s*:\s*', '\n\nRed Flags:\n', plain)
        plain = re.sub(r'"highlights"\s*:\s*', '\n\nHighlights:\n', plain)
        # Hapus sisa tanda kutip
        plain = plain.replace('"', '').strip()
            
        return {
            "ringkasan_fundamental": plain[:800] + ("..." if len(plain) > 800 else ""),
            "ringkasan_valuasi": "Silakan baca ringkasan fundamental di atas.",
            "alasan_keputusan": "Gagal memisahkan format analisis.",
            "red_flags": [],
            "highlights": [],
        }

    def _fallback_analysis(
        self,
        ticker: str,
        metrics: FundamentalMetrics,
        valuasi: ValuasiMetrics,
        fundamental_score: float,
        valuasi_score: float,
        keputusan: str,
        margin_of_safety: Optional[float],
    ) -> dict:
        """Fallback jika Claude tidak tersedia — generate analisis berbasis angka"""

        highlights = []
        red_flags = []

        # Generate highlights berdasarkan angka
        if metrics.roe and abs(metrics.roe) > 15:
            highlights.append(f"ROE tinggi {metrics.roe:.1f}% menunjukkan efisiensi penggunaan modal")

        if metrics.profit_growth_yoy and metrics.profit_growth_yoy > 5:
            highlights.append(f"Laba tumbuh {metrics.profit_growth_yoy:.1f}% YoY secara konsisten")

        if metrics.revenue_cagr_5y and metrics.revenue_cagr_5y > 8:
            highlights.append(f"CAGR Revenue 5 tahun {metrics.revenue_cagr_5y:.1f}% menunjukkan pertumbuhan struktural")

        if margin_of_safety and margin_of_safety > 20:
            highlights.append(f"Harga menarik dengan Margin of Safety {margin_of_safety:.1f}%")

        if metrics.npl_gross and metrics.npl_gross < 2:
            highlights.append(f"Kualitas aset sangat baik, NPL hanya {metrics.npl_gross:.2f}%")

        if metrics.casa_ratio and metrics.casa_ratio > 50:
            highlights.append(f"Pendanaan murah (CASA {metrics.casa_ratio:.1f}%) memberikan keunggulan biaya")

        # Generate red flags
        if metrics.profit_growth_yoy and metrics.profit_growth_yoy < -5:
            red_flags.append(f"Laba turun {abs(metrics.profit_growth_yoy):.1f}% YoY")

        if metrics.debt_to_equity and metrics.debt_to_equity > 3:
            red_flags.append(f"Rasio utang tinggi (DER {metrics.debt_to_equity:.1f}x)")

        if metrics.npl_gross and metrics.npl_gross > 3:
            red_flags.append(f"NPL perlu diperhatikan ({metrics.npl_gross:.2f}%)")

        if margin_of_safety and margin_of_safety < 0:
            red_flags.append(f"Harga saat ini di atas nilai wajar ({abs(margin_of_safety):.1f}% premium)")

        # Ringkasan
        roe_str = f"ROE {metrics.roe:.1f}%" if metrics.roe else ""
        growth_str = f"pertumbuhan laba {metrics.profit_growth_yoy:.1f}% YoY" if metrics.profit_growth_yoy else ""

        ringkasan_fund = (
            f"{ticker} menunjukkan fundamental yang {'kuat' if fundamental_score >= 7 else 'cukup' if fundamental_score >= 5 else 'perlu perhatian'} "
            f"dengan skor {fundamental_score}/10. "
            f"{'Perusahaan mencatat ' + roe_str if roe_str else ''}"
            f"{' dengan ' + growth_str if growth_str else ''}. "
            f"{'Manajemen berhasil menjaga kualitas portofolio dengan ' + f'NPL {metrics.npl_gross:.2f}%' if metrics.npl_gross else ''}"
        )

        per_str = f"PER {valuasi.per:.1f}x" if valuasi.per else ""
        pbv_str = f"PBV {valuasi.pbv:.2f}x" if valuasi.pbv else ""

        ringkasan_val = (
            f"Dari sisi valuasi ({valuasi_score}/10), saham diperdagangkan pada "
            f"{per_str} {pbv_str}. "
            f"{'Margin of Safety ' + str(margin_of_safety) + '% memberikan ruang keamanan yang ' + ('baik' if (margin_of_safety or 0) > 20 else 'terbatas') + '.' if margin_of_safety else ''}"
        )

        keputusan_map = {
            "BELI": "Kombinasi fundamental kuat dan harga yang menarik menjadikan saham ini layak dibeli sekarang.",
            "CICIL_BELI": "Fundamental solid dengan harga cukup menarik. Cicil pembelian untuk rata-rata harga.",
            "TUNGGU": "Fundamental baik namun harga masih di atas nilai wajar. Tunggu koreksi ke area yang lebih menarik.",
            "LEWATI": "Ada perhatian pada fundamental atau risiko yang memerlukan evaluasi lebih lanjut.",
        }

        return {
            "ringkasan_fundamental": ringkasan_fund,
            "ringkasan_valuasi": ringkasan_val,
            "alasan_keputusan": keputusan_map.get(keputusan, ""),
            "red_flags": red_flags,
            "highlights": highlights if highlights else [f"Skor fundamental {fundamental_score}/10"],
        }
