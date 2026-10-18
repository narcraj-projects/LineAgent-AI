"""Frozen snapshot, byte reproducibility and JSON round-trip checks."""

import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys

from scripts.generate_data import GROUPS, artifact_bytes, generate, write_dataset
from src.data_models import Benchmark, Dataset, validate_benchmark

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT_SHA256 = "daad2556dd36be2cca9c94452af2f1a4e2c4969a71146deb9ca89752703ff213"


def test_default_seed_matches_frozen_full_artifact_snapshot() -> None:
    artifacts = artifact_bytes(*generate())
    contents = b"".join(name.encode() + b"\0" + value for name, value in sorted(artifacts.items()))
    assert hashlib.sha256(contents).hexdigest() == DEFAULT_SNAPSHOT_SHA256


def test_generator_is_repeatable_without_mutating_global_random_state() -> None:
    state = random.getstate()
    first = artifact_bytes(*generate())
    assert random.getstate() == state
    assert first == artifact_bytes(*generate())
    alternate = artifact_bytes(*generate(17))
    assert alternate == artifact_bytes(*generate(17))
    assert alternate["synthetic/spare_parts.json"] != first["synthetic/spare_parts.json"]
    assert alternate["synthetic/alarms.json"] == first["synthetic/alarms.json"]


def test_written_json_round_trips_and_manifest_hashes_match(tmp_path: Path) -> None:
    counts = write_dataset(tmp_path)
    assert counts["evaluation_questions"] == 30
    manifest = json.loads((tmp_path / "manifest.json").read_bytes())
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
    payload = json.loads((tmp_path / "synthetic/metadata.json").read_bytes())
    payload.pop("notice")
    payload.update({name: json.loads((tmp_path / f"synthetic/{name}.json").read_bytes()) for name in GROUPS})
    data = Dataset.model_validate_json(json.dumps(payload))
    benchmark = Benchmark.model_validate_json(json.dumps({
        "seed": data.seed, "alarm_truth": json.loads((tmp_path / "eval/alarm_truth.json").read_bytes()),
        "questions": json.loads((tmp_path / "eval/questions.json").read_bytes()),
    }))
    validate_benchmark(data, benchmark)
    assert artifact_bytes(data, benchmark) == artifact_bytes(*generate())
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*.json")}
    write_dataset(tmp_path)
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*.json")}


def test_separate_processes_and_hash_seeds_produce_identical_bytes(tmp_path: Path) -> None:
    for index, hash_seed in enumerate(("1", "987")):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        result = subprocess.run(
            [sys.executable, "-m", "scripts.generate_data", "--output-dir", str(tmp_path / str(index))],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["counts"]["work_orders"] == 50
    first = {str(p.relative_to(tmp_path / "0")): p.read_bytes() for p in (tmp_path / "0").rglob("*.json")}
    second = {str(p.relative_to(tmp_path / "1")): p.read_bytes() for p in (tmp_path / "1").rglob("*.json")}
    assert first == second


def test_checked_in_dataset_is_the_frozen_default_snapshot() -> None:
    artifacts = artifact_bytes(*generate())
    for name, content in artifacts.items():
        assert (ROOT / "data" / name).read_bytes() == content, f"Regenerate {name}"
