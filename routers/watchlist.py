"""
Watchlist Router
GET /api/watchlist — Ambil semua saham di watchlist
POST /api/watchlist — Tambah saham ke watchlist
DELETE /api/watchlist/{ticker} — Hapus dari watchlist
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from db.database import get_watchlist, add_to_watchlist, remove_from_watchlist

router = APIRouter()


class AddWatchlistRequest(BaseModel):
    ticker: str
    company_name: str = ""
    target_buy_price: Optional[float] = None
    notes: Optional[str] = None


@router.get("/watchlist")
async def get_watchlist_endpoint():
    """Ambil semua saham di watchlist"""
    items = await get_watchlist()
    return {"watchlist": items, "count": len(items)}


@router.post("/watchlist")
async def add_watchlist(request: AddWatchlistRequest):
    """Tambah saham ke watchlist"""
    await add_to_watchlist(
        ticker=request.ticker,
        company_name=request.company_name,
        target_buy_price=request.target_buy_price,
        notes=request.notes,
    )
    return {"message": f"{request.ticker.upper()} berhasil ditambahkan ke watchlist"}


@router.delete("/watchlist/{ticker}")
async def remove_watchlist(ticker: str):
    """Hapus saham dari watchlist"""
    await remove_from_watchlist(ticker)
    return {"message": f"{ticker.upper()} dihapus dari watchlist"}
