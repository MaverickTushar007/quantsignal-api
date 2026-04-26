"""
domain/ai/verifier.py
Phase 3 — Answer verifier.
Runs after every AI-generated finance answer.
Checks: numeric consistency, freshness, citation coverage, contradictions.
A verified answer is one Perseus can stand behind.
"""
import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    passed:           bool
    score:            float          # 0-1 composite quality score
    numeric_ok:       bool
    freshness_ok:     bool
    citation_coverage: float         # 0-1 fraction of factual claims with evidence
    issues:           List[str] = field(default_factory=list)
    warnings:         List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed":            self.passed,
            "score":             round(self.score, 3),
            "numeric_ok":        self.numeric_ok,
            "freshness_ok":      self.freshness_ok,
            "citation_coverage": round(self.citation_coverage, 3),
            "issues":            self.issues,
            "warnings":          self.warnings,
        }


# Sentence patterns that signal a factual/numeric claim
FACTUAL_MARKERS = [
    r'\d+\.?\d*\s*%',           # percentages
    r'\$[\d,]+',                 # dollar amounts
    r'₹[\d,]+',                  # rupee amounts
    r'\d+\.?\d*x',              # multiples
    r'rose|fell|gained|lost|up|down|increased|decreased|reported|announced',
    r'revenue|profit|eps|ebitda|margin|growth',
]

FACTUAL_PATTERN = re.compile('|'.join(FACTUAL_MARKERS), re.I)

# Direction contradiction patterns
DIRECTION_PAIRS = [
    (r'\bbullish\b', r'\bbearish\b'),
    (r'\bbuy\b',     r'\bsell\b'),
    (r'\blong\b',    r'\bshort\b'),
    (r'\boverweight\b', r'\bunderweight\b'),
]


class AnswerVerifier:
    """
    Verifies AI-generated finance answers against evidence.
    Used in the reasoning pipeline after every LLM call.
    """

    def verify(
        self,
        answer: str,
        evidence: list,
        packet=None,
        max_freshness_seconds: int = 86400,
    ) -> VerificationResult:
        issues:   List[str] = []
        warnings: List[str] = []

        # 1. Numeric consistency
        numeric_ok = self._check_numerics(answer, evidence)
        if not numeric_ok:
            issues.append("Numeric values in answer may not match source data — verify before acting")

        # 2. Freshness
        freshness_ok = self._check_freshness(packet, max_freshness_seconds)
        if not freshness_ok:
            issues.append(f"Some data may be stale (>{max_freshness_seconds//3600}h old)")

        # 3. Citation coverage
        citation_coverage = self._estimate_citation_coverage(answer, evidence)
        if citation_coverage < 0.5:
            warnings.append(f"Low citation coverage ({citation_coverage:.0%}) — answer may contain unsupported claims")
        elif citation_coverage < 0.3:
            issues.append(f"Very low citation coverage ({citation_coverage:.0%}) — treat answer as directional only")

        # 4. Contradiction detection
        contradictions = self._detect_contradictions(answer)
        for c in contradictions:
            issues.append(f"Contradiction: {c}")

        # 5. Composite score
        score = (
            0.30 * (1.0 if numeric_ok else 0.0) +
            0.20 * (1.0 if freshness_ok else 0.0) +
            0.30 * min(citation_coverage, 1.0) +
            0.20 * (1.0 if not contradictions else 0.0)
        )

        passed = score >= 0.55 and not any(
            "Contradiction" in i for i in issues
        )

        return VerificationResult(
            passed=passed,
            score=score,
            numeric_ok=numeric_ok,
            freshness_ok=freshness_ok,
            citation_coverage=citation_coverage,
            issues=issues,
            warnings=warnings,
        )

    def _check_numerics(self, answer: str, evidence: list) -> bool:
        """Numbers in the answer should appear somewhere in the evidence."""
        numbers = re.findall(r'[\d,]+\.?\d*', answer)
        if not numbers:
            return True  # no numbers = nothing to check

        evidence_text = self._evidence_to_text(evidence)
        if not evidence_text:
            return True  # no evidence = can't check, don't fail

        # Allow if at least 70% of numbers appear in evidence
        found = sum(
            1 for n in numbers
            if n.replace(",", "") in evidence_text.replace(",", "")
        )
        return (found / len(numbers)) >= 0.70

    def _check_freshness(self, packet, max_seconds: int) -> bool:
        if not packet:
            return True
        freshness = getattr(packet, "freshness_seconds", 0)
        return freshness < max_seconds

    def _estimate_citation_coverage(self, answer: str, evidence: list) -> float:
        """Estimate what fraction of factual sentences have supporting evidence."""
        sentences = [s.strip() for s in answer.split('.') if s.strip()]
        factual = [s for s in sentences if FACTUAL_PATTERN.search(s)]

        if not factual:
            return 1.0  # no factual claims = nothing to verify

        evidence_text = self._evidence_to_text(evidence).lower()
        if not evidence_text:
            return 0.0

        supported = sum(
            1 for s in factual
            if any(
                word in evidence_text
                for word in s.lower().split()
                if len(word) > 4 and word.isalpha()
            )
        )
        return supported / len(factual)

    def _detect_contradictions(self, answer: str) -> List[str]:
        found = []
        lower = answer.lower()
        for pos_pat, neg_pat in DIRECTION_PAIRS:
            if re.search(pos_pat, lower) and re.search(neg_pat, lower):
                found.append(
                    f"Answer contains both '{pos_pat.strip(chr(92)+'b')}' "
                    f"and '{neg_pat.strip(chr(92)+'b')}' framing"
                )
        return found

    def _evidence_to_text(self, evidence: list) -> str:
        parts = []
        for e in evidence:
            if hasattr(e, "content"):
                parts.append(e.content)
            elif isinstance(e, dict):
                parts.append(e.get("content", str(e)))
            else:
                parts.append(str(e))
        return " ".join(parts)


# Module-level singleton
verifier = AnswerVerifier()
