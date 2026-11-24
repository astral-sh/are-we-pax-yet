from datetime import date


def render_history(history: dict[date, int]) -> str:
    """Plot daily observations in chronological order, leaving gaps for missed days."""
    first = next(iter(history))
    last = next(reversed(history))
    span = (last - first).days

    # Round the tick interval up to a multiple of 1, 2 or 5 times a power of ten.
    peak = max(1, max(history.values()))
    interval = (peak + 3) // 4
    unit = 10 ** (len(str(interval)) - 1)
    step = next(n * unit for n in (1, 2, 5, 10) if n * unit >= interval)
    ceiling = ((peak + step - 1) // step) * step

    def y(count):
        return 200 - 160 * count / ceiling

    svg = [
        '<svg class="history" height="256" role="img" aria-labelledby="history-title">',
        '<title id="history-title">Daily count of non-pax or unclassified sdists</title>',
        f"<desc>{history[first]:,} on {first} to {history[last]:,} on {last}.</desc>"
        if span
        else f"<desc>{history[last]:,} on {last}.</desc>",
        '<text x="-10" y="16" text-anchor="end">Sdists</text>',
        '<text x="50%" y="248" text-anchor="middle">Date (UTC)</text>',
    ]
    for tick in range(0, ceiling + 1, step):
        pos = y(tick)
        svg.extend(
            [
                f'<line x2="100%" y1="{pos}" y2="{pos}"/>',
                f'<text x="-10" y="{pos + 5}" text-anchor="end">{tick:,}</text>',
            ]
        )

    if span:
        svg.extend(
            [
                f'<text y="224">{first}</text>',
                f'<text x="100%" y="224" text-anchor="end">{last}</text>',
            ]
        )
    else:
        svg.extend(
            [
                f'<text x="50%" y="224" text-anchor="middle">{last}</text>',
                (
                    f'<text x="50%" y="{y(history[last]) - 10}" text-anchor="middle">'
                    f"{history[last]:,}</text>"
                ),
            ]
        )

    # Scale only the path horizontally so labels and dots keep their size.
    path = []
    dots = []
    previous = None
    for day, count in history.items():
        x = 100 * (day - first).days / span if span else 50
        pos = y(count)
        connected = previous is not None and (day - previous).days == 1
        path.append(f"{'L' if connected else 'M'}{x:.4f} {pos:.2f}")
        if span <= 30 or not connected or day == last:
            dots.append(
                f'<circle cx="{x:.4f}%" cy="{pos:.2f}" r="3.5">'
                f"<title>{day} · {count:,}</title></circle>"
            )
        previous = day
    svg.extend(
        [
            '<svg viewBox="0 0 100 256" preserveAspectRatio="none">',
            f'<path d="{" ".join(path)}" vector-effect="non-scaling-stroke"/>',
            "</svg>",
            *dots,
            "</svg>",
        ]
    )
    return "\n".join(svg)
