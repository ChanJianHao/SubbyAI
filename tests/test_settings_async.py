"""Disk slowness must not block Overlay, and stale snapshots must not win."""

import json
import threading

from subbyai.core.settings import SettingsStore


def test_bounded_writer_coalesces_rapid_changes_and_keeps_latest(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / "settings.json")
    original = store._write
    entered, release = threading.Event(), threading.Event()
    snapshots = []

    def write(settings):
        snapshots.append(settings.overlay.font_size)
        if len(snapshots) == 1:
            entered.set()
            assert release.wait(2)
        original(settings)

    monkeypatch.setattr(store, "_write", write)
    store.settings.overlay.font_size = 20
    store.save_async(lambda *args: None)
    assert entered.wait(1)
    for size in range(21, 50):
        store.settings.overlay.font_size = size
        store.save_async(lambda *args: None)
    release.set()
    assert store.flush_pending(2)
    assert snapshots == [20, 49]
    assert SettingsStore(store.path).settings.overlay.font_size == 49


def test_old_snapshot_cannot_replace_a_newer_synchronous_save(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    generation, snapshot = store._save_snapshot()
    store.settings.overlay.visible = False
    store.save()
    store._write_snapshot(generation, snapshot)
    assert not json.loads(store.path.read_text())["overlay"]["visible"]


def test_async_failure_is_delivered_only_on_owner_and_can_retry(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / "settings.json")
    original = store._write
    completions, errors = [], []
    store.subscribe_errors(lambda message: errors.append((threading.get_ident(), message)))

    def fail(settings):
        raise OSError("settings disk unavailable")

    monkeypatch.setattr(store, "_write", fail)
    store.save_async(lambda *result: completions.append(result))
    assert store.flush_pending(2)
    assert not errors  # worker never touches the UI or its listeners
    store.finish_async_save(*completions[-1])
    assert errors[0][0] == threading.get_ident() and store.last_save_error
    monkeypatch.setattr(store, "_write", original)
    store.save_async(lambda *result: completions.append(result))
    assert store.flush_pending(2)
    store.finish_async_save(*completions[-1])
    assert not store.last_save_error


def test_stale_failure_does_not_hide_a_newer_success(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    old, _ = store._save_snapshot()
    new, _ = store._save_snapshot()
    store.finish_async_save(new, True)
    store.finish_async_save(old, False)
    assert not store.last_save_error
