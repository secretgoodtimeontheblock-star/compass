# Compass — Product & Architecture Research Notes

_Date: 2026-10-01_

## Purpose

This note captures the conclusions from a detailed review of Compass and comparison with mature open-source trading/backtesting systems. The target user is important: **Compass is primarily for a person entering trading**, while still being designed from the beginning for both crypto and Moscow Exchange instruments.

The central product principle:

> **Professional correctness under a beginner-friendly interface.**

The engine may become sophisticated. The beginner should not be forced to understand that sophistication before the application can help them.

---

## 1. Current assessment of Compass

Compass is already beyond a toy prototype. It is becoming a local trading decision-support workstation rather than an automated trading bot.

Strong foundations already present:

- market adapters for MOEX and crypto;
- candle cache and stale-data handling;
- strategies and signals;
- backtesting with next-bar execution;
- fees and slippage;
- risk-based position sizing;
- stops, targets and gap handling;
- OOS validation;
- strategy versioning;
- immutable trade plans;
- real / paper / historical journal modes;
- journal backup/restore and soft deletion;
- experiment history;
- live crypto data;
- AI explanation layer separated from numerical calculations;
- tests for engine and UI;
- local desktop distribution.

A particularly good architectural decision is that **AI explains facts calculated by code rather than calculating trading truth itself**.

### Main risk now: feature creep

Compass has accumulated scanner, journal, AI, replay, validation, plans, accounts, risk cockpit, live data and other features quickly.

The next major improvement should therefore **not** be more indicators, more strategies or more AI features.

The next improvement should be **trustworthiness and coherence**.

---

## 2. The largest current gap: Portfolio Truth

Compass can analyse a great deal, but its account state is still partly reconstructed from the journal.

Current limitations include:

- unrealized P&L is not the authoritative basis of account equity;
- exposure can be based on entry cost rather than current mark;
- open-position risk depends on manually recorded stops;
- journal state and actual brokerage state can diverge.

The Day/Risk panel can therefore look more authoritative than the underlying data really is.

### Proposed layer: Portfolio Truth

Create one canonical portfolio-state service:

```text
Market data / Broker snapshot / Journal
                ↓
        Portfolio Truth Layer
                ↓
positions
mark prices
cash
realized P&L
unrealized P&L
equity
exposure
open risk
daily risk
concentration
data freshness
reconciliation status
```

Every screen should consume this same state instead of calculating its own interpretation.

If read-only broker integration is added later, distinguish explicitly:

- **Broker truth** — what the broker reports;
- **Compass truth** — what Compass reconstructed;
- **Journal truth** — what the user recorded;
- **difference / reconciliation status**.

Never silently merge conflicting states.

---

## 3. Execution realism

The existing backtest is already conservative in several important places, but intraday trading exposes limitations of OHLC modelling.

Important remaining concerns:

- liquidity is not modelled;
- order size relative to volume is not modelled;
- OHLC cannot reveal the true order of intrabar events;
- real exchange sessions/clearing are not fully represented;
- spread is not yet a first-class execution cost;
- delayed MOEX ISS data materially limits real-time scalping use.

### Proposed abstraction: Execution Model

Backtest, replay and trade planning should eventually share the same execution assumptions:

```text
TradingSession
ExecutionModel
FeeModel
SlippageModel
SpreadModel
LiquidityModel
InstrumentRules
```

The beginner-facing UI should translate those models into plain language:

> “The historical result assumes an execution cost of X. A worse fill changes the result to Y.”

rather than forcing the user to configure microstructure manually.

---

## 4. Statistical validation: protect the user from self-deception

Compass already has a meaningful OOS implementation: optimization uses only the train period, the test period is unseen during selection, minimum sample rules exist, and cost sensitivity is available.

This is a strong start, but a single train/test split is not sufficient evidence of robustness.

### Next validation layer

Add gradually:

1. rolling / anchored walk-forward;
2. multiple independent OOS windows;
3. Monte Carlo trade-sequence simulation;
4. bootstrap confidence intervals;
5. parameter stability maps;
6. cost/spread/slippage stress;
7. regime sensitivity;
8. eventually PBO / Deflated Sharpe or similarly explicit multiple-testing controls.

### Do not lead with quantitative jargon

For a beginner, the primary output should be conclusions such as:

- **Not enough data**
- **Result is unstable**
- **Result survived unseen data**
- **Small parameter changes destroy the result**
- **Higher costs remove the advantage**
- **The historical result may be dominated by a few trades**

Sharpe, Sortino, Calmar, confidence intervals and detailed diagnostics belong in an expandable research layer.

---

## 5. Reference projects and what Compass should learn from them

### NautilusTrader

Useful pattern: event-driven state, portfolio derived from canonical account/position/price events, reconciliation with external venue state.

Take:

- canonical Portfolio Truth;
- explicit reconciliation;
- one state model consumed by the rest of the application.

Do **not** copy its full professional execution infrastructure into Compass.

### Freqtrade

Useful patterns:

- explicit look-ahead analysis;
- backtest integrity checks;
- exchange precision and minimum-order constraints;
- handling of execution ambiguity;
- finer timeframe/detail data to improve execution modelling.

Take:

- a **Backtest Integrity Suite**;
- automated tests specifically designed to detect future leakage;
- explicit warnings about candle-level ambiguity.

### Backtesting.py

Useful pattern: parameter heatmaps rather than showing only the winning parameter set.

Take:

- **Robustness Map**;
- show broad stable regions;
- treat isolated high-performing peaks as possible overfit.

### Backtrader

Useful patterns:

- execution/slippage abstractions;
- open/high/low/limit matching behaviour;
- volume/partial-fill concepts.

Take:

- richer Execution Model;
- configurable conservative fill assumptions.

### QuantStats

Useful patterns:

- richer performance/risk analytics;
- drawdown analysis;
- Expected Shortfall/CVaR-style downside analysis.

Take:

- drawdown depth + duration + recovery;
- tail-risk explanations;
- human-readable risk narratives.

### bt

Useful pattern: strategies are compositions of selection, weighting, scheduling and rebalancing algorithms.

Long-term Compass direction:

```text
Universe
→ Filter
→ Signal
→ Entry
→ Position sizing
→ Stop
→ Exit
→ Portfolio constraints
```

This is preferable to indefinitely creating unrelated Strategy1/Strategy2/Strategy3 implementations.

### QuantConnect LEAN

Useful pattern: Brokerage Models isolate venue-specific buying power, fees, fills, margin and execution rules.

Compass adaptation:

- beginner-facing **Crypto** and **MOEX** market profiles;
- detailed rules hidden underneath;
- explain differences rather than exposing every technical setting.

### Jesse

Useful pattern: robustness/Monte Carlo tooling can answer a human question rather than merely output statistics.

Compass framing:

> “Was this strategy robust, or did this historical sequence simply make it look good?”

### vn.py

Useful pattern: professional capabilities are modular.

Compass should keep advanced modules available without putting all of them in front of a beginner at once.

---

## 6. Beginner-first product model

The intended user may initially not know what slippage, overfitting, Sharpe, market regimes or position heat mean.

Compass should teach these concepts **at the moment they matter**, not require a trading course before use.

### Progressive disclosure

Proposed experience levels:

#### 1. Начинаю

Show:

- chart;
- what happened;
- why a signal exists;
- risk in RUB/USDT;
- position size;
- stop;
- simple paper trade;
- basic journal;
- contextual definitions;
- warnings about uncertainty.

Hide most quantitative research machinery.

#### 2. Торгую

Add:

- account/portfolio state;
- plans;
- journal review;
- daily limits;
- portfolio heat;
- correlations/concentration;
- plan-vs-fact;
- scanner;
- more detailed execution assumptions.

#### 3. Исследую

Expose:

- OOS;
- walk-forward;
- Monte Carlo;
- parameter heatmaps;
- experiment registry;
- Sharpe/Sortino/Calmar;
- MAE/MFE;
- execution modelling;
- sensitivity and stress testing.

These are **three presentations of one engine**, not three different products.

---

## 7. Crypto is the entry door, not the architecture

Crypto is attractive to a beginner and can be the easiest first experience.

But Compass must not become crypto-first internally.

The core should remain market-neutral around concepts such as:

```text
Market
Instrument
DataSource
TradingSession
ExecutionModel
Account
Portfolio
Strategy
Plan
Trade
Experiment
```

BTC/USDT and SBER should differ through adapters, instrument metadata and market rules rather than separate application logic.

MOEX must be a first-class market, not a compatibility layer added after crypto.

---

## 8. The strongest possible identity for Compass

The product should not compete on “more signals”.

Signals are abundant.

A more defensible identity is:

> **Compass is a system for learning disciplined trading decisions and testing whether those decisions survive contact with data and reality.**

The valuable lifecycle is:

```text
Hypothesis
→ Historical test
→ Robustness check
→ Signal
→ Immutable plan
→ Position sizing
→ Execution / paper execution
→ Journal
→ Plan-vs-fact review
→ Weekly review
→ Updated hypothesis
```

Compass already contains early versions of many pieces of this chain.

The strategic goal should be to connect them into one coherent learning loop.

---

## 9. Recommended development order

### Priority 1 — Portfolio Truth

- mark-to-market positions;
- realized + unrealized P&L;
- authoritative equity;
- current exposure;
- freshness/provenance;
- reconciliation-ready architecture.

### Priority 2 — Unified execution model

- market sessions/calendars;
- fees;
- slippage;
- spread;
- gap rules;
- liquidity/volume assumptions;
- shared by backtest, replay and planning.

### Priority 3 — Validation Lab

- rolling/anchored walk-forward;
- multiple OOS windows;
- Monte Carlo;
- bootstrap;
- parameter stability heatmaps;
- execution-cost stress;
- experiment comparison.

### Priority 4 — Decision Cockpit

One coherent answer to:

- What is happening now?
- What positions do I have?
- How much am I actually risking?
- Which plans are active?
- Which signals require attention?
- Am I violating my own rules?
- Is my data current?

### Priority 5 — AI as discipline/explanation layer

AI should focus on questions such as:

- What changed?
- Why is this signal present?
- What assumption makes this backtest weak?
- Where did I violate my plan?
- How does this trade differ from my previous trades?
- What concept should I understand before making this decision?

Avoid positioning AI as an oracle that says what to buy.

---

## 10. Things not to prioritize now

Do not make these the next major roadmap items:

- dozens of new indicators;
- many additional strategies;
- autonomous order execution;
- more AI providers for their own sake;
- professional terminal density in the beginner UI;
- complex microstructure controls exposed directly to beginners;
- a single “strategy score” that hides uncertainty.

The project currently benefits more from **truth, validation, coherence and education** than from feature count.

---

## 11. Product principle

A useful design test for every new Compass feature:

> **Does this help the user make a more informed, disciplined and falsifiable decision, or does it merely make the terminal look more sophisticated?**

Prefer the former.

The long-term opportunity is not to build a smaller Bloomberg/TradingView/QuantConnect.

It is to build a trading learning and decision system where professional quantitative safeguards exist underneath, while a beginner sees understandable consequences, uncertainty and next steps.
