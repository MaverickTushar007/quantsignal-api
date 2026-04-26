"""
domain/documents/intelligence.py
Phase 4 — Document Intelligence Pipeline.
Handles: PDF text extraction, image OCR via Claude vision,
table detection, finance schema mapping, LLM summary.
Sits on top of existing ingest/indexer/retriever.
"""
import re
import logging
import base64
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

log = logging.getLogger(__name__)


class DocumentType(str, Enum):
    PDF_REPORT        = "pdf_report"
    EARNINGS_DECK     = "earnings_deck"
    BROKER_SCREENSHOT = "broker_screenshot"
    CHART_IMAGE       = "chart_image"
    SPREADSHEET       = "spreadsheet"
    OPTION_CHAIN      = "option_chain"
    UNKNOWN           = "unknown"


@dataclass
class ExtractedTable:
    headers: List[str]
    rows:    List[List[str]]
    context: str  # surrounding text explaining the table


@dataclass
class DocumentIntelligenceResult:
    doc_type:      DocumentType
    raw_text:      str
    tables:        List[ExtractedTable]
    key_metrics:   Dict[str, Any]   # extracted numbers + labels
    entities:      List[str]        # tickers, companies
    summary:       str
    finance_schema: Dict            # mapped to canonical finance fields
    confidence:    float
    page_count:    int = 0
    error:         Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "doc_type":      self.doc_type.value,
            "summary":       self.summary,
            "key_metrics":   self.key_metrics,
            "entities":      self.entities,
            "finance_schema": self.finance_schema,
            "confidence":    self.confidence,
            "page_count":    self.page_count,
            "table_count":   len(self.tables),
            "error":         self.error,
        }


# Finance metric extraction patterns
METRIC_PATTERNS = {
    "revenue":       r'(?:revenue|net sales|total income)[:\s₹$]*([0-9,]+\.?[0-9]*)\s*(cr|crore|billion|million|B|M|Cr)?',
    "net_profit":    r'(?:net profit|pat|profit after tax)[:\s₹$]*([0-9,]+\.?[0-9]*)\s*(cr|crore|billion|million|B|M|Cr)?',
    "eps":           r'(?:eps|earnings per share)[:\s₹$]*([0-9]+\.?[0-9]*)',
    "ebitda":        r'(?:ebitda)[:\s₹$]*([0-9,]+\.?[0-9]*)\s*(cr|crore|billion|million|B|M|Cr)?',
    "gross_margin":  r'(?:gross margin|gm)[:\s]*([0-9]+\.?[0-9]*)%',
    "operating_margin": r'(?:operating margin|ebit margin)[:\s]*([0-9]+\.?[0-9]*)%',
    "price_target":  r'(?:price target|target price|pt)[:\s₹$]*([0-9,]+\.?[0-9]*)',
    "debt":          r'(?:total debt|net debt)[:\s₹$]*([0-9,]+\.?[0-9]*)\s*(cr|crore|billion|million|B|M|Cr)?',
    "yoy_growth":    r'(?:yoy|year.on.year|y.o.y)[:\s]*([+-]?[0-9]+\.?[0-9]*)%',
    "guidance":      r'(?:guidance|outlook)[:\s]*([^\n.]{10,80})',
}

STOP_WORDS = {
    "THE","AND","FOR","ARE","BUT","NOT","YOU","ALL","CAN","HER",
    "WAS","ONE","OUR","OUT","HAD","HIS","HER","HAS","ITS","WHO",
    "DID","GET","MAY","NOW","ANY","TWO","NEW","USE","HOW","GET",
}


class DocumentIntelligencePipeline:
    """
    Unified pipeline for all document types.
    Flow: detect → extract text → extract tables → map metrics → summarize
    """

    async def process(
        self,
        file_bytes: bytes,
        filename: str,
        user_question: str = None,
        symbol: str = None,
    ) -> DocumentIntelligenceResult:

        doc_type = self._detect_type(filename, file_bytes)

        # Step 1: Extract text
        raw_text = ""
        page_count = 0
        error = None
        try:
            if doc_type == DocumentType.CHART_IMAGE or filename.lower().endswith(
                (".png", ".jpg", ".jpeg", ".webp")
            ):
                raw_text = await self._ocr_image(file_bytes)
            else:
                raw_text, page_count = self._extract_pdf_text(file_bytes)
        except Exception as e:
            error = f"Text extraction failed: {e}"
            log.warning(f"[doc_intel] extraction error: {e}")

        # Step 2: Extract tables
        tables = self._extract_tables(raw_text)

        # Step 3: Extract entities + metrics
        entities    = self._extract_entities(raw_text)
        key_metrics = self._extract_metrics(raw_text)

        # Step 4: Finance schema mapping
        finance_schema = self._map_to_finance_schema(key_metrics, doc_type, symbol)

        # Step 5: LLM summary
        summary = await self._generate_summary(
            raw_text, tables, key_metrics, user_question, doc_type, symbol
        )

        confidence = 0.9 if raw_text and len(raw_text) > 200 else 0.4

        return DocumentIntelligenceResult(
            doc_type=doc_type,
            raw_text=raw_text,
            tables=tables,
            key_metrics=key_metrics,
            entities=entities,
            summary=summary,
            finance_schema=finance_schema,
            confidence=confidence,
            page_count=page_count,
            error=error,
        )

    def _detect_type(self, filename: str, content: bytes) -> DocumentType:
        fname = filename.lower()
        if fname.endswith(".pdf"):
            # Heuristic: "deck" or "presentation" in name = earnings deck
            if any(w in fname for w in ["deck", "presentation", "investor"]):
                return DocumentType.EARNINGS_DECK
            return DocumentType.PDF_REPORT
        elif fname.endswith((".png", ".jpg", ".jpeg", ".webp")):
            if any(w in fname for w in ["chart", "graph", "screen"]):
                return DocumentType.CHART_IMAGE
            return DocumentType.BROKER_SCREENSHOT
        elif fname.endswith((".xlsx", ".csv", ".xls")):
            return DocumentType.SPREADSHEET
        return DocumentType.UNKNOWN

    def _extract_pdf_text(self, pdf_bytes: bytes):
        """Extract text from PDF using pypdf."""
        try:
            import pypdf, io
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
            return "\n".join(pages), len(reader.pages)
        except ImportError:
            log.warning("[doc_intel] pypdf not installed — install with: pip install pypdf")
            return "", 0
        except Exception as e:
            log.warning(f"[doc_intel] PDF extraction failed: {e}")
            return "", 0

    async def _ocr_image(self, image_bytes: bytes) -> str:
        """Use Claude vision (haiku) to extract text from chart/screenshot."""
        try:
            import anthropic
            client = anthropic.Anthropic()
            image_b64 = base64.b64encode(image_bytes).decode()

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            }
                        },
                        {
                            "type": "text",
                            "text": (
                                "Extract all visible text, numbers, labels, and data from this "
                                "financial image. Include: ticker symbols, prices, dates, "
                                "percentages, indicators, and all data points. "
                                "Return as structured plain text."
                            )
                        }
                    ]
                }]
            )
            return response.content[0].text
        except Exception as e:
            log.warning(f"[doc_intel] OCR failed: {e}")
            return ""

    def _extract_tables(self, text: str) -> List[ExtractedTable]:
        """Detect and parse tables from extracted text."""
        tables = []
        lines  = text.split("\n")
        in_table    = False
        current_rows: List[List[str]] = []

        for line in lines:
            stripped = line.strip()
            # Table row heuristic: pipe separators or 3+ tab-separated fields
            is_row = ("|" in stripped and len(stripped.split("|")) >= 3) or \
                     (stripped.count("\t") >= 2)

            if is_row:
                in_table = True
                sep = "|" if "|" in stripped else "\t"
                current_rows.append([c.strip() for c in stripped.split(sep) if c.strip()])
            elif in_table:
                if len(current_rows) >= 2:
                    tables.append(ExtractedTable(
                        headers=current_rows[0],
                        rows=current_rows[1:],
                        context=stripped[:100],
                    ))
                in_table = False
                current_rows = []

        # Flush last table
        if in_table and len(current_rows) >= 2:
            tables.append(ExtractedTable(
                headers=current_rows[0],
                rows=current_rows[1:],
                context="",
            ))

        return tables

    def _extract_entities(self, text: str) -> List[str]:
        """Extract ticker symbols and company names."""
        # NSE/BSE tickers: uppercase 2-10 chars, optionally .NS/.BO
        tickers = re.findall(r'\b[A-Z]{2,10}(?:\.NS|\.BO)?\b', text)
        return list(set(t for t in tickers if t not in STOP_WORDS))

    def _extract_metrics(self, text: str) -> Dict[str, Any]:
        """Extract key financial metrics using regex patterns."""
        metrics = {}
        for key, pattern in METRIC_PATTERNS.items():
            match = re.search(pattern, text, re.I)
            if match:
                metrics[key] = match.group(1).replace(",", "").strip()
                if match.lastindex and match.lastindex >= 2 and match.group(2):
                    metrics[f"{key}_unit"] = match.group(2)
        return metrics

    def _map_to_finance_schema(
        self, metrics: Dict, doc_type: DocumentType, symbol: str = None
    ) -> Dict:
        """Map extracted metrics to canonical finance fields."""
        schema = {
            "type":    doc_type.value,
            "symbol":  symbol,
            "metrics": {},
        }

        # Normalize units to crore for Indian docs
        for k, v in metrics.items():
            if k.endswith("_unit"):
                continue
            unit = metrics.get(f"{k}_unit", "")
            try:
                val = float(v)
                if unit.lower() in ("billion", "b"):
                    val *= 6800  # approx USD billion to INR crore
                schema["metrics"][k] = val
            except (ValueError, TypeError):
                schema["metrics"][k] = v  # keep as string if not numeric

        return schema

    async def _generate_summary(
        self,
        text: str,
        tables: List[ExtractedTable],
        metrics: Dict,
        question: str,
        doc_type: DocumentType,
        symbol: str,
    ) -> str:
        """Generate structured summary using Groq smart model."""
        if not text and not metrics:
            return "Could not extract content from document."

        tables_note   = f"{len(tables)} table(s) found." if tables else ""
        metrics_note  = f"Key metrics: {metrics}" if metrics else ""
        question_note = f"User question: {question}" if question else ""
        symbol_note   = f"Asset: {symbol}" if symbol else ""

        prompt = f"""You are Perseus, a financial document analyst.
Analyze this financial document and provide a structured summary.

Document type: {doc_type.value}
{symbol_note}
{tables_note}
{metrics_note}
{question_note}

Document content (first 3000 chars):
{text[:3000]}

Provide exactly:
1. Executive summary (2-3 sentences with specific numbers)
2. Key financial metrics identified
3. Main insight for a trader
4. One risk or red flag
5. One open question this document raises

Be specific. Cite numbers. Flag anything unusual."""

        try:
            from app.domain.reasoning.service import _groq_smart
            return _groq_smart(prompt)
        except Exception as e:
            log.warning(f"[doc_intel] LLM summary failed: {e}")
            # Fallback: structured text from metrics
            if metrics:
                lines = [f"Document: {doc_type.value}"]
                for k, v in metrics.items():
                    if not k.endswith("_unit"):
                        lines.append(f"- {k}: {v}")
                return "\n".join(lines)
            return f"Extracted {len(text)} characters from {doc_type.value}. LLM summary unavailable."


# Module-level singleton
pipeline = DocumentIntelligencePipeline()
