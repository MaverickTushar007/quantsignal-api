"""
core/rag_ingest.py
Chunks quant research papers and stores embeddings in Supabase pgvector.
Run once: python -m core.rag_ingest
"""
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None
from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()

PAPERS = {
    "kelly_1956": """
The Kelly Criterion states that the optimal fraction of capital to bet is f* = (bp - q) / b,
where b is the net odds, p is probability of winning, q = 1-p is probability of losing.
This maximizes the expected logarithm of wealth, which is equivalent to maximizing long-run
growth rate. Overbetting beyond Kelly leads to ruin. Half-Kelly is recommended in practice
to reduce variance while preserving most of the growth rate advantage.
The Kelly formula for stocks: f* = (mu - r) / sigma^2, where mu is expected return,
r is risk-free rate, sigma is volatility. Position sizing should scale with edge divided by odds.
Never risk more than Kelly suggests — the penalty for overbetting is asymmetric and severe.
""",
    "jegadeesh_titman_1993": """
Momentum strategy: buying past winners and selling past losers generates significant abnormal returns.
Assets with highest returns over past 3-12 months continue to outperform over the next 3-12 months.
The momentum effect is strongest at 6-month formation and 6-month holding periods.
Momentum profits are not explained by systematic risk. The effect is present across all size quintiles.
Price momentum is distinct from earnings momentum. Winners continue winning due to delayed price
reaction to firm-specific information. Reversals occur at 1-month (short-term) and 3-5 years (long-term).
SMA crossovers capture momentum: when short-term SMA crosses above long-term SMA, momentum is bullish.
RSI above 50 confirms positive momentum. Volume confirmation strengthens momentum signals.
""",
    "bollinger_2001": """
Bollinger Bands consist of a middle band (20-period SMA) with upper and lower bands at 2 standard
deviations. Prices near the upper band indicate overbought conditions; near lower band indicate oversold.
Volatility contraction (band squeeze) precedes significant price moves — direction uncertain but move likely.
The %B indicator measures price position relative to bands: above 1.0 is above upper band (overbought),
below 0.0 is below lower band (oversold). Band width measures volatility — narrow bands signal low volatility.
Walking the bands: prices can ride the upper band in strong uptrends for extended periods.
Mean reversion trades: fade moves to the bands when confirmed by volume and momentum divergence.
Breakouts from tight bands (squeeze) tend to be powerful and sustained.
""",
    "wilder_1978": """
Average True Range (ATR) measures market volatility. True Range = max of: current high minus current low,
absolute value of current high minus previous close, absolute value of current low minus previous close.
ATR is a 14-period smoothed average of True Range. Higher ATR means higher volatility.
ATR-based stops: place stop loss at 1x-2x ATR below entry for long positions. Take profit at 2x-3x ATR.
RSI (Relative Strength Index): 100 - (100 / (1 + RS)) where RS = average gain / average loss over 14 periods.
RSI above 70 = overbought, below 30 = oversold. RSI divergence with price is a powerful reversal signal.
Parabolic SAR follows price in a trend and flips when trend reverses — useful for trailing stops.
Directional Movement Index (DMI): +DI above -DI indicates bullish trend. ADX above 25 confirms strong trend.
""",
    "fama_french_1992": """
Expected returns are explained by three factors: market risk (beta), size (SMB), and value (HML).
Small-cap stocks outperform large-cap over long periods — the size premium averages 3% annually.
Value stocks (high book-to-market) outperform growth stocks — value premium averages 5% annually.
Beta alone does not explain cross-sectional variation in returns — size and value add explanatory power.
High momentum combined with value characteristics produces the strongest risk-adjusted returns.
Factor investing: diversifying across uncorrelated risk factors improves Sharpe ratio significantly.
Market volatility regimes affect factor performance — momentum works best in trending markets,
mean reversion works best in range-bound markets with high RSI extremes.
""",
    "atr_risk_management": """
Position sizing based on volatility: risk a fixed percentage (1-2%) of portfolio per trade.
Risk per trade = Portfolio * Risk% / ATR. This normalizes position size across different volatility regimes.
A 2:1 reward-to-risk ratio means take profit at 2x ATR, stop loss at 1x ATR from entry.
Kelly-optimal sizing combined with ATR stops: f* = edge / (reward/risk ratio).
When ATR is high (volatile market), reduce position size. When ATR is low, increase position size.
Confluence of signals reduces uncertainty: 7+ bullish confluences out of 9 factors justifies larger sizing.
Drawdown management: if portfolio drawdown exceeds 20%, reduce all position sizes by 50%.
Expected value = (win_rate * avg_win) - (loss_rate * avg_loss). Only trade when EV is positive.
""",
}

def chunk_text(text: str, chunk_size: int = 200) -> list[str]:
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size // 2):
        chunk = " ".join(words[i:i + chunk_size])
        if len(chunk) > 50:
            chunks.append(chunk.strip())
    return chunks

def ingest():
    print("Loading embedding model...")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    
    print("Connecting to Supabase...")
    client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
    
    # Clear existing chunks
    client.table("research_chunks").delete().neq("id", 0).execute()
    
    total = 0
    for paper_name, content in PAPERS.items():
        chunks = chunk_text(content)
        print(f"Embedding {paper_name}: {len(chunks)} chunks...")
        
        for i, chunk in enumerate(chunks):
            embedding = model.encode(chunk).tolist()
            client.table("research_chunks").insert({
                "paper": paper_name,
                "chunk_index": i,
                "content": chunk,
                "embedding": embedding,
            }).execute()
            total += 1
    
    print(f"Done — {total} chunks stored in Supabase pgvector")

if __name__ == "__main__":
    ingest()
