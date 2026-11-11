# BlackBox

Ingests logs, alerts, deploy events and chat messages from an incident, normalises them into events,
finds key moments, shows an interactive timeline, and drafts a postmortem where every claim links to
supporting event ids.

## Stack
FastAPI, SQLite, Drain3, optional OpenAI-compatible LLM client (httpx), React 19 + Vite + vis-timeline,
deterministic chaos simulator in demo/ (Docker is optional).

## Rules
- Type hints everywhere; pytest (`python -m pytest backend/tests -q`).
- All timestamps stored in UTC (`timeutil.to_utc`); queries always sort by time.
- LLM output validated with Pydantic; failures fall back to the template postmortem.
- Postmortem sentences must cite event ids; ids are verified against the store; invalid -> "unverified".
- Trigger candidates are correlations; never call one "the root cause".
- Blameless wording: no individual names in the postmortem.

## Layout
backend/app (ingest/, store, patterns, metrics, correlate, pipeline, postmortem, main), backend/tests,
demo/simulate.py, eval/run_eval.py, frontend (dist committed), docs.
