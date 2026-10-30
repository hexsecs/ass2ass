import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pipe_benchmarks", ROOT / "benchmarks/pipes.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


@unittest.skipUnless(os.name == "posix", "experimental POSIX benchmark only")
class BenchmarkTests(unittest.TestCase):
    def test_codec_page_matches_measured_sizes(self):
        spec = importlib.util.spec_from_file_location("wire_size_benchmarks", ROOT / "benchmarks/codecs.py")
        sizes = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sizes)
        raw = json.loads((ROOT / "docs/results/codecs.json").read_text())
        self.assertEqual(sizes.render(raw), (ROOT / "docs/codec-metrics.md").read_text())
        self.assertEqual(len(raw["rows"]), 32)
        self.assertTrue(all(row["round_trip_preserved"] for row in raw["rows"]))

    def test_committed_leaderboard_is_generated_from_raw_results(self):
        raw = json.loads((ROOT / "docs/results/pipes.json").read_text())
        self.assertEqual(benchmark.render(raw), (ROOT / "docs/leaderboard.md").read_text())
        self.assertEqual(raw["status"], "passed")
        self.assertTrue(all(len(row["latency_ms"]) == row["messages"]
                            for row in raw["trials"] + raw["slow_reader_trials"]))

    def test_deadline_records_failure_without_publishing_rankings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw, markdown = root / "failed.json", root / "leaderboard.md"
            raw.write_text('previous successful results')
            markdown.write_text('previous leaderboard')
            result = subprocess.run([sys.executable, str(ROOT / "benchmarks/pipes.py"),
                                     "--deadline", "0.001", "--output", str(raw), "--markdown", str(markdown)],
                                    capture_output=True, text=True, timeout=5,
                                    env=dict(os.environ, TMPDIR=folder))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads((root / "failed.failed.json").read_text())["status"], "failed")
            self.assertEqual(raw.read_text(), 'previous successful results')
            self.assertEqual(markdown.read_text(), 'previous leaderboard')
            self.assertFalse(any(p.is_dir() for p in root.iterdir()))


if __name__ == "__main__":
    unittest.main()
