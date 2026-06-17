"""
Database module — SQLite dengan SQLAlchemy async
Cache hasil analisis dan laporan keuangan untuk menghindari fetch berulang
"""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, Integer, Boolean, Text, DateTime
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "./saham_analyzer.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class AnalysisCache(Base):
    """Cache hasil analisis saham"""
    __tablename__ = "analysis_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    analysis_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)


class WatchlistDB(Base):
    """Watchlist saham pengguna"""
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(200), default="")
    target_buy_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class PDFRecord(Base):
    """Record PDF yang sudah didownload/diupload"""
    __tablename__ = "pdf_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    year: Mapped[int] = mapped_column(Integer)
    report_type: Mapped[str] = mapped_column(String(20))
    pdf_path: Mapped[str] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_manual_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


async def init_db():
    """Inisialisasi database dan buat tabel jika belum ada"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized")


async def get_cached_analysis(ticker: str, max_age_hours: int = 24) -> Optional[dict]:
    """
    Ambil hasil analisis dari cache jika masih valid.
    """
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        cutoff = datetime.now() - timedelta(hours=max_age_hours)

        result = await session.execute(
            select(AnalysisCache)
            .where(AnalysisCache.ticker == ticker.upper())
            .where(AnalysisCache.created_at > cutoff)
            .where(AnalysisCache.is_valid == True)
            .order_by(AnalysisCache.created_at.desc())
            .limit(1)
        )
        cached = result.scalar_one_or_none()

        if cached:
            logger.info(f"Cache hit untuk {ticker}")
            return json.loads(cached.analysis_json)

    return None


async def save_analysis_cache(ticker: str, analysis_data: dict):
    """Simpan hasil analisis ke cache"""
    async with AsyncSessionLocal() as session:
        # Invalidate cache lama
        from sqlalchemy import update
        await session.execute(
            update(AnalysisCache)
            .where(AnalysisCache.ticker == ticker.upper())
            .values(is_valid=False)
        )

        # Simpan cache baru
        cache = AnalysisCache(
            ticker=ticker.upper(),
            analysis_json=json.dumps(analysis_data, default=str),
        )
        session.add(cache)
        await session.commit()
        logger.info(f"Analysis cache saved untuk {ticker}")


async def clear_analysis_cache(ticker: str):
    """Hapus cache untuk memaksa analisis ulang"""
    async with AsyncSessionLocal() as session:
        from sqlalchemy import update
        await session.execute(
            update(AnalysisCache)
            .where(AnalysisCache.ticker == ticker.upper())
            .values(is_valid=False)
        )
        await session.commit()
        logger.info(f"Cache invalidated untuk {ticker}")


async def get_watchlist() -> list:
    """Ambil semua saham di watchlist"""
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WatchlistDB).order_by(WatchlistDB.added_at.desc())
        )
        items = result.scalars().all()
        return [
            {
                "ticker": item.ticker,
                "company_name": item.company_name,
                "target_buy_price": item.target_buy_price,
                "notes": item.notes,
                "added_at": item.added_at.isoformat(),
            }
            for item in items
        ]


async def add_to_watchlist(
    ticker: str,
    company_name: str = "",
    target_buy_price: Optional[float] = None,
    notes: Optional[str] = None,
):
    """Tambah saham ke watchlist"""
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        # Cek apakah sudah ada
        result = await session.execute(
            select(WatchlistDB).where(WatchlistDB.ticker == ticker.upper())
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.target_buy_price = target_buy_price
            existing.notes = notes
            existing.company_name = company_name
        else:
            item = WatchlistDB(
                ticker=ticker.upper(),
                company_name=company_name,
                target_buy_price=target_buy_price,
                notes=notes,
            )
            session.add(item)

        await session.commit()
        logger.info(f"{ticker} ditambahkan ke watchlist")


async def remove_from_watchlist(ticker: str):
    """Hapus saham dari watchlist"""
    from sqlalchemy import delete

    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(WatchlistDB).where(WatchlistDB.ticker == ticker.upper())
        )
        await session.commit()
        logger.info(f"{ticker} dihapus dari watchlist")
