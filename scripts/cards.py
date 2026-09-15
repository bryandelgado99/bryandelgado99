#!/usr/bin/env python3
"""
cards.py - render GitHub stat and language cards as SVGs. Stdlib only.

Stand-in for github-readme-stats / github-profile-trophy / streak-stats, which
are shared public instances that go down (503), run out of quota (402) or time
out. These are files in your own repo, so they render as long as GitHub renders.

    python scripts/cards.py --user bryandelgado99 --out assets

Writes <out>/card-stats-{dark,light}.svg and <out>/card-langs-{dark,light}.svg.

Stars, repos, followers and the language breakdown come from the live REST API
on every run. A token in $GITHUB_TOKEN additionally unlocks the contribution and
streak tiles (they need the GraphQL API); without one the stats card still
renders, just without those three tiles.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

UA = {"User-Agent": "cards.py"}

THEMES = {
    "dark": {
        "bg": "#090506", "border": "#2b1a1d", "title": "#f5efed",
        "text": "#b8a5a5", "muted": "#8a7373", "value": "#f5efed",
        "accent": "#e11d48", "bar_bg": "#241619", "other": "#3a2427",
    },
    "light": {
        "bg": "#ffffff", "border": "#e6dbd7", "title": "#160b0d",
        "text": "#5d4a4a", "muted": "#8a7373", "value": "#160b0d",
        "accent": "#dc2626", "bar_bg": "#f0e9e6", "other": "#d9cec9",
    },
}

# GitHub linguist colours
LANG_COLOR = {
    "Dart": "#00B4AB", "Kotlin": "#A97BFF", "Java": "#b07219",
    "JavaScript": "#f1e05a", "TypeScript": "#3178c6", "Python": "#3572A5",
    "HTML": "#e34c26", "CSS": "#563d7c", "SCSS": "#c6538c",
    "Vue": "#41b883", "Astro": "#ff5a03", "Svelte": "#ff3e00",
    "C": "#555555", "C++": "#f34b7d", "C#": "#178600",
    "Go": "#00ADD8", "Rust": "#dea584", "Ruby": "#701516", "PHP": "#4F5D95",
    "Swift": "#F05138", "Shell": "#89e051", "Makefile": "#427819",
    "Jupyter Notebook": "#DA5B0B", "Blade": "#f7523f", "EJS": "#a91e50",
    "Objective-C": "#438eff", "Lua": "#000080", "R": "#198CE7",
    "GDScript": "#355570", "Twig": "#c1d026",
}

FONT = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"


# --------------------------------------------------------------------------- #
# api
# --------------------------------------------------------------------------- #


def rest(path: str, token: str | None):
    req = urllib.request.Request("https://api.github.com" + path, headers=dict(UA))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            sys.exit("GitHub API rate limit hit. Set GITHUB_TOKEN and retry.")
        raise


def graphql(query: str, variables: dict, token: str):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body,
        headers={**UA, "Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


CONTRIB_QUERY = """
query($login:String!){
  user(login:$login){
    contributionsCollection{
      contributionCalendar{
        totalContributions
        weeks{ contributionDays{ date contributionCount } }
      }
    }
  }
}
"""


def fetch_contributions(user: str, token: str | None):
    """Return (total, current_streak, longest_streak) or None without a token."""
    if not token:
        return None
    try:
        data = graphql(CONTRIB_QUERY, {"login": user}, token)
    except urllib.error.HTTPError as e:
        print(f"  contributions unavailable (HTTP {e.code})", file=sys.stderr)
        return None
    if data.get("errors"):
        print(f"  contributions unavailable: {data['errors'][0].get('message')}",
              file=sys.stderr)
        return None

    cal = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    days = [(dt.date.fromisoformat(d["date"]), d["contributionCount"])
            for w in cal["weeks"] for d in w["contributionDays"]]
    days.sort()

    longest = run = 0
    for _, c in days:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)

    # Today counts only if it already has activity; an empty today does not
    # break a streak that was alive yesterday.
    current = 0
    for date, c in reversed(days):
        if c > 0:
            current += 1
        elif date != days[-1][0]:
            break
    return cal["totalContributions"], current, longest


def fetch_languages(repos, token: str | None):
    """Sum bytes per language across the given repos."""
    totals: dict[str, int] = {}
    for r in repos:
        try:
            langs = rest(f"/repos/{r['full_name']}/languages", token)
        except urllib.error.HTTPError as e:
            print(f"  !! languages for {r['full_name']} skipped (HTTP {e.code})",
                  file=sys.stderr)
            continue
        if not isinstance(langs, dict):
            continue
        for name, count in langs.items():
            totals[name] = totals.get(name, 0) + count
    return totals


# --------------------------------------------------------------------------- #
# svg helpers
# --------------------------------------------------------------------------- #


def esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def text_width(s: str, size: float) -> float:
    return len(s) * size * 0.6


def human(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.1f} kB"
    return f"{n} B"


def frame(w, h, c, body, label):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" role="img" aria-label="{esc(label)}" '
        f'font-family="{FONT}">'
        f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="12" '
        f'fill="{c["bg"]}" stroke="{c["border"]}"/>'
        f"{body}</svg>"
    )


def header(w, pad, c, title, note):
    return [
        f'<text x="{pad}" y="{pad + 15}" font-size="15" font-weight="700" '
        f'fill="{c["title"]}">{esc(title)}</text>',
        f'<text x="{w - pad}" y="{pad + 15}" font-size="11" text-anchor="end" '
        f'fill="{c["muted"]}">{esc(note)}</text>',
        f'<line x1="{pad}" y1="{pad + 28}" x2="{w - pad}" y2="{pad + 28}" '
        f'stroke="{c["border"]}"/>',
    ]


# --------------------------------------------------------------------------- #
# cards
# --------------------------------------------------------------------------- #


def render_stats(user, tiles, theme):
    c = THEMES[theme]
    W, pad, cols, rh = 480, 22, 3, 48
    rows = (len(tiles) + cols - 1) // cols
    H = pad + 52 + (rows - 1) * rh + 18 + pad
    tw = (W - 2 * pad) / cols

    out = header(W, pad, c, user, "at a glance")
    top = pad + 52
    for i, (label, value) in enumerate(tiles):
        cx = pad + (i % cols) * tw
        cy = top + (i // cols) * rh
        out.append(f'<text x="{cx:.0f}" y="{cy:.0f}" font-size="23" '
                   f'font-weight="700" fill="{c["value"]}">{esc(value)}</text>')
        out.append(f'<text x="{cx:.0f}" y="{cy + 18:.0f}" font-size="10.5" '
                   f'fill="{c["muted"]}">{esc(label)}</text>')
    return frame(W, H, c, "".join(out), f"{user} GitHub statistics")


def render_langs(user, langs, theme, top_n):
    c = THEMES[theme]
    W, pad = 480, 22
    inner = W - 2 * pad
    total = sum(langs.values()) or 1
    items = sorted(langs.items(), key=lambda kv: -kv[1])[:top_n]

    rows = (len(items) + 1) // 2
    bar_top = pad + 52
    bar_h = 10
    list_top = bar_top + bar_h + 24
    rh = 24
    H = list_top + (rows - 1) * rh + 16 + pad

    out = header(W, pad, c, "Most used languages", f"{len(langs)} languages")

    # stacked bar: top items plus an "Other" remainder so it always fills 100%
    segs = [(name, count, LANG_COLOR.get(name, c["muted"])) for name, count in items]
    rest = total - sum(count for _, count, _ in segs)
    if rest > 0:
        segs.append(("Other", rest, c["other"]))

    out.append(f'<clipPath id="bar"><rect x="{pad}" y="{bar_top}" width="{inner}" '
               f'height="{bar_h}" rx="{bar_h / 2}"/></clipPath>')
    out.append(f'<rect x="{pad}" y="{bar_top}" width="{inner}" height="{bar_h}" '
               f'rx="{bar_h / 2}" fill="{c["bar_bg"]}"/>')
    x = pad
    for name, count, col in segs:
        w = inner * count / total
        out.append(f'<rect x="{x:.2f}" y="{bar_top}" width="{w:.2f}" '
                   f'height="{bar_h}" fill="{col}" clip-path="url(#bar)"/>')
        x += w

    col_w = inner / 2
    for i, (name, count) in enumerate(items):
        cx = pad + (i % 2) * col_w
        cy = list_top + (i // 2) * rh
        col = LANG_COLOR.get(name, c["muted"])
        pct = count / total * 100
        out.append(f'<circle cx="{cx + 5:.0f}" cy="{cy - 4:.0f}" r="5" fill="{col}"/>')
        out.append(f'<text x="{cx + 16:.0f}" y="{cy:.0f}" font-size="12" '
                   f'fill="{c["text"]}">{esc(name)}</text>')
        out.append(f'<text x="{cx + col_w - 12:.0f}" y="{cy:.0f}" font-size="11" '
                   f'text-anchor="end" fill="{c["muted"]}">{pct:.1f}%</text>')
    return frame(W, H, c, "".join(out), f"{user} most used languages")


# --------------------------------------------------------------------------- #


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user", required=True)
    p.add_argument("--out", type=Path, default=Path("assets"))
    p.add_argument("--top", type=int, default=8, help="languages listed (default 8)")
    args = p.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    args.out.mkdir(parents=True, exist_ok=True)

    user = rest(f"/users/{args.user}", token)
    repos, page = [], 1
    while True:
        batch = rest(f"/users/{args.user}/repos?per_page=100&page={page}&type=owner",
                     token)
        repos += batch
        if len(batch) < 100:
            break
        page += 1
    owned = [r for r in repos if not r["fork"]]
    stars = sum(r["stargazers_count"] for r in owned)

    tiles = [("Total stars", f"{stars:,}"),
             ("Public repos", f"{user['public_repos']:,}"),
             ("Followers", f"{user['followers']:,}")]

    contrib = fetch_contributions(args.user, token)
    if contrib:
        total_c, current, longest = contrib
        tiles += [("Contributions (1y)", f"{total_c:,}"),
                  ("Current streak", f"{current:,}"),
                  ("Longest streak", f"{longest:,}")]
    else:
        print("  note: no token, skipping contribution tiles", file=sys.stderr)

    langs = fetch_languages(owned, token)

    for theme in ("dark", "light"):
        (args.out / f"card-stats-{theme}.svg").write_text(
            render_stats(args.user, tiles, theme), encoding="utf-8")
    print(f"wrote card-stats-*.svg  ({len(tiles)} tiles)")

    for theme in ("dark", "light"):
        (args.out / f"card-langs-{theme}.svg").write_text(
            render_langs(args.user, langs, theme, args.top), encoding="utf-8")
    top3 = ", ".join(f"{n} {c / sum(langs.values()) * 100:.0f}%"
                     for n, c in sorted(langs.items(), key=lambda kv: -kv[1])[:3])
    print(f"wrote card-langs-*.svg  ({len(langs)} languages; top: {top3})")


if __name__ == "__main__":
    main()
