import json
from pathlib import Path
from bot.system_log import append_entry, MAX_ENTRIES


def test_creates_file_and_appends(tmp_path):
    path = tmp_path / "log.json"
    append_entry(path, level="info", title="hello", detail="world", source="test")
    entries = json.loads(path.read_text())
    assert len(entries) == 1
    e = entries[0]
    assert e["level"] == "info"
    assert e["title"] == "hello"
    assert e["detail"] == "world"
    assert e["source"] == "test"
    assert "id" in e
    assert "timestamp" in e


def test_rolling_cap(tmp_path):
    path = tmp_path / "log.json"
    # Seeded at the cap in one write, then pushed over it through append_entry().
    path.write_text(json.dumps([{"id": str(i), "timestamp": "2026-01-01T00:00:00+00:00", "level": "info",
                                 "title": f"title {i}", "detail": "", "source": "test"}
                                for i in range(MAX_ENTRIES)]))
    for i in range(MAX_ENTRIES, MAX_ENTRIES + 10):
        append_entry(path, "info", f"title {i}", "", "test")
    entries = json.loads(path.read_text())
    assert len(entries) == MAX_ENTRIES
    # Oldest entry should be gone — first surviving entry should be index 10
    assert entries[0]["title"] == f"title {10}"


def test_atomic_write_no_partial(tmp_path):
    path = tmp_path / "log.json"
    append_entry(path, "info", "a", "", "test")
    # tmp file must not remain after write
    assert not (tmp_path / "log.json.tmp").exists()


def test_existing_corrupt_file_is_reset(tmp_path):
    path = tmp_path / "log.json"
    path.write_text("{{broken json")
    append_entry(path, "warning", "b", "", "test")
    entries = json.loads(path.read_text())
    assert len(entries) == 1
