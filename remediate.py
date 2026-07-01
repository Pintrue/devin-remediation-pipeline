"""Pick up labeled issues, run a Devin session for each, poll, and report.

This is the consumer side of the flow. State is kept in state.json so re-runs
resume polling instead of starting over, and report.md gives an engineering
leader a one-glance view of what the system has done.
"""
import argparse
import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB = "https://api.github.com"
DEVIN = "https://api.devin.ai/v1"
REPO = os.environ.get("TARGET_REPO", "Pintrue/superset")
LABEL = os.environ.get("REMEDIATE_LABEL", "devin-remediate")
GH_HEADERS = {"Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
              "Accept": "application/vnd.github+json"}
DEVIN_HEADERS = {"Authorization": f"Bearer {os.environ.get('DEVIN_API_KEY', '')}"}
STATE_FILE = "state.json"
DONE = {"finished", "blocked", "stopped", "expired"}

PROMPT = """You are fixing one issue in the GitHub repo {repo}.

Issue #{num}: {title}

{body}

Clone {repo}, apply the minimal fix to EVERY occurrence listed in the issue, matching the
existing code style in each file, and open a single pull request whose description contains "Fixes #{num}".

Keep your structured output updated as a JSON object with keys: pr_url (string or null),
status (one of: fixed, blocked, no_change_needed), summary (string). Update it as soon as
you open the pull request.
"""


def load_state():
    return json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}


def save_state(state):
    json.dump(state, open(STATE_FILE, "w"), indent=2)


def open_issues():
    r = requests.get(f"{GITHUB}/repos/{REPO}/issues", headers=GH_HEADERS,
                     params={"labels": LABEL, "state": "open", "per_page": 100})
    r.raise_for_status()
    return [i for i in r.json() if "pull_request" not in i]


def start_session(issue):
    prompt = PROMPT.format(repo=REPO, num=issue["number"], title=issue["title"], body=issue["body"])
    session_title = f"Remediate #{issue['number']}: {issue['title']}"
    r = requests.post(f"{DEVIN}/sessions", headers=DEVIN_HEADERS, timeout=30,
                      json={"prompt": prompt, "idempotent": True, "title": session_title})
    r.raise_for_status()
    return r.json()


def session_status(session_id):
    r = requests.get(f"{DEVIN}/sessions/{session_id}", headers=DEVIN_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def comment(num, text):
    requests.post(f"{GITHUB}/repos/{REPO}/issues/{num}/comments", headers=GH_HEADERS, json={"body": text})


def dispatch(state):
    for issue in open_issues():
        key = str(issue["number"])
        if key in state:
            continue
        s = start_session(issue)
        state[key] = {"title": issue["title"], "session_id": s.get("session_id"),
                      "session_url": s.get("url"), "status": "running", "pr_url": None}
        print(f"dispatched #{key} -> {s.get('session_id')}")


def poll(state):
    for key, task in state.items():
        if task["status"] != "running":
            continue
        s = session_status(task["session_id"])
        status = s.get("status_enum", "running")
        if status not in DONE:
            continue
        out = s.get("structured_output") or {}
        if isinstance(out, str):
            try:
                out = json.loads(out)
            except json.JSONDecodeError:
                out = {}
        task["status"], task["pr_url"] = status, out.get("pr_url")
        if task["pr_url"]:
            comment(int(key), f"Devin opened a pull request: {task['pr_url']}")
        print(f"#{key} -> {status} (pr={task['pr_url']})")


def write_report(state):
    total = len(state)
    finished = sum(1 for t in state.values() if t["status"] == "finished")
    prs = sum(1 for t in state.values() if t.get("pr_url"))
    rate = round(100 * finished / total) if total else 0
    lines = [
        "# Remediation report", "",
        f"- Issues picked up: **{total}**",
        f"- Completed: **{finished}**",
        f"- PRs opened: **{prs}**",
        f"- Success rate: **{rate}%**", "",
        "| Issue | Status | Session | PR |", "|---|---|---|---|",
    ]
    for key, t in sorted(state.items(), key=lambda kv: int(kv[0])):
        pr = f"[PR]({t['pr_url']})" if t.get("pr_url") else "-"
        lines.append(f"| #{key} {t['title'][:40]} | {t['status']} | [session]({t['session_url']}) | {pr} |")
    open("report.md", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--interval", type=int, default=20, help="seconds between polls")
    args = ap.parse_args()
    while True:
        state = load_state()
        dispatch(state)
        poll(state)
        save_state(state)
        write_report(state)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
