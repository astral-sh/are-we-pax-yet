import gzip
import io
import tarfile
import unittest

from generate import tar_format


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
