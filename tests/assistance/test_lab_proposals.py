from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from project_control.assistance.lab_proposals import (
    ProposalError, parse_proposal, validate_proposal, verify_artifacts, write_artifacts,
)


def proposal_object(**overrides):
    value = {
        "hypothesis": "the measured behavior changes",
        "source_citations": [{"path": "src/module.py", "sha256": "a" * 64}],
        "artifacts": [{"path": "test_generated.py", "content": "print('ok')\n"}],
        "argv": ["python3", "/proposal/test_generated.py"],
        "measurements": ["exit status", "stdout"],
        "stop_rule": "stop after one result",
        "done": False,
    }
    value.update(overrides)
    return value


class LabProposalTests(unittest.TestCase):
    def test_parses_and_materializes_strict_proposal(self):
        proposal = parse_proposal(proposal_object())
        with tempfile.TemporaryDirectory() as temp:
            root = write_artifacts(proposal, Path(temp) / "proposal")
            verify_artifacts(proposal, root)
            self.assertEqual((root / "test_generated.py").read_text(), "print('ok')\n")
            self.assertEqual((root / "test_generated.py").stat().st_mode & 0o777, 0o400)

    def test_rejects_markdown_extra_keys_duplicate_keys_and_nonfinite_values(self):
        with self.assertRaises(ProposalError):
            parse_proposal("```json\n{}\n```")
        with self.assertRaisesRegex(ProposalError, "exactly"):
            parse_proposal(proposal_object(unreviewed="field"))
        with self.assertRaisesRegex(ProposalError, "duplicate"):
            parse_proposal('{"done":false,"done":true}')
        with self.assertRaisesRegex(ProposalError, "non-finite"):
            parse_proposal('{"x":NaN}')

    def test_rejects_path_escape_bad_tool_and_unselected_or_stale_citation(self):
        for path in ("../outside.py", "/tmp/out.py", "src\\escape.py", "a//b.py"):
            with self.subTest(path=path), self.assertRaises(ProposalError):
                parse_proposal(proposal_object(artifacts=[{"path": path, "content": "x"}]))
        proposal = parse_proposal(proposal_object(argv=["bash", "-c", "true"]))
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            (source / "src").mkdir()
            data = b"value = 1\n"
            (source / "src" / "module.py").write_bytes(data)
            manifest = {"files": [{"path": "src/module.py", "sha256": hashlib.sha256(data).hexdigest()}]}
            matching = parse_proposal(proposal_object(source_citations=[{
                "path": "src/module.py", "sha256": hashlib.sha256(data).hexdigest()}],
                argv=["bash", "-c", "true"]))
            with self.assertRaisesRegex(ProposalError, "tool set"):
                validate_proposal(matching, source_root=source, source_manifest=manifest,
                                  allowed_tools=("python3",))
            wrong = parse_proposal(proposal_object(source_citations=[{"path": "src/module.py", "sha256": "b" * 64}]))
            with self.assertRaisesRegex(ProposalError, "does not match"):
                validate_proposal(wrong, source_root=source, source_manifest=manifest,
                                  allowed_tools=("python3",))

    def test_artifact_verification_detects_replacement_and_extra_files(self):
        proposal = parse_proposal(proposal_object())
        with tempfile.TemporaryDirectory() as temp:
            root = write_artifacts(proposal, Path(temp) / "proposal")
            file = root / "test_generated.py"
            file.chmod(0o600)
            file.write_text("changed")
            file.chmod(0o400)
            with self.assertRaisesRegex(ProposalError, "bytes changed"):
                verify_artifacts(proposal, root)


if __name__ == "__main__":
    unittest.main()
