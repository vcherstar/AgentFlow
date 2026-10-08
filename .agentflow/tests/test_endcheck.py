"""The Task File header (everything above "## Result") ignores trailing blank lines. Regression: a worker that
appended the missing "## Result" heading after an empty line failed the end check although nothing above changed."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import gate  # noqa: E402

TASK = "# T-1: Probe\n\nRole: developer\n\n## Checks\n\n- `npm test` - unit\n"


class HeaderTests(unittest.TestCase):
    def test_appended_result_after_blank_line_keeps_the_hash(self):
        later = TASK + "\n## Result\nOutcome: completed\n"
        self.assertEqual(gate.sha256(gate.header(later)), gate.sha256(gate.header(TASK)))

    def test_existing_baselines_stay_valid(self):
        self.assertEqual(gate.header(TASK), TASK)  # a file ending in one newline hashes as before
        self.assertEqual(gate.header(TASK.replace("\n", "\r\n")), TASK)

    def test_a_real_change_above_result_still_counts(self):
        changed = TASK.replace("- `npm test` - unit", "- `npm test -- --grep x` - unit") + "\n## Result\n"
        self.assertNotEqual(gate.header(changed), gate.header(TASK))


if __name__ == "__main__":
    unittest.main()
