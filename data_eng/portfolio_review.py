"""Phase 8: LLM portfolio review — investment committee.

Asks the local LLM to review today's portfolio decisions for
contradictions, concentration risks, and cross-holding interactions.
Not a decision-maker — acts as a second pair of eyes.
"""

import logging
from datetime import date

import requests

from stock_bot.config import LLAMA_MODEL, LLAMA_URL

from .db import get_connection

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_today_decisions(today: date) -> list[dict]:
    """Load today's portfolio decisions."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT ticker, action, position_pct, shares, stop_loss, reason
           FROM portfolio_decisions WHERE date = ?
           ORDER BY action, ticker""",
        [today],
    ).fetchall()
    conn.close()
    return [
        {
            "ticker": r[0],
            "action": r[1],
            "position_pct": r[2],
            "shares": r[3],
            "stop_loss": r[4],
            "reason": r[5],
        }
        for r in rows
    ]


def _load_ta_summaries() -> dict[str, str]:
    """Load latest TradingAgents summary per ticker."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT ticker, summary
        FROM trading_agent_decisions
        WHERE (ticker, date) IN (
            SELECT ticker, MAX(date) FROM trading_agent_decisions GROUP BY ticker
        )
        AND summary IS NOT NULL AND summary != ''
    """).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _ask_llm(system: str, user: str) -> str | None:
    """Call local llama-server. Returns reply text, or None on failure."""
    try:
        resp = requests.post(
            LLAMA_URL,
            json={
                "model": LLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
            },
            timeout=180,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except requests.ConnectionError:
        log.warning("Portfolio review: LLM server not reachable at %s", LLAMA_URL)
        return None
    except Exception as e:
        log.error("Portfolio review: LLM request failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are an investment committee reviewing a portfolio's trade proposals. "
    "You do NOT make decisions — you identify risks, contradictions, and "
    "interactions that the rules engine may have missed. "
    "Be concise. Use bullet points. Focus on actionable concerns."
)


def _build_prompt(decisions: list[dict], summaries: dict[str, str]) -> str:
    """Build the review prompt from portfolio decisions and TA summaries."""
    buys = [d for d in decisions if d["action"] == "BUY"]
    sells = [d for d in decisions if d["action"] == "SELL"]
    holds = [d for d in decisions if d["action"] == "HOLD"]

    lines = ["# Today's Portfolio Decisions\n"]

    if buys:
        lines.append("## Proposed BUYS")
        for d in buys:
            stop = f"${d['stop_loss']:.2f}" if d.get("stop_loss") else "N/A"
            pct = f"{d['position_pct']:.1f}%" if d.get("position_pct") else "?"
            lines.append(
                f"- **{d['ticker']}**: {d['shares']:.0f} shares "
                f"({pct} of portfolio), stop {stop}. "
                f"Reason: {d.get('reason', '')}"
            )
            ta = summaries.get(d["ticker"])
            if ta:
                lines.append(f"  - TA summary: {ta[:200]}")
        lines.append("")

    if sells:
        lines.append("## Proposed SELLS")
        for d in sells:
            lines.append(f"- **{d['ticker']}**: {d['shares']:.0f} shares. Reason: {d.get('reason', '')}")
        lines.append("")

    if holds:
        lines.append("## HOLD positions")
        for d in holds:
            pct = f"{d['position_pct']:.1f}%" if d.get("position_pct") else "?"
            lines.append(f"- **{d['ticker']}** ({pct}): {d.get('reason', '')}")
        lines.append("")

    lines.append(
        "## Your Task\n"
        "Review these decisions. Flag:\n"
        "1. Contradictions (e.g., buying two highly correlated stocks)\n"
        "2. Concentration risks (sector, single-stock, or timing)\n"
        "3. Interactions across holdings\n"
        "4. Any position that looks oversized relative to conviction\n"
        "5. Missing considerations the rules engine can't see\n"
        "\nKeep it under 300 words."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_portfolio_review(review_date: date | None = None) -> str:
    """Run the LLM portfolio review. Returns the review text.

    Stores the result in portfolio_reviews table.
    """
    today = review_date or date.today()

    decisions = _load_today_decisions(today)
    if not decisions:
        log.info("Portfolio review: no decisions for %s, skipping", today)
        return ""

    summaries = _load_ta_summaries()
    prompt = _build_prompt(decisions, summaries)

    log.info("Portfolio review: asking LLM to review %d decisions...", len(decisions))
    review_text = _ask_llm(SYSTEM_PROMPT, prompt)

    if review_text is None:
        # LLM error (already logged) — nothing to store
        return ""

    # Store review
    _store_review(today, review_text)
    log.info("Portfolio review: stored review for %s (%d chars)", today, len(review_text))

    return review_text


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _store_review(review_date: date, review_text: str) -> None:
    """Upsert review into DuckDB."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO portfolio_reviews (date, review_text)
           VALUES (?, ?)
           ON CONFLICT (date) DO UPDATE SET review_text = EXCLUDED.review_text""",
        [review_date, review_text],
    )
    conn.close()
