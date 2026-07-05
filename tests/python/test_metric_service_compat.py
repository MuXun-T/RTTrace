from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from desktop.sample_data import write_scenario
from metric.service import MetricEngine
from parser import load_dataset


class MetricServiceCompatTests(unittest.TestCase):
    def test_legacy_metric_engine_forwards_to_core_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            trace_path = write_scenario(Path(temp_dir) / "compat-metric.trace", name="basic")
            dataset = load_dataset(trace_path)
            self.assertTrue(dataset.ok, dataset.message)

            engine = MetricEngine()
            ingested = engine.metric_Ingest(dataset.data.bundle)
            self.assertTrue(ingested.is_ok, ingested.message)

            bundle = dataset.data.bundle
            metrics = engine.metric_Compute(
                bundle.event_stream[0].timestamp_aligned,
                bundle.event_stream[-1].timestamp_aligned,
                {},
            )
            self.assertTrue(metrics.is_ok, metrics.message)
            metric_ids = {item.metric_id for item in metrics.data}
            self.assertIn("response_time", metric_ids)
            self.assertIn("ready_wait_time", metric_ids)


if __name__ == "__main__":
    unittest.main()
