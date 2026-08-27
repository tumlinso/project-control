from __future__ import annotations

import copy
import json
import unittest

from project_control.models import ArchitectureContextInput
from project_control.services.architecture import architecture_context

try:
    from .cellerator_fixture import cellerator_snapshot
except ImportError:
    from cellerator_fixture import cellerator_snapshot


class ArchitectureContextTests(unittest.TestCase):
    def test_broad_question_preserves_multiple_authority_clusters(self) -> None:
        snapshot = cellerator_snapshot()
        request = ArchitectureContextInput(
            project="cellerator",
            question="current execution image interface tasks source artifacts and performance evidence",
            max_items=20,
        )
        result = architecture_context(snapshot, request)
        themes = {cluster["theme"] for cluster in result.data["clusters"]}
        self.assertTrue({"architecture", "planning", "source", "performance"}.issubset(themes))
        self.assertNotEqual([cluster["theme"] for cluster in result.data["clusters"]], ["source"])
        self.assertEqual(
            result.data["retrieval_basis"]["algorithm"],
            "multi_seed_exact_lexical_then_structured_graph_expansion",
        )
        self.assertTrue(any(
            relation["authority_label"] == "derived_relationship"
            for cluster in result.data["clusters"] for relation in cluster["relationships"]
        ))
        self.assertIn("observation_preconditions", result.data)
        self.assertIn("provider_components", result.data["provenance"])

    def test_output_is_deterministic_and_observation_is_read_only(self) -> None:
        snapshot = cellerator_snapshot()
        before = snapshot.model_dump(mode="json")
        request = ArchitectureContextInput(project="cellerator", question="Execution Image v2 source tests")
        first = architecture_context(snapshot, request).model_dump(mode="json")
        second = architecture_context(snapshot, request).model_dump(mode="json")
        self.assertEqual(first, second)
        self.assertEqual(before, snapshot.model_dump(mode="json"))
        self.assertLessEqual(len(json.dumps(first["data"], sort_keys=True).encode()), 48 * 1024)

    def test_scope_excludes_superseded_candidates_and_reports_missing_evidence(self) -> None:
        snapshot = cellerator_snapshot()
        result = architecture_context(snapshot, ArchitectureContextInput(
            project="cellerator", question="old math runtime CP-MATH", scope="current",
        ))
        ids = {
            seed["id"] for cluster in result.data["clusters"] for seed in cluster["seeds"]
        }
        self.assertNotIn("CP-MATH-17", ids)
        self.assertTrue(all(item["authority_label"] == "missing_evidence" for item in result.data["open_assumptions_or_missing_evidence"]))


if __name__ == "__main__":
    unittest.main()
