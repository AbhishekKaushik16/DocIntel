"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.api.documents import router as documents_router
from app.api.search import router as search_router
from app.api.query import router as query_router
import logging

# Configure root logger with timestamps
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    # Startup: ensure upload directory exists and database tables are created
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    await init_db()

    # Initialize Elasticsearch index (non-fatal)
    try:
        from app.elasticsearch import ensure_index
        await ensure_index()
    except Exception:
        pass  # ES may not be running

    yield
    # Shutdown: cleanup if needed


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Turn messy documents into structured, queryable data.",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────
app.include_router(documents_router)
app.include_router(search_router)
app.include_router(query_router)


# ── Health check ──────────────────────────────────────────────
@app.get("/api/health")
async def health_check():
    """Health check endpoint for deployment monitoring."""
    return {
        "status": "healthy",
        "version": settings.app_version,
        "llm_available": settings.llm_available,
    }


# ── Global error handler ─────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Catch-all handler to return clean JSON errors instead of HTML tracebacks."""
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal error occurred. Please try again.",
            "type": type(exc).__name__,
        },
    )
