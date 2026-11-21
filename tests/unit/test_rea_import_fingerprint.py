"""rea.import must not read its own summary (dumps/rea/import.json) as Evidence: it changed the job's fingerprint on
every run, so the job and every analysis after it reran on each gst study."""

from __future__ import annotations

import json
from types import SimpleNamespace

from gst.rea import import_index
from gst.rea.extractors import ReaImport


def _ws(tmp_path):
    rea = tmp_path / "dumps" / "rea"
    (rea / "evidence" / "managed_graph").mkdir(parents=True)
    (rea / "evidence" / "managed_graph" / "Game.json").write_text(json.dumps({"normalized_result": {}}), encoding="utf-8")
    (rea / "evidence" / "managed_graph" / "Game.request.json").write_text("{}", encoding="utf-8")
    (rea / "split").mkdir()
    (rea / "split" / "x.json").write_text("{}", encoding="utf-8")
    (rea / "bundle.json").write_text(json.dumps({"records": []}), encoding="utf-8")
    return tmp_path


def test_evidence_files_leave_out_the_import_summary(tmp_path):
    ws = _ws(tmp_path)
    (ws / "dumps" / "rea" / "import.json").write_text(json.dumps({"calls": 1}), encoding="utf-8")
    rels = [p.relative_to(ws / "dumps" / "rea").as_posix() for p in import_index.evidence_files(ws)]
    assert rels == ["evidence/managed_graph/Game.json", "bundle.json"]


def test_rea_import_fingerprint_is_stable_across_its_own_summary(tmp_path):
    ws = _ws(tmp_path)
    ctx = SimpleNamespace(workspace=ws, game={"buildid": "1"})
    before = next(ReaImport().discover(ctx)).fingerprint
    (ws / "dumps" / "rea" / "import.json").write_text(json.dumps({"calls": 2}), encoding="utf-8")  # what run() writes
    assert next(ReaImport().discover(ctx)).fingerprint == before
    (ws / "dumps" / "rea" / "bundle.json").write_text(json.dumps({"records": [{}]}), encoding="utf-8")  # new Evidence
    assert next(ReaImport().discover(ctx)).fingerprint != before
