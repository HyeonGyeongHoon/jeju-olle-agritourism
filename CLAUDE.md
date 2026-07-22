# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

제주올레 도슨트 (Jeju Olle Docent) — a LangGraph-based RAG agent that recommends Jeju Olle trail courses, weaves in crop/harvest-season narration tied to each course, and appends nearby local cafe/restaurant recommendations. Backed by Supabase (Postgres + pgvector), served over FastAPI SSE streaming.

## Commands

```powershell
# Setup (Windows PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run the API server
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
# -> POST /api/v1/chat/stream (SSE), GET /health, GET /docs (Swagger UI does NOT render SSE live — see Gotchas)

# Tests
python -m pytest                          # full suite
python -m pytest tests/test_router.py -v  # single file
python -m pytest tests/test_router.py::test_route_intent_course_info -v  # single test

# Lint / format
ruff check .
ruff format .

# Re-ingest DB (Supabase tables + Solar embeddings + Visit Jeju local recs) — Gate B, ask before running
python scripts/run_db_ingestion.py
```

Required `.env` keys actually read by the code: `SUPABASE_URL`, `SUPABASE_KEY`, `UPSTAGE_API_KEY` (Solar chat + embeddings), `VISIT_JEJU_API_KEY`, `KMA_API_KEY`. `.env.example` is stale (missing the last two, lists an unused `OPENAI_API_KEY`).

## Architecture

### LangGraph pipeline (`src/agent/`)

Entry point `agent_runtime` is built in `graph.py` and consumed by `src/main.py`'s SSE endpoint via `agent_runtime.stream(inputs)`. Fixed-path graph with one intent-based conditional branch and one quality-retry loop:

```
intent_router → intent_parser → safety_evaluator → retriever → docent_generator ─┬─(course_recommendation)─→ local_recommender ─┐
                                                                                   └─(otherwise)──────────────────────────────────┼─→ quality_checker ─┬─(passed / loop_count≥3)─→ END
                                                                                                                                                        └─(failed)──→ query_rewriter → retriever (loop, max 3x)
```

- **`intent_router`** (`route_intent_node` → `router.py`'s `route_intent`): classifies the query into `course_info` / `course_recommendation` / `olle_general_info` / `other`. This is the ONLY thing that gates whether `local_recommender` runs — everything else in the graph runs unconditionally regardless of category.
- **`intent_parser`**: separate LLM call that extracts RAG search filters (`hard_constraints.wheelchair_required`, `soft_constraints.max_time_hours/max_distance_km/difficulty`, `vector_query`). Not the same thing as `intent_router` — don't confuse the two.
- **`safety_evaluator`**: merges real KMA weather (`get_current_weather`) with a keyword-based `simulate_weather_by_query` simulator. Only the simulator's **DANGER** tier (태풍/폭우/홍수) is allowed to override the real reading; its WARNING tier (바람/비/강풍 — common in ordinary phrasing) is intentionally excluded from the merge to avoid false-positive safety reroutes. Don't re-add WARNING to the merge without discussing the false-positive tradeoff.
- **`retriever`**: 3-tier fallback (strict RDB filter → relax soft constraints → hard constraints only) before pgvector similarity search via the `match_course_chunks` RPC.
- **`recommend_local_node`**: for each retrieved course, iterates its (crop × administrative_area) combinations and calls Visit Jeju's API per combination — cached in a local dict per request to avoid duplicate calls across courses that share a combination.
- **`quality_checker`** / **`query_rewriter`**: self-critique/rewrite loop, capped at `loop_count` 3.
- State shape lives in `state.py` (`AgentState` TypedDict) — every node returns a partial dict merged into this state.

### SSE streaming (`src/main.py`)

`event_generator` runs the *entire* graph synchronously to completion first (`run_in_executor`), THEN replays the collected per-node events to emit `event: metadata` (once, at the `retriever` step) and `event: token` (tokenized from whichever node — `docent_generator` or `local_recommender` — last set `final_response`). It is not truly incremental token generation; it's a post-hoc typing-effect simulation over an already-complete answer.

### External integrations, all with defensive fallback to mock/SAFE data on failure

- `src/agent/llm_client.py`: Upstage Solar chat completions, used by 4 different nodes (intent parsing, docent generation, quality check, query rewrite).
- `src/agent/weather_client.py`: KMA 기상특보 조회서비스 (`WthrWrnInfoService/getWthrWrnList`, **not** the differently-named service some old code/docs may reference). Region filtering is by `stnId` (station code: 184=제주시, 189=서귀포시 via `_resolve_stn_id`) — the API's warning titles never contain place names, so text-matching on the title for a location is a dead end.
- `src/ingestion/visit_jeju_client.py`: Visit Jeju Open API for local cafe/restaurant recs, falls back to a hardcoded mock DB.
- Both HTTP clients call `truststore.inject_into_ssl()` at import time — required in this dev environment because the local network's TLS-inspecting proxy issues a cert that `certifi`'s bundle doesn't trust but the OS trust store does.

### Data layer (`src/ingestion/database_loader.py`, `supabase/schema.sql`)

Supabase Postgres + pgvector. `courses` table carries `crops`/`administrative_areas` (comma-separated strings, not normalized) used both for RAG context and for driving the local-recommender's per-combination API lookups. `scripts/run_db_ingestion.py` re-chunks course text and re-embeds via Solar (4096-dim) — irreversible/costly, see Gate B below.

## Known environment gotchas (verified this session, not hypothetical)

- **Typing-effect token pacing doesn't work under Python 3.10.9 + the currently pinned uvicorn**: `await asyncio.sleep(0.01)` inside the SSE async generator resolves near-instantly instead of pacing — confirmed to be an upstream uvicorn/asyncio timer-scheduling bug (reproduces even in a bare ASGI app with zero Starlette/FastAPI involved), not application logic. Reported fix upstream is Python 3.11+. Left unfixed by user decision; if revisited, prefer a `time.sleep()`-in-thread + `asyncio.Queue` producer/consumer pacing instead of relying on `asyncio.sleep()` inside the streaming coroutine.
- Windows dev machine: `python`/`python3` may open the Microsoft Store instead of running — disable the App Execution Alias for `python.exe`/`python3.exe` if that happens.

## Harness / safety gates for agents working in this repo

(Adapted from `.agents/AGENTS.md`, the project's agent-rules file for its own AI pair-programming tool — kept in sync here for Claude Code.)

**Gates (Safety Gates)**
- **Gate A (Plan before acting)**: before modifying core logic, assess the blast radius and lay out a plan before starting.
- **Gate B (Irreversible-action control)**: never auto-run, without explicit prior user approval, any of: DB ingestion (`scripts/run_db_ingestion.py`), embedding API calls, `git push`, external network requests, or bulk file deletion.
- **Gate C (Quality gate)**: after a code change, run `pytest` and a syntax/lint check (`ruff check .`) to confirm it actually works before calling it done.
- **Gate D (Secrets protection)**: never let API keys, DB passwords, or other sensitive/personal info leak into source files or the conversation.
- **Gate E (Final report & walkthrough)**: after finishing, transparently brief what changed and how it was verified.

**Domain logic rules** (same source, also summarized in the Architecture section above):
- Hard constraints (e.g. wheelchair access) are never relaxed by the fallback search. Soft constraints (time/distance/difficulty) are relaxed hierarchically instead of returning an empty result, and the relaxation reason must be surfaced in the generated answer.

**File link rules** (when referencing a file in a reply):
- Only link files that actually exist at the given path, with the correct extension — never a made-up or unverified path.
- Use a real `file:///c:/Users/.../path.py`-style URI (forward slashes) or, inside a markdown doc, a path relative to that doc (`./` or `../../`).
- URL-encode spaces (`%20`) or wrap the path in `< >` if it contains spaces/special characters.
- Leave a space after a markdown link's closing `)` before any Korean particle (은/는/이/가/을/를/의/에 etc.) so the link doesn't get mis-parsed.

**Other**
- `docs/` is gitignored — files there won't show up in `git status`; don't assume doc edits are tracked/committed.
