"""Tests for reading an api-export output directory."""

import json

import pytest

from granola_export.export_store import ExportStore
from granola_export.search import MeetingSearcher, SearchQuery

NOTES = {
    "type": "doc",
    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "mine"}]}],
}
SUMMARY = {
    "type": "doc",
    "content": [
        {
            "type": "heading",
            "attrs": {"level": 3},
            "content": [{"type": "text", "text": "Decisions"}],
        },
    ],
}


def _write_export(root, docs, transcripts=None, **extra):
    root.mkdir(parents=True, exist_ok=True)
    (root / "all_meetings.json").write_text(json.dumps({"meetings": docs}))
    tdir = root / "transcripts"
    tdir.mkdir(exist_ok=True)
    for doc_id, segments in (transcripts or {}).items():
        (tdir / f"transcript_{doc_id[:8]}.json").write_text(
            json.dumps({"document_id": doc_id, "transcript": segments})
        )
    for name, data in extra.items():
        (root / f"{name}.json").write_text(json.dumps(data))
    return root


@pytest.fixture
def export_dir(tmp_path):
    docs = [
        {
            "id": "aaaaaaaa-1111",
            "title": "Planning",
            "created_at": "2026-01-05T15:00:00Z",
            "notes": NOTES,
            "last_viewed_panel": {"id": "p1", "title": "Summary", "content": SUMMARY},
            "people": {
                "attendees": [
                    {
                        "email": "a@x.test",
                        "details": {"person": {"name": {"fullName": "Ann"}}},
                    }
                ]
            },
        },
        {
            "id": "bbbbbbbb-2222",
            "title": "Shared",
            "_shared": True,
            "_source": "web",
            "notes": "<p>from the web</p>",
        },
    ]
    transcripts = {
        "aaaaaaaa-1111": [
            {
                "text": "hello budget",
                "source": "microphone",
                "start_timestamp": "2026-01-05T15:00:00Z",
                "end_timestamp": "2026-01-05T15:00:05Z",
            },
        ]
    }
    return _write_export(
        tmp_path / "export",
        docs,
        transcripts,
        folders=[{"id": "f1", "title": "Team", "documents": [{"id": "aaaaaaaa-1111"}]}],
        workspaces=[{"workspace": {"workspace_id": "w1", "display_name": "Acme"}}],
        people=[{"id": "u1", "name": "Me", "email": "me@x.test"}],
        manifest={"export_date": "2026-01-06T00:00:00", "errors": []},
    )


class TestExportStore:
    def test_missing_dir_raises_with_hint(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="api-export --sync"):
            ExportStore(tmp_path / "nope").load()

    def test_meetings_have_panels_and_transcripts(self, export_dir):
        store = ExportStore(export_dir).load()
        meetings = {m.id: m for m in store.meetings()}

        planning = meetings["aaaaaaaa-1111"]
        assert [(p.title, p.content) for p in planning.document.panels] == [
            ("My Notes", "mine"),
            ("Summary", "### Decisions"),
        ]
        assert planning.document.participants == ["Ann"]
        assert planning.created_at.tzinfo is not None
        assert planning.has_transcript
        assert planning.transcript.full_text == "hello budget"
        assert planning.transcript.duration_seconds == 5

        shared = meetings["bbbbbbbb-2222"]
        assert shared.document.is_shared
        assert shared.document.panels[0].content == "from the web"
        assert not shared.has_transcript

    def test_without_transcripts_skips_parsing(self, export_dir):
        store = ExportStore(export_dir).load()

        meetings = list(store.meetings(with_transcripts=False))

        assert all(m.transcript is None for m in meetings)
        assert store.has_transcript("aaaaaaaa-1111")
        assert not store.has_transcript("bbbbbbbb-2222")

    def test_transcript_prefix_collision_checks_full_id(self, tmp_path):
        root = _write_export(
            tmp_path / "e",
            [{"id": "aaaaaaaa-1111"}, {"id": "aaaaaaaa-9999"}],
            {"aaaaaaaa-9999": [{"text": "other"}]},
        )
        store = ExportStore(root).load()

        assert store.get_transcript("aaaaaaaa-1111") is None
        assert store.get_transcript("aaaaaaaa-9999").full_text == "other"

    def test_side_files(self, export_dir):
        store = ExportStore(export_dir).load()

        assert [(w.id, w.name) for w in store.workspaces()] == [("w1", "Acme")]
        assert [f.document_ids for f in store.folders()] == [["aaaaaaaa-1111"]]
        assert [p.email for p in store.people()] == ["me@x.test"]
        assert list(store.calendars()) == []
        stats = store.get_stats()
        assert stats["documents"] == 2
        assert stats["meetings_with_transcripts"] == 1
        assert stats["total_transcript_words"] == 2

    def test_search_finds_notes_and_transcript(self, export_dir):
        searcher = MeetingSearcher(ExportStore(export_dir))

        by_notes = list(searcher.search(SearchQuery(text="decisions")))
        by_transcript = list(searcher.search(SearchQuery(text="budget")))

        assert [r.match_type for r in by_notes] == ["notes"]
        assert [r.match_type for r in by_transcript] == ["transcript"]
