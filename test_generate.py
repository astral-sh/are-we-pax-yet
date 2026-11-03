import gzip
import io
import json
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from generate import RANKING_URL, survey, tar_format, write_site


class TarFormatTests(unittest.TestCase):
    def classify(self, *members, global_pax_headers=None):
        archive = io.BytesIO()
        with tarfile.open(
            fileobj=archive, mode="w:", pax_headers=global_pax_headers
        ) as tar:
            for member, format in members:
                tar.format = format
                tar.addfile(member)
        return tar_format(io.BytesIO(gzip.compress(archive.getvalue())))

    def test_pax_extension_after_plain_header(self):
        plain = tarfile.TarInfo("demo/PKG-INFO")
        extended = tarfile.TarInfo("demo/" + "x" * 110)
        self.assertEqual(
            self.classify((plain, tarfile.PAX_FORMAT), (extended, tarfile.PAX_FORMAT)),
            "pax",
        )

    def test_global_pax_header(self):
        member = tarfile.TarInfo("demo/PKG-INFO")
        self.assertEqual(
            self.classify(
                (member, tarfile.PAX_FORMAT), global_pax_headers={"comment": "demo"}
            ),
            "pax",
        )

    def test_ustar_and_pax_without_extensions_classify_as_pax(self):
        for format in (tarfile.USTAR_FORMAT, tarfile.PAX_FORMAT):
            with self.subTest(format=format):
                member = tarfile.TarInfo("demo/PKG-INFO")
                self.assertEqual(self.classify((member, format)), "pax")

    def test_gnu_magic_without_extensions(self):
        member = tarfile.TarInfo("demo/PKG-INFO")
        self.assertEqual(self.classify((member, tarfile.GNU_FORMAT)), "gnu")

    def test_gnu_header_after_pax_extension(self):
        extended = tarfile.TarInfo("demo/" + "x" * 110)
        gnu = tarfile.TarInfo("demo/PKG-INFO")
        self.assertEqual(
            self.classify((extended, tarfile.PAX_FORMAT), (gnu, tarfile.GNU_FORMAT)),
            "gnu",
        )

    def test_unreadable_archive_is_unclassified(self):
        self.assertEqual(tar_format(io.BytesIO(b"not a gzip file")), "other")


class SurveyTests(unittest.TestCase):
    def test_full_results_keep_rank_and_reuse_cached_classifications(self):
        rows = [
            {"project": "wheel-only", "download_count": 3},
            {"project": "pax-project", "download_count": 2},
            {"project": "gnu-project", "download_count": 1},
        ]
        sdists = {
            row["project"]: {
                "name": row["project"],
                "downloads": row["download_count"],
                "version": "1.0",
                "filename": f"{row['project']}-1.0.tar.gz",
                "url": f"https://files.pythonhosted.org/{row['project']}-1.0.tar.gz",
            }
            for row in rows[1:]
        }
        archive_formats = {
            sdists["pax-project"]["url"]: "pax",
            sdists["gnu-project"]["url"]: "gnu",
        }

        def fetch(url, _):
            return {"rows": rows} if url == RANKING_URL else archive_formats[url]

        with TemporaryDirectory() as directory:
            output = Path(directory) / "build"
            with (
                patch("generate.CACHE_PATH", Path(directory) / "cache.sqlite"),
                patch(
                    "generate.latest_sdist",
                    side_effect=lambda row: sdists.get(row["project"]),
                ),
                patch("generate.fetch", side_effect=fetch) as fetch_mock,
            ):
                results, total = survey(None)
                self.assertEqual(fetch_mock.call_count, 3)
                fetch_mock.reset_mock()
                self.assertEqual(survey(None), (results, total))
                self.assertEqual(fetch_mock.call_count, 1)

            write_site(results, total, 1, output)
            wheel = json.loads((output / "results.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [package["name"] for package in wheel["data"]], ["pax-project"]
            )
            full = (output / "full.html").read_text(encoding="utf-8")
            self.assertIn(
                '>1</td><th scope="row" class="fw-normal"><a href="https://pypi.org/project/wheel-only/">',
                full,
            )
            self.assertIn("No sdist</td>", full)
            self.assertIn("GNU-style tar headers</span>", full)
            self.assertIn("1 of 2 sdists use pax (50.0%).", full)
