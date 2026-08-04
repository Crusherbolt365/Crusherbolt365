#!/usr/bin/env python3
"""Generate a combined GitHub commit calendar for the profile README."""

from __future__ import annotations

import html
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


GRAPHQL_URL = "https://api.github.com/graphql"
OLD_USER = os.getenv("OLD_GITHUB_USER", "Crusherbolt")
NEW_USER = os.getenv("NEW_GITHUB_USER", "Crusherbolt365")
CUTOVER = date.fromisoformat(os.getenv("ACCOUNT_CUTOVER_DATE", "2026-07-17"))
YEAR = int(os.getenv("GRAPH_YEAR", str(CUTOVER.year)))
OUTPUT = Path(os.getenv("GRAPH_OUTPUT", "assets/combined-commits.svg"))
DATA_OUTPUT = Path(os.getenv("GRAPH_DATA_OUTPUT", "assets/combined-commits.json"))

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      commitContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner }
        firstDays: contributions(first: 100) {
          nodes { occurredAt commitCount }
        }
        lastDays: contributions(last: 100) {
          nodes { occurredAt commitCount }
        }
      }
    }
  }
}
"""


def iso_start(day: date) -> str:
    return f"{day.isoformat()}T00:00:00Z"


def iso_end(day: date) -> str:
    return f"{day.isoformat()}T23:59:59Z"


def github_query(token: str, login: str, start: date, end: date) -> dict:
    body = json.dumps(
        {
            "query": QUERY,
            "variables": {
                "login": login,
                "from": iso_start(start),
                "to": iso_end(end),
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Crusherbolt365-profile-commit-graph",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL request failed ({error.code}): {detail}") from error

    if payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL returned errors: {payload['errors']}")
    user = payload.get("data", {}).get("user")
    if user is None:
        raise RuntimeError(f"GitHub user @{login} was not found")
    return user["contributionsCollection"]


def daily_commits(collection: dict) -> tuple[dict[date, int], int]:
    commits: dict[date, int] = defaultdict(int)
    seen: set[tuple[str, str]] = set()

    for repository_group in collection["commitContributionsByRepository"]:
        repository = repository_group["repository"]["nameWithOwner"]
        nodes = repository_group["firstDays"]["nodes"] + repository_group["lastDays"]["nodes"]
        for node in nodes:
            day = node["occurredAt"][:10]
            key = (repository, day)
            if key in seen:
                continue
            seen.add(key)
            commits[date.fromisoformat(day)] += int(node["commitCount"])

    total = int(collection["totalCommitContributions"])
    calculated = sum(commits.values())
    if calculated != total:
        raise RuntimeError(
            f"Daily commit sum ({calculated}) does not match GitHub total ({total}); "
            "the contribution query may have reached an API limit."
        )
    return dict(commits), total


def color_for(count: int, palette: list[str]) -> str:
    if count <= 0:
        return "#ebedf0"
    if count == 1:
        return palette[0]
    if count <= 3:
        return palette[1]
    if count <= 6:
        return palette[2]
    return palette[3]


def svg_text(x: float, y: float, value: str, css_class: str, anchor: str = "start") -> str:
    return (
        f'<text x="{x}" y="{y}" class="{css_class}" text-anchor="{anchor}">'
        f"{html.escape(value)}</text>"
    )


def build_svg(old_days: dict[date, int], new_days: dict[date, int], old_total: int, new_total: int, today: date) -> str:
    year_start = date(YEAR, 1, 1)
    year_end = date(YEAR, 12, 31)
    grid_start = year_start - timedelta(days=(year_start.weekday() + 1) % 7)
    grid_end = year_end + timedelta(days=(5 - year_end.weekday()) % 7)
    weeks = ((grid_end - grid_start).days // 7) + 1

    cell = 11
    gap = 3
    step = cell + gap
    left = 58
    top = 132
    width = left + weeks * step + 34
    height = 267
    old_palette = ["#99f6e4", "#5eead4", "#2dd4bf", "#0f766e"]
    new_palette = ["#bae6fd", "#7dd3fc", "#38bdf8", "#0369a1"]
    combined_total = old_total + new_total

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">Combined GitHub commit calendar for {YEAR}</title>',
        f'<desc id="desc">{old_total} commits by {html.escape(OLD_USER)} before July 17, {new_total} commits by {html.escape(NEW_USER)} from July 17, {combined_total} commits in total.</desc>',
        "<style>",
        "text{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif}",
        ".title{font-size:18px;font-weight:700;fill:#134e4a}",
        ".subtitle{font-size:12px;fill:#475569}",
        ".stat-label{font-size:10px;font-weight:600;fill:#64748b;text-transform:uppercase;letter-spacing:.5px}",
        ".stat-value{font-size:22px;font-weight:700;fill:#0f172a}",
        ".axis{font-size:10px;fill:#64748b}",
        ".legend{font-size:11px;fill:#334155}",
        ".cutover{font-size:10px;font-weight:600;fill:#7c3aed}",
        "</style>",
        '<rect width="100%" height="100%" rx="12" fill="#ffffff" stroke="#e2e8f0"/>',
        svg_text(24, 31, f"Combined Commit Activity · {YEAR}", "title"),
        svg_text(24, 51, f"@{OLD_USER} before 17 July + @{NEW_USER} from 17 July", "subtitle"),
    ]

    stats = [
        (24, "OLD ID", old_total, "#0f766e"),
        (204, "NEW ID", new_total, "#0369a1"),
        (384, "COMBINED TOTAL", combined_total, "#7c3aed"),
    ]
    for x, label, value, accent in stats:
        parts.append(f'<rect x="{x}" y="66" width="160" height="48" rx="8" fill="#f8fafc" stroke="#e2e8f0"/>')
        parts.append(f'<rect x="{x}" y="66" width="4" height="48" rx="2" fill="{accent}"/>')
        parts.append(svg_text(x + 14, 83, label, "stat-label"))
        parts.append(svg_text(x + 14, 106, str(value), "stat-value"))

    for weekday, label in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        parts.append(svg_text(left - 8, top + weekday * step + 9, label, "axis", "end"))

    for month in range(1, 13):
        first = date(YEAR, month, 1)
        week_index = (first - grid_start).days // 7
        parts.append(svg_text(left + week_index * step, top - 9, first.strftime("%b"), "axis"))

    current = grid_start
    while current <= grid_end:
        week = (current - grid_start).days // 7
        weekday = (current.weekday() + 1) % 7
        x = left + week * step
        y = top + weekday * step

        if current.year != YEAR:
            current += timedelta(days=1)
            continue

        is_future = current > today
        if current < CUTOVER:
            count = old_days.get(current, 0)
            owner = OLD_USER
            fill = color_for(count, old_palette)
        else:
            count = new_days.get(current, 0)
            owner = NEW_USER
            fill = color_for(count, new_palette)

        opacity = "0.35" if is_future else "1"
        label = f"{count} commit{'s' if count != 1 else ''} on {current.isoformat()} · @{owner}"
        parts.append(
            f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{fill}" opacity="{opacity}">'
            f"<title>{html.escape(label)}</title></rect>"
        )
        current += timedelta(days=1)

    cutover_week = (CUTOVER - grid_start).days // 7
    cutover_x = left + cutover_week * step - 2
    parts.append(f'<line x1="{cutover_x}" y1="{top - 3}" x2="{cutover_x}" y2="{top + 7 * step - gap}" stroke="#7c3aed" stroke-width="2" stroke-dasharray="3 3"/>')
    parts.append(svg_text(cutover_x + 4, top + 7 * step + 13, "17 Jul: account switch", "cutover"))

    legend_y = height - 17
    parts.append(f'<rect x="24" y="{legend_y - 9}" width="10" height="10" rx="2" fill="#0f766e"/>')
    parts.append(svg_text(40, legend_y, f"@{OLD_USER}", "legend"))
    parts.append(f'<rect x="154" y="{legend_y - 9}" width="10" height="10" rx="2" fill="#0369a1"/>')
    parts.append(svg_text(170, legend_y, f"@{NEW_USER}", "legend"))
    parts.append(svg_text(width - 24, legend_y, f"Updated {today.isoformat()} · GitHub-counted commits", "axis", "end"))
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> int:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN or GH_TOKEN is required", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc).date()
    year_start = date(YEAR, 1, 1)
    year_end = date(YEAR, 12, 31)
    effective_today = min(max(now, year_start), year_end)
    old_end = min(CUTOVER - timedelta(days=1), year_end, effective_today)
    new_start = max(CUTOVER, year_start)

    old_days: dict[date, int] = {}
    old_total = 0
    if year_start <= old_end:
        old_days, old_total = daily_commits(github_query(token, OLD_USER, year_start, old_end))

    new_days: dict[date, int] = {}
    new_total = 0
    if new_start <= effective_today:
        new_days, new_total = daily_commits(github_query(token, NEW_USER, new_start, effective_today))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(build_svg(old_days, new_days, old_total, new_total, now), encoding="utf-8")
    DATA_OUTPUT.write_text(
        json.dumps(
            {
                "year": YEAR,
                "cutover_date": CUTOVER.isoformat(),
                "updated_at": now.isoformat(),
                "old": {"username": OLD_USER, "commits": old_total},
                "new": {"username": NEW_USER, "commits": new_total},
                "combined_commits": old_total + new_total,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Generated {OUTPUT}: {old_total} + {new_total} = {old_total + new_total} commits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
