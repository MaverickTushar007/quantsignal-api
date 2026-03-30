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
    "derman_1999_vol_regimes": """Goldman Sachs Quantitative Strategies Research: Regimes of Volatility (Derman 1999).
Three volatility regimes exist in equity markets: sticky strike, sticky delta, and sticky implied tree.
In sticky strike regime: implied volatility of a given strike remains constant as spot moves.
This regime dominates in trending markets. Momentum signals are more reliable in this regime.
In sticky delta regime: implied volatility of a given delta remains constant as spot moves.
Dominates in range-bound markets. RSI overbought/oversold signals have higher hit rate here.
Variance risk premium: implied vol consistently exceeds realized vol by 2-4 vol points on average.
This premium is compensation for bearing volatility risk.""",
    "derman_1994_local_vol": """Goldman Sachs Quantitative Strategies: Implied Trinomial Trees and Local Volatility (Derman 1994).
Local volatility surface captures market expectation of future volatility at each price level and time.
Negative skew in equity markets: OTM puts are expensive relative to Black-Scholes due to crash fear.
High implied vol skew signals institutional hedging demand — smart money buying protection.
When skew is steep, market participants are defensively positioned — often a contrarian bullish signal.
For signal generation: high put/call skew on a BUY signal is a conflict — reduce position size.
Term structure upward sloping means market expects more volatility ahead — uncertainty regime.""",
    "goldman_var_framework": """Goldman Sachs Risk Management: Value at Risk and Portfolio Greeks.
VaR at 95% confidence: maximum expected loss over 1 day = 1.65 * daily_vol * position_size.
Portfolio VaR is not additive — correlation reduces total risk. Two uncorrelated positions reduce VaR by sqrt(2).
Delta measures price sensitivity to underlying moves. Gamma is rate of change of delta.
High gamma stocks near earnings have explosive move potential — ATR understates true risk in this case.
Kelly sizing should be reduced by 50% for high-gamma situations due to path dependency of returns.
Vega sensitivity to implied volatility. Long options benefit from vol increases.""",
    "goldman_stat_arb": """Goldman Sachs Quantitative Strategies: Statistical Arbitrage and Convergence Trading.
Pairs trading: identify two cointegrated assets, long underperformer, short outperformer.
Z-score entry when spread exceeds 2.0 standard deviations, exit at mean reversion to zero.
Cross-asset correlation regime: during risk-off events, correlations spike toward 1.0 across all assets.
Diversification fails precisely when needed most — only truly uncorrelated factors provide protection.
When multiple signals in portfolio are BUY simultaneously, check correlation between assets.
If assets are highly correlated above 0.7, treat multiple positions as a single concentrated bet.
Effective diversification requires correlation-adjusted position sizing — reduce size when correlation rises.""",
    "goldman_momentum_factors": """Goldman Sachs Asset Management: Factor-Based Momentum and Cross-Sectional Alpha.
Cross-sectional momentum: rank assets by 12-1 month returns, long top decile, short bottom decile.
This strategy has generated 1-2 percent monthly alpha historically with Sharpe ratio above 0.8.
Momentum crashes occur after prolonged bear markets — momentum reverses sharply in recovery.
Risk management: cap momentum exposure during high VIX environments above 30 — momentum unreliable.
Factor timing: momentum works best in trending macro environments. Value works best when spreads are wide.
Signal decay: momentum signals have 3-6 month half-life. Refresh signals monthly minimum.
Momentum features such as 12-1 month return and 3-month return are highest importance features
in cross-sectional return prediction models at institutional quant funds.""",
    "goldman_regime_detection": """Goldman Sachs Global Investment Research: Market Regime Classification Framework.
Four primary market regimes: Risk-On Growth with high returns and low vol, Risk-Off Recession with
negative returns and high vol, Goldilocks with moderate returns and declining vol, and Stagflation.
Bull regime: breadth above 60 percent of stocks above 200-day MA, VIX below 20, yield curve positive.
Bear regime: breadth below 40 percent, VIX above 25, yield curve inverted, credit spreads widening.
Reduce position sizes by 30-50 percent in bear regime. Defensive factors outperform.
Ranging regime: breadth 40-60 percent, VIX 15-25, no clear macro trend. Mean reversion optimal.
Signal calibration by regime: in bull regimes BUY signals above 55 percent probability are actionable.
In bear regimes raise threshold to 70 percent probability. In ranging only trade confluence above 8 of 9.""",
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
