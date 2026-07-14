"""
Unit test untuk CalculatorEngine — semua rasio fundamental & valuasi.
Angka referensi memakai profil mirip BBCA FY2025 (Rupiah absolut)
supaya hasil bisa dicek terhadap realita.
"""
import pytest

from services.calculator import CalculatorEngine
from models.schemas import (
    FinancialData, LabaRugi, Neraca, ArusKas, FundamentalMetrics,
)


@pytest.fixture(scope="module")
def calc():
    return CalculatorEngine()


def _bank_year(year, net_profit, equity, assets, liabilities,
               interest_income=None, loans=None, casa=None, shares=None):
    """Helper: FinancialData ala bank dalam Rupiah absolut."""
    return FinancialData(
        ticker="TEST",
        company_name="Test Bank",
        year=year,
        laba_rugi=LabaRugi(
            year=year, net_profit=net_profit, interest_income=interest_income,
        ),
        neraca=Neraca(
            year=year, total_equity=equity, total_assets=assets,
            total_liabilities=liabilities, total_loans=loans, casa=casa,
        ),
        arus_kas=ArusKas(year=year),
        shares_outstanding=shares,
    )


SHARES = 123_275_050_000  # ± saham beredar BBCA

BBCA_2025 = _bank_year(
    2025,
    net_profit=57_537_287e6,
    equity=281_687_555e6,
    assets=1_586_828_536e6,
    liabilities=1_294_508_286e6,
    interest_income=85_548_157e6,
    loans=970_233_234e6,
    casa=1_039_130_070e6,
)
BBCA_2024 = _bank_year(
    2024,
    net_profit=54_836_305e6,
    equity=260_000_000e6,
    assets=1_449_000_000e6,
    liabilities=1_189_000_000e6,
    interest_income=82_264_164e6,
)


class TestSafeDivide:
    def test_normal(self, calc):
        assert calc.safe_divide(10, 4) == 2.5

    def test_pembagi_nol(self, calc):
        assert calc.safe_divide(10, 0) is None

    def test_none(self, calc):
        assert calc.safe_divide(None, 5) is None
        assert calc.safe_divide(5, None) is None


class TestSafeGrowth:
    def test_naik(self, calc):
        assert calc.safe_growth(110, 100) == pytest.approx(10.0)

    def test_turun(self, calc):
        assert calc.safe_growth(90, 100) == pytest.approx(-10.0)

    def test_basis_negatif_pakai_abs(self, calc):
        # dari rugi -100 ke laba 50 → +150%
        assert calc.safe_growth(50, -100) == pytest.approx(150.0)

    def test_basis_nol(self, calc):
        assert calc.safe_growth(50, 0) is None


class TestCAGR:
    def test_dobel_dalam_5_tahun(self, calc):
        # 100 → 200 dalam 5 tahun ≈ 14.87%/tahun
        assert calc.cagr(100, 200, 5) == pytest.approx(14.87, abs=0.01)

    def test_input_nol_atau_negatif(self, calc):
        assert calc.cagr(0, 200, 5) is None
        assert calc.cagr(100, -1, 5) is None
        assert calc.cagr(100, 200, 0) is None


@pytest.fixture(scope="module")
def metrics(calc):
    return calc.calculate_fundamental(
        financial_data_list=[BBCA_2025, BBCA_2024],
        shares_outstanding=SHARES,
    )


class TestFundamentalBank:
    """Rasio fundamental dihitung dari profil mirip BBCA."""

    def test_eps(self, metrics):
        # 57.537T ÷ 123.275M lembar ≈ Rp466.7
        assert metrics.eps == pytest.approx(466.7, abs=1.0)

    def test_bvps(self, metrics):
        # 281.688T ÷ 123.275M ≈ Rp2.285
        assert metrics.bvps == pytest.approx(2285, abs=5)

    def test_roe(self, metrics):
        # 57.537 / 281.688 ≈ 20.4%
        assert metrics.roe == pytest.approx(20.4, abs=0.2)

    def test_roa(self, metrics):
        assert metrics.roa == pytest.approx(3.6, abs=0.2)

    def test_npm_pakai_interest_income(self, metrics):
        # revenue None → fallback interest_income: 57.5/85.5 ≈ 67%
        assert metrics.npm == pytest.approx(67.3, abs=0.5)

    def test_profit_growth_yoy(self, metrics):
        # 57.537 vs 54.836 ≈ +4.9%
        assert metrics.profit_growth_yoy == pytest.approx(4.9, abs=0.1)

    def test_der(self, metrics):
        # 1294.5 / 281.7 ≈ 4.6x (normal untuk bank)
        assert metrics.debt_to_equity == pytest.approx(4.6, abs=0.05)

    def test_casa_ratio_positif_dan_masuk_akal(self, metrics):
        assert metrics.casa_ratio is not None
        assert 0 < metrics.casa_ratio <= 100

    def test_data_kosong_tidak_crash(self, calc):
        m = calc.calculate_fundamental(financial_data_list=[])
        assert m.eps is None


@pytest.fixture(scope="module")
def valuasi(calc):
    m = FundamentalMetrics(eps=468.25, bvps=2292.45, dps=356.0)
    return calc.calculate_valuasi(
        metrics=m,
        current_price=6075.0,
        historical_per=11.49,
        historical_pbv=2.35,
    )


class TestValuasi:

    def test_per(self, valuasi):
        assert valuasi.per == pytest.approx(12.97, abs=0.01)

    def test_pbv(self, valuasi):
        assert valuasi.pbv == pytest.approx(2.65, abs=0.01)

    def test_dividend_yield_dari_dps(self, valuasi):
        # 356 / 6075 ≈ 5.86% — dan TIDAK boleh ratusan persen (regresi bug x100)
        assert valuasi.dividend_yield == pytest.approx(5.86, abs=0.01)
        assert valuasi.dividend_yield < 100

    def test_fair_value_conservative(self, valuasi):
        assert valuasi.fair_value_conservative == pytest.approx(468.25 * 15, abs=1)

    def test_graham_number(self, valuasi):
        expected = (22.5 * 468.25 * 2292.45) ** 0.5
        assert valuasi.fair_value_graham == pytest.approx(expected, abs=1)

    def test_margin_of_safety(self, valuasi):
        fv = 468.25 * 15
        expected = (fv - 6075.0) / fv * 100
        assert valuasi.margin_of_safety == pytest.approx(expected, abs=0.1)

    def test_eps_negatif_tidak_menghasilkan_per(self, calc):
        v = calc.calculate_valuasi(
            metrics=FundamentalMetrics(eps=-100, bvps=500),
            current_price=1000,
        )
        assert v.per is None
        assert v.fair_value_conservative is None


class TestValuationBaseline:
    """Opsi C: historis emiten → ROE-justified → sektor default."""

    def test_historis_emiten_jika_3_tahun(self, calc):
        years = [
            _bank_year(2025, 57e12, 280e12, 1500e12, 1220e12, shares=SHARES),
            _bank_year(2024, 54e12, 260e12, 1400e12, 1140e12, shares=SHARES),
            _bank_year(2023, 48e12, 240e12, 1300e12, 1060e12, shares=SHARES),
        ]
        prices = {2025: 6000, 2024: 9000, 2023: 9500}
        b = calc.compute_valuation_baseline(
            financial_data_list=years,
            year_end_prices=prices,
            metrics=FundamentalMetrics(roe=20.0),
        )
        assert b["method"] == "historis_emiten"
        assert b["years_used"] == 3
        assert b["pbv"] > 0 and b["per"] > 0

    def test_roe_justified_jika_kurang_tahun(self, calc):
        b = calc.compute_valuation_baseline(
            financial_data_list=[],
            year_end_prices={},
            metrics=FundamentalMetrics(roe=20.4),
        )
        assert b["method"] == "roe_justified"
        # PBV* = (0.204-0.04)/(0.11-0.04) ≈ 2.34
        assert b["pbv"] == pytest.approx(2.34, abs=0.02)

    def test_sektor_default_tanpa_roe(self, calc):
        b = calc.compute_valuation_baseline(
            financial_data_list=[],
            year_end_prices={},
            metrics=FundamentalMetrics(),
            sector_per=12.0,
        )
        assert b["method"] == "sektor_default"
        assert b["per"] == 12.0
        assert b["pbv"] == 2.0
