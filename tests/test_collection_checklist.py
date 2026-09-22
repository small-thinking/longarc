"""Missing source data is acceptable; skipped collection must remain visible."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "collection_checklist", Path(__file__).parents[1] / "scripts/collection_checklist.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def completed():
    doc = module.template("QQQ")
    doc.update(run_id="fixture", started_at="2026-09-22T14:30:00Z",
               finished_at="2026-09-22T14:35:00Z")
    for item in doc["items"].values():
        item.update(status="blocked", reason="fixture login required", source_ref="login page",
                    checked_at=doc["finished_at"], evidence_ref="fixture-evidence")
    return doc


def test_missing_source_data_does_not_block_analysis():
    doc = completed()
    doc["items"]["quote_times"]["status"] = "not_provided"
    result = module.audit(doc)
    assert result["status"] == "documented_with_missing_data"
    assert result["analysis_allowed"] and not result["trade_approval"]
    assert not result["errors"]


def test_skipped_and_undocumented_sources_are_distinct():
    doc = completed()
    del doc["items"]["dividend_events"]
    doc["items"]["fees"] = {"status": "not_attempted", "reason": "time budget"}
    doc["items"]["quote_times"]["evidence_ref"] = None
    result = module.audit(doc)
    assert {"dividend_events", "fees"} <= set(result["not_attempted"])
    assert "quote_times: evidence_ref required" in result["errors"]


def test_observed_candidates_require_per_contract_fields():
    doc = completed()
    doc["items"]["four_week_candidates"]["status"] = "observed"
    assert module.audit(doc)["errors"]
    doc["contracts"] = [{"contract_id": "QQQ-20261016-C-795", "fields": {}}]
    assert "QQQ-20261016-C-795.probability_touch" in module.audit(doc)["not_attempted"]


def test_no_contracts_is_valid_when_login_blocked():
    result = module.audit(completed())
    assert not result["errors"]
    assert not result["not_attempted"]


def test_malformed_item_is_reported_not_crashed():
    doc = completed()
    doc["items"]["four_week_candidates"] = None
    assert module.audit(doc)["status"] == "needs_followup"
