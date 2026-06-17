"""
Saham Analyzer — FastAPI Main Application
Aplikasi analisis saham Indonesia profesional berdasarkan framework Buffett + Graham
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
load_dotenv()

from routers import analysis, watchlist, upload
from db.database import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and cache on startup"""
    await init_db()
    cache_dir = os.getenv("CACHE_DIR", "./cache")
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(f"{cache_dir}/pdfs", exist_ok=True)
    yield

app = FastAPI(
    title="Saham Analyzer API",
    description="Analisis saham Indonesia profesional — Framework Buffett + Graham",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend to access
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(analysis.router, prefix="/api", tags=["analysis"])
app.include_router(watchlist.router, prefix="/api", tags=["watchlist"])
app.include_router(upload.router, prefix="/api", tags=["upload"])


@app.get("/")
async def root():
    return {
        "app": "Saham Analyzer",
        "version": "1.0.0",
        "description": "Analisis saham Indonesia — Framework Buffett + Graham",
        "endpoints": {
            "analyze": "/api/analyze/{ticker}",
            "watchlist": "/api/watchlist",
            "upload": "/api/upload/{ticker}",
            "health": "/api/health",
        }
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "message": "Saham Analyzer API is running"}
