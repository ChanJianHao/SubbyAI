"""Exercise the real, staged language-pack installer against hostile input."""

import json
import zipfile

import pytest

from subbyai.translation.packages import PackageManager, _trusted_link


def archive(path, *, complete=True, malicious=False):
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("pack/metadata.json", json.dumps({"from_code": "en", "to_code": "fr"}))
        bundle.writestr("pack/sentencepiece.model", b"fixture tokenizer")
        if complete:
            bundle.writestr("pack/model/model.bin", b"fixture weights")
        if malicious:
            bundle.writestr("../escaped", b"untrusted")


def test_install_is_staged_and_existing_argos_format_is_discoverable(tmp_path):
    manager = PackageManager(tmp_path / "packs")
    path = tmp_path / "pack.zip"
    archive(path)
    manager.install_from_path(path)
    installed = manager.get_installed_packages()
    assert [(pack.from_code, pack.to_code) for pack in installed] == [("en", "fr")]
    assert installed[0].package_path.name == "translate-en_fr"
    assert not list(tmp_path.glob("language-install-*"))


@pytest.mark.parametrize("malicious", [False, True], ids=["incomplete", "traversal"])
def test_failed_install_preserves_existing_pack(tmp_path, malicious):
    manager = PackageManager(tmp_path / "packs")
    good, bad = tmp_path / "good.zip", tmp_path / "bad.zip"
    archive(good)
    manager.install_from_path(good)
    original = (tmp_path / "packs/translate-en_fr/model/model.bin").read_bytes()
    archive(bad, complete=malicious, malicious=malicious)
    with pytest.raises(ValueError):
        manager.install_from_path(bad)
    assert (tmp_path / "packs/translate-en_fr/model/model.bin").read_bytes() == original
    assert not (tmp_path / "escaped").exists()


def test_interrupted_directory_does_not_hide_good_pack(tmp_path):
    manager = PackageManager(tmp_path / "packs")
    path = tmp_path / "good.zip"
    archive(path)
    manager.install_from_path(path)
    (manager.models_dir / "incomplete").mkdir()
    assert len(manager.get_installed_packages()) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://argos-net.com/v1/a.argosmodel",
        "https://example.com/v1/a.argosmodel",
        "https://argos-net.com.evil.example/v1/a.argosmodel",
        "https://user:pw@argos-net.com/v1/a.argosmodel",
        "https://argos-net.com:bad/v1/a.argosmodel",
        "file:///weights",
        "ipfs://untrusted",
    ],
)
def test_pack_downloads_are_limited_to_official_https_host(url):
    assert not _trusted_link(url)


def test_official_pack_download_link_is_accepted():
    assert _trusted_link("https://argos-net.com/v1/translate-en_fr-1_9.argosmodel")


def test_cancelled_stream_removes_partial_language_pack(tmp_path, monkeypatch):
    import threading

    import httpx

    from subbyai.translation import packages

    cancel = threading.Event()
    manager = PackageManager(tmp_path / "packs", cancel)

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"a" * 65536
            cancel.set()
            yield b"b" * 65536

    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream()))
    )
    monkeypatch.setattr(packages.httpx, "Client", lambda **kwargs: client)
    pack = packages.AvailablePackage(
        {"from_code": "en", "to_code": "fr", "links": ["https://argos-net.com/v1/pack"]},
        manager,
    )
    with pytest.raises(InterruptedError):
        pack.download()
    assert not list(manager.downloads_dir.iterdir())


def test_pack_replace_refuses_linked_backup_without_deleting_external_data(tmp_path, monkeypatch):
    from subbyai.translation import packages

    manager = PackageManager(tmp_path / "packs")
    path = tmp_path / "pack.zip"
    archive(path)
    external = tmp_path / "external"
    external.mkdir()
    marker = external / "keep.txt"
    marker.write_text("private data")
    # Model a junction without requiring administrator symlink permissions.
    original = packages._is_link
    monkeypatch.setattr(
        packages,
        "_is_link",
        lambda candidate: candidate.name.startswith(".previous-") or original(candidate),
    )
    with pytest.raises(ValueError, match="storage folder"):
        manager.install_from_path(path)
    assert marker.read_text() == "private data"
    assert not list(tmp_path.glob("language-install-*"))
