#!/usr/bin/env python3
"""Normalize a DSView logic CSV and derive observer-only capture facts."""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from phase1_common import sha256, write_new_json


def parse_channels(text: str) -> tuple[str, ...]:
    channels = tuple(part.strip() for part in text.split(",") if part.strip())
    if not channels or len(set(channels)) != len(channels) or any(not channel.isdigit() or not 0 <= int(channel) <= 15 for channel in channels):
        raise SystemExit("channels must be distinct DSView channel numbers from 0 through 15")
    if "0" not in channels or "1" not in channels:
        raise SystemExit("channels must include CH0 epoch and CH1 calibration")
    return channels


SAMPLE_RATE = re.compile(r"^;\s*Sample rate:\s*([0-9]+(?:\.[0-9]+)?)\s*MHz\s*$")
SAMPLE_COUNT = re.compile(r"^;\s*Sample count:\s*([0-9]+(?:\.[0-9]+)?)\s*M Samples\s*$")


def stream_dsview_csv(path: Path, normalized_output: Path, requested_channels: tuple[str, ...], allow_no_epochs: bool) -> tuple[set[str], list[float], float, float | None]:
    sample_rate_hz = sample_count = None
    with path.open(newline="", encoding="utf-8", errors="replace") as source:
        for line in source:
            if match := SAMPLE_RATE.match(line): sample_rate_hz = float(match.group(1)) * 1_000_000
            if match := SAMPLE_COUNT.match(line): sample_count = float(match.group(1)) * 1_000_000
            if line.startswith("Time(s),"):
                header = next(csv.reader([line], skipinitialspace=True))
                break
        else:
            raise SystemExit("DSView CSV is missing its Time(s) header")
        channels = {name.strip() for name in header if name.strip().isdigit()}
        if not set(requested_channels).issubset(channels):
            raise SystemExit("DSView CSV does not contain every requested channel")
        if normalized_output.exists():
            raise FileExistsError(f"refusing to overwrite existing file: {normalized_output}")
        normalized_output.parent.mkdir(parents=True, exist_ok=True)
        rows = csv.DictReader(source, fieldnames=header, skipinitialspace=True)
        changed: set[str] = set()
        epoch_times: list[float] = []
        previous: dict[str, str] | None = None
        final_timestamp: float | None = None
        with normalized_output.open("x", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=["timestamp_s", "CH0", "CH1"])
            writer.writeheader()
            for row in rows:
                timestamp = float(row["Time(s)"].strip())
                current = {channel: row[channel].strip() for channel in requested_channels}
                if previous is None:
                    writer.writerow({"timestamp_s": timestamp, "CH0": current["0"], "CH1": current["1"]})
                else:
                    changed_now = {channel for channel in requested_channels if current[channel] != previous[channel]}
                    changed.update(changed_now)
                    if previous["0"] == "0" and current["0"] == "1":
                        epoch_times.append(timestamp)
                    if "0" in changed_now or "1" in changed_now:
                        writer.writerow({"timestamp_s": timestamp, "CH0": current["0"], "CH1": current["1"]})
                previous = current
                final_timestamp = timestamp
    if previous is None or final_timestamp is None:
        raise SystemExit("DSView CSV has no sample rows")
    if not epoch_times and not allow_no_epochs:
        raise SystemExit("CH0 has no rising epoch edge")
    return changed, epoch_times, final_timestamp, sample_count / sample_rate_hz if sample_count and sample_rate_hz else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("--raw-observer", required=True, type=Path)
    parser.add_argument("--epoch-sequence-start", required=True, type=int)
    parser.add_argument("--discard-leading-epochs", type=int, default=0,
                        help="explicitly retain no mapping for this many earliest CH0 rises")
    parser.add_argument("--allow-no-epochs", action="store_true",
                        help="permit a static baseline with no CH0 rising edge")
    parser.add_argument("--expected-duration-s", required=True, type=float)
    parser.add_argument("--channels", default="0,1,2,3,4,5,6,7", help="comma-separated DSView channels retained in this capture")
    parser.add_argument("--normalized-output", required=True, type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--epochs-output", required=True, type=Path)
    args = parser.parse_args()

    if not args.raw_observer.is_file() or args.raw_observer.stat().st_size == 0:
        raise SystemExit("raw observer file is missing or empty")
    requested_channels = parse_channels(args.channels)
    changed, epoch_times, final_timestamp, declared_duration = stream_dsview_csv(args.csv, args.normalized_output, requested_channels, args.allow_no_epochs)
    if args.discard_leading_epochs < 0 or (epoch_times and args.discard_leading_epochs >= len(epoch_times)) or (not epoch_times and args.discard_leading_epochs):
        raise SystemExit("discard-leading-epochs must retain at least one CH0 rising edge")
    duration = declared_duration if args.allow_no_epochs and not changed and declared_duration is not None else final_timestamp
    complete = duration >= args.expected_duration_s * 0.99

    discarded_epoch_times = epoch_times[:args.discard_leading_epochs]
    retained_epoch_times = epoch_times[args.discard_leading_epochs:]
    epochs = [{"sequence": args.epoch_sequence_start + index, "timestamp_s": timestamp} for index, timestamp in enumerate(retained_epoch_times)]
    write_new_json(args.epochs_output, {"schema_version": "phase1-observer-epochs-v1", "epochs": epochs})
    write_new_json(args.summary_output, {
        "schema_version": "phase1-observer-summary-v1",
        "observer_file_sha256": sha256(args.raw_observer),
        "normalized_observer_file_sha256": sha256(args.normalized_output),
        "epoch_begin_sequences": [entry["sequence"] for entry in epochs],
        "epoch_end_sequences": [entry["sequence"] for entry in epochs],
        "discarded_leading_epoch_count": args.discard_leading_epochs,
        "discarded_leading_epoch_timestamps_s": discarded_epoch_times,
        "captured_channels": list(requested_channels),
        "nonflat_channels": sorted(changed, key=int),
        "capture_complete": complete,
        "duration_s": duration,
        "duration_source": "dsview-sample-count-and-rate" if duration == declared_duration else "last-csv-timestamp",
        "source_csv_sha256": sha256(args.csv),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
