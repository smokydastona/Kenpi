import asyncio
import tempfile
import unittest
from pathlib import Path

from kenpi.media.library import LocalMediaLibrary, MediaError, categorize
from kenpi.media.service import MediaEntry


class WritingProvider:
    """Explicit external-boundary test double for a camera media provider."""

    def __init__(self, data: bytes = b"video-bytes", fail: BaseException | None = None):
        self.data = data
        self.fail = fail

    async def list_files(self, timeout_seconds: float):
        return ()

    async def download(self, camera_id: str, destination: str, timeout_seconds: float):
        Path(destination).write_bytes(self.data[: len(self.data) // 2])
        if self.fail:
            raise self.fail
        Path(destination).write_bytes(self.data)


class LibraryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.library = LocalMediaLibrary(self.root, minimum_free_bytes=0)

    async def test_download_is_atomic_and_listed(self) -> None:
        entry = MediaEntry("cam-1", "clip_001.mp4", size_bytes=11)
        media = await self.library.download(WritingProvider(), entry, 5)
        self.assertEqual(media.category, "video")
        self.assertEqual((self.root / "clip_001.mp4").read_bytes(), b"video-bytes")
        self.assertEqual([m.name for m in self.library.list_media()], ["clip_001.mp4"])
        self.assertEqual(list(self.root.glob("*.part")), [])

    async def test_failed_download_leaves_no_partial_or_final_file(self) -> None:
        entry = MediaEntry("cam-1", "clip_002.mp4")
        for failure in (OSError("link dropped"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                with self.assertRaises(type(failure)):
                    await self.library.download(WritingProvider(fail=failure), entry, 5)
                self.assertEqual(list(self.root.iterdir()), [])

    async def test_never_overwrites_existing_media(self) -> None:
        (self.root / "clip_003.mp4").write_bytes(b"original")
        with self.assertRaises(MediaError):
            await self.library.download(
                WritingProvider(), MediaEntry("c", "clip_003.mp4"), 5
            )
        self.assertEqual((self.root / "clip_003.mp4").read_bytes(), b"original")

    async def test_size_mismatch_is_rejected(self) -> None:
        with self.assertRaises(MediaError):
            await self.library.download(
                WritingProvider(), MediaEntry("c", "clip_004.mp4", size_bytes=999), 5
            )
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_insufficient_space_refuses_before_download(self) -> None:
        library = LocalMediaLibrary(self.root, minimum_free_bytes=2**60)
        with self.assertRaises(MediaError):
            await library.download(WritingProvider(), MediaEntry("c", "a.mp4"), 5)

    def test_unsafe_names_are_rejected(self) -> None:
        for name in ("../x.mp4", "a/b.mp4", "", ".hidden.mp4", "x.mp4.part", "a" * 200):
            with self.subTest(name=name), self.assertRaises(MediaError):
                self.library.open_path(name)

    def test_symlinks_are_neither_listed_nor_served(self) -> None:
        target = Path(self._tmp.name).parent / "outside.txt"
        target.write_text("secret")
        self.addCleanup(target.unlink)
        (self.root / "link.mp4").symlink_to(target)
        self.assertEqual(self.library.list_media(), ())
        with self.assertRaises(MediaError):
            self.library.open_path("link.mp4")

    def test_recovery_removes_only_partials(self) -> None:
        (self.root / "done.mp4").write_bytes(b"x")
        (self.root / "half.mp4.part").write_bytes(b"y")
        self.assertEqual(
            self.library.recover_interrupted_transfers(), ("half.mp4.part",)
        )
        self.assertTrue((self.root / "done.mp4").exists())

    def test_delete_removes_only_local_copy(self) -> None:
        (self.root / "done.mp4").write_bytes(b"x")
        self.library.delete_local_copy("done.mp4")
        self.assertFalse((self.root / "done.mp4").exists())
        with self.assertRaises(MediaError):
            self.library.delete_local_copy("done.mp4")

    def test_categories(self) -> None:
        self.assertEqual(categorize("a.mp4"), "video")
        self.assertEqual(categorize("EVENT_a.mp4"), "event_video")
        self.assertEqual(categorize("a.JPG"), "photo")
        self.assertEqual(categorize("a.bin"), "other")
