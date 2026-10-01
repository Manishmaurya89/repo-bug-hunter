import json

import pytest

from repo_bug_hunter.merge import merge


def job(root, iid, status="submitted", grade=None, model="m"):
    """What one workflow job uploads: runs/demo/ holding one task."""
    run = root / "runs" / "demo"
    (run / "trajs").mkdir(parents=True)
    (run / "trajs" / f"{iid}.json").write_text(json.dumps({"instance_id": iid, "exit_status": status,
                                                           "patch": f"diff {iid} {status}"}))
    (run / "config.json").write_text(json.dumps({"variant": "baseline", "provider": "openrouter",
                                                 "model": model, "effort": None, "instances": [iid]}))
    if grade is not None:
        (run / "eval.json").write_text(json.dumps({iid: grade}))
    return root


def test_merge_combines_jobs_and_replaces_retried_tasks(tmp_path):
    dest = tmp_path / "runs" / "demo"
    day1 = [job(tmp_path / "a1", "a", grade={"resolved": True}),
            job(tmp_path / "b1", "b", status="api_error", grade={"resolved": False, "error": "empty_patch"})]
    assert merge(day1, dest) == 2
    # The next day b is retried and graded, and c ran but its grading step failed.
    day2 = [job(tmp_path / "b2", "b", grade={"resolved": True}), job(tmp_path / "c2", "c")]
    assert merge(day2, dest) == 2

    assert json.loads((dest / "trajs" / "b.json").read_text())["exit_status"] == "submitted"
    assert json.loads((dest / "eval.json").read_text()) == {"a": {"resolved": True}, "b": {"resolved": True}}
    assert json.loads((dest / "config.json").read_text())["instances"] == ["a", "b", "c"]
    preds = [json.loads(line) for line in (dest / "preds.jsonl").read_text().splitlines()]
    assert [(p["instance_id"], p["model_patch"], p["model_name_or_path"]) for p in preds] == [
        ("a", "diff a submitted", "demo"), ("b", "diff b submitted", "demo"), ("c", "diff c submitted", "demo")]


def test_merge_refuses_to_mix_models(tmp_path):
    dest = tmp_path / "runs" / "demo"
    merge([job(tmp_path / "a", "a")], dest)
    with pytest.raises(ValueError, match="model='other'"):
        merge([job(tmp_path / "b", "b", model="other")], dest)
    with pytest.raises(ValueError, match="no results"):
        merge([tmp_path / "empty"], tmp_path / "runs" / "fresh")
