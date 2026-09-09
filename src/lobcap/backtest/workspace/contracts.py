"""Versioned workspace contracts and canonical serialization."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from lobcap.config.backtest import BacktestConfig, variant
from lobcap.backtest.types import SampleSpec, jsonable
from lobcap.l2.schema import timestamp_ns


@dataclass(frozen=True)
class StrategyDraft:
    config: BacktestConfig
    name: str
    hypothesis: str = ""
    draft_id: str = ""
    revision: int = 0
    parent_run_id: str | None = None
    research_policy: dict | None = None


def fingerprint(value):
    return hashlib.sha256(json.dumps(jsonable(value), sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def config_from_document(doc):
    """Restore a submitted configuration without temporary files or defaults patches."""
    s = doc["sample"]
    config = BacktestConfig(Path("workspace-submission.yaml"), fingerprint(doc), fingerprint(doc),
        SampleSpec(s["id"], timestamp_ns(s["start"]), timestamp_ns(s["end"]),
            s["warmup_seconds"] * 1_000_000_000, normalization_version=s["normalization_version"]),
        Path(doc["data_root"]), Path(doc["output_path"]))
    return variant(config, **doc["settings"])


def config_from_run_spec(row):
    return config_from_document(row["config_document"])
