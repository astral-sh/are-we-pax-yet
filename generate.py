#!/usr/bin/env -S uv run --locked --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///

import argparse
import gzip
import json
import os
import re
import sqlite3
import sys
import tarfile
import time
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
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
PYPI_INDEX_URL = "https://pypi.org/simple/"
TO_CHART = 360
WORKERS = 32
CACHE_PATH = ROOT / ".sdist-formats-v2.sqlite"

STYLES = {
    "pax": ("success", "📦", "pax tar archive"),
    "gnu": ("warning", "", "GNU-style tar headers"),
    "other": ("default", "", "Unclassified archive"),
}


def fetch(url, read, *, headers=None):
    request = Request(
        url,
        headers={
            "Accept-Encoding": "gzip",
            "User-Agent": "are-we-pax-yet (+https://github.com/astral-sh/are-we-pax-yet)",
            **(headers or {}),
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


def latest_sdist(name):
    try:
        project = fetch(f"https://pypi.org/pypi/{quote(name, safe='')}/json", json.load)
    except HTTPError as error:
        if error.code == 404:
            return None, None
        raise

    serial = project.get("last_serial")
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
        return serial, None
    return serial, {
        "version": project["info"]["version"],
        "filename": sdist["filename"],
        "url": sdist["url"],
    }


def reusable_sdists(cache, rows):
    ranked_names = {row["project"] for row in rows}
    entries = {
        name: (serial, sdist)
        for name, serial, sdist in cache.execute(
            "SELECT name, serial, sdist FROM project_sdists WHERE serial IS NOT NULL"
        )
        if name in ranked_names
    }
    if not entries:
        return {}

    try:
        projects = fetch(
            PYPI_INDEX_URL,
            json.load,
            headers={"Accept": "application/vnd.pypi.simple.v1+json"},
        )["projects"]
    except (
        OSError,
        IncompleteRead,
        EOFError,
        ValueError,
        KeyError,
        zlib.error,
    ) as error:
        print(f"Could not read PyPI's project index: {error}", flush=True)
        return {}

    # Both PyPI APIs report the same project serial. Match it exactly so a
    # stale project response cannot be stored under a newer index serial.
    separators = re.compile(r"[-_.]+")
    cached_sdists = {}
    for project in projects:
        name = separators.sub("-", project["name"]).lower()
        entry = entries.get(name)
        if entry is not None and entry[0] == project.get("_last-serial"):
            cached_sdists[name] = json.loads(entry[1])
    return cached_sdists


def read_tar_header(source):
    try:
        with gzip.GzipFile(fileobj=source) as data:
            return data.read(tarfile.BLOCKSIZE)
    except (gzip.BadGzipFile, EOFError, zlib.error):
        return b""


def tar_format(url):
    # A byte range addresses the gzip file. Its prefix normally expands to
    # at least one full tar header.
    try:
        block = fetch(
            url,
            read_tar_header,
            headers={"Range": "bytes=0-1023", "Accept-Encoding": "identity"},
        )
    except HTTPError as error:
        if error.code != 416:
            raise
        block = b""

    if len(block) < tarfile.BLOCKSIZE:
        block = fetch(url, read_tar_header)

    try:
        member = tarfile.TarInfo.frombuf(block, tarfile.ENCODING, "surrogateescape")
    except (tarfile.HeaderError, ValueError):
        return "other"

    magic = block[257:265]
    if magic == tarfile.GNU_MAGIC or member.type in (b"K", b"L", b"N", b"S"):
        return "gnu"
    if magic == tarfile.POSIX_MAGIC and member.type in b"\x0001234567xg":
        return "pax"
    return "other"


def inspect_project(row, cached_formats, cached_sdists):
    name = row["project"]
    update = None
    if name in cached_sdists:
        sdist = cached_sdists[name]
    else:
        serial, sdist = latest_sdist(name)
        update = (name, serial, json.dumps(sdist))

    if sdist is None:
        return None, update

    url = sdist["url"]
    kind = cached_formats.get(url)
    if kind is None:
        kind = tar_format(url) if sdist["filename"].endswith(".tar.gz") else "other"
    css_class, icon, label = STYLES[kind]
    package = sdist | {
        "name": name,
        "downloads": row["download_count"],
        "format": kind,
        "css_class": css_class,
        "icon": icon,
        "title": f"{sdist['filename']} — {label}",
    }
    return package, update


@contextmanager
def survey_workers():
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    interrupted = False
    try:
        yield pool
    except KeyboardInterrupt:
        interrupted = True
        raise
    finally:
        pool.shutdown(wait=not interrupted, cancel_futures=interrupted)


def survey(number):
    print("Fetching the 30-day download ranking…", flush=True)
    rows = fetch(RANKING_URL, json.load)["rows"]
    results = []
    sdist_count = 0
    batch_size = WORKERS if number is not None else 512
    with (
        survey_workers() as pool,
        closing(sqlite3.connect(CACHE_PATH)) as cache,
    ):
        cache.execute(
            "CREATE TABLE IF NOT EXISTS formats (url TEXT PRIMARY KEY, format TEXT NOT NULL)"
        )
        cache.execute(
            "CREATE TABLE IF NOT EXISTS project_sdists ("
            "name TEXT PRIMARY KEY, serial INTEGER, sdist TEXT NOT NULL)"
        )
        cached_formats = dict(
            cache.execute("SELECT url, format FROM formats WHERE format != 'other'")
        )
        cached_sdists = reusable_sdists(cache, rows) if number is None else {}
        if number is None:
            print(
                f"Reusing metadata for {len(cached_sdists):,} of {len(rows):,} ranked projects",
                flush=True,
            )
        inspect = partial(
            inspect_project, cached_formats=cached_formats, cached_sdists=cached_sdists
        )
        for batch in batched(rows, batch_size, strict=False):
            packages, updates = zip(*pool.map(inspect, batch), strict=True)
            # Checkpoint known formats. Recheck unclassified archives in case a read failed.
            new_formats = {
                package["url"]: package["format"]
                for package in packages
                if package is not None
                and package["format"] != "other"
                and package["url"] not in cached_formats
            }
            with cache:
                cache.executemany(
                    "INSERT OR REPLACE INTO formats VALUES (?, ?)", new_formats.items()
                )
                cache.executemany(
                    "INSERT OR REPLACE INTO project_sdists VALUES (?, ?, ?)",
                    (update for update in updates if update is not None),
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
        status = '<span class="format-chip missing">No sdist</span>'
        if package is not None:
            kind = package["format"]
            counts[kind] += 1
            version = escape(package["version"])
            css_class, _, status = STYLES[kind]
            if package["filename"].lower().endswith(".zip"):
                status = '<span class="format-chip zip">ZIP sdist</span>'
            elif kind != "other":
                status = f'<span class="format-chip {css_class}">{status}</span>'

        rows.append(
            f'<tr id="project-{name}"><td>{rank:,}</td>'
            '<th scope="row">'
            f'<a href="https://pypi.org/project/{project}/">{name}</a> '
            f'<a href="#project-{project}" aria-label="Permalink to {name}" title="Link to this row">#</a>'
            "</th>"
            f"<td>{status}</td><td>{version}</td>"
            f"<td>{row['download_count']:,}</td></tr>"
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
            f'class="package-link {package["css_class"]}" title="{title}">{name}{icon}</a>'
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


def main():
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
    try:
        results, ranking_count = survey(args.number)
    except KeyboardInterrupt:
        print("\nScan interrupted.", file=sys.stderr, flush=True)
        # The cache is closed. Exit directly because interpreter shutdown would
        # wait for any worker still blocked on a network request.
        os._exit(130)

    write_site(results, ranking_count, args.number or TO_CHART, args.output)


if __name__ == "__main__":
    main()
