"""Offline tests only: no local decoder, network, app settings or user database."""
import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

SPEC = importlib.util.spec_from_file_location("deployment_video", Path(__file__).with_name("check-video.py"))
assert SPEC and SPEC.loader
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class ControlledVideoError(Exception):
    pass


class VideoChecks(unittest.TestCase):
    def test_protocol_checks_input_not_output_or_substrings(self):
        self.assertTrue(TOOL.has_input_fd(b"Supported protocols:\nInput:\n  fd\n  file\nOutput:\n  pipe\n"))
        self.assertFalse(TOOL.has_input_fd(b"Input:\nfile\nOutput:\nfd\n"))
        self.assertFalse(TOOL.has_input_fd(b"Input:\nfdwhatever\nOutput:\nfile\n"))
        self.assertFalse(TOOL.has_input_fd(b"nonsense"))

    def test_version_only_reports_safe_token_not_configuration_paths(self):
        self.assertEqual(TOOL.parse_version(b"ffmpeg version 9.0.1 Copyright\nconfiguration: /private/path", "ffmpeg"), "9.0.1")
        with self.assertRaises(TOOL.CheckError):
            TOOL.parse_version(b"ffmpeg version /secret/path", "ffmpeg")

    def test_non_linux_never_claims_linux_validation(self):
        with patch.object(TOOL.sys, "platform", "darwin"):
            with self.assertRaisesRegex(TOOL.CheckError, "linux_x64_required"):
                TOOL.linux_only()

    def test_missing_dependency_safe_error(self):
        class Service:
            VideoError = ControlledVideoError

            @staticmethod
            def executables():
                raise ControlledVideoError("do not print installation path")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TOOL.CheckError, "video_dependencies_unavailable"):
                asyncio.run(TOOL.preflight(Service, Path(directory)))

    def test_unsupported_fd_stops_without_weakening_input_arguments(self):
        class Service:
            VideoError = ControlledVideoError
            executables = staticmethod(lambda: ("/usr/bin/ffprobe", "/usr/bin/ffmpeg"))
            run_tool = AsyncMock(side_effect=[b"ffprobe version 6.1 Copyright", b"Input:\nfile\nOutput:\nfd\n", b"enable_drefs use_absolute_path"])

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TOOL.CheckError, "fd_input_protocol_unavailable"):
                asyncio.run(TOOL.preflight(Service, Path(directory)))
            self.assertEqual(Service.run_tool.await_count, 3)

    def test_supported_controlled_tools_record_paths_and_versions_no_media(self):
        class Service:
            VideoError = ControlledVideoError
            executables = staticmethod(lambda: ("/usr/bin/ffprobe", "/usr/bin/ffmpeg"))
            run_tool = AsyncMock(side_effect=[
                b"ffprobe version 9.0.1 Copyright", b"Input:\nfd\nOutput:\npipe\n", b"enable_drefs use_absolute_path",
                b"ffmpeg version 9.0.1 Copyright", b"Input:\nfd\nOutput:\npipe\n", b"enable_drefs use_absolute_path",
            ])

        with tempfile.TemporaryDirectory() as directory:
            result = asyncio.run(TOOL.preflight(Service, Path(directory)))
            self.assertEqual(result["ffprobe"]["version"], "9.0.1")
            self.assertTrue(result["ffmpeg"]["fd_input"])
            self.assertEqual(Service.run_tool.await_count, 6)

    def test_temporary_files_clean_after_synthetic_failure_without_real_decoder(self):
        created = []

        async def failed(service, root):
            created.append(root)
            (root / "controlled.mp4").write_bytes(b"controlled")
            raise TOOL.CheckError("controlled_failure")

        with patch.object(TOOL, "linux_only"), patch.object(TOOL, "preflight", AsyncMock(return_value={})), \
                patch.object(TOOL, "synthetic", failed):
            # Import actual pure service declarations only; no Settings or API app.
            with self.assertRaisesRegex(TOOL.CheckError, "controlled_failure"):
                asyncio.run(TOOL.check("synthetic"))
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists())


if __name__ == "__main__":
    unittest.main()
