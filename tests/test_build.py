"""Packaging and archive contents for macOS and Linux without a host toolchain."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import build


class BuildTests(unittest.TestCase):
    def test_windows_bundle_contains_usb_runtime_and_only_deliverable_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("Readme", encoding="utf-8")
            (root / "LICENSE").write_text("License", encoding="utf-8")
            executable = root / "dist/windows/dji-goggles-bridge-windows.exe"
            commands = []

            def run(command):
                commands.append(command)
                if "PyInstaller" in command:
                    executable.parent.mkdir(parents=True)
                    executable.write_bytes(b"fake executable")

            with (
                patch.object(build, "ROOT", root),
                patch.object(build.sys, "platform", "win32"),
                patch.object(build.platform, "machine", return_value="AMD64"),
                patch.object(build, "run", side_effect=run),
            ):
                self.assertEqual(build.main(["--skip-install"]), 0)
            package = next(command for command in commands if "PyInstaller" in command)
            collections = [
                package[index + 1]
                for index, value in enumerate(package)
                if value == "--collect-all"
            ]
            self.assertIn("libusb_package", collections)
            self.assertIn("aiortc", collections)
            with build.zipfile.ZipFile(
                root / "dist/dji-goggles-bridge-windows-x86_64.zip"
            ) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"dji-goggles-bridge-windows.exe", "README.md", "LICENSE"},
                )

    def test_macos_embeds_libusb_and_archives_only_the_executable_and_documents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "libusb-1.0.0.dylib"
            library.write_bytes(b"fake libusb")
            (root / "README.md").write_text("Readme", encoding="utf-8")
            (root / "LICENSE").write_text("License", encoding="utf-8")
            name = "dji-goggles-bridge-macos"
            executable = root / "dist/macos" / name
            commands = []

            def run(command):
                commands.append(command)
                if "PyInstaller" in command:
                    executable.parent.mkdir(parents=True)
                    executable.write_bytes(b"fake executable")
                    (executable.parent / "unrelated-file").write_bytes(b"keep out of archive")

            with (
                patch.object(build, "ROOT", root),
                patch.object(build.sys, "platform", "darwin"),
                patch.object(build.platform, "machine", return_value="arm64"),
                patch.object(build.ctypes.util, "find_library", return_value=str(library)),
                patch.object(build, "run", side_effect=run),
            ):
                self.assertEqual(build.main(["--skip-install"]), 0)

            package_command = next(command for command in commands if "PyInstaller" in command)
            self.assertIn("--onefile", package_command)
            binary = package_command[package_command.index("--add-binary") + 1]
            source, destination = binary.rsplit(os.pathsep, 1)
            self.assertTrue(Path(source).samefile(library))
            self.assertEqual(destination, ".")
            self.assertEqual(
                [command for command in commands if command[0] == str(executable)],
                [
                    [str(executable), "--help"],
                    [str(executable), "viewer", "--help"],
                    [str(executable), "server", "--help"],
                    [str(executable), "webrtc", "--help"],
                ],
            )
            with build.tarfile.open(root / "dist" / f"{name}-arm64.tar.gz") as archive:
                self.assertEqual(
                    set(archive.getnames()),
                    {f"{name}/{name}", f"{name}/README.md", f"{name}/LICENSE"},
                )
                self.assertEqual(archive.extractfile(f"{name}/{name}").read(), b"fake executable")

    def test_linux_bundles_libusb_and_packages_appimage_with_desktop_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "libusb-1.0.so.0"
            library.write_bytes(b"fake libusb")
            (root / "README.md").write_text("Readme", encoding="utf-8")
            (root / "LICENSE").write_text("License", encoding="utf-8")
            icons = root / "assets" / "icons"
            icons.mkdir(parents=True)
            for size in (128, 256, 512):
                (icons / f"dji-goggles-bridge-icon-{size}.png").write_bytes(b"fake png")
            name = "dji-goggles-bridge-linux"
            executable = root / "dist/linux" / name
            appimage = root / "dist" / f"{name}-x86_64.AppImage"
            fake_tool = root / "appimagetool"
            commands = []

            def run(command, env=None):
                commands.append((command, env))
                if "PyInstaller" in command:
                    executable.parent.mkdir(parents=True)
                    executable.write_bytes(b"fake executable")
                elif (
                    str(command[0]).endswith("appimagetool")
                    or "appimagetool" in Path(command[0]).name
                ):
                    appimage.parent.mkdir(parents=True, exist_ok=True)
                    appimage.write_bytes(b"fake appimage")

            with (
                patch.object(build, "ROOT", root),
                patch.object(build.sys, "platform", "linux"),
                patch.object(build.platform, "machine", return_value="x86_64"),
                patch.object(build.ctypes.util, "find_library", return_value=str(library)),
                patch.object(build, "appimagetool", return_value=str(fake_tool)),
                patch.object(build, "run", side_effect=run),
            ):
                self.assertEqual(build.main(["--skip-install"]), 0)

            package_command = next(command for command, _ in commands if "PyInstaller" in command)
            self.assertIn(str(root / "src" / "apps" / "linux_app.py"), package_command)
            binary = package_command[package_command.index("--add-binary") + 1]
            source, destination = binary.rsplit(os.pathsep, 1)
            self.assertTrue(Path(source).samefile(library))
            self.assertEqual(destination, ".")

            appimage_call = next(
                (command, env)
                for command, env in commands
                if "appimagetool" in Path(command[0]).name
            )
            appimage_command, appimage_env = appimage_call
            appdir = Path(appimage_command[1])
            self.assertEqual(appdir, root / "build" / "appimage" / f"{name}.AppDir")
            self.assertEqual(appimage_command[2], str(appimage))
            self.assertEqual(appimage_env, {**build.os.environ, "APPIMAGE_EXTRACT_AND_RUN": "1"})

            self.assertEqual((appdir / "usr" / "bin" / name).read_bytes(), b"fake executable")
            self.assertTrue((appdir / "dji-goggles-bridge.png").is_file())
            self.assertIn(
                'exec "$APPDIR/usr/bin/' + name + '" "$@"', (appdir / "AppRun").read_text()
            )
            desktop = (appdir / "dji-goggles-bridge.desktop").read_text()
            self.assertIn("Name=DJI Goggles Bridge", desktop)
            self.assertIn(f"Exec={name}", desktop)
            for size in (128, 256, 512):
                self.assertTrue(
                    (
                        appdir
                        / f"usr/share/icons/hicolor/{size}x{size}/apps/dji-goggles-bridge.png"
                    ).is_file()
                )
            self.assertEqual(appimage.read_bytes(), b"fake appimage")
