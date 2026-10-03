"""Create license notices and a dependency inventory for the actual build."""

from __future__ import annotations

import contextlib
import importlib.metadata as metadata
import json
import shutil
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def runtime_distributions():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pending = [Requirement(raw) for raw in project["dependencies"]]
    for raw in project["optional-dependencies"]["gpu"]:
        requirement = Requirement(raw)
        with contextlib.suppress(metadata.PackageNotFoundError):
            metadata.distribution(requirement.name)
            pending.append(requirement)
    seen, distributions = set(), []
    while pending:
        requirement = pending.pop()
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        name = canonicalize_name(requirement.name)
        if name in seen:
            continue
        seen.add(name)
        distribution = metadata.distribution(name)
        distributions.append(distribution)
        pending.extend(Requirement(raw) for raw in distribution.requires or [])
    return sorted(distributions, key=lambda dist: dist.metadata["Name"].lower())


def main():
    output = ROOT / "build" / "licenses"
    if output.is_symlink() or output.resolve().parent != (ROOT / "build").resolve():
        raise ValueError("The license output must stay in the build directory.")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    inventory = []
    for distribution in runtime_distributions():
        name = canonicalize_name(distribution.metadata["Name"])
        entry = {
            "name": name,
            "version": distribution.version,
            "bundled": name != "av",
            "license": distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License", "See notices"),
            "project_urls": distribution.metadata.get_all("Project-URL") or [],
        }
        inventory.append(entry)
        folder = output / name
        folder.mkdir(exist_ok=True)
        for file in distribution.files or []:
            if any(part in {"__pycache__", "tests"} for part in file.parts):
                continue
            filename = file.name.lower()
            if not (
                filename.startswith(("license", "licence", "copying", "notice"))
                or "licenses" in file.parts
            ):
                continue
            path = Path(distribution.locate_file(file))
            if not path.is_file() or path.stat().st_size > 2 * 1024**2:
                continue
            # Flatten relative paths, never embed the build machine's absolute path.
            target = folder / "__".join(file.parts[-3:])
            target.write_bytes(path.read_bytes())
        (folder / "metadata.json").write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        (output / "Python-LICENSE.txt").write_bytes(python_license.read_bytes())
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared license notices and inventory for {len(inventory)} runtime distributions.")


if __name__ == "__main__":
    main()
