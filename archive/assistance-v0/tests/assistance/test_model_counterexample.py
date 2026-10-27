"""Parser guards for the live A31 to LAB bridge; no live result is fabricated."""
from __future__ import annotations

import unittest

from scripts.pa1_model_counterexample import (
    CounterexampleError, extract_model_test, validate_unittest_source,
)


VALID_TEST = (
    "import unittest\n"
    "from demo.pairs import pair_sum\n"
    "\n"
    "class OddTailTest(unittest.TestCase):\n"
    "    def test_last_item_is_included(self):\n"
    "        self.assertEqual(pair_sum([2, 3, 5]), 10)\n"
)


class ModelCounterexampleParserTests(unittest.TestCase):
    def test_extracts_exact_single_model_code_block_and_discriminating_input(self):
        answer = "Test input [2, 3, 5], expected total 10.\n```python\n" + VALID_TEST + "```\n"
        source, details = extract_model_test(answer)
        self.assertEqual(source, VALID_TEST)
        self.assertEqual(details["input"], [2, 3, 5])
        self.assertEqual(details["expected_total"], 10)

    def test_requires_exactly_one_python_block_without_fallback(self):
        with self.assertRaisesRegex(CounterexampleError, "exactly one"):
            extract_model_test("The odd tail should be included; no test source was supplied.")
        with self.assertRaisesRegex(CounterexampleError, "exactly one"):
            extract_model_test("```python\n" + VALID_TEST + "```\n```python\n" + VALID_TEST + "```")

    def test_rejects_nondiscriminating_and_unrestricted_test_programs(self):
        for source in (
            VALID_TEST.replace("[2, 3, 5]), 10", "[2, 3]), 5"),
            VALID_TEST.replace("[2, 3, 5]), 10", "[2, 3, 0]), 5"),
            VALID_TEST.replace("import unittest", "import os\nimport unittest"),
            VALID_TEST.replace("10)", "sum([2, 3, 5]))"),
        ):
            with self.subTest(source=source):
                with self.assertRaises(CounterexampleError):
                    validate_unittest_source(source)


if __name__ == "__main__":
    unittest.main()
