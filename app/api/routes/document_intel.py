"""
api/routes/document_intel.py
Phase 4 — Document intelligence endpoints.
POST /documents/analyze  — upload PDF/image, get structured intelligence
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import Optional
from app.api.routes.auth import get_current_user

router = APIRouter()
log = logging.getLogger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
ALLOWED_TYPES = {
    "application/pdf", "image/png", "image/jpeg",
    "image/webp", "text/csv",
}


@router.post("/documents/analyze")
async def analyze_document(
    file:     UploadFile = File(...),
    symbol:   Optional[str] = Form(None),
    question: Optional[str] = Form(None),
    user:     dict = Depends(get_current_user),
):
    """
    Upload a financial document (PDF, image, CSV).
    Returns structured intelligence: metrics, summary, risk flags, entities.
    """
    # Size check
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large — max 20MB")

    # Type check
    content_type = file.content_type or ""
    filename = file.filename or "upload"
    if not any(filename.lower().endswith(ext) for ext in
               [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".csv"]):
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type — use PDF, PNG, JPG, WEBP, or CSV"
        )

    try:
        from app.domain.documents.intelligence import pipeline
        result = await pipeline.process(
            file_bytes=content,
            filename=filename,
            user_question=question,
            symbol=symbol.upper() if symbol else None,
        )
        return {
            "status":  "ok",
            "filename": filename,
            "user_id": user.get("id"),
            **result.to_dict(),
        }
    except Exception as e:
        log.error(f"[document_intel] analyze failed: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@router.post("/documents/analyze/url")
async def analyze_document_url(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    """
    Analyze a document from a URL (BSE filings, public PDFs).
    Body: {"url": "...", "symbol": "...", "question": "..."}
    """
    url      = payload.get("url", "").strip()
    symbol   = payload.get("symbol", "")
    question = payload.get("question", "")

    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Could not fetch URL: {resp.status_code}")
            content = resp.content
            filename = url.split("/")[-1] or "document.pdf"

        from app.domain.documents.intelligence import pipeline
        result = await pipeline.process(
            file_bytes=content,
            filename=filename,
            user_question=question,
            symbol=symbol.upper() if symbol else None,
        )
        return {"status": "ok", "url": url, **result.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[document_intel] URL analyze failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
PYEOFcat > app/api/routes/document_intel.py << 'PYEOF'
"""
api/routes/document_intel.py
Phase 4 — Document intelligence endpoints.
POST /documents/analyze  — upload PDF/image, get structured intelligence
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from typing import Optional
from app.api.routes.auth import get_current_user

router = APIRouter()
log = logging.getLogger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
ALLOWED_TYPES = {
    "application/pdf", "image/png", "image/jpeg",
    "image/webp", "text/csv",
}


@router.post("/documents/analyze")
async def analyze_document(
    file:     UploadFile = File(...),
    symbol:   Optional[str] = Form(None),
    question: Optional[str] = Form(None),
    user:     dict = Depends(get_current_user),
):
    """
    Upload a financial document (PDF, image, CSV).
    Returns structured intelligence: metrics, summary, risk flags, entities.
    """
    # Size check
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large — max 20MB")

    # Type check
    content_type = file.content_type or ""
    filename = file.filename or "upload"
    if not any(filename.lower().endswith(ext) for ext in
               [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".csv"]):
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type — use PDF, PNG, JPG, WEBP, or CSV"
        )

    try:
        from app.domain.documents.intelligence import pipeline
        result = await pipeline.process(
            file_bytes=content,
            filename=filename,
            user_question=question,
            symbol=symbol.upper() if symbol else None,
        )
        return {
            "status":  "ok",
            "filename": filename,
            "user_id": user.get("id"),
            **result.to_dict(),
        }
    except Exception as e:
        log.error(f"[document_intel] analyze failed: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@router.post("/documents/analyze/url")
async def analyze_document_url(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    """
    Analyze a document from a URL (BSE filings, public PDFs).
    Body: {"url": "...", "symbol": "...", "question": "..."}
    """
    url      = payload.get("url", "").strip()
    symbol   = payload.get("symbol", "")
    question = payload.get("question", "")

    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Could not fetch URL: {resp.status_code}")
            content = resp.content
            filename = url.split("/")[-1] or "document.pdf"

        from app.domain.documents.intelligence import pipeline
        result = await pipeline.process(
            file_bytes=content,
            filename=filename,
            user_question=question,
            symbol=symbol.upper() if symbol else None,
        )
        return {"status": "ok", "url": url, **result.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"[document_intel] URL analyze failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
