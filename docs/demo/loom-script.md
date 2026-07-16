# QuoteMason — 10-minute Loom demo: notes + script

**Goal of the video:** prove the working deployed app *and* show you understand the build (the 7 tasks). Rubric weights the demo at 10 pts — graders want to see it live, hear the problem→solution in your own words, and see the one thing competitors don't do: **code/zoning triggers flagged inside a cited quote, priced from the contractor's own past jobs, with a human approving before anything ships.**

---

## Pre-flight checklist (do this 5 min BEFORE hitting record)

- [ ] **Wake the Render backend first.** Free tier cold-starts ~60s. Open `https://quotemason-api.onrender.com/docs` and hit any endpoint (or load `/estimator`) a minute before recording so the first chat isn't a dead-air wait.
- [ ] **Tabs open, in this order** (so you just Cmd+`/click across, no fumbling):
  1. `docs/submission.md` (or the rendered GitHub view) — for the Task tour
  2. `https://quotemason-frontend.vercel.app` — landing page
  3. `https://quotemason-frontend.vercel.app/estimate` — intake chat (fresh tab = fresh thread)
  4. `https://quotemason-frontend.vercel.app/estimator` — review console
  5. Editor with the submission architecture diagram (§2.2 / §2.3) visible, OR the mermaid rendered
  6. Terminal in `backend/`, ready to run the eval command (optional segment)
- [ ] **Have a second `/estimate` tab pre-run** with the Q3 egress scenario already drafted and sitting in the queue — cold drafting can take 20–40s and you don't want to narrate silence. Demo the live one, but have the pre-baked draft as your fallback to open in `/estimator`.
- [ ] Zoom browser to ~110–125% so text is legible in the recording.
- [ ] Close Slack/email notifications. Silence phone.
- [ ] Loom set to **screen + cam bubble**, mic checked.
- [ ] Have the **egress prompt** copied to clipboard (below) so you paste, not type.

**The paste-ready intake prompt (Q3 — the money shot):**
> Finishing my basement in Cambridge, about 900 square feet. I want one bedroom and a full bathroom, laminate flooring throughout. The bedroom would be in the back corner where there's only a small window well.

*(The small/undersized window is what should trip the OBC 9.9.10 egress flag — that's the whole demo.)*

---

## Timing map (10:00 total)

| # | Segment | Time | Cumulative |
|---|---|---|---|
| 1 | Hook + who you are + the problem | 1:00 | 1:00 |
| 2 | The solution in one breath + architecture | 1:30 | 2:30 |
| 3 | **Live demo — intake chat** | 2:00 | 4:30 |
| 4 | **Live demo — the cited draft + the "join"** | 2:00 | 6:30 |
| 5 | **Live demo — estimator review gate** | 1:30 | 8:00 |
| 6 | Data + evaluation (proof it works, with numbers) | 1:30 | 9:30 |
| 7 | Next steps + close | 0:30 | 10:00 |

Keep a clock visible. If you're running long, segment 6 is the one to compress (say the numbers, skip running the harness live).

---

## FULL SCRIPT (word-for-word narration + [on-screen actions])

### 1 — Hook + problem (1:00)
*[On screen: the Vercel landing page.]*

"Hi — I'm Mohtsham, and this is **QuoteMason**, an agentic estimation assistant for residential renovation contractors.

Here's the problem it solves. When a homeowner asks a small renovation company to finish their basement, an estimator has to turn that request into an accurate, defensible quote — fast enough to win the job. Today that takes hours: they do a material takeoff from undocumented rules of thumb, phone around for current prices, and — this is the expensive part — try to remember which building-code and zoning triggers apply, out of a twelve-hundred-page code compendium. When they miss one — say a basement bedroom needs an egress window — it doesn't surface until inspection, and it becomes a change order.

I grounded this in a real partner, a GTA basement-renovation builder. Their own revised quotes missed by **minus twenty-six, minus twelve, and plus eighteen percent** — one of those was a single missed code trigger worth twelve and a half thousand dollars. That's the pain QuoteMason attacks."

### 2 — Solution + architecture (1:30)
*[On screen: switch to the §2.3 agent-workflow diagram.]*

"The insight is that the two obvious products already exist and are commoditized: tools that write quotes, and tools that check code. Neither does the **join** — and that join is the whole value: code and zoning triggers flagged *inside* the quote, with clause-level citations, priced from the contractor's *own* past projects.

Architecturally it's a **LangGraph** agent: `intake → retrieve → price → draft → human review`.

- Intake is a cheap chat model — Claude Haiku — that screens every message for manual-intervention triggers and then does structured slot-filling over the twelve highest-cost-driving questions.
- Retrieval is **RAG over one shared Qdrant collection**, filtered by jurisdiction and doc-type: past project quotes, builder guidelines, Ontario Building Code Part 9, and the Cambridge zoning by-law.
- **Tavily** — the external search tool — spot-checks current material prices, the one thing a private corpus can never contain.
- Drafting is Claude Sonnet, composing one fully-cited quote.
- And then the non-negotiable part: **a licensed estimator reviews and approves. The agent never sends anything itself.**

Memory is an Upstash Redis checkpointer, drafts persist to Neon Postgres, everything's traced in LangSmith, front end on Vercel, back end on Render. Let me show you it running."

### 3 — Live intake (2:00)
*[On screen: /estimate, fresh tab. Paste the egress prompt. Send.]*

"This is the customer-facing intake. I'll describe a real scenario: a nine-hundred-square-foot basement finish in Cambridge, one bedroom, full bath — and note the bedroom's in a back corner with only a small window well. Watch what the agent does with that.

*[As it asks follow-ups]* — see, it's not jumping to a number. It's slot-filling: it's asking the high-variance questions an estimator would ask — ceiling height, the scope fork between a finished basement and a legal second unit, bathroom rough-in. This conversation is checkpointed in Redis, so it survives a reconnect.

*[Answer its questions briefly — e.g. 'ceiling is 7 and a half feet, just a finished basement not a legal unit, no rough-in yet'. Let intake complete.]*

And here's a deliberate design choice: the customer **never sees the draft**. When intake completes, the agent thanks the customer, then finishes retrieval, pricing, and drafting in the background — and drops the result into the estimator's queue. The draft's only exit is through a human. Let me switch to that side."

### 4 — The cited draft + the join (2:00)
*[On screen: /estimator console. Open the draft that just landed — or your pre-baked fallback.]*

"This is the estimator's review console — the system of record. Here's the draft that intake produced.

*[Scroll to the categorized quote.]* It's in the contractor's real format: work categories, tier allowances — Essential, Superior, Supreme — milestones, timeline, HST.

Now the payoff — **the join.** *[Scroll to / highlight the egress line item.]* Because I described a bedroom with an undersized window, the agent flagged the **Ontario Building Code 9.9.10 egress requirement** — and it didn't just mention it, it **priced it**: the concrete cutting and enlarged window, with a current price range from Tavily, cited right on the line item. That's the change order that would've surfaced at inspection — caught inside the first draft instead.

*[Scroll to another line.]* And every priced line traces to a source — a past-project code, a guideline allowance, or a price check. Where the corpus only has an unverified placeholder rate, it says so — *rate unverified* — rather than laundering a made-up number into an authoritative-looking price. That traceability is something I specifically measured and improved, which I'll show in a second."

### 5 — Estimator gate (1:30)
*[On screen: still /estimator. Show edit + revise + approve.]*

"The estimator is in control here. They can **edit** a line directly — and every edit is logged to LangSmith as labeled training data, which is the seed of a feedback flywheel.

They can **request changes** — *[click revise / show it]* — which resumes the *same* conversation thread, skips intake, and produces a new version that supersedes the old one. Everything's versioned in Postgres.

And when it's right, they **approve** — which returns a mailto stand-in. *[Show it.]* There is deliberately **no auto-send path in the system**. The human gate isn't a demo simplification — it's the core product principle. An AI drafts; a licensed estimator signs off."

### 6 — Data + evaluation (1:30)
*[On screen: submission.md §5.3 tables, or run the eval command in terminal.]*

"Two quick words on rigor, because 'it demos well' isn't the same as 'it works.'

On **data**: everything's PII-redacted before processing, and chunking is **structure-aware** — I split on code articles, work categories, and by-law sections, never fixed token windows, so every chunk keeps its citation lineage. That's 730 chunks across the four sources.

On **evaluation**: I have a hand-anchored golden set with exact clause-level ground truth, scored two ways — retrieval metrics plus a **cross-family GPT-5.1 judge** — deliberately not an Anthropic model, so the drafter isn't grading itself.

The numbers: for Task 6 I added a **hybrid dense-plus-BM25 retriever**, because dense embeddings blur exact clause numbers and table values. That moved hit-at-5 from **0.83 to 0.92**, and zoning — the weakest slice — from 0.47 to 0.64 MRR, with **zero filter violations**. And my second improvement, priced-line traceability, is measured before-and-after on the judge: the trace criterion went from partial to **pass** on every scenario that produced a draft. Every claim here is backed by a committed eval result, not a vibe."

### 7 — Next steps + close (0:30)
*[On screen: back to landing page or your cam.]*

"For the capstone, the priority is **quote-accuracy evaluation** — leave-one-out on the real past projects, so I can say the estimate lands within X percent of what the contractor actually charged, not just that retrieval hit-rate went up. After that: a deterministic daily price tool to replace live search, duplicate-quote guardrails, and auth on the estimator console.

That's QuoteMason: the join nobody else does, with a human always in the loop. Thanks for watching."

---

## Delivery tips

- **Talk to the value, not the plumbing.** Graders can read the stack in the doc; the video is where you prove you understand *why* each piece is there. Every time you name a tool, follow it with the job it does ("Redis — so the conversation survives a reconnect").
- **Narrate over dead air.** When the draft is generating, keep talking about the design choice (the human gate, background drafting) — never let the recording sit silent watching a spinner.
- **If the live draft is slow or flakes**, say "let me pull one I ran a moment ago" and open the pre-baked queue item. Rehearsed recovery reads as competence, not failure.
- **The egress line is the single most important frame in the video.** Make sure it's on screen, highlighted, and you say the words "Ontario Building Code 9.9.10" out loud. That one moment is the whole differentiator.
- **Do one full rehearsal run** with the clock. First take is almost always 12–13 min; the second, after you know where you're going, lands at 10.
- Record in **one take if you can** — it reads as more confident than cuts. But it's fine to record segments and stitch; graders care about content, not production.

## One-line fallbacks if something breaks on camera
- Backend cold / 500: "Render's free tier spun down — that ~minute is the documented cold-start; here's a run from moments ago." *(open pre-baked)*
- Intake loops on a question: answer it as "unknown" — the agent is built to draft on filled-or-explicitly-unknown slots; that's a feature to point out.
- Draft missing the egress flag (model variance): open your pre-baked Q3 draft that has it. This is exactly why you pre-ran one.
