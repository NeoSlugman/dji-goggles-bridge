"""Build the host platform's console launcher and a downloadable archive.

Windows and macOS produce a self-contained archive (ZIP / tar.gz) around the
PyInstaller executable. Linux produces an AppImage from the same onefile
executable, with a desktop entry and icon for desktop integration.
"""

from __future__ import annotations

import argparse
import ctypes.util
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], env: dict | None = None) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env=env)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-install", action="store_true", help="Use installed dependencies")
    args = parser.parse_args(argv)
    if sys.platform not in ("win32", "darwin", "linux"):
        parser.error(
            "Build on Windows, macOS, or Linux; PyInstaller does not cross-compile applications"
        )
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required")

    target = {"win32": "windows", "darwin": "macos", "linux": "linux"}[sys.platform]
    python = ROOT / ".venv" / ("Scripts/python.exe" if target == "windows" else "bin/python")
    if args.skip_install:
        python = python if python.is_file() else Path(sys.executable)
    else:
        if not python.is_file():
            run([sys.executable, "-m", "venv", str(ROOT / ".venv")])
        run([str(python), "-m", "pip", "install", ".[apps,exe]"])
    modules = "import PyInstaller, av, numpy, cv2, usb, aiohttp, aiortc"
    if target == "windows":
        modules += ", libusb_package"
    run([str(python), "-c", modules])

    name = f"dji-goggles-bridge-{target}"
    work = ROOT / "build" / "pyinstaller" / target
    dist = ROOT / "dist" / target
    command = [
        str(python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--console",
        "--noupx",
        "--onefile",
        "--name",
        name,
        "--paths",
        str(ROOT / "src"),
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(ROOT / "build" / "specs"),
        "--collect-all",
        "av",
        "--collect-all",
        "aiortc",
        "--collect-data",
        "apps.goggles_resources",
    ]
    if target == "windows":
        command += ["--collect-all", "libusb_package"]
    if target == "macos":
        # PyUSB loads this library dynamically; bundle it for Macs without Homebrew.
        library = ctypes.util.find_library("usb-1.0")
        if not library or not Path(library).is_file():
            candidates = [
                Path(prefix) / "lib/libusb-1.0.dylib" for prefix in ("/opt/homebrew", "/usr/local")
            ]
            library = next((str(path) for path in candidates if path.is_file()), None)
        if library is None:
            parser.error("Install libusb before building: brew install libusb")
        command += ["--add-binary", f"{Path(library).resolve()}{os.pathsep}."]
    if target == "linux":
        # PyUSB loads this library dynamically; bundle it so the AppImage works
        # on machines without a system libusb.
        library = ctypes.util.find_library("usb-1.0")
        if not library or not Path(library).is_file():
            prefixes = (
                f"/usr/lib/{platform.machine().lower()}-linux-gnu",
                "/usr/lib64",
                "/usr/lib",
                "/usr/local/lib",
            )
            library = next(
                (
                    str(path)
                    for prefix in prefixes
                    for path in (Path(prefix) / "libusb-1.0.so.0",)
                    if path.is_file()
                ),
                None,
            )
        if library is None:
            parser.error(
                "Install libusb before building: sudo apt install libusb-1.0-0 (Debian/Ubuntu) "
                "or sudo dnf install libusb1 (Fedora)"
            )
        command += ["--add-binary", f"{Path(library).resolve()}{os.pathsep}."]
    entry = {"windows": "windows_app.py", "macos": "mac_app.py", "linux": "linux_app.py"}[target]
    command.append(str(ROOT / "src" / "apps" / entry))
    run(command)

    executable = dist / (f"{name}.exe" if target == "windows" else name)
    # Test the launcher and all mode parsers without accessing hardware.
    for options in (["--help"], ["viewer", "--help"], ["server", "--help"], ["webrtc", "--help"]):
        run([str(executable), *options])

    architecture = platform.machine().lower()
    architecture = {"amd64": "x86_64", "aarch64": "arm64"}.get(architecture, architecture)
    archive_base = ROOT / "dist" / f"{name}-{architecture}"
    if target == "windows":
        archive = archive_base.with_suffix(".zip")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.write(executable, executable.name)
            for filename in ("README.md", "LICENSE"):
                output.write(ROOT / filename, filename)
    elif target == "linux":
        archive = build_appimage(name, architecture)
    else:
        # Preserve executable permissions and include only the deliverable files.
        archive = archive_base.with_suffix(".tar.gz")
        with tarfile.open(archive, "w:gz") as output:
            output.add(executable, arcname=f"{name}/{name}")
            for filename in ("README.md", "LICENSE"):
                output.add(ROOT / filename, arcname=f"{name}/{filename}")
    print(f"\nBuilt: {executable}\nDownloadable archive: {archive}")
    return 0


def build_appimage(name: str, architecture: str) -> Path:
    """Package the built executable as an AppImage with desktop integration."""
    appdir = ROOT / "build" / "appimage" / f"{name}.AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    (appdir / "usr" / "bin").mkdir(parents=True)
    (appdir / "usr" / "share" / "icons" / "hicolor").mkdir(parents=True)
    shutil.copy2(ROOT / "dist" / "linux" / name, appdir / "usr" / "bin" / name)
    (appdir / "usr" / "bin" / name).chmod(0o755)
    (appdir / "AppRun").write_text(
        '#!/bin/sh\nexec "$APPDIR/usr/bin/' + name + '" "$@"\n', encoding="utf-8"
    )
    (appdir / "AppRun").chmod(0o755)
    # appimagetool resolves the desktop Icon against the AppDir root.
    shutil.copy2(
        ROOT / "assets" / "icons" / "dji-goggles-bridge-icon-256.png",
        appdir / "dji-goggles-bridge.png",
    )
    # The stable app name keeps menu entries stable across architectures.
    (appdir / "dji-goggles-bridge.desktop").write_text(
        "\n".join(
            (
                "[Desktop Entry]",
                "Name=DJI Goggles Bridge",
                "Comment=Watch DJI Goggles video or stream it to OBS",
                f"Exec={name}",
                "Terminal=true",
                "Type=Application",
                "Icon=dji-goggles-bridge",
                "Categories=AudioVideo;",
                "",
            )
        ),
        encoding="utf-8",
    )
    for size in (128, 256, 512):
        icon_dir = appdir / "usr" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
        icon_dir.mkdir(parents=True)
        shutil.copy2(
            ROOT / "assets" / "icons" / f"dji-goggles-bridge-icon-{size}.png",
            icon_dir / "dji-goggles-bridge.png",
        )
    tool = appimagetool()
    archive = ROOT / "dist" / f"{name}-{architecture}.AppImage"
    # Extract-and-run keeps the packager working where FUSE is unavailable.
    run([tool, str(appdir), str(archive)], env={**os.environ, "APPIMAGE_EXTRACT_AND_RUN": "1"})
    archive.chmod(0o755)
    return archive


def appimagetool() -> str:
    """Return a path to appimagetool, downloading a pinned release if needed."""
    for candidate in ("appimagetool", "appimagetool-x86_64"):
        found = shutil.which(candidate)
        if found:
            return found
    architecture = platform.machine().lower()
    architecture = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "aarch64"}.get(
        architecture, "x86_64"
    )
    tools = ROOT / "build" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    tool = tools / f"appimagetool-{architecture}.AppImage"
    if not tool.is_file():
        url = f"https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-{architecture}.AppImage"
        print(f"Downloading {url} ...")
        urllib.request.urlretrieve(url, tool)
    tool.chmod(0o755)
    return str(tool)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
