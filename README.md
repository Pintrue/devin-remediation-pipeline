# Autonomous remediation pipeline

A small event-driven system that finds code-quality / security issues in a repo
and has [Devin](https://devin.ai) fix them, end to end:

```
  scan_and_file.py                 remediate.py
  ----------------                 ------------
  bandit scans the repo            reads the open labeled issues
  files one issue per     ──────►  starts a Devin session per issue
  bandit rule, listing             (fixes every listed occurrence in one PR)
  all occurrences (label           polls each session to completion
  "devin-remediate")               comments the PR back, writes report.md
```

The scheduled scan is the trigger; GitHub issues are the event queue; Devin is
the worker; `report.md` is the observability.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env     # then fill in DEVIN_API_KEY and GITHUB_TOKEN
```

`.env` values:

| Var | What |
|---|---|
| `DEVIN_API_KEY` | Devin API key (`cog_...`), from app.devin.ai/settings/api-keys |
| `GITHUB_TOKEN` | GitHub token with issues:write + contents:read on the fork |
| `TARGET_REPO` | repo to remediate, e.g. `Pintrue/superset` |
| `REMEDIATE_LABEL` | issue label the pipeline watches (default `devin-remediate`) |

Devin's GitHub integration must have access to `TARGET_REPO` so it can open PRs.

## Run

```bash
python scan_and_file.py        # real bandit scan -> files issues (capped at 5)
python remediate.py            # dispatch + poll Devin; writes report.md
```

## Docker

```bash
docker compose up                              # scanner + remediate
docker compose run --rm scanner python scan_and_file.py   # one-shot scan
```

## Files

- `scan_and_file.py` - scanner / event producer
- `remediate.py` - session orchestrator + reporter / event consumer
- `devin.py` - the initial API spike (create a single session)
- `state.json`, `report.md` - generated at runtime
