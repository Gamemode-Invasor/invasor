import io
import os
import shutil
import stat
import subprocess
import tarfile
import threading
import time
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from invasor import archive
from invasor.schema import InvalidArgument, Unavailable

from .helpers import patch, temp_dir

HAS_BSDTAR = shutil.which("bsdtar") is not None


def tar_entry(name, kind=tarfile.REGTYPE, data=b"", mode=0o644, linkname="", pax=None):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.mode = mode
    info.linkname = linkname
    if pax:
        info.pax_headers = pax
    if kind == tarfile.REGTYPE:
        info.size = len(data)
    return info, (io.BytesIO(data) if kind == tarfile.REGTYPE else None)


def bsdtar(src, out, *flags):
    subprocess.run(["bsdtar", *flags, "-cf", str(out), "-C", str(src), "."], check=True, capture_output=True)


def children():
    """Pids of this process's children (zombies included: they stay children until reaped)."""
    pids = []
    for task in Path(f"/proc/{os.getpid()}/task").iterdir():
        try:
            pids += (task / "children").read_text().split()
        except OSError:
            pass
    return pids


class CancelAfter(threading.Event):
    """An Event that turns itself on after `n` checks, to cancel half-way through."""

    def __init__(self, n):
        super().__init__()
        self.left = n

    def is_set(self):
        self.left -= 1
        if self.left < 0:
            self.set()
        return super().is_set()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
        self.work = self.tmp / "work"  # where archives are built
        self.work.mkdir()
        self.box = self.tmp / "box"  # dest.parent: nothing but dest may ever appear here
        self.box.mkdir()
        self.dest = self.box / "out"
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        (self.outside / "keep.txt").write_text("untouched")
        self.n = 0

    def path(self, suffix):
        self.n += 1
        return self.work / f"a{self.n}{suffix}"

    def tar(self, entries, mode="w", suffix=".tar", fmt=tarfile.PAX_FORMAT):
        p = self.path(suffix)
        with tarfile.open(p, mode, format=fmt) as t:
            for e in entries:
                t.addfile(*tar_entry(*e) if isinstance(e, tuple) else tar_entry(**e))
        return p

    def zip(self, files, suffix=".zip", compression=zipfile.ZIP_DEFLATED):
        """files: {name: data} or [(ZipInfo or name, data)]."""
        p = self.path(suffix)
        items = files.items() if isinstance(files, dict) else files
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # duplicate names
            with zipfile.ZipFile(p, "w", compression) as z:
                for name, data in items:
                    z.writestr(name, data)
        return p

    def src(self, files, links=()):
        d = self.path("-src")
        for name, data in files.items():
            f = d / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
        for name, target in links:
            (d / name).symlink_to(target)
        return d

    def extract(self, a, **kw):
        return archive.extract(a, self.dest, **kw)

    def assert_clean(self, dest_was="missing"):
        """After a failure: dest as it was, no temp folders, nothing written outside."""
        if dest_was == "missing":
            self.assertFalse(os.path.lexists(self.dest))
            self.assertEqual(os.listdir(self.box), [])
        else:
            self.assertEqual(os.listdir(self.dest), [])
            self.assertEqual(os.listdir(self.box), ["out"])
        self.assert_outside_untouched()

    def assert_outside_untouched(self):
        self.assertEqual(os.listdir(self.outside), ["keep.txt"])
        self.assertEqual((self.outside / "keep.txt").read_text(), "untouched")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["box", "outside", "work"])
        self.assertFalse([n for n in os.listdir(self.box) if n != "out"])

    def refused(self, a, dest_was="missing", **kw):
        with self.assertRaises(InvalidArgument):
            self.extract(a, **kw)
        self.assert_clean(dest_was)

    def read(self, rel):
        return (self.dest / rel).read_bytes()


FILES = {"top/a.txt": b"alpha", "top/sub/b.bin": bytes(range(256)) * 10, "top/sub/deep/c.txt": b"gamma"}


class Formats(Case):
    def check(self, result):
        self.assertEqual(result, sorted(result))
        self.assertTrue(set(FILES) <= set(result), result)
        for name in result:
            self.assertIsInstance(name, str)
            self.assertFalse(name.startswith(("/", "./")))
        for name, data in FILES.items():
            self.assertEqual(self.read(name), data)
        self.assertEqual(os.listdir(self.box), ["out"])

    def tar_of(self, mode, suffix):
        return self.tar([(n, tarfile.REGTYPE, d) for n, d in FILES.items()], mode, suffix)

    def test_zip(self):
        self.check(self.extract(self.zip(FILES)))

    def test_zip_stored(self):
        self.check(self.extract(self.zip(FILES, compression=zipfile.ZIP_STORED)))

    def test_zip_lzma_and_bz2_members(self):
        p = self.path(".zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr(zipfile.ZipInfo("top/a.txt"), FILES["top/a.txt"], zipfile.ZIP_LZMA)
            z.writestr(zipfile.ZipInfo("top/sub/b.bin"), FILES["top/sub/b.bin"], zipfile.ZIP_BZIP2)
            z.writestr("top/sub/deep/c.txt", FILES["top/sub/deep/c.txt"])
        self.check(self.extract(p))

    def test_plain_tar(self):
        self.check(self.extract(self.tar_of("w", ".tar")))

    def test_plain_gnu_and_ustar_tar(self):
        for fmt in (tarfile.GNU_FORMAT, tarfile.USTAR_FORMAT):
            with self.subTest(fmt=fmt):
                shutil.rmtree(self.dest, ignore_errors=True)
                p = self.tar([(n, tarfile.REGTYPE, d) for n, d in FILES.items()], fmt=fmt)
                self.check(self.extract(p))

    def test_tar_gz(self):
        self.check(self.extract(self.tar_of("w:gz", ".tar.gz")))

    def test_tar_xz(self):
        self.check(self.extract(self.tar_of("w:xz", ".tar.xz")))

    def test_tar_bz2(self):
        self.check(self.extract(self.tar_of("w:bz2", ".tar.bz2")))

    @unittest.skipUnless(HAS_BSDTAR, "bsdtar missing")
    def test_7z(self):
        p = self.path(".7z")
        bsdtar(self.src(FILES), p, "--format", "7zip")
        self.check(self.extract(p))
        self.assertEqual(children(), [])

    @unittest.skipUnless(HAS_BSDTAR, "bsdtar missing")
    def test_tar_zst(self):
        p = self.path(".tar.zst")
        bsdtar(self.src(FILES), p, "--zstd")
        self.check(self.extract(p))
        self.assertEqual(children(), [])

    def test_str_paths_accepted(self):
        result = archive.extract(str(self.zip(FILES)), str(self.dest))
        self.assertTrue(set(FILES) <= set(result))

    def test_detected_by_content_not_extension(self):
        self.check(self.extract(self.tar_of("w:gz", ".zip")))
        shutil.rmtree(self.dest)
        self.check(self.extract(self.zip(FILES, suffix=".tar.gz")))
        shutil.rmtree(self.dest)
        self.check(self.extract(self.tar_of("w:xz", ".7z")))

    @unittest.skipUnless(HAS_BSDTAR, "bsdtar missing")
    def test_7z_named_zip(self):
        p = self.path(".zip")
        bsdtar(self.src(FILES), p, "--format", "7zip")
        self.check(self.extract(p))

    def test_unknown_bytes(self):
        for data in (b"", b"hello world, not an archive" * 50, b"\x00" * 1024, b"PK", b"Rar!\x1a\x07\x00" + b"x" * 600):
            with self.subTest(data=data[:10]):
                p = self.path(".zip")
                p.write_bytes(data)
                self.refused(p)

    def test_empty_zip(self):
        result = self.extract(self.zip({}))
        self.assertEqual(result, [])
        self.assertTrue(self.dest.is_dir())

    def test_without_bsdtar_7z_is_unavailable_but_zip_works(self):
        patch(self, "invasor.archive.shutil.which", lambda name, *a, **k: None)
        if HAS_BSDTAR:
            p = self.path(".7z")
            bsdtar(self.src(FILES), p, "--format", "7zip")
        else:
            p = self.path(".7z")
            p.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 64)
        with self.assertRaises(Unavailable):
            self.extract(p)
        self.assert_clean()
        z = self.path(".tar.zst")
        z.write_bytes(b"\x28\xb5\x2f\xfd" + b"\x00" * 64)
        with self.assertRaises(Unavailable):
            self.extract(z)
        self.assert_clean()
        self.check(self.extract(self.zip(FILES)))

    def test_folder_entry_after_its_child(self):
        p = self.tar([("a/b/f.txt", tarfile.REGTYPE, b"x"), ("a/b", tarfile.DIRTYPE), ("a", tarfile.DIRTYPE)])
        result = self.extract(p)
        self.assertIn("a/b/f.txt", result)
        self.assertEqual(self.read("a/b/f.txt"), b"x")

    def test_dot_slash_prefix_accepted(self):
        p = self.tar([("./", tarfile.DIRTYPE), ("./a", tarfile.DIRTYPE), ("./a/f.txt", tarfile.REGTYPE, b"x")])
        result = self.extract(p)
        self.assertIn("a/f.txt", result)
        self.assertEqual(self.read("a/f.txt"), b"x")

    def test_trailing_slash_folder(self):
        result = self.extract(self.zip({"a/": b"", "a/f.txt": b"x"}))
        self.assertIn("a/f.txt", result)
        self.assertTrue((self.dest / "a").is_dir())


class Names(Case):
    BAD = ["/etc/evil", "../evil", "a/../../evil", "a/../evil", "a/./../evil", "..", "C:/evil", "c:evil",
           "a\\..\\..\\evil", "a\\b", "/", "//evil"]

    def test_bad_names_in_tar(self):
        for name in self.BAD:
            with self.subTest(name=name):
                self.refused(self.tar([("ok.txt", tarfile.REGTYPE, b"ok"), (name, tarfile.REGTYPE, b"pwn")]))

    def test_bad_names_in_zip(self):
        for name in self.BAD:
            with self.subTest(name=name):
                self.refused(self.zip([("ok.txt", b"ok"), (zipfile.ZipInfo(name), b"pwn")]))

    def test_bad_folder_names(self):
        for name in ("../evil", "/evil", "a/../../evil"):
            with self.subTest(name=name):
                self.refused(self.tar([(name, tarfile.DIRTYPE)]))

    def test_nul_in_name(self):
        # ustar cuts names at NUL; a pax "path" record keeps it.
        p = self.tar([{"name": "x", "data": b"pwn", "pax": {"path": "a\x00/../../evil"}}])
        with tarfile.open(p) as t:
            self.assertIn("\x00", t.getnames()[0])
        self.refused(p)

    def test_pax_path_overrides_harmless_header(self):
        self.refused(self.tar([{"name": "harmless", "data": b"pwn", "pax": {"path": "../evil"}}]))

    def test_empty_name(self):
        self.refused(self.tar([{"name": "x", "data": b"pwn", "pax": {"path": ""}}]))

    def test_duplicate_file(self):
        self.refused(self.tar([("a.txt", tarfile.REGTYPE, b"1"), ("a.txt", tarfile.REGTYPE, b"2")]))
        self.refused(self.zip([("a.txt", b"1"), ("a.txt", b"2")]))

    def test_duplicate_after_normalisation(self):
        self.refused(self.tar([("a.txt", tarfile.REGTYPE, b"1"), ("./a.txt", tarfile.REGTYPE, b"2")]))
        self.refused(self.tar([("d", tarfile.DIRTYPE), ("d/", tarfile.DIRTYPE)]))

    def test_file_and_folder_with_same_name(self):
        self.refused(self.tar([("a", tarfile.REGTYPE, b"1"), ("a", tarfile.DIRTYPE)]))
        self.refused(self.tar([("a", tarfile.DIRTYPE), ("a", tarfile.REGTYPE, b"1")]))

    def test_file_under_a_file(self):
        self.refused(self.tar([("a", tarfile.REGTYPE, b"1"), ("a/b", tarfile.REGTYPE, b"2")]))

    def test_error_names_the_entry(self):
        with self.assertRaises(InvalidArgument) as cm:
            self.extract(self.tar([("../sneaky-entry", tarfile.REGTYPE, b"x")]))
        self.assertIn("sneaky-entry", str(cm.exception))


class Links(Case):
    def test_symlink_refused_by_default(self):
        self.refused(self.tar([("l", tarfile.SYMTYPE, b"", 0o777, "target")]))

    def test_zip_symlink_refused_by_default(self):
        info = zipfile.ZipInfo("l")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.refused(self.zip([(info, b"target")]))

    def test_bad_symlinks_value(self):
        for value in ("allow", "", None, True, "INSIDE"):
            with self.subTest(value=value):
                with self.assertRaises(InvalidArgument):
                    self.extract(self.zip({"a.txt": b"x"}), symlinks=value)
                self.assert_clean()

    def test_inside_link_works(self):
        p = self.tar([("d/f.txt", tarfile.REGTYPE, b"x"), ("d/l", tarfile.SYMTYPE, b"", 0o777, "f.txt"),
                      ("up", tarfile.SYMTYPE, b"", 0o777, "d/f.txt"), ("d/e/back", tarfile.SYMTYPE, b"", 0o777, "../f.txt")])
        result = self.extract(p, symlinks="inside")
        self.assertIn("d/l", result)
        self.assertEqual(os.readlink(self.dest / "d/l"), "f.txt")
        self.assertEqual((self.dest / "up").read_bytes(), b"x")
        self.assertEqual((self.dest / "d/e/back").read_bytes(), b"x")

    def test_inside_zip_link_works(self):
        info = zipfile.ZipInfo("l")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.extract(self.zip([("f.txt", b"x"), (info, b"f.txt")]), symlinks="inside")
        self.assertTrue((self.dest / "l").is_symlink())
        self.assertEqual(os.readlink(self.dest / "l"), "f.txt")

    def test_escaping_links_refused(self):
        for name, target in (("l", "../x"), ("l", "/etc/passwd"), ("l", "a/../../x"), ("d/l", "../../x"),
                             ("l", ".."), ("d/l", "../.."), ("l", "/")):
            with self.subTest(name=name, target=target):
                self.refused(self.tar([(name, tarfile.SYMTYPE, b"", 0o777, target)]), symlinks="inside")

    def test_zip_escaping_link_refused(self):
        info = zipfile.ZipInfo("l")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.refused(self.zip([(info, str(self.outside))]), symlinks="inside")

    def try_extract(self, entries):
        """For attacks where refusing or a harmless success are both fine: never anything outside."""
        try:
            self.extract(self.tar(entries), symlinks="inside")
        except InvalidArgument:
            self.assert_clean()
            return False
        self.assert_outside_untouched()
        self.assert_links_stay_inside()
        return True

    def assert_links_stay_inside(self):
        root = os.path.realpath(self.dest)
        for d, dirs, files in os.walk(self.dest):
            for n in dirs + files:
                p = os.path.join(d, n)
                if os.path.islink(p):
                    real = os.path.realpath(p)
                    self.assertTrue(real == root or real.startswith(root + os.sep), f"{p} -> {real} leaves dest")

    def test_file_through_dot_link(self):
        self.try_extract([("l", tarfile.SYMTYPE, b"", 0o777, "."), ("l/x", tarfile.REGTYPE, b"x")])

    def test_file_through_subfolder_link(self):
        self.try_extract([("sub", tarfile.DIRTYPE), ("l", tarfile.SYMTYPE, b"", 0o777, "sub"),
                          ("l/x", tarfile.REGTYPE, b"x")])

    def test_file_overwriting_through_link_target(self):
        # link to an existing file, then a file with the link's name: must not write into the target
        self.refused(self.tar([("f", tarfile.REGTYPE, b"orig"), ("l", tarfile.SYMTYPE, b"", 0o777, "f"),
                               ("l", tarfile.REGTYPE, b"pwn")]), symlinks="inside")

    def test_file_through_escaping_link(self):
        self.refused(self.tar([("l", tarfile.SYMTYPE, b"", 0o777, str(self.outside)),
                               ("l/x", tarfile.REGTYPE, b"pwn")]), symlinks="inside")
        self.refused(self.tar([("l", tarfile.SYMTYPE, b"", 0o777, "../../outside"),
                               ("l/x", tarfile.REGTYPE, b"pwn")]), symlinks="inside")

    def test_file_through_escaping_link_by_default(self):
        self.refused(self.tar([("l", tarfile.SYMTYPE, b"", 0o777, str(self.outside)),
                               ("l/x", tarfile.REGTYPE, b"pwn")]))

    def test_chained_links_escape_lexical_check(self):
        # d/up -> .. is lexically inside (it is the root). d/up/esc -> .. is lexically "d", but really
        # root/esc -> .. = the folder holding the temp root. Then anything under d/up/esc/ goes outside.
        chain = [("d", tarfile.DIRTYPE), ("d/up", tarfile.SYMTYPE, b"", 0o777, ".."),
                 ("d/up/esc", tarfile.SYMTYPE, b"", 0o777, "..")]
        for tail in ([("d/up/esc/pwn.txt", tarfile.REGTYPE, b"pwn")],
                     [("d/up/esc/pwndir", tarfile.DIRTYPE)],
                     [("d/up/esc/pwndir/f.txt", tarfile.REGTYPE, b"pwn")],
                     []):
            with self.subTest(tail=tail):
                shutil.rmtree(self.dest, ignore_errors=True)
                self.try_extract(chain + tail)

    def test_folder_through_escaping_link(self):
        # with "refuse", the link itself fails; with "inside" the escaping link fails
        for symlinks in ("refuse", "inside"):
            with self.subTest(symlinks=symlinks):
                self.refused(self.tar([("l", tarfile.SYMTYPE, b"", 0o777, "../../outside"), ("l/newdir", tarfile.DIRTYPE)]),
                             symlinks=symlinks)

    def test_link_named_like_folder_already_created(self):
        self.refused(self.tar([("a/f", tarfile.REGTYPE, b"x"), ("a", tarfile.SYMTYPE, b"", 0o777, ".")]), symlinks="inside")

    def test_hard_link_refused(self):
        for symlinks in ("refuse", "inside"):
            for target in ("f", "/etc/passwd", "../x"):
                with self.subTest(symlinks=symlinks, target=target):
                    self.refused(self.tar([("f", tarfile.REGTYPE, b"x"), ("h", tarfile.LNKTYPE, b"", 0o644, target)]),
                                 symlinks=symlinks)

    def test_devices_and_fifos_refused(self):
        for kind in (tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE, b"V", b"Z"):
            for symlinks in ("refuse", "inside"):
                with self.subTest(kind=kind, symlinks=symlinks):
                    shutil.rmtree(self.dest, ignore_errors=True)
                    self.refused(self.tar([("ok", tarfile.REGTYPE, b"x"), ("dev", kind, b"", 0o666)], fmt=tarfile.GNU_FORMAT),
                                 symlinks=symlinks)

    def test_zip_fifo_and_device_modes_refused(self):
        for kind in (stat.S_IFIFO, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK):
            with self.subTest(kind=oct(kind)):
                info = zipfile.ZipInfo("dev")
                info.external_attr = (kind | 0o666) << 16
                info.create_system = 3
                self.refused(self.zip([(info, b"")]), symlinks="inside")

    @unittest.skipUnless(HAS_BSDTAR, "bsdtar missing")
    def test_7z_symlink(self):
        src = self.src({"top/f.txt": b"x"}, links=[("top/l", "f.txt")])
        p = self.path(".7z")
        bsdtar(src, p, "--format", "7zip")
        self.refused(p)
        self.extract(p, symlinks="inside")
        self.assertEqual(os.readlink(self.dest / "top/l"), "f.txt")

    @unittest.skipUnless(HAS_BSDTAR, "bsdtar missing")
    def test_tar_zst_escaping_symlink(self):
        src = self.src({"top/f.txt": b"x"}, links=[("top/l", "/etc/passwd")])
        p = self.path(".tar.zst")
        bsdtar(src, p, "--zstd")
        self.refused(p, symlinks="inside")
        self.assertEqual(children(), [])


class Limits(Case):
    def test_max_files(self):
        files = {f"f{i}": b"x" for i in range(5)}
        self.refused(self.zip(files), max_files=4)
        self.refused(self.tar([(n, tarfile.REGTYPE, d) for n, d in files.items()]), max_files=4)
        self.extract(self.zip(files), max_files=5)

    def test_many_small_files_over_max_bytes(self):
        self.refused(self.zip({f"f{i}": b"x" * 300 for i in range(5)}), max_bytes=1000)
        self.extract(self.zip({f"f{i}": b"x" * 200 for i in range(5)}), max_bytes=1000)

    def test_one_big_file(self):
        self.refused(self.tar([("big", tarfile.REGTYPE, b"x" * 1001)]), max_bytes=1000)
        self.extract(self.tar([("big", tarfile.REGTYPE, b"x" * 1000)]), max_bytes=1000)

    def test_zip_bomb(self):
        p = self.path(".zip")
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            with z.open("bomb", "w", force_zip64=True) as f:
                chunk = b"\x00" * (1 << 20)
                for _ in range(64):
                    f.write(chunk)
        self.assertLess(p.stat().st_size, 1 << 20)
        start = time.monotonic()
        self.refused(p, max_bytes=4 << 20)
        self.assertLess(time.monotonic() - start, 10)

    def test_lying_zip_header_size(self):
        # header says 10 bytes, the data inflates to 1 MB: never trust headers
        p = self.zip({"f": b"\x00" * (1 << 20)})
        data = bytearray(p.read_bytes())
        for sig, off in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
            i = data.find(sig)
            data[i + off:i + off + 4] = (10).to_bytes(4, "little")
        p.write_bytes(bytes(data))
        try:
            self.extract(p, max_bytes=1000)
        except InvalidArgument:
            self.assert_clean()
        else:
            self.assertLessEqual((self.dest / "f").stat().st_size, 1000)

    def test_tar_bomb_gz(self):
        p = self.path(".tar.gz")
        with tarfile.open(p, "w:gz") as t:
            big = b"\x00" * (8 << 20)
            t.addfile(*tar_entry("bomb", data=big))
        self.refused(p, max_bytes=1 << 20)

    @unittest.skipUnless(HAS_BSDTAR, "bsdtar missing")
    def test_7z_limits(self):
        p = self.path(".7z")
        bsdtar(self.src({f"f{i}": b"y" * 400 for i in range(5)}), p, "--format", "7zip")
        self.refused(p, max_files=4)
        self.refused(p, max_bytes=1000)
        self.assertEqual(children(), [])


class Modes(Case):
    def mode(self, rel):
        return stat.S_IMODE(os.lstat(self.dest / rel).st_mode)

    def test_tar_modes(self):
        p = self.tar([("d", tarfile.DIRTYPE, b"", 0o1777), ("e", tarfile.DIRTYPE, b"", 0o700),
                      ("d/suid", tarfile.REGTYPE, b"x", 0o4755), ("d/sgid", tarfile.REGTYPE, b"x", 0o2644),
                      ("d/sticky", tarfile.REGTYPE, b"x", 0o1600), ("d/owner_x", tarfile.REGTYPE, b"x", 0o700),
                      ("d/other_x", tarfile.REGTYPE, b"x", 0o601), ("d/group_x", tarfile.REGTYPE, b"x", 0o010),
                      ("d/plain", tarfile.REGTYPE, b"x", 0o600), ("d/world_w", tarfile.REGTYPE, b"x", 0o666),
                      ("d/none", tarfile.REGTYPE, b"x", 0o000), ("d/suid_only", tarfile.REGTYPE, b"x", 0o4000)])
        self.extract(p)
        expect = {"d": 0o755, "e": 0o755, "d/suid": 0o755, "d/sgid": 0o644, "d/sticky": 0o644, "d/owner_x": 0o755,
                  "d/other_x": 0o755, "d/group_x": 0o755, "d/plain": 0o644, "d/world_w": 0o644, "d/none": 0o644,
                  "d/suid_only": 0o644}
        self.assertEqual({k: oct(self.mode(k)) for k in expect}, {k: oct(v) for k, v in expect.items()})

    def test_implicit_folders_are_755(self):
        self.extract(self.zip({"a/b/c.txt": b"x"}))
        self.assertEqual(self.mode("a"), 0o755)
        self.assertEqual(self.mode("a/b"), 0o755)
        self.assertEqual(self.mode("a/b/c.txt"), 0o644)

    def test_zip_modes(self):
        items = []
        for name, mode in (("x", 0o100755), ("s", 0o104755), ("p", 0o100600), ("w", 0o100666)):
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            info.create_system = 3
            items.append((info, b"data"))
        items.append(("nomode", b"data"))
        self.extract(self.zip(items))
        self.assertEqual(self.mode("x"), 0o755)
        self.assertEqual(self.mode("s"), 0o755)
        self.assertEqual(self.mode("p"), 0o644)
        self.assertEqual(self.mode("w"), 0o644)
        self.assertEqual(self.mode("nomode"), 0o644)

    def test_dest_root_mode(self):
        self.extract(self.zip({"a": b"x"}))
        self.assertEqual(stat.S_IMODE(self.dest.stat().st_mode), 0o755)


class Selection(Case):
    def test_only_top_skips_others(self):
        p = self.tar([("bin/a", tarfile.REGTYPE, b"a"), ("lib/b", tarfile.REGTYPE, b"b"), ("share/c", tarfile.REGTYPE, b"c"),
                      ("top.txt", tarfile.REGTYPE, b"t"), ("binx/d", tarfile.REGTYPE, b"d"), ("bin", tarfile.DIRTYPE)])
        result = self.extract(p, only_top=("bin", "lib"))
        self.assertIn("bin/a", result)
        self.assertIn("lib/b", result)
        for skipped in ("share/c", "top.txt", "binx/d"):
            self.assertNotIn(skipped, result)
            self.assertFalse(os.path.lexists(self.dest / skipped))
        self.assertEqual(sorted(os.listdir(self.dest)), ["bin", "lib"])

    def test_only_top_skipped_not_counted(self):
        entries = [("bin/a", tarfile.REGTYPE, b"a")] + [(f"junk/{i}", tarfile.REGTYPE, b"x" * 500) for i in range(10)]
        self.extract(self.tar(entries), only_top=("bin",), max_files=2, max_bytes=100)  # bin and bin/a
        self.assertEqual(self.read("bin/a"), b"a")

    def test_only_top_skipped_link_not_refused(self):
        p = self.tar([("bin/a", tarfile.REGTYPE, b"a"), ("other/l", tarfile.SYMTYPE, b"", 0o777, "/etc/passwd")])
        self.extract(p, only_top=("bin",))
        self.assertEqual(sorted(os.listdir(self.dest)), ["bin"])

    def test_only_top_with_dot_slash(self):
        self.extract(self.tar([("./bin/a", tarfile.REGTYPE, b"a"), ("./x", tarfile.REGTYPE, b"x")]), only_top=("bin",))
        self.assertEqual(sorted(os.listdir(self.dest)), ["bin"])

    def test_single_root(self):
        root = self.extract(self.zip({"pkg/a.txt": b"a", "pkg/sub/b.txt": b"b"}), single_root=True)
        self.assertIsInstance(root, Path)
        self.assertEqual(root, self.dest / "pkg")
        self.assertEqual((root / "sub/b.txt").read_bytes(), b"b")

    def test_single_root_with_explicit_folder_entry(self):
        root = self.extract(self.tar([("./pkg", tarfile.DIRTYPE), ("./pkg/a", tarfile.REGTYPE, b"a")]), single_root=True)
        self.assertEqual(root, self.dest / "pkg")

    def test_single_root_refusals(self):
        cases = {"empty": {}, "two": {"a/x": b"1", "b/y": b"2"}, "file": {"only.txt": b"1"},
                 "folder and file": {"a/x": b"1", "readme": b"r"}}
        for label, files in cases.items():
            with self.subTest(label):
                self.refused(self.zip(files), single_root=True)

    def test_single_root_top_symlink_refused(self):
        p = self.tar([("real/f", tarfile.REGTYPE, b"x"), ("pkg", tarfile.SYMTYPE, b"", 0o777, "real")])
        self.refused(p, single_root=True, only_top=("pkg",), symlinks="inside")

    def test_single_root_with_only_top(self):
        root = self.extract(self.zip({"pkg/a": b"a", "other/b": b"b", "x.txt": b"x"}), single_root=True, only_top=("pkg",))
        self.assertEqual(root, self.dest / "pkg")


class Dest(Case):
    def test_dest_not_empty(self):
        self.dest.mkdir()
        (self.dest / "old.txt").write_text("old")
        with self.assertRaises(InvalidArgument):
            self.extract(self.zip({"a": b"x"}))
        self.assertEqual(os.listdir(self.dest), ["old.txt"])
        self.assertEqual(os.listdir(self.box), ["out"])

    def test_dest_is_a_file(self):
        self.dest.write_text("file")
        with self.assertRaises(InvalidArgument):
            self.extract(self.zip({"a": b"x"}))
        self.assertEqual(self.dest.read_text(), "file")
        self.assertEqual(os.listdir(self.box), ["out"])

    def test_dest_is_a_symlink(self):
        os.symlink(self.outside, self.dest)
        with self.assertRaises((InvalidArgument, Unavailable)):
            self.extract(self.zip({"a": b"x"}))
        self.assertTrue(os.path.islink(self.dest))
        self.assertEqual(os.listdir(self.outside), ["keep.txt"])

    def test_empty_dest_folder_ok(self):
        self.dest.mkdir()
        self.assertIn("a", self.extract(self.zip({"a": b"x"})))
        self.assertEqual(self.read("a"), b"x")
        self.assertEqual(os.listdir(self.box), ["out"])

    def test_empty_dest_kept_on_failure(self):
        self.dest.mkdir()
        self.refused(self.zip({"../evil": b"x"}), dest_was="empty")

    def test_parent_missing(self):
        with self.assertRaises((InvalidArgument, Unavailable)):
            archive.extract(self.zip({"a": b"x"}), self.box / "no" / "out")
        self.assertEqual(os.listdir(self.box), [])

    def test_archive_missing(self):
        with self.assertRaises((InvalidArgument, Unavailable)):
            self.extract(self.work / "nope.zip")
        self.assert_clean()

    def test_failure_late_in_archive_cleans_written_files(self):
        entries = [(f"d/f{i}", tarfile.REGTYPE, b"x" * 100) for i in range(20)] + [("../evil", tarfile.REGTYPE, b"x")]
        self.refused(self.tar(entries, "w:gz", ".tar.gz"))

    def test_temp_dir_inside_dest_parent(self):
        seen = []
        real_mkdtemp = archive.tempfile.mkdtemp

        def spy(*a, **k):
            d = real_mkdtemp(*a, **k)
            seen.append(d)
            return d
        patch(self, "invasor.archive.tempfile.mkdtemp", spy)
        self.extract(self.zip({"a": b"x"}))
        self.assertEqual(len(seen), 1)
        self.assertEqual(Path(seen[0]).parent, self.box)
        self.assertTrue(Path(seen[0]).name.startswith(".extract-"))
        self.assertFalse(os.path.exists(seen[0]))


class Cancel(Case):
    def test_already_set(self):
        ev = threading.Event()
        ev.set()
        with self.assertRaises(Unavailable):
            self.extract(self.zip(FILES), cancel=ev)
        self.assert_clean()

    def test_cancel_half_way(self):
        p = self.zip({f"f{i}": b"x" * 10 for i in range(50)})
        with self.assertRaises(Unavailable):
            self.extract(p, cancel=CancelAfter(5))
        self.assert_clean()

    def test_cancel_inside_big_file(self):
        # checked between 1 MB chunks: one entry, many chunks
        p = self.tar([("big", tarfile.REGTYPE, os.urandom(1 << 20) * 8)], "w:gz", ".tar.gz")
        with self.assertRaises(Unavailable):
            self.extract(p, cancel=CancelAfter(3))
        self.assert_clean()

    def test_unset_event_does_nothing(self):
        self.assertIn("top/a.txt", self.extract(self.zip(FILES), cancel=threading.Event()))

    @unittest.skipUnless(HAS_BSDTAR, "bsdtar missing")
    def test_7z_already_set(self):
        p = self.path(".7z")
        bsdtar(self.src(FILES), p, "--format", "7zip")
        ev = threading.Event()
        ev.set()
        start = time.monotonic()
        with self.assertRaises(Unavailable):
            self.extract(p, cancel=ev)
        self.assertLess(time.monotonic() - start, 5)
        self.assert_clean()
        self.assertEqual(children(), [])

    @unittest.skipUnless(HAS_BSDTAR, "bsdtar missing")
    def test_7z_cancel_half_way_kills_bsdtar(self):
        src = self.src({f"f{i}": os.urandom(1 << 20) for i in range(16)})
        p = self.path(".7z")
        bsdtar(src, p, "--format", "7zip")
        start = time.monotonic()
        with self.assertRaises(Unavailable):
            self.extract(p, cancel=CancelAfter(3))
        self.assertLess(time.monotonic() - start, 10)
        self.assert_clean()
        self.assertEqual(children(), [])


class Damaged(Case):
    def test_truncated(self):
        makers = {
            "zip": lambda: self.zip({"a": os.urandom(5000), "b": b"x" * 5000}),
            "tar.gz": lambda: self.tar([("a", tarfile.REGTYPE, os.urandom(5000))], "w:gz", ".tar.gz"),
            "tar.xz": lambda: self.tar([("a", tarfile.REGTYPE, os.urandom(5000))], "w:xz", ".tar.xz"),
            "tar.bz2": lambda: self.tar([("a", tarfile.REGTYPE, os.urandom(5000))], "w:bz2", ".tar.bz2"),
            "tar": lambda: self.tar([("a", tarfile.REGTYPE, os.urandom(5000))]),
        }
        for label, make in makers.items():
            with self.subTest(label):
                p = make()
                data = p.read_bytes()
                p.write_bytes(data[:len(data) // 2])
                self.refused(p)

    def test_garbage_after_magic(self):
        for magic in (b"\x1f\x8b", b"\xfd7zXZ\x00", b"BZh9", b"PK\x03\x04"):
            with self.subTest(magic=magic):
                p = self.path(".bin")
                p.write_bytes(magic + os.urandom(2000))
                self.refused(p)

    def test_bad_crc(self):
        p = self.zip({"a": b"hello world" * 100}, compression=zipfile.ZIP_STORED)
        data = bytearray(p.read_bytes())
        i = data.find(b"hello world")
        data[i] ^= 0xFF
        p.write_bytes(bytes(data))
        self.refused(p)

    def test_encrypted_zip_entry(self):
        p = self.zip({"a": b"secret" * 10}, compression=zipfile.ZIP_STORED)
        data = bytearray(p.read_bytes())
        for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            i = data.find(sig)
            data[i + off] |= 0x01
        p.write_bytes(bytes(data))
        self.refused(p)

    @unittest.skipUnless(HAS_BSDTAR, "bsdtar missing")
    def test_truncated_7z_and_zst(self):
        for flags, suffix in ((("--format", "7zip"), ".7z"), (("--zstd",), ".tar.zst")):
            with self.subTest(suffix):
                p = self.path(suffix)
                bsdtar(self.src({"a": os.urandom(20000), "b": os.urandom(20000)}), p, *flags)
                data = p.read_bytes()
                p.write_bytes(data[:len(data) // 2])
                self.refused(p)
                self.assertEqual(children(), [])

    @unittest.skipUnless(HAS_BSDTAR, "bsdtar missing")
    def test_garbage_7z(self):
        p = self.path(".7z")
        p.write_bytes(b"7z\xbc\xaf\x27\x1c" + os.urandom(500))
        self.refused(p)
        self.assertEqual(children(), [])


class OtherDev:
    """A stat result that says it lives on another device."""

    def __init__(self, st):
        self._st = st
        self.st_dev = st.st_dev + 12345

    def __getattr__(self, name):
        return getattr(self._st, name)


class SwapDir(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir(self)
        self.new = self.tmp / "staging" / "new"
        self.new.mkdir(parents=True)
        (self.new / "f.txt").write_text("new")
        (self.new / "sub").mkdir()
        (self.new / "sub" / "g.txt").write_text("g")
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.target = self.home / "thing"

    def leftovers(self):
        return [n for n in os.listdir(self.home) if n != "thing"]

    def test_target_missing(self):
        self.assertEqual(archive.swap_dir(self.new, self.target), self.target)
        self.assertEqual((self.target / "f.txt").read_text(), "new")
        self.assertFalse(self.new.exists())
        self.assertEqual(self.leftovers(), [])

    def test_str_args(self):
        self.assertEqual(Path(archive.swap_dir(str(self.new), str(self.target))), self.target)

    def test_target_replaced(self):
        self.target.mkdir()
        (self.target / "old.txt").write_text("old")
        (self.target / "deep").mkdir()
        (self.target / "deep" / "x").write_text("x")
        archive.swap_dir(self.new, self.target)
        self.assertEqual(sorted(os.listdir(self.target)), ["f.txt", "sub"])
        self.assertEqual(self.leftovers(), [])

    def test_final_rename_fails_restores_old(self):
        self.target.mkdir()
        (self.target / "old.txt").write_text("old")
        real = os.rename
        calls = []

        def flaky(src, dst, *a, **k):
            calls.append((src, dst))
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            return real(src, dst, *a, **k)
        patch(self, "invasor.archive.os.rename", flaky)
        with self.assertRaises(Unavailable):
            archive.swap_dir(self.new, self.target)
        self.assertEqual(os.listdir(self.target), ["old.txt"])
        self.assertEqual((self.target / "old.txt").read_text(), "old")
        self.assertEqual(self.leftovers(), [])

    def test_new_not_a_dir(self):
        f = self.tmp / "file"
        f.write_text("x")
        for bad in (f, self.tmp / "missing"):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidArgument):
                    archive.swap_dir(bad, self.target)
                self.assertFalse(self.target.exists())
        self.assertEqual(os.listdir(self.home), [])

    def test_target_parent_missing(self):
        with self.assertRaises(InvalidArgument):
            archive.swap_dir(self.new, self.home / "no" / "thing")
        self.assertTrue((self.new / "f.txt").exists())

    def test_target_is_a_file_replaced_or_refused(self):
        self.target.write_text("file")
        try:
            archive.swap_dir(self.new, self.target)
        except (InvalidArgument, Unavailable):
            self.assertEqual(self.target.read_text(), "file")
        else:
            self.assertEqual((self.target / "f.txt").read_text(), "new")
        self.assertEqual(self.leftovers(), [])

    def fake_other_fs(self):
        new = os.path.realpath(self.new)
        real_stat = os.stat

        def fake(p, *a, **k):
            st = real_stat(p, *a, **k)
            if os.path.realpath(p) == new:
                return OtherDev(st)
            return st
        patch(self, "invasor.archive.os.stat", fake)
        accessor = getattr(__import__("pathlib"), "_NormalAccessor", None)
        if accessor is not None and hasattr(accessor, "stat"):  # Python < 3.11: Path.stat does not look up os.stat
            patch(self, "pathlib._NormalAccessor.stat", staticmethod(fake))
        copy = mock.Mock(wraps=shutil.copytree)
        patch(self, "invasor.archive.shutil.copytree", copy)
        return copy

    def test_other_filesystem_copies(self):
        copy = self.fake_other_fs()
        os.symlink("f.txt", self.new / "link")
        self.target.mkdir()
        (self.target / "old.txt").write_text("old")
        archive.swap_dir(self.new, self.target)
        self.assertTrue(copy.called, "copy branch not taken")
        self.assertEqual((self.target / "f.txt").read_text(), "new")
        self.assertEqual((self.target / "sub" / "g.txt").read_text(), "g")
        self.assertTrue((self.target / "link").is_symlink())
        self.assertNotIn("old.txt", os.listdir(self.target))
        self.assertEqual(self.leftovers(), [])

    def test_other_filesystem_rename_fails(self):
        copy = self.fake_other_fs()
        self.target.mkdir()
        (self.target / "old.txt").write_text("old")
        real = os.rename
        calls = []

        def flaky(src, dst, *a, **k):
            calls.append((src, dst))
            if len(calls) == 2:
                raise OSError(5, "I/O error")
            return real(src, dst, *a, **k)
        patch(self, "invasor.archive.os.rename", flaky)
        with self.assertRaises(Unavailable):
            archive.swap_dir(self.new, self.target)
        self.assertTrue(copy.called)
        self.assertEqual(os.listdir(self.target), ["old.txt"])
        self.assertEqual(self.leftovers(), [])

    def test_other_filesystem_copy_fails(self):
        self.fake_other_fs()
        partial = []

        def broken(src, dst, *a, **k):
            os.mkdir(dst)
            (Path(dst) / "half").write_text("x")
            partial.append(dst)
            raise OSError(28, "No space left on device")
        patch(self, "invasor.archive.shutil.copytree", broken)
        with self.assertRaises(Unavailable):
            archive.swap_dir(self.new, self.target)
        self.assertTrue(partial)
        self.assertFalse(self.target.exists())
        self.assertEqual(os.listdir(self.home), [])
        self.assertTrue((self.new / "f.txt").exists())

class LinkChains(Case):
    """Links that each stay inside on their own but, together, point outside."""

    def link(self, name, target):
        return dict(name=name, kind=tarfile.SYMTYPE, linkname=target)

    def test_a_chain_through_an_earlier_link_is_refused(self):
        p = self.tar([self.link("x/up", ".."), self.link("y", "x/up/.."), ("x/k.txt", tarfile.REGTYPE, b"k")])
        with self.assertRaises(InvalidArgument):
            archive.extract(p, self.dest, symlinks="inside")
        self.assertFalse(self.dest.exists())
        self.assertEqual(os.listdir(self.box), [])

    def test_a_later_link_that_makes_an_earlier_one_point_outside_is_refused(self):
        p = self.tar([self.link("y", "z/.."), self.link("z", "x/up"), self.link("x/up", ".."), ("x/k.txt", tarfile.REGTYPE, b"k")])
        with self.assertRaises(InvalidArgument):
            archive.extract(p, self.dest, symlinks="inside")
        self.assertEqual(os.listdir(self.box), [])

    def test_links_that_stay_inside_still_work(self):
        p = self.tar([("lib/real.so", tarfile.REGTYPE, b"x"), self.link("lib/link.so", "real.so"), self.link("top", "lib")])
        archive.extract(p, self.dest, symlinks="inside")
        self.assertEqual((self.dest / "top" / "link.so").read_bytes(), b"x")


class ImplicitFolders(Case):
    def test_folders_made_for_deep_files_count_too(self):
        p = self.tar([("a/b/c/d/e/f.txt", tarfile.REGTYPE, b"x")])
        with self.assertRaises(InvalidArgument):
            archive.extract(p, self.dest, max_files=3)
        archive.extract(p, self.box / "ok", max_files=6)

    def test_many_files_in_their_own_folders_stay_within_max_files(self):
        p = self.tar([(f"a/{i}/f", tarfile.REGTYPE, b"x") for i in range(3)])  # 3 files + a + 3 folders = 7
        with self.assertRaises(InvalidArgument):
            archive.extract(p, self.dest, max_files=6)
        archive.extract(p, self.box / "ok", max_files=7)

    def test_a_folder_listed_after_its_files_counts_once(self):
        p = self.tar([("a/f", tarfile.REGTYPE, b"x"), ("a", tarfile.DIRTYPE, b"")])
        archive.extract(p, self.dest, max_files=2)


class ModuleArchive(Case):
    """ctx.archive: the same, stopped when the module is unloaded, refused on the event loop."""

    class Ctx:
        def __init__(self):
            self._stopped = threading.Event()

        @property
        def _dead(self):
            return self._stopped.is_set()

    def setUp(self):
        super().setUp()
        self.ctx = self.Ctx()
        self.mod = archive.ModuleArchive(self.ctx)
        self.archive = self.zip({"top/a.txt": b"a"})

    def test_extract_and_swap(self):
        top = self.mod.extract(self.archive, self.dest, single_root=True)
        self.assertEqual(self.mod.swap_dir(top, self.box / "final"), self.box / "final")
        self.assertEqual((self.box / "final" / "a.txt").read_bytes(), b"a")

    def test_unloaded_module_is_refused(self):
        self.ctx._stopped.set()
        with self.assertRaises(Unavailable):
            self.mod.extract(self.archive, self.dest)
        with self.assertRaises(Unavailable):
            self.mod.swap_dir(self.work, self.box / "final")

    def test_unloading_stops_a_running_extract(self):
        # The module's own cancel and the ctx's retirement both count.
        own = threading.Event()
        self.ctx._stopped.set()
        with mock.patch.object(type(self.ctx), "_dead", property(lambda s: False)):  # passed the check, then unloaded
            with self.assertRaises(Unavailable):
                self.mod.extract(self.archive, self.dest, cancel=own)
        self.assertFalse(self.dest.exists())

    def test_on_the_event_loop_it_is_refused_and_the_async_twin_works(self):
        import asyncio

        async def run():
            with self.assertRaises(Unavailable):
                self.mod.extract(self.archive, self.dest)
            return await self.mod.extract_async(self.archive, self.dest, single_root=True)

        self.assertEqual(asyncio.run(run()), self.dest / "top")


if __name__ == "__main__":
    unittest.main()
