# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

제주올레 도슨트 (Jeju Olle Docent) — a LangGraph-based RAG agent that turns a free-form natural-language query (visit timing/crop/region/theme) into a structured **B2B tourism-product proposal document** (기획서) for Jeju Olle trail courses: a spec/Market-Insight section, a timeline table with crop/harvest-season narration, a local-partnership ideas section, a climate-risk/Plan-B section, and a trust-tagging footer. Backed by Supabase (Postgres + pgvector), served over FastAPI SSE streaming. (The project pivoted from an earlier B2C chatbot design — see `docs/retrospectives/` for that history — so older code comments/docs may still describe the B2C framing; this file reflects the current B2B state.)

## Commands

```powershell
# Setup (Windows PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run the API server
python -m uvicorn src.main:app --host 0.0.0.0 --port 8000
# -> POST /api/v1/report/generate {query: str} (SSE: event: node_progress / report / end / error),
#    GET /health, GET /docs (Swagger UI does NOT render SSE live — see Gotchas)

# Tests
python -m pytest                          # full suite
python -m pytest tests/test_router.py -v  # single file
python -m pytest tests/test_router.py::test_route_intent_course_info -v  # single test

# Lint / format
ruff check .
ruff format .

# Re-ingest DB — all three are Gate B (irreversible: embedding API calls + DB writes), ask before running
python scripts/run_db_ingestion.py               # courses + course_chunks + sub_segments + wheelchair/safety
python scripts/run_culture_db_ingestion.py        # culture_crop_knowledge (crop/culture docs, title-keyed upsert)
python scripts/run_visitor_analytics_ingestion.py # visitor_analytics (PDF reports, year_month+region_dong upsert)
```

Required `.env` keys actually read by the code: `SUPABASE_URL`, `SUPABASE_KEY`, `UPSTAGE_API_KEY` (Solar chat + embeddings), `VISIT_JEJU_API_KEY`. There is no KMA/weather API key — `weather_client.py` is 100% local (static seasonal table + keyword simulator), not a real KMA call; don't add a `KMA_API_KEY` requirement back without actually wiring a real weather call. `.env.example` is currently accurate (these four keys, nothing else).

## Architecture

### LangGraph pipeline (`src/agent/`)

Entry point `agent_runtime` is built in `graph.py` and consumed by `src/main.py`'s SSE endpoint via `agent_runtime.stream(inputs)`. Fixed-path graph (9 nodes) with one intent-based conditional branch and one quality-retry loop:

```
intent_router → intent_parser → market_location_resolver → safety_evaluator → retriever → docent_generator ─┬─(course_recommendation)─→ local_recommender ─┐
                                                                                                               └─(otherwise)──────────────────────────────────┼─→ quality_checker ─┬─(passed / loop_count≥3)─→ END
                                                                                                                                                                                    └─(failed)──→ query_rewriter → retriever (loop, max 3x)
```

- **`intent_router`** (`route_intent_node` → `router.py`'s `route_intent`): classifies the query into `course_info` / `course_recommendation` / `olle_general_info` / `other`. This is the ONLY thing that gates whether `local_recommender` runs — everything else in the graph runs unconditionally regardless of category.
- **`intent_parser`**: separate LLM call that extracts RAG search filters into `B2BQueryParams` (`hard_constraints.wheelchair_required`, `vector_query`, `target_month`/`season`, `key_item_or_crop`, `preferred_location`, `concept_theme`, `target_audience`, `include_market_insights`). There is no `soft_constraints` (time/distance/difficulty) any more — that B2C-era relaxation mechanism was removed; the RDB filter is now hard-constraints-only, single pass, no fallback tiers. When the query names a region by a **visitor-statistic condition** instead of a place name (e.g. "외국인 관광객이 많았던 지역"), `preferred_location` is left null and `market_location_query` (metric/year/month/direction) is filled instead. Not the same thing as `intent_router` — don't confuse the two.
- **`market_location_resolver`**: only runs when `market_location_query.metric` is set. Resolves the natural-language statistic condition to an actual region by querying `visitor_analytics` (via the Supabase query builder — `metric` is restricted to the `MarketLocationMetric` enum, so no SQL-injection path exists), restricted to administrative dongs that Olle courses actually pass through (`_get_olle_relevant_admin_dongs`, using `data/jeju_districts.csv` + a hardcoded 법정동→행정동 map for the urban "동" areas the CSV doesn't disambiguate — see `_LEGAL_DONG_TO_ADMIN_DONG` in `nodes.py`). Writes the resolved region into `b2b_params.preferred_location` (and `market_location_resolution` for citing the reason later) so every downstream node treats it exactly like a directly-named region.
- **`safety_evaluator`**: **no real weather API involved** — combines a static per-month seasonal-climate table (`get_seasonal_climate_note`) with a keyword-based `simulate_weather_by_query` simulator. Only the simulator's **DANGER** tier (태풍/폭우/홍수) is allowed to override the seasonal reading; its WARNING tier (바람/비/강풍 — common in ordinary phrasing) is intentionally excluded from the merge to avoid false-positive safety reroutes. Don't re-add WARNING to the merge without discussing the false-positive tradeoff. (There is no `get_current_weather` function and no `KMA_API_KEY` — don't assume a live KMA call is happening here.)
- **`retriever`**: single-pass RDB filter on hard constraints only (no relaxation tiers — `fallback_applied`/`fallback_reason` are always `False`/`None` now, kept as dead state fields), then pgvector similarity search via `match_course_chunks`. Also independently queries `culture_crop_knowledge` (RPC `match_culture_chunks`, falling back to local JSON keyword search only if that RPC fails/is empty) and `visitor_analytics` (Market Insight, keyed off `preferred_location`/`target_month` — which may have been auto-filled by `market_location_resolver`).
- **`docent_generator`**: for `culture_crop_knowledge` rows that carry `target_crop`/`region_tag`/`active_months`/`season_stage` (currently only 7 of the ~26 crop/culture docs — see Data layer below), it compares `active_months` against the visit month and instructs the LLM to describe the crop as in-season or not-in-season accordingly, instead of always describing it as "currently in full bloom/harvest."
- **`recommend_local_node`**: for each retrieved course, iterates its (crop × administrative_area) combinations and calls Visit Jeju's API per combination — cached in a local dict per request to avoid duplicate calls across courses that share a combination.
- **`quality_checker`** / **`query_rewriter`**: self-critique/rewrite loop, capped at `loop_count` 3. `query_rewriter` loops back straight to `retriever`, not through `market_location_resolver` — a resolved region persists across retries.
- State shape lives in `state.py` (`AgentState` TypedDict) — every node returns a partial dict merged into this state.

### SSE streaming (`src/main.py`)

`report_event_generator` (endpoint: `POST /api/v1/report/generate`) runs the graph in a background thread (`loop.run_in_executor`) and streams events **live** as each node actually completes, via an `asyncio.Queue` the background thread pushes into with `call_soon_threadsafe` — it does not wait for the whole graph to finish first. Per node completion it emits `event: node_progress` (`{node, label}`, label text from `NODE_PROGRESS_LABELS`); once some node has set `final_response` (usually `docent_generator`, possibly overwritten later by `local_recommender`), the queue drains and it emits one `event: report` (the complete Markdown 기획서, not token-by-token) followed by `event: end`. On exception, `event: error` instead. There is no typing-effect/token-pacing simulation — that belonged to an earlier, now-deleted `/chat/stream` endpoint.

### External integrations, all with defensive fallback to mock/SAFE data on failure

- `src/agent/llm_client.py`: Upstage Solar chat completions, used by 4 different nodes (intent parsing, docent generation, quality check, query rewrite).
- `src/agent/weather_client.py`: KMA 기상특보 조회서비스 (`WthrWrnInfoService/getWthrWrnList`, **not** the differently-named service some old code/docs may reference). Region filtering is by `stnId` (station code: 184=제주시, 189=서귀포시 via `_resolve_stn_id`) — the API's warning titles never contain place names, so text-matching on the title for a location is a dead end.
- `src/ingestion/visit_jeju_client.py`: Visit Jeju Open API for local cafe/restaurant recs, falls back to a hardcoded mock DB.
- Both HTTP clients call `truststore.inject_into_ssl()` at import time — required in this dev environment because the local network's TLS-inspecting proxy issues a cert that `certifi`'s bundle doesn't trust but the OS trust store does.

### Data layer (`src/ingestion/database_loader.py`, `supabase/schema.sql`)

Supabase Postgres + pgvector. `courses` table carries `crops`/`administrative_areas` (comma-separated strings, not normalized) used both for RAG context and for driving the local-recommender's per-combination API lookups. `scripts/run_db_ingestion.py` re-chunks course text and re-embeds via Solar (4096-dim) — irreversible/costly, see Gate B below.

Two more tables were added later and are now populated: `culture_crop_knowledge` (crop/culture narration docs — see `schema.sql` sections 8/8-1; `crop_name` is legacy, `target_crop`/`region_tag`/`active_months`/`season_stage` are the newer columns, only populated for the 7 docs sourced from `data/culture_knowledge/crop_seven_docs.json`) and `visitor_analytics` (Jeju Tourism Organization monthly visitor-pattern PDF data, section 9 — `region_dong` is 행정동/읍/면 level, a different administrative tier from `courses.administrative_areas`' 법정리/법정동 level; see `_LEGAL_DONG_TO_ADMIN_DONG` in `nodes.py` for the mapping between the two). Both tables' DDL sat unexecuted in `schema.sql` for a while before actually being run in the Supabase SQL editor — if a fresh Supabase project ever throws `relation ... does not exist` for either, that's why; there's no automated way to apply `schema.sql`, it must be run manually in the SQL editor.

## Known environment gotchas

- Windows dev machine: `python`/`python3` may open the Microsoft Store instead of running — disable the App Execution Alias for `python.exe`/`python3.exe` if that happens.
- Supabase table/RPC DDL in `supabase/schema.sql` is never auto-applied — there is no raw-SQL execution channel via the REST client, so each new section (e.g. the 8-1 `culture_crop_knowledge` ALTER, or the `visitor_analytics` DDL) must be run by hand in the Supabase SQL editor before the corresponding ingestion script will work. Don't assume a table exists just because its `CREATE TABLE` has been sitting in this file for a while.

## Harness / safety gates for agents working in this repo

(Adapted from `.agents/AGENTS.md`, the project's agent-rules file for its own AI pair-programming tool — kept in sync here for Claude Code.)

**Gates (Safety Gates)**
- **Gate A (Plan before acting)**: before modifying core logic, assess the blast radius and lay out a plan before starting.
- **Gate B (Irreversible-action control)**: never auto-run, without explicit prior user approval, any of: DB ingestion (`scripts/run_db_ingestion.py`, `scripts/run_culture_db_ingestion.py`, `scripts/run_visitor_analytics_ingestion.py`), embedding API calls, `git push`, external network requests, or bulk file deletion. Also covers running the ALTER/CREATE statements in `supabase/schema.sql` against the live Supabase project (must be done manually in the SQL editor regardless — see gotchas above — but still confirm with the user before telling them to run a specific block).
- **Gate C (Quality gate)**: after a code change, run `pytest` and a syntax/lint check (`ruff check .`) to confirm it actually works before calling it done.
- **Gate D (Secrets protection)**: never let API keys, DB passwords, or other sensitive/personal info leak into source files or the conversation.
- **Gate E (Final report & walkthrough)**: after finishing, transparently brief what changed and how it was verified.

**Domain logic rules** (same source, also summarized in the Architecture section above):
- Hard constraints (e.g. wheelchair access) are never relaxed. There is no soft-constraint relaxation any more (that B2C-era mechanism — time/distance/difficulty fallback tiers — was intentionally removed; `fallback_applied`/`fallback_reason` remain in `AgentState` as dead fields, always `False`/`None`). Don't reintroduce soft-constraint relaxation without discussing it first — it was a deliberate cleanup, not an oversight.
- `market_location_resolver` must only resolve a region to one that Olle courses actually pass through (`_get_olle_relevant_admin_dongs` in `nodes.py`) — never let it pick a statistically-top region with no course coverage (e.g. downtown Jeju-si dongs like 연동/노형동) just because it ranks highest on some `visitor_analytics` metric.

**File link rules** (when referencing a file in a reply):
- Only link files that actually exist at the given path, with the correct extension — never a made-up or unverified path.
- Use a real `file:///c:/Users/.../path.py`-style URI (forward slashes) or, inside a markdown doc, a path relative to that doc (`./` or `../../`).
- URL-encode spaces (`%20`) or wrap the path in `< >` if it contains spaces/special characters.
- Leave a space after a markdown link's closing `)` before any Korean particle (은/는/이/가/을/를/의/에 etc.) so the link doesn't get mis-parsed.

**Other**
- `docs/` is gitignored — files there won't show up in `git status`; don't assume doc edits are tracked/committed.
