#!/usr/bin/env python3
"""Generate the current-year GitHub contribution calendar across two profiles."""

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
YEAR_OVERRIDE = os.getenv("GRAPH_YEAR")
OUTPUT = Path(os.getenv("GRAPH_OUTPUT", "assets/combined-commits.svg"))
DATA_OUTPUT = Path(os.getenv("GRAPH_DATA_OUTPUT", "assets/combined-commits.json"))

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
      totalCommitContributions
      restrictedContributionsCount
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
            "User-Agent": "Crusherbolt365-profile-contribution-graph",
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


def empty_period() -> dict:
    return {
        "days": {},
        "contributions": 0,
        "public_commits": 0,
        "restricted": 0,
    }


def parse_period(collection: dict) -> dict:
    days: dict[date, int] = defaultdict(int)
    calendar = collection["contributionCalendar"]
    for week in calendar["weeks"]:
        for contribution_day in week["contributionDays"]:
            day = date.fromisoformat(contribution_day["date"])
            days[day] += int(contribution_day["contributionCount"])

    total = int(calendar["totalContributions"])
    calculated = sum(days.values())
    if calculated != total:
        raise RuntimeError(
            f"Daily contribution sum ({calculated}) does not match GitHub total ({total})"
        )

    return {
        "days": dict(days),
        "contributions": total,
        "public_commits": int(collection["totalCommitContributions"]),
        "restricted": int(collection["restrictedContributionsCount"]),
    }


def query_period(token: str, login: str, start: date, end: date) -> dict:
    if start > end:
        return empty_period()
    return parse_period(github_query(token, login, start, end))


def merge_days(*groups: dict[date, int]) -> dict[date, int]:
    merged: dict[date, int] = defaultdict(int)
    for group in groups:
        for day, count in group.items():
            merged[day] += count
    return dict(merged)


def collect_year(token: str, year: int, today: date) -> dict:
    year_start = date(year, 1, 1)
    year_end = min(date(year, 12, 31), today)
    if year_start > today:
        return {
            "year": year,
            "old": empty_period(),
            "new": empty_period(),
            "days": {},
            "contributions": 0,
            "public_commits": 0,
            "restricted": 0,
        }

    old_end = min(year_end, CUTOVER - timedelta(days=1))
    old = query_period(token, OLD_USER, year_start, old_end)

    new_start = max(year_start, CUTOVER)
    new = query_period(token, NEW_USER, new_start, year_end)

    return {
        "year": year,
        "old": old,
        "new": new,
        "days": merge_days(old["days"], new["days"]),
        "contributions": old["contributions"] + new["contributions"],
        "public_commits": old["public_commits"] + new["public_commits"],
        "restricted": old["restricted"] + new["restricted"],
    }


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


def year_grid(year: int) -> tuple[date, date, int]:
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    grid_start = year_start - timedelta(days=(year_start.weekday() + 1) % 7)
    grid_end = year_end + timedelta(days=(5 - year_end.weekday()) % 7)
    weeks = ((grid_end - grid_start).days // 7) + 1
    return grid_start, grid_end, weeks


def public_year(year_data: dict) -> dict:
    return {
        "year": year_data["year"],
        "contributions": year_data["contributions"],
        "public_commits": year_data["public_commits"],
        "restricted_contributions": year_data["restricted"],
        "old": {
            "username": OLD_USER,
            "contributions": year_data["old"]["contributions"],
            "public_commits": year_data["old"]["public_commits"],
            "restricted_contributions": year_data["old"]["restricted"],
        },
        "new": {
            "username": NEW_USER,
            "contributions": year_data["new"]["contributions"],
            "public_commits": year_data["new"]["public_commits"],
            "restricted_contributions": year_data["new"]["restricted"],
        },
    }


def build_svg(years: list[dict], today: date) -> str:
    cell = 10
    gap = 2
    step = cell + gap
    left = 86
    top = 150
    row_height = 112
    width = 980
    height = top + len(years) * row_height + 52
    old_palette = ["#99f6e4", "#5eead4", "#2dd4bf", "#0f766e"]
    new_palette = ["#bae6fd", "#7dd3fc", "#38bdf8", "#0369a1"]

    old_total = sum(item["old"]["contributions"] for item in years)
    new_total = sum(item["new"]["contributions"] for item in years)
    contribution_total = old_total + new_total
    public_commits = sum(item["public_commits"] for item in years)
    restricted_total = sum(item["restricted"] for item in years)
    year_span = (
        str(years[0]["year"])
        if len(years) == 1
        else f"{years[0]['year']}-{years[-1]['year']}"
    )

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">Combined GitHub contribution calendar for {year_span}</title>',
        f'<desc id="desc">{contribution_total} GitHub-counted contributions across @{html.escape(OLD_USER)} and @{html.escape(NEW_USER)}, including {public_commits} public commit contributions.</desc>',
        "<style>",
        "text{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif}",
        ".title{font-size:18px;font-weight:700;fill:#134e4a}",
        ".subtitle{font-size:12px;fill:#475569}",
        ".stat-label{font-size:9px;font-weight:600;fill:#64748b;letter-spacing:.5px}",
        ".stat-value{font-size:22px;font-weight:700;fill:#0f172a}",
        ".year{font-size:14px;font-weight:700;fill:#0f172a}",
        ".axis{font-size:9px;fill:#64748b}",
        ".summary{font-size:11px;font-weight:600;fill:#334155}",
        ".detail{font-size:9px;fill:#64748b}",
        ".legend{font-size:11px;fill:#334155}",
        ".cutover{font-size:9px;font-weight:600;fill:#7c3aed}",
        "</style>",
        '<rect width="100%" height="100%" rx="12" fill="#ffffff" stroke="#e2e8f0"/>',
        svg_text(24, 31, f"Combined GitHub Activity / {year_span}", "title"),
        svg_text(24, 51, f"@{OLD_USER} through 16 July 2026 + @{NEW_USER} from 17 July 2026", "subtitle"),
    ]

    stats = [
        (24, "OLD ID CONTRIBUTIONS", old_total, "#0f766e"),
        (258, "NEW ID CONTRIBUTIONS", new_total, "#0369a1"),
        (492, "COMBINED CONTRIBUTIONS", contribution_total, "#7c3aed"),
        (726, "PUBLIC COMMIT CONTRIBUTIONS", public_commits, "#334155"),
    ]
    for x, label, value, accent in stats:
        parts.append(f'<rect x="{x}" y="66" width="218" height="50" rx="8" fill="#f8fafc" stroke="#e2e8f0"/>')
        parts.append(f'<rect x="{x}" y="66" width="4" height="50" rx="2" fill="{accent}"/>')
        parts.append(svg_text(x + 14, 84, label, "stat-label"))
        parts.append(svg_text(x + 14, 108, str(value), "stat-value"))

    for row_index, year_data in enumerate(years):
        year = year_data["year"]
        grid_start, grid_end, _ = year_grid(year)
        row_top = top + row_index * row_height
        grid_top = row_top + 23
        parts.append(svg_text(24, row_top + 13, str(year), "year"))

        for month in range(1, 13):
            first = date(year, month, 1)
            week_index = (first - grid_start).days // 7
            parts.append(svg_text(left + week_index * step, row_top + 12, first.strftime("%b"), "axis"))

        for weekday, label in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
            parts.append(svg_text(left - 8, grid_top + weekday * step + 8, label, "axis", "end"))

        current = grid_start
        while current <= grid_end:
            if current.year != year:
                current += timedelta(days=1)
                continue

            week = (current - grid_start).days // 7
            weekday = (current.weekday() + 1) % 7
            x = left + week * step
            y = grid_top + weekday * step
            count = year_data["days"].get(current, 0)
            owner = OLD_USER if current < CUTOVER else NEW_USER
            palette = old_palette if owner == OLD_USER else new_palette
            fill = color_for(count, palette)
            opacity = "0.35" if current > today else "1"
            label = f"{count} contribution{'s' if count != 1 else ''} on {current.isoformat()} / @{owner}"
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{fill}" opacity="{opacity}">'
                f"<title>{html.escape(label)}</title></rect>"
            )
            current += timedelta(days=1)

        summary_x = 748
        parts.append(svg_text(summary_x, row_top + 39, f"{year_data['contributions']} contributions", "summary"))
        parts.append(svg_text(summary_x, row_top + 57, f"{year_data['public_commits']} public commits", "detail"))
        parts.append(svg_text(summary_x, row_top + 73, f"{year_data['restricted']} restricted/private", "detail"))

        if year == CUTOVER.year:
            cutover_week = (CUTOVER - grid_start).days // 7
            cutover_x = left + cutover_week * step - 1
            parts.append(
                f'<line x1="{cutover_x}" y1="{grid_top - 2}" x2="{cutover_x}" y2="{grid_top + 7 * step - gap}" stroke="#7c3aed" stroke-width="2" stroke-dasharray="3 3"/>'
            )
            parts.append(svg_text(cutover_x + 4, grid_top + 7 * step + 10, "account switch", "cutover"))

    legend_y = height - 19
    parts.append(f'<rect x="24" y="{legend_y - 9}" width="10" height="10" rx="2" fill="#0f766e"/>')
    parts.append(svg_text(40, legend_y, f"@{OLD_USER}", "legend"))
    parts.append(f'<rect x="154" y="{legend_y - 9}" width="10" height="10" rx="2" fill="#0369a1"/>')
    parts.append(svg_text(170, legend_y, f"@{NEW_USER}", "legend"))
    parts.append(svg_text(292, legend_y, f"Restricted/private total: {restricted_total}", "detail"))
    parts.append(svg_text(width - 24, legend_y, f"Updated {today.isoformat()} / GitHub contribution calendar", "axis", "end"))
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> int:
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN or GH_TOKEN is required", file=sys.stderr)
        return 2

    today = datetime.now(timezone.utc).date()
    graph_year = int(YEAR_OVERRIDE) if YEAR_OVERRIDE else today.year
    if graph_year > today.year:
        print("GRAPH_YEAR must not be in the future", file=sys.stderr)
        return 2

    years = [collect_year(token, graph_year, today)]
    old_total = sum(item["old"]["contributions"] for item in years)
    new_total = sum(item["new"]["contributions"] for item in years)
    public_commits = sum(item["public_commits"] for item in years)
    restricted_total = sum(item["restricted"] for item in years)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(build_svg(years, today), encoding="utf-8")
    DATA_OUTPUT.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "metric": "github_contributions",
                "start_year": graph_year,
                "end_year": graph_year,
                "cutover_date": CUTOVER.isoformat(),
                "updated_at": today.isoformat(),
                "old": {"username": OLD_USER, "contributions": old_total},
                "new": {"username": NEW_USER, "contributions": new_total},
                "totals": {
                    "contributions": old_total + new_total,
                    "public_commit_contributions": public_commits,
                    "restricted_contributions": restricted_total,
                },
                "years": [public_year(item) for item in years],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Generated {OUTPUT}: {old_total} old + {new_total} new = "
        f"{old_total + new_total} contributions; {public_commits} public commits"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
