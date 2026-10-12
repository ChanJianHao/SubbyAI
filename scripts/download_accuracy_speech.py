"""Fetch a small, attributed FLEURS evaluation sample (explicit network action).

FLEURS is CC BY 4.0. These public samples stay in ignored build output, with
their references and source URLs in the manifest. No user audio is uploaded.
"""

from __future__ import annotations

import csv
import json
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def archive_sample(root: Path, config: str, language: str) -> dict:
    """Read one WAV from a streaming archive, without downloading the full corpus."""
    base = f"https://huggingface.co/datasets/google/fleurs/resolve/main/data/{config}"
    with urllib.request.urlopen(base + "/test.tsv", timeout=30) as response:
        rows = list(csv.reader(response.read().decode("utf-8").splitlines(), delimiter="\t"))
    references = {row[1]: row for row in rows if len(row) >= 4}
    with (
        urllib.request.urlopen(base + "/audio/test.tar.gz", timeout=30) as response,
        tarfile.open(fileobj=response, mode="r|gz") as archive,
    ):
        for member in archive:
            name = Path(member.name).name
            if name not in references or not member.isfile():
                continue
            if member.size > 5 * 1024**2:
                raise ValueError("Unexpectedly large speech fixture")
            data = archive.extractfile(member).read()
            (root / f"fleurs-{language}.wav").write_bytes(data)
            row = references[name]
            return {
                "id": f"fleurs-{language}",
                "audio": f"fleurs-{language}.wav",
                "reference": row[2],
                "language": language,
                "tags": ["human", "multilingual", language],
                "attribution": "FLEURS, Google, CC BY 4.0",
                "source": base + "/test.tsv",
                "source_filename": name,
            }
    raise ValueError("No speech fixture found")


def main() -> None:
    root = Path(sys.argv[1])
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    for config, language in (("ko_kr", "ko"), ("ja_jp", "ja"), ("cmn_hans_cn", "zh")):
        query = urllib.parse.urlencode(
            {
                "dataset": "google/fleurs",
                "config": config,
                "split": "test",
                "offset": 0,
                "length": 1,
            }
        )
        try:
            with urllib.request.urlopen(
                "https://datasets-server.huggingface.co/rows?" + query, timeout=30
            ) as response:
                row = json.load(response)["rows"][0]["row"]
        except urllib.error.HTTPError:
            case = archive_sample(root, config, language)
            manifest = [c for c in manifest if c["id"] != case["id"]] + [case]
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"Downloaded {config} from archive", flush=True)
            continue
        audio = row["audio"][0] if isinstance(row["audio"], list) else row["audio"]
        url = audio["src"]
        filename = f"fleurs-{language}.wav"
        urllib.request.urlretrieve(url, root / filename)
        case = {
            "id": f"fleurs-{language}",
            "audio": filename,
            "reference": row.get("transcription", row.get("raw_transcription")),
            "language": language,
            "tags": ["human", "multilingual", language],
            "attribution": "FLEURS, Google, CC BY 4.0",
            "source": f"https://huggingface.co/datasets/google/fleurs/viewer/{config}/test",
        }
        manifest = [c for c in manifest if c["id"] != case["id"]]
        manifest.append(case)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Downloaded {config}", flush=True)


if __name__ == "__main__":
    main()
