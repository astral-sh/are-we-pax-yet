#!/usr/bin/env -S uv run --locked --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///

import argparse
import gzip
import json
import sqlite3
import tarfile
import time
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from functools import partial
from html import escape
from http.client import IncompleteRead
from itertools import batched
from pathlib import Path
from shutil import copyfile
from string import Template
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from svg_wheel import generate_svg_wheel

ROOT = Path(__file__).resolve().parent
RANKING_URL = (
    "https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/"
    "top-pypi-packages-30-days.min.json"
)
TO_CHART = 360
WORKERS = 32
CACHE_PATH = ROOT / ".sdist-formats-v1.sqlite"

STYLES = {
    "pax": ("success", "📦", "pax tar archive"),
    "gnu": ("warning", "", "GNU-style tar headers"),
    "other": ("default", "", "Unclassified archive"),
}


def fetch(url, read):
    request = Request(
        url,
        headers={
            "Accept-Encoding": "gzip",
            "User-Agent": "are-we-pax-yet (+https://github.com/astral-sh/are-we-pax-yet)",
        },
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                if response.headers.get("Content-Encoding") == "gzip":
                    with gzip.GzipFile(fileobj=response) as body:
                        return read(body)
                return read(response)
        except HTTPError as error:
            error.close()
            if attempt == 2 or (error.code != 429 and error.code < 500):
                raise
        except (URLError, TimeoutError, ConnectionError, IncompleteRead):
            if attempt == 2:
                raise
        time.sleep(2**attempt)


def latest_sdist(row):
    name = row["project"]
    try:
        project = fetch(f"https://pypi.org/pypi/{quote(name, safe='')}/json", json.load)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise

    sdist = max(
        (
            file
            for file in project["urls"]
            if file["packagetype"] == "sdist" and not file["yanked"]
        ),
        key=lambda file: (
            file["filename"].endswith(".tar.gz"),
            file["upload_time_iso_8601"],
        ),
        default=None,
    )
    if sdist is None:
        return None
    return {
        "name": name,
        "downloads": row["download_count"],
        "version": project["info"]["version"],
        "filename": sdist["filename"],
        "url": sdist["url"],
    }


class GNUHeader(Exception):
    """Stop reading as soon as a GNU-style header determines the result."""


def tar_format(source):
    formats = set()

    class FormatTrackingTarInfo(tarfile.TarInfo):
        @staticmethod
        def record_header(buf, member):
            # Inspect raw headers because tarfile folds extension headers into
            # the members they describe. Plain ustar headers count as pax.
            magic = buf[257:265]
            if magic == b"ustar  \0" or member.type in (b"K", b"L", b"N", b"S"):
                raise GNUHeader
            if magic == b"ustar\x0000" and member.type in b"\x0001234567xg":
                formats.add("pax")
            else:
                formats.add("other")
            return member

        @classmethod
        def frombuf(cls, buf, encoding, errors):
            # Python 3.13 dispatches through frombuf. Newer versions can
            # dispatch through _frombuf directly when reading extensions.
            return cls.record_header(buf, super().frombuf(buf, encoding, errors))

        @classmethod
        def _frombuf(cls, buf, encoding, errors, **kwargs):
            return cls.record_header(
                buf, super()._frombuf(buf, encoding, errors, **kwargs)
            )

    try:
        with (
            gzip.GzipFile(fileobj=source) as data,
            tarfile.open(
                fileobj=data,
                mode="r|",
                tarinfo=FormatTrackingTarInfo,
                stream=True,
                bufsize=65536,
            ) as archive,
        ):
            # GNU or other headers can appear after ordinary pax headers.
            for _ in archive:
                pass
    except GNUHeader:
        return "gnu"
    except (tarfile.TarError, gzip.BadGzipFile, EOFError, ValueError, zlib.error):
        return "other"

    return "pax" if formats == {"pax"} else "other"


def inspect_project(row, cached_formats):
    sdist = latest_sdist(row)
    if sdist is None:
        return None

    url = sdist["url"]
    kind = cached_formats.get(url)
    if kind is None:
        kind = (
            fetch(url, tar_format) if sdist["filename"].endswith(".tar.gz") else "other"
        )
    css_class, icon, label = STYLES[kind]
    return sdist | {
        "format": kind,
        "css_class": css_class,
        "icon": icon,
        "title": f"{sdist['filename']} — {label}",
    }


def survey(number):
    print("Fetching the 30-day download ranking…", flush=True)
    rows = fetch(RANKING_URL, json.load)["rows"]
    results = []
    sdist_count = 0
    batch_size = WORKERS if number is not None else 512
    with (
        closing(sqlite3.connect(CACHE_PATH)) as cache,
        ThreadPoolExecutor(max_workers=WORKERS) as pool,
    ):
        cache.execute(
            "CREATE TABLE IF NOT EXISTS formats (url TEXT PRIMARY KEY, format TEXT NOT NULL)"
        )
        cached_formats = dict(cache.execute("SELECT url, format FROM formats"))
        inspect = partial(inspect_project, cached_formats=cached_formats)
        for batch in batched(rows, batch_size, strict=False):
            packages = list(pool.map(inspect, batch))
            # Checkpoint completed batches so a retry can reuse their classifications.
            new_formats = {
                package["url"]: package["format"]
                for package in packages
                if package is not None and package["url"] not in cached_formats
            }
            with cache:
                cache.executemany(
                    "INSERT OR IGNORE INTO formats VALUES (?, ?)", new_formats.items()
                )
            cached_formats.update(new_formats)
            for row, package in zip(batch, packages, strict=True):
                results.append((row, package))
                if package is not None:
                    sdist_count += 1
                    if sdist_count == number:
                        return results, len(rows)
            print(
                f"Checked {len(results):,} of {len(rows):,} ranked projects", flush=True
            )

    return results, len(rows)


def render_full_page(results, ranking_count, dates):
    rows = []
    counts = Counter()
    for rank, (row, package) in enumerate(results, start=1):
        name = escape(row["project"])
        project = quote(row["project"], safe="")
        version = "—"
        status = "No sdist"
        if package is not None:
            kind = package["format"]
            counts[kind] += 1
            version = escape(package["version"])
            css_class, _, status = STYLES[kind]
            if kind != "other":
                status = f'<span class="badge text-bg-{css_class}">{status}</span>'

        rows.append(
            f'<tr><td class="text-end">{rank:,}</td>'
            f'<th scope="row" class="fw-normal"><a href="https://pypi.org/project/{project}/">{name}</a></th>'
            f'<td class="text-end">{row["download_count"]:,}</td>'
            f"<td>{version}</td><td>{status}</td></tr>"
        )

    coverage = (
        f"all {ranking_count:,} projects"
        if len(results) == ranking_count
        else f"the first {len(results):,} of {ranking_count:,} projects"
    )
    sdist_count = counts.total()
    return Template((ROOT / "full.html").read_text(encoding="utf-8")).substitute(
        coverage=coverage,
        sdist_count=f"{sdist_count:,}",
        pax_count=f"{counts['pax']:,}",
        pax_percent=f"{counts['pax'] / sdist_count:.1%}" if sdist_count else "0%",
        rows="\n".join(rows),
        **dates,
    )


def write_site(results, ranking_count, number, output):
    packages = [package for _, package in results if package is not None][:number]
    if len(packages) < number:
        raise RuntimeError(
            f"Found only {len(packages)} sdists for {number} places in the chart"
        )
    counts = Counter(package["css_class"] for package in packages)
    now = datetime.now(UTC).replace(microsecond=0)
    dates = {
        "timestamp": now.isoformat(),
        "last_update": now.strftime("%d %B %Y, %H:%M UTC"),
    }
    package_links = []
    for package in packages:
        name = escape(package["name"])
        project = quote(package["name"], safe="")
        title = escape(package["title"])
        icon = (
            f' <span aria-hidden="true">{package["icon"]}</span>'
            if package["icon"]
            else ""
        )
        package_links.append(
            f'<a href="https://pypi.org/project/{project}/" '
            f'class="btn btn-{package["css_class"]}" title="{title}">{name}{icon}</a>'
        )

    index = Template((ROOT / "index.html").read_text(encoding="utf-8")).substitute(
        package_links="\n".join(package_links),
        total=len(packages),
        pax_count=counts["success"],
        pax_percent=f"{counts['success'] / len(packages):.0%}",
        gnu_percent=f"{counts['warning'] / len(packages):.0%}",
        other_percent=f"{counts['default'] / len(packages):.0%}",
        **dates,
    )
    full = render_full_page(results, ranking_count, dates)

    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(index, encoding="utf-8")
    (output / "full.html").write_text(full, encoding="utf-8")
    (output / "results.json").write_text(
        json.dumps({"data": packages, "last_update": now.isoformat()}, indent=2) + "\n",
        encoding="utf-8",
    )
    generate_svg_wheel(packages, output / "wheel.svg")
    copyfile(ROOT / "wheel.css", output / "wheel.css")
    copyfile(ROOT / "site.css", output / "site.css")
    print(f"Wrote {output} with {len(results):,} ranked projects and {number} wedges.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "-n", "--number", type=int, help="generate a preview with this many sdists"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "build", help="site output directory"
    )
    args = parser.parse_args()
    if args.number is not None and args.number < 1:
        parser.error("--number must be positive")
    if args.output.resolve() == ROOT:
        parser.error("--output must be different from the source directory")
    results, ranking_count = survey(args.number)
    write_site(results, ranking_count, args.number or TO_CHART, args.output)
