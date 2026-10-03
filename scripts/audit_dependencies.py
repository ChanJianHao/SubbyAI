"""Query OSV for every pinned dependency, including other supported platforms."""

import json
import tomllib
from pathlib import Path

import httpx
from prepare_release import ROOT


def main():
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    queries = [
        {"package": {"ecosystem": "PyPI", "name": package["name"]}, "version": package["version"]}
        for package in lock["package"] if package["name"] != "subbyai"
    ]
    response = httpx.post(
        "https://api.osv.dev/v1/querybatch", json={"queries": queries}, timeout=60, trust_env=False
    )
    response.raise_for_status()
    results = response.json()["results"]
    entries = [
        {
            "package": query["package"]["name"],
            "version": query["version"],
            "advisories": [vuln["id"] for vuln in result.get("vulns", [])],
        }
        for query, result in zip(queries, results, strict=True)
    ]
    report = {"database": "https://osv.dev/", "packages": entries}
    output: Path = ROOT / "build" / "dependency-audit.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    failures = [entry for entry in entries if entry["advisories"]]
    print(f"Checked {len(entries)} locked packages; {len(failures)} with advisory matches.")
    for entry in failures:
        print(entry["package"], entry["version"], ", ".join(entry["advisories"]))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
