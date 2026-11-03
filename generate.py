#!/usr/bin/env -S uv run --locked --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///

import argparse
import gzip
import json
import tarfile
import time
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html import escape
from http.client import IncompleteRead
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
WORKERS = 16

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


def inspect_sdist(sdist):
    kind = (
        fetch(sdist["url"], tar_format)
        if sdist["filename"].endswith(".tar.gz")
        else "other"
    )
    css_class, icon, label = STYLES[kind]
    return sdist | {
        "format": kind,
        "css_class": css_class,
        "icon": icon,
        "title": f"{sdist['filename']} — {label}",
    }


def survey(number):
    print("Fetching download rankings and latest release metadata…", flush=True)
    rows = fetch(RANKING_URL, json.load)["rows"]
    sdists = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for start in range(0, len(rows), WORKERS):
            batch = rows[start : start + WORKERS]
            sdists.extend(
                sdist for sdist in pool.map(latest_sdist, batch) if sdist is not None
            )
            if len(sdists) >= number:
                break
        if len(sdists) < number:
            raise RuntimeError(
                f"Found only {len(sdists)} sdists for {number} places in the chart"
            )
        print(f"Inspecting {number} sdists from PyPI…", flush=True)
        return list(pool.map(inspect_sdist, sdists[:number]))


def write_site(packages, output):
    counts = Counter(package["css_class"] for package in packages)
    now = datetime.now(UTC).replace(microsecond=0)
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
        timestamp=now.isoformat(),
        last_update=now.strftime("%d %B %Y, %H:%M UTC"),
    )

    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(index, encoding="utf-8")
    (output / "results.json").write_text(
        json.dumps({"data": packages, "last_update": now.isoformat()}, indent=2) + "\n",
        encoding="utf-8",
    )
    generate_svg_wheel(packages, output / "wheel.svg")
    copyfile(ROOT / "wheel.css", output / "wheel.css")
    print(f"Wrote {output} with {counts['success']}/{len(packages)} pax sdists.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "-n", "--number", type=int, default=TO_CHART, help="number of sdists to chart"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "build", help="site output directory"
    )
    args = parser.parse_args()
    if args.number < 1:
        parser.error("--number must be positive")
    if args.output.resolve() == ROOT:
        parser.error("--output must be different from the source directory")
    write_site(survey(args.number), args.output)
