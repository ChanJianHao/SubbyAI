"""Fail release builds when tag, project metadata and app version disagree."""

import argparse
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from subbyai.branding import VERSION  # noqa: E402


def read_windows_versions(path: Path) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Read compiled fixed PE versions, independently of friendly display strings."""
    if sys.platform != "win32":
        raise OSError("Windows artifact version checks require Windows.")
    import ctypes
    import struct
    from ctypes import wintypes

    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    ]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [
        wintypes.LPCVOID, wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.UINT),
    ]
    version.VerQueryValueW.restype = wintypes.BOOL
    filename = str(path.resolve())
    ignored = wintypes.DWORD()
    size = version.GetFileVersionInfoSizeW(filename, ctypes.byref(ignored))
    if not 52 <= size <= 16 * 1024**2:
        raise ValueError(f"{path.name}: missing or invalid compiled version resource.")
    data = ctypes.create_string_buffer(size)
    address, length = wintypes.LPVOID(), wintypes.UINT()
    if not version.GetFileVersionInfoW(filename, 0, size, data) or not version.VerQueryValueW(
        data, "\\", ctypes.byref(address), ctypes.byref(length),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if length.value != 52:
        raise ValueError(f"{path.name}: invalid fixed version payload.")
    fields = struct.unpack("<13I", ctypes.string_at(address, length.value))
    if fields[0] != 0xFEEF04BD:
        raise ValueError(f"{path.name}: invalid fixed version signature.")

    def parts(high: int, low: int) -> tuple[int, ...]:
        return high >> 16, high & 0xFFFF, low >> 16, low & 0xFFFF

    return parts(fields[2], fields[3]), parts(fields[4], fields[5])


def check_windows_artifact(path: Path, expected: str) -> None:
    parts = tuple(map(int, expected.split(".")))
    expected_parts = parts + (0,) * (4 - len(parts))
    file_version, product_version = read_windows_versions(path)
    if file_version != expected_parts or product_version != expected_parts:
        raise ValueError(
            f"{path.name}: compiled file/product versions {file_version}/{product_version} "
            f"do not match {expected_parts}.",
        )
    print(f"{path.name}: compiled file and product versions match {expected}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-executable", type=Path)
    parser.add_argument("--windows-installer", type=Path)
    args = parser.parse_args()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    tag = os.environ.get("GITHUB_REF_NAME", "")
    tag_mismatch = os.environ.get("GITHUB_REF_TYPE") == "tag" and tag != "v" + project["version"]
    if tag_mismatch or project["version"] != VERSION:
        print("Release tag, pyproject.toml and app version must agree.")
        return 1
    try:
        for artifact in (args.windows_executable, args.windows_installer):
            if artifact is not None:
                check_windows_artifact(artifact, project["version"])
    except (OSError, ValueError) as exc:
        print(str(exc))
        return 1
    print("Release version is consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
