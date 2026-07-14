"""
Unit test untuk PDFParser._normalize_number — konversi teks angka
format IDX/US ke float. Fungsi ini punya banyak cabang format dan
menjadi fondasi semua angka dari parser regex.
"""
import pytest

from services.pdf_parser import PDFParser


@pytest.fixture(scope="module")
def parser():
    return PDFParser()


class TestFormatIDX:
    """Format Indonesia: titik ribuan, koma desimal"""

    def test_ribuan_titik(self, parser):
        assert parser._normalize_number("1.234.567") == 1_234_567

    def test_ribuan_dan_desimal(self, parser):
        assert parser._normalize_number("1.234.567,89") == pytest.approx(1234567.89)

    def test_satu_koma_desimal(self, parser):
        assert parser._normalize_number("1234,5") == pytest.approx(1234.5)

    def test_satu_titik_tiga_digit_dianggap_ribuan(self, parser):
        # 1.000 → seribu, bukan 1.0
        assert parser._normalize_number("1.000") == 1000

    def test_satu_titik_desimal(self, parser):
        # 1.5 → desimal (bukan ribuan karena hanya 1 digit setelah titik)
        assert parser._normalize_number("1.5") == pytest.approx(1.5)


class TestFormatUS:
    """Format US: koma ribuan"""

    def test_ribuan_koma(self, parser):
        assert parser._normalize_number("1,234,567") == 1_234_567


class TestNegatif:
    """Tanda kurung = negatif (konvensi akuntansi)"""

    def test_kurung_jadi_negatif(self, parser):
        assert parser._normalize_number("(1.234)") == -1234

    def test_kurung_dengan_desimal(self, parser):
        assert parser._normalize_number("(1.234.567,89)") == pytest.approx(-1234567.89)

    def test_minus_eksplisit(self, parser):
        assert parser._normalize_number("-500") == -500


class TestEdgeCases:
    def test_string_kosong(self, parser):
        assert parser._normalize_number("") is None

    def test_none_input(self, parser):
        assert parser._normalize_number(None) is None

    def test_bukan_angka(self, parser):
        assert parser._normalize_number("abc") is None

    def test_simbol_mata_uang_dibuang(self, parser):
        assert parser._normalize_number("Rp1.000") == 1000

    def test_spasi_dibuang(self, parser):
        assert parser._normalize_number(" 1.234.567 ") == 1_234_567

    def test_angka_polos(self, parser):
        assert parser._normalize_number("42") == 42
