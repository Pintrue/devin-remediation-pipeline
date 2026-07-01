"""Pick up labeled issues, run a Devin session for each, poll, and report.

This is the consumer side of the flow. State is kept in state.json so re-runs
resume polling instead of starting over, and report.md gives an engineering
leader a one-glance view of what the system has done.
"""
import argparse
import json
import os
import re
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
        state[key] = {"title": issue["title"], "issue_url": issue["html_url"],
                      "session_id": s.get("session_id"), "session_url": s.get("url"),
                      "status": "running", "pr_url": None}
        print(f"dispatched #{key} -> {s.get('session_id')}")


def prs_by_issue():
    """Map issue number -> PR url from 'Fixes #N' references in PR bodies.

    GitHub is the source of truth for whether a fix actually landed - more reliable
    than the agent's own structured output, which it does not always populate.
    """
    mapping, page = {}, 1
    while True:
        r = requests.get(f"{GITHUB}/repos/{REPO}/pulls", headers=GH_HEADERS,
                         params={"state": "all", "per_page": 100, "page": page})
        r.raise_for_status()
        prs = r.json()
        for pr in prs:
            for n in re.findall(r"(?:fixes|closes|resolves)\s+#(\d+)", pr.get("body") or "", re.I):
                mapping.setdefault(int(n), {"url": pr["html_url"],
                                            "merged": pr.get("merged_at") is not None})
        if len(prs) < 100:
            return mapping
        page += 1


def poll(state):
    # re-check every pass so an open PR that later merges is picked up
    linked = prs_by_issue()
    for key, task in state.items():
        num = int(key)
        pr = linked.get(num)
        if pr:
            first_time = not task.get("pr_url")
            task["pr_url"] = pr["url"]
            # success only counts once the PR is MERGED; an open PR still awaits review
            task["status"] = "merged" if pr["merged"] else "pr_open"
            if first_time:
                comment(num, f"Devin opened a pull request: {pr['url']}")
            print(f"#{key} -> {task['status']} ({pr['url']})")
            continue
        # no PR yet - refresh the raw session status for visibility
        sid = task.get("session_id")
        if task.get("status") == "running" and sid and not str(sid).startswith("sim"):
            try:
                task["status"] = session_status(sid).get("status_enum", task["status"])
            except Exception:
                pass
        print(f"#{key} -> {task.get('status')}")


STATUS_LABELS = {
    "running": "in progress",
    "blocked": "needs input",
    "finished": "done (no PR)",
    "stopped": "stopped",
    "expired": "expired",
    "pr_open": "PR open (awaiting merge)",
    "merged": "merged (fixed)",
}


def display_status(task):
    s = task.get("status", "running")
    return STATUS_LABELS.get(s, s)


def scan_detail(title):
    """Pull the bandit rule and occurrence count back out of the issue title."""
    rule = re.match(r"\[([^\]]+)\]", title)
    count = re.search(r"(\d+) occurrence", title)
    return (rule.group(1) if rule else "-", count.group(1) if count else "1")


def write_report(state):
    total = len(state)
    merged = sum(1 for t in state.values() if t.get("status") == "merged")
    awaiting = sum(1 for t in state.values() if t.get("status") == "pr_open")
    rate = round(100 * merged / total) if total else 0
    lines = [
        "# Remediation report", "",
        f"- Issues picked up: **{total}**",
        f"- PRs awaiting merge: **{awaiting}**",
        f"- Merged (fixed): **{merged}**",
        f"- Success rate (merged): **{rate}%**", "",
        "| Issue | Rule | Occurrences | Status | Session | PR |",
        "|---|---|---|---|---|---|",
    ]
    for key, t in sorted(state.items(), key=lambda kv: int(kv[0])):
        rule, count = scan_detail(t["title"])
        issue = f"[#{key}]({t['issue_url']})" if t.get("issue_url") else f"#{key}"
        pr = f"[PR]({t['pr_url']})" if t.get("pr_url") else "-"
        lines.append(f"| {issue} | {rule} | {count} | {display_status(t)} | "
                     f"[session]({t['session_url']}) | {pr} |")
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
