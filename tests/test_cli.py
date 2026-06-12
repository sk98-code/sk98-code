import json
from pathlib import Path

from click.testing import CliRunner

from bimigrate.cli import main


def test_cli_discover(estate_dir: Path, tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "disc"
    result = runner.invoke(
        main,
        [
            "discover",
            str(estate_dir),
            "--out",
            str(out),
            "--job-db",
            str(out / "jobs.db"),
            "--workers",
            "2",
            "--scrub",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out / "inventory.xlsx").exists()
    assert (out / "inventory.html").exists()
    payload = json.loads((out / "inventory.json").read_text())
    assert payload["summary"]["artifact_count"] == 5
    assert payload["summary"]["by_source_tool"]["tableau"] == 2


def test_cli_assess(estate_dir: Path, tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "assess"
    result = runner.invoke(
        main,
        ["assess", str(estate_dir), "--out", str(out), "--job-db", str(out / "jobs.db"), "--workers", "2"],
    )
    assert result.exit_code == 0, result.output
    assert (out / "feature_mapping_matrix.xlsx").exists()
    unsupported = json.loads((out / "unsupported_features.json").read_text())
    assert all(row["recommendation"] for row in unsupported)


def test_cli_convert(estate_dir: Path, tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "conv"
    result = runner.invoke(
        main,
        [
            "convert",
            str(estate_dir / "sales.twb"),
            "--out",
            str(out),
            "--job-db",
            str(out / "jobs.db"),
            "--workers",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    decisions = json.loads((out / "conversion_decisions.json").read_text())
    assert decisions
    assert {d["tier"] for d in decisions} <= {"AUTO", "ASSISTED", "MANUAL"}
    assert (out / "sales" / "sales.pbip").exists()


def test_cli_validate(estate_dir: Path, tmp_path: Path):
    runner = CliRunner()
    out = tmp_path / "val"
    result = runner.invoke(
        main,
        [
            "validate",
            str(estate_dir / "sales.twb"),
            "--out",
            str(out),
            "--job-db",
            str(out / "jobs.db"),
            "--workers",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads((out / "validation_report.json").read_text())
    assert "accuracy_matrix" in report and "exception_register" in report


def test_cli_kb_init(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        main, ["kb", "init", "--db", str(tmp_path / "kb.db"), "--export-dir", str(tmp_path / "kbx")]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "kbx/conversion_rules.json").exists()
    assert (tmp_path / "kbx/knowledge_base.md").exists()


def test_cli_mappings_export(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        main, ["mappings", "export", "--db", str(tmp_path / "m.db"), "--out", str(tmp_path / "mx")]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "mx/feature_mapping_matrix.xlsx").exists()
