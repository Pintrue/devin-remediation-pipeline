"""Scan the target repo with bandit and open a GitHub issue per finding-type.

The scan is the trigger: it runs on a schedule and turns findings into labeled
issues, which remediate.py then picks up. Findings are grouped by bandit rule
(e.g. all weak-MD5 hits) so one Devin session fixes a whole class in one PR
instead of one session per occurrence.
"""
import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from collections import defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB = "https://api.github.com"
REPO = os.environ.get("TARGET_REPO", "Pintrue/superset")
LABEL = os.environ.get("REMEDIATE_LABEL", "devin-remediate")
MAX_NEW = int(os.environ.get("MAX_NEW_ISSUES", "5"))
HEADERS = {
    "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
    "Accept": "application/vnd.github+json",
}
SEVERITY_RANK = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}


def scan_repo():
    """Shallow-clone REPO and run bandit on superset/, returning its findings."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", "--depth", "1", f"https://github.com/{REPO}.git", tmp],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        report = os.path.join(tmp, "bandit.json")
        # bandit exits non-zero when it finds issues, so we don't check=True here.
        subprocess.run(["bandit", "-r", os.path.join(tmp, "superset"), "-f", "json", "-o", report],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        results = json.load(open(report)).get("results", [])
        for r in results:  # drop the temp path so paths are stable across runs
            r["filename"] = r["filename"].split(tmp + "/", 1)[-1]
        return results


def already_filed():
    """Rule ids (fingerprints) we've already filed - our dedupe key."""
    seen, page = set(), 1
    while True:
        r = requests.get(f"{GITHUB}/repos/{REPO}/issues", headers=HEADERS,
                         params={"labels": LABEL, "state": "all", "per_page": 100, "page": page})
        r.raise_for_status()
        for issue in r.json():
            seen |= set(re.findall(r"<!-- fp:(.+?) -->", issue.get("body") or ""))
        if len(r.json()) < 100:
            return seen
        page += 1


def ensure_label():
    requests.post(f"{GITHUB}/repos/{REPO}/labels", headers=HEADERS,
                  json={"name": LABEL, "color": "d73a4a", "description": "Auto-remediate via Devin"})


def file_group_issue(rule, items):
    """One issue per bandit rule, listing every occurrence for a single fix pass."""
    top_severity = max(items, key=lambda i: SEVERITY_RANK.get(i["issue_severity"], 0))["issue_severity"]
    locations = "\n".join(f"- `{i['filename']}`:{i['line_number']}" for i in items)
    title = f"[{rule}] {items[0]['issue_text'][:60]} - {len(items)} occurrence(s)"
    body = (
        f"Found by an automated bandit scan: {len(items)} occurrence(s) of rule `{rule}` "
        f"({top_severity} severity). Apply the same minimal fix to all of them, matching "
        f"existing conventions in each file.\n\n"
        f"{items[0]['issue_text']}.\n\n"
        f"**Locations:**\n{locations}\n\n"
        f"**Verify:** bandit no longer reports `{rule}` anywhere in the repo.\n\n"
        f"<!-- fp:{rule} -->"
    )
    r = requests.post(f"{GITHUB}/repos/{REPO}/issues", headers=HEADERS,
                      json={"title": title, "body": body, "labels": [LABEL]})
    r.raise_for_status()
    print(f"  filed #{r.json()['number']}: {title}")


def run():
    ensure_label()
    findings = [f for f in scan_repo() if f["issue_severity"] in ("MEDIUM", "HIGH")]

    # group by rule so each Devin session fixes a whole class in one PR
    groups = defaultdict(list)
    for f in findings:
        groups[f["test_id"]].append(f)

    # order groups: highest severity first, then most occurrences
    def group_rank(items):
        return (max(SEVERITY_RANK.get(i["issue_severity"], 0) for i in items), len(items))

    ordered = sorted(groups.items(), key=lambda kv: group_rank(kv[1]), reverse=True)
    seen = already_filed()
    new = [(rule, items) for rule, items in ordered if rule not in seen][:MAX_NEW]
    print(f"{len(findings)} findings in {len(groups)} rule-group(s), {len(new)} new to file "
          f"(high severity first)")
    for rule, items in new:
        file_group_issue(rule, items)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=0, help="re-scan every N seconds (0 = once)")
    args = ap.parse_args()
    while True:
        run()
        if not args.interval:
            break
        print(f"sleeping {args.interval}s")
        time.sleep(args.interval)
