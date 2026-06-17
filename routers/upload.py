"""
Upload Router — Upload PDF laporan keuangan secara manual
POST /api/upload/{ticker} — Upload PDF untuk ticker tertentu
"""
import os
import logging
from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from typing import Optional

from services.idx_fetcher import IDXFetcher
from models.schemas import UploadResponse

router = APIRouter()
logger = logging.getLogger(__name__)
idx_fetcher = IDXFetcher()

@router.get(
    "/upload/check/{ticker}",
    summary="Cek apakah PDF sudah ada",
    description="Mengecek apakah PDF untuk tahun tersebut sudah ada di cache",
)
async def check_pdf_exists(ticker: str, year: int):
    ticker = ticker.upper().strip()
    pdf_path = idx_fetcher.get_cached_pdf(ticker, year)
    return {"exists": pdf_path is not None, "path": pdf_path}

@router.post(
    "/upload/{ticker}",
    response_model=UploadResponse,
    summary="Upload PDF laporan keuangan",
    description="Upload PDF laporan keuangan dari IDX secara manual untuk ticker tertentu",
)
async def upload_pdf(
    ticker: str,
    file: UploadFile = File(...),
    year: int = Form(..., description="Tahun laporan keuangan, contoh: 2024"),
    report_type: str = Form("annual", description="Tipe laporan: annual atau quarterly"),
):
    """
    Upload PDF laporan keuangan IDX secara manual.
    
    Digunakan jika auto-fetch dari IDX gagal.
    
    Steps:
    1. Validasi file adalah PDF
    2. Validasi ukuran (max 50MB)
    3. Simpan ke cache directory
    4. Return path untuk digunakan analisis
    """
    ticker = ticker.upper().strip()

    # Validasi extension
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Hanya file PDF yang diterima"
        )

    # Baca konten file
    content = await file.read()

    # Validasi ukuran (max 50MB)
    max_size = 50 * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File terlalu besar. Maksimum 50MB, file ini {len(content)/1024/1024:.1f}MB"
        )

    # Validasi minimal PDF header
    if not content.startswith(b"%PDF"):
        raise HTTPException(
            status_code=400,
            detail="File bukan PDF yang valid"
        )

    # Simpan ke cache
    pdf_path = idx_fetcher.save_manual_upload(ticker, year, content)
    
    # Hapus cache analisis agar frontend/API menghitung ulang data PDF baru
    from db.database import clear_analysis_cache
    await clear_analysis_cache(ticker)

    logger.info(
        f"PDF manual diupload untuk {ticker} tahun {year}: "
        f"{file.filename} ({len(content)/1024:.1f}KB)"
    )

    return UploadResponse(
        ticker=ticker,
        message=(
            f"PDF laporan keuangan {ticker} tahun {year} berhasil diupload. "
            f"Sekarang jalankan analisis di /api/analyze/{ticker}?force_refresh=true"
        ),
        pdf_path=pdf_path,
        year=year,
    )
