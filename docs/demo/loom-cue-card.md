# QuoteMason demo — CUE CARD (glance-only)

**⏱ 10:00 · keep clock visible · egress line = the money shot · narrate over every spinner**

Pre-flight: Render awake · 5 tabs open · Q3 draft pre-baked in queue · egress prompt on clipboard · notifs off

---

### 1 · HOOK + PROBLEM — 1:00 → *[landing page]*
- "I'm Mohtsham — **QuoteMason**, agentic estimation assistant for renovation contractors."
- Estimator turns a request → accurate quote fast. Hours today: takeoff, phone for prices, **miss a code trigger** → change order at inspection.
- Real partner: revised quotes **−26 / −12 / +18%** · one miss = **$12.5k**.

### 2 · SOLUTION + ARCHITECTURE — 1:30 → *[§2.3 diagram]*
- Two products exist & commoditized: writes quotes / checks code. **Neither does the JOIN.**
- Join = code+zoning triggers **inside** the quote, cited, priced from **own past jobs**.
- **LangGraph:** intake → retrieve → price → draft → **human review**.
  - Intake = Haiku (trigger scan + slot-fill)
  - RAG = **one Qdrant collection**, filtered jurisdiction+doc_type (quotes / guidelines / OBC / zoning)
  - **Tavily** = live prices
  - Draft = Sonnet, fully cited
  - **Estimator approves — agent never sends.**
- Redis memory · Neon drafts · LangSmith traces · Vercel + Render.

### 3 · LIVE INTAKE — 2:00 → *[/estimate fresh tab, PASTE prompt]*
- 900 sqft Cambridge basement, 1 bed + bath, **bedroom back corner, small window well**.
- "Not jumping to a number — **slot-filling** the high-variance qs." (ceiling / scope fork / rough-in)
- Answer briefly → intake completes.
- **Customer never sees draft** → thanks user, drafts in **background** → lands in estimator queue.

### 4 · CITED DRAFT + THE JOIN — 2:00 → *[/estimator, open the draft]*
- Real format: categories · tiers (Essential/Superior/Supreme) · milestones · timeline · HST.
- **⭐ THE JOIN:** undersized window → flagged **"Ontario Building Code 9.9.10"** egress → **PRICED** (concrete cut + window, Tavily range) → **cited on the line**. *(say the clause number out loud)*
- Every priced line traces to a source (P-code / allowance / price check). Unverified → says **"rate unverified."**

### 5 · ESTIMATOR GATE — 1:30 → *[/estimator]*
- **Edit** → logged to LangSmith = labeled training data (flywheel).
- **Request changes** → resumes same thread, skips intake, **new version supersedes** (versioned in Postgres).
- **Approve** → mailto stand-in. **No auto-send exists** — human gate is the product principle.

### 6 · DATA + EVAL — 1:30 → *[§5.3 tables / terminal]*
- Data: **PII-redacted** · **structure-aware chunking** (code articles / categories / by-law sections, never fixed windows) · **730 chunks**.
- Eval: hand-anchored golden set + **cross-family GPT-5.1 judge** (not Anthropic — no self-grading).
- **Numbers:** hybrid dense+BM25 → hit@5 **0.83 → 0.92** · zoning MRR **0.47 → 0.64** · **0 violations**.
- Traceability improvement: judge criterion **partial → pass** on every drafted scenario. Every claim = committed eval result.

### 7 · NEXT STEPS + CLOSE — 0:30 → *[landing / cam]*
- Capstone priority: **quote-accuracy eval** — leave-one-out on real projects → "within X% of actual."
- Then: daily price tool · dup-quote guardrails · estimator auth.
- "The join nobody else does, human always in the loop. Thanks."

---

**IF IT BREAKS:** cold 500 → "documented cold-start, here's one from moments ago" *(open pre-baked)* · intake loops → answer "unknown" (it's a feature) · draft misses egress → open pre-baked Q3.
