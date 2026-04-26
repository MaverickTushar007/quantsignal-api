"""
domain/ai/router.py
Phase 3 — Task router.
Routes incoming queries to the right model tier without using an LLM.
Fast (haiku) for cheap classification tasks.
Strong (sonnet) for deep synthesis, document QA, scenario analysis.
"""
import re
import logging
from enum import Enum
from typing import Tuple

log = logging.getLogger(__name__)


class TaskType(str, Enum):
    MARKET_MOVE_EXPLAIN  = "market_move_explain"
    TRADE_SETUP_ANALYSIS = "trade_setup_analysis"
    DOCUMENT_QA          = "document_qa"
    NEWS_SYNTHESIS       = "news_synthesis"
    PORTFOLIO_ANALYSIS   = "portfolio_analysis"
    EDUCATION            = "education"
    STRATEGY_RESEARCH    = "strategy_research"
    CHART_ANALYSIS       = "chart_analysis"
    GENERAL              = "general"


class ModelTier(str, Enum):
    FAST   = "fast"    # claude-haiku-4-5-20251001  — cheap, <200ms
    STRONG = "strong"  # claude-sonnet-4-6           — rich reasoning


# Model strings — update here only if model names change
MODEL_MAP = {
    ModelTier.FAST:   "claude-haiku-4-5-20251001",
    ModelTier.STRONG: "claude-sonnet-4-6",
}

# Rules: regex pattern → (TaskType, ModelTier)
# Evaluated in order — first match wins
ROUTING_RULES = [
    # Document / upload tasks → always strong
    (r"pdf|document|report|filing|upload|read this|annual report|balance sheet|income statement",
     TaskType.DOCUMENT_QA, ModelTier.STRONG),

    # Trade setup → strong (money decisions)
    (r"setup|entry point|stop loss|take profit|position size|kelly|trade this|should i buy|should i sell",
     TaskType.TRADE_SETUP_ANALYSIS, ModelTier.STRONG),

    # Portfolio → strong (multi-position reasoning)
    (r"portfolio|my holdings|my positions|allocation|diversif|concentration|rebalance",
     TaskType.PORTFOLIO_ANALYSIS, ModelTier.STRONG),

    # Strategy research → strong
    (r"strategy|backtest|alpha|edge|research|regime|factor|momentum|mean reversion",
     TaskType.STRATEGY_RESEARCH, ModelTier.STRONG),

    # Chart analysis → strong (visual reasoning)
    (r"chart|pattern|breakout|support|resistance|trendline|candlestick|technical",
     TaskType.CHART_ANALYSIS, ModelTier.STRONG),

    # Market move explain → fast (simple cause-effect)
    (r"why.*moved|what happened|explain.*drop|explain.*rise|why is.*down|why is.*up|why did",
     TaskType.MARKET_MOVE_EXPLAIN, ModelTier.FAST),

    # News synthesis → fast (summarization)
    (r"news|headline|announcement|earnings|report|press release|what.*said",
     TaskType.NEWS_SYNTHESIS, ModelTier.FAST),

    # Education → fast (definitions, explanations)
    (r"what is|what are|explain|define|how does|teach me|beginner|basics",
     TaskType.EDUCATION, ModelTier.FAST),
]


def route_query(query: str) -> Tuple[TaskType, ModelTier, str]:
    """
    Route a user query to the appropriate task type and model tier.
    Returns: (TaskType, ModelTier, model_string)
    Never raises.
    """
    q = query.lower().strip()

    for pattern, task_type, model_tier in ROUTING_RULES:
        if re.search(pattern, q):
            model = MODEL_MAP[model_tier]
            log.debug(f"[router] '{query[:50]}' → {task_type.value} / {model_tier.value}")
            return task_type, model_tier, model

    # Default: general query → fast model
    return TaskType.GENERAL, ModelTier.FAST, MODEL_MAP[ModelTier.FAST]


def get_model_for_task(task_type: TaskType) -> str:
    """Direct lookup by task type — for when caller already knows the task."""
    for _, t, tier in ROUTING_RULES:
        if t == task_type:
            return MODEL_MAP[tier]
    return MODEL_MAP[ModelTier.FAST]
