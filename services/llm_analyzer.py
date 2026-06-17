"""
LLM Analyzer Service — Claude Sonnet Integration
Menggunakan Claude Sonnet untuk analisis naratif laporan keuangan.
"""
import logging
import os
from typing import Optional
from anthropic import Anthropic, APIStatusError
from dotenv import load_dotenv

from models.schemas import FinancialData, FundamentalMetrics, ValuasiMetrics, RisikoAssessment

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
