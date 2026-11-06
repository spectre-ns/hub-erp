"""Server-rendered inline SVG charts for the dashboard: no script, no CDN, colours from the theme tokens (CSS
classes), every bar a link to the list it counts, and a text alternative in the title. Font sizes are set in CSS,
never as attributes."""

from __future__ import annotations

from datetime import date, timedelta

from .text import esc

SWATCHES = ["sw", "sw2", "sw3", "sw4", "sw5", "sw6"]


def _a(href: str | None, inner: str, title: str) -> str:
    t = f"<title>{esc(title)}</title>"
    if href:
        return f'<a href="{esc(href)}">{t}{inner}</a>'
    return f"<g>{t}{inner}</g>"


def stacked_bars(rows: list[tuple[str, list[tuple[str, int, str | None]]]], width: int = 520, legend: list[tuple[str, str]] | None = None,
                 label: str = "") -> str:
    """Horizontal stacked bars: one row per label, segments (name, count, href) in a fixed swatch order."""
    if not rows:
        return "<p class='muted'>Nothing to show.</p>"
    left, rh, gap = 120, 22, 8
    height = len(rows) * (rh + gap) + 4
    total_max = max((sum(n for _, n, _ in segs) for _, segs in rows), default=1) or 1
    scale = (width - left - 40) / total_max
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label or "stacked bars")}">']
    for i, (name, segs) in enumerate(rows):
        y = i * (rh + gap) + 2
        out.append(f'<text class="lbl" x="{left - 8}" y="{y + rh * 0.7:.0f}" text-anchor="end">{esc(name[:18])}</text>')
        x = left
        total = sum(n for _, n, _ in segs)
        for j, (seg, n, href) in enumerate(segs):
            if not n:
                continue
            w = max(n * scale, 2)
            out.append(_a(href, f'<rect class="{SWATCHES[j % len(SWATCHES)]}" x="{x:.1f}" y="{y}" width="{w:.1f}" height="{rh}" rx="2"/>', f"{name}: {n} {seg}"))
            x += w
        out.append(f'<text class="val" x="{x + 6:.1f}" y="{y + rh * 0.7:.0f}">{total}</text>')
    out.append("</svg>")
    leg = "".join(f'<span><i class="{SWATCHES[j % len(SWATCHES)]}"></i>{esc(name)}</span>' for j, (name, _) in enumerate(legend or []))
    return "".join(out) + (f'<div class="legend">{leg}</div>' if leg else "")


def day_bars(points: list[tuple[str, int]], href_for=None, width: int = 520, label: str = "") -> str:
    """Vertical bars per day (ISO dates in order); ``href_for(day)`` links each bar."""
    if not points:
        return "<p class='muted'>Nothing to show.</p>"
    h, base, top = 150, 128, 10
    n = len(points)
    slot = (width - 30) / n
    bw = max(slot - 3, 2)
    peak = max((v for _, v in points), default=1) or 1
    out = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="{esc(label or "bars per day")}">',
           f'<line class="axis" x1="20" y1="{base}" x2="{width - 10}" y2="{base}"/>']
    for i, (day, v) in enumerate(points):
        x = 20 + i * slot
        bh = (v / peak) * (base - top) if v else 0
        rect = f'<rect class="sw2" x="{x:.1f}" y="{base - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="1"/>' if v else \
            f'<rect class="sw-line" x="{x:.1f}" y="{base - 1}" width="{bw:.1f}" height="1"/>'
        out.append(_a(href_for(day) if (href_for and v) else None, rect, f"{day}: {v}"))
        if i % max(1, n // 6) == 0 or i == n - 1:
            out.append(f'<text class="lbl" x="{x + bw / 2:.1f}" y="{h - 2}" text-anchor="middle">{esc(day[5:])}</text>')
    out.append(f'<text class="val" x="{width - 10}" y="{top + 4}" text-anchor="end">peak {peak}</text></svg>')
    return "".join(out)


def hbars(rows: list[tuple[str, float, str | None, str]], width: int = 520, swatch: str = "sw3", unit: str = "", label: str = "") -> str:
    """Horizontal bars: (name, value, href, value text)."""
    if not rows:
        return "<p class='muted'>Nothing to show.</p>"
    left, rh, gap = 130, 18, 8
    height = len(rows) * (rh + gap) + 4
    peak = max((v for _, v, _, _ in rows), default=1) or 1
    scale = (width - left - 70) / peak
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label or "bars")}">']
    for i, (name, v, href, text) in enumerate(rows):
        y = i * (rh + gap) + 2
        w = max(v * scale, 2)
        out.append(f'<text class="lbl" x="{left - 8}" y="{y + rh * 0.75:.0f}" text-anchor="end">{esc(name[:20])}</text>')
        out.append(_a(href, f'<rect class="{swatch}" x="{left}" y="{y}" width="{w:.1f}" height="{rh}" rx="2"/>', f"{name}: {text}"))
        out.append(f'<text class="val" x="{left + w + 6:.1f}" y="{y + rh * 0.75:.0f}">{esc(text)}</text>')
    out.append("</svg>")
    return "".join(out)


def sparkline(values: list[int], width: int = 120, height: int = 28) -> str:
    """A small line with a soft area under it; decorative (the number beside it is the information)."""
    if not values:
        values = [0]
    peak = max(values) or 1
    n = len(values)
    step = width / max(n - 1, 1)
    pts = [(i * step, height - 2 - (v / peak) * (height - 6)) for i, v in enumerate(values)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"0,{height} " + line + f" {pts[-1][0]:.1f},{height}"
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none" aria-hidden="true">'
            f'<polygon class="spark-area" points="{area}"/><polyline class="spark-line" points="{line}"/></svg>')


def last_days(n: int, today: str) -> list[str]:
    end = date.fromisoformat(today)
    return [(end - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def per_day(stamps: list[str], days: list[str]) -> list[tuple[str, int]]:
    counts = {d: 0 for d in days}
    for s in stamps:
        d = str(s or "")[:10]
        if d in counts:
            counts[d] += 1
    return [(d, counts[d]) for d in days]


def median(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2
