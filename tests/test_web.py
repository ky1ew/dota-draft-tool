from __future__ import annotations

from pathlib import Path

import pytest

from draft_engine.server import WEB_DIR


def test_web_assets_exist():
    assert (WEB_DIR / "index.html").is_file()
    assert (WEB_DIR / "static" / "app.js").is_file()
    assert (WEB_DIR / "static" / "styles.css").is_file()


@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent / "cache" / "heroes.json").exists(),
    reason="OpenDota cache not built",
)
def test_draft_session_snapshot_shape():
    import argparse

    from draft_engine.server import DraftSession

    args = argparse.Namespace(
        refresh=False,
        refresh_synergy=False,
        synergy=False,
        order="7.40",
        side="radiant",
        lookahead=False,
        lookahead_depth=None,
    )
    session = DraftSession(args)
    snap = session.snapshot()

    assert snap["step"] == 0
    assert snap["total"] == 24
    assert snap["turn"]["action"] == "ban"
    assert len(snap["heroes"]) == 127
    assert len(snap["suggestions"]["items"]) == 5
    assert len(snap["order"]) == 24
