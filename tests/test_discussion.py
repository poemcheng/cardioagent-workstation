import json
from pathlib import Path
import unittest

from cardio.discussion import Discussion, FLAGS, REVIEW_FLAGS


ROOT = Path(__file__).resolve().parents[1]


class DiscussionTests(unittest.TestCase):
    def setUp(self):
        self.discussion = Discussion(ROOT)
        self.analysis = {"id": "analysis-test", "version": 1, "case_id": "demo-ct-positive",
                         "source": {"modality": "CT", "synthetic": True},
                         "recognition": {"candidate_count": 4},
                         "reconstruction": {"geometry": {"verified": True, "units": "mm"}}}
        self.complete = {key: True for key in REVIEW_FLAGS} | {"acute": False}

    def test_unknown_or_unreviewed_fields_remain_incomplete(self):
        result = self.discussion.assess(self.analysis, {})
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(set(result["missing"]), set(FLAGS))
        result = self.discussion.assess(self.analysis, self.complete | {"risk_reviewed": False})
        self.assertEqual(result["missing"], ["risk_reviewed"])
        self.assertEqual(result["checks"][4]["origin"], "human_attestation")

    def test_complete_flags_only_allow_mdt_not_surgery(self):
        result = self.discussion.assess(self.analysis, self.complete)
        self.assertEqual(result["status"], "ready_for_mdt")
        self.assertEqual(result["missing"], [])
        self.assertIsNone(result["recommendation"]["procedure"])
        self.assertFalse(result["surgery_planning_ready"])
        self.assertFalse(result["automatic_orders_created"])
        self.assertFalse(result["model_used"])
        self.assertEqual(len(result["options"]), 3)
        self.assertTrue(all("score" not in option and "rank" not in option for option in result["options"]))

    def test_acute_priority_suppresses_routine_options(self):
        result = self.discussion.assess(self.analysis, {"acute": True})
        self.assertEqual(result["status"], "emergency")
        self.assertEqual(result["options"], [])
        self.assertEqual(result["checks"][0]["status"], "urgent")
        self.assertIsNone(result["recommendation"]["procedure"])

    def test_existing_acute_analysis_cannot_be_cleared_by_discussion_checkbox(self):
        analysis = self.analysis | {"run_snapshot": {"id": "run-acute", "acute": True}}
        result = self.discussion.assess(analysis, self.complete)
        self.assertEqual(result["status"], "emergency")
        self.assertTrue(result["acute_conflict"])
        self.assertTrue(result["context"]["acute"])
        self.assertEqual(result["checks"][0]["origin"], "existing_analysis_snapshot")
        self.assertEqual(result["options"], [])

    def test_strict_tri_state_validation_rejects_numbers_and_strings(self):
        for value in (1, 0, "true", "false", [], {}, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.discussion.assess(self.analysis, {"risk_reviewed": value})
        for context in (None, [], "", {"age": 70}, {"procedure": "CABG"}):
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.discussion.assess(self.analysis, context)

    def test_clinician_note_stays_human_text_without_model_interpretation(self):
        text = "請 Heart Team 複核；這是人工討論備註。"
        result = self.discussion.assess(self.analysis, self.complete | {"clinician_note": text})
        self.assertEqual(result["clinical_note"], {"text": text, "origin": "clinician_entered", "verified": False})
        for note in (None, 12, [], "x" * 4001):
            with self.subTest(note=type(note).__name__), self.assertRaises(ValueError):
                self.discussion.assess(self.analysis, {"clinician_note": note})

    def test_synthetic_verified_geometry_cannot_enable_surgical_planning(self):
        result = self.discussion.assess(self.analysis, self.complete)
        self.assertTrue(result["evidence_snapshot"]["synthetic"])
        self.assertFalse(result["surgery_planning_ready"])
        self.assertTrue(any("合成" in text for text in result["evidence_limits"]))
        self.assertTrue(any("閾值" in text and "手術規劃" in text for text in result["evidence_limits"]))

    def test_mri_and_candidate_count_never_supply_missing_clinical_evidence(self):
        for count in (0, 100):
            analysis = self.analysis | {"source": {"modality": "MRI", "synthetic": False},
                                       "recognition": {"candidate_count": count}}
            result = self.discussion.assess(analysis, {"acute": False})
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(set(result["missing"]), set(REVIEW_FLAGS))
            self.assertTrue(any("MRI" in text for text in result["evidence_limits"]))
            self.assertIsNone(result["recommendation"]["procedure"])

    def test_sources_are_identifiable_offline_summaries_and_copied(self):
        result = self.discussion.assess(self.analysis, {})
        ids = {source["id"] for source in result["sources"]}
        for check in result["checks"]:
            self.assertTrue(set(check["source_ids"]) <= ids)
        self.assertTrue(all(source["url"].startswith("https://") and source["accessed_at"] == "2026-10-31" for source in result["sources"]))
        self.assertEqual(next(s for s in result["sources"] if s["year"] == 2025)["type"], "scientific_statement")
        result["sources"][0]["summary"] = "changed"
        self.assertNotEqual(self.discussion.assess(self.analysis, {})["sources"][0]["summary"], "changed")
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_analysis_input_must_be_structured(self):
        for value in (None, [], "analysis"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.discussion.assess(value, {})


if __name__ == "__main__":
    unittest.main()
