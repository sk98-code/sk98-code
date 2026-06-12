from pathlib import Path

from bimigrate.engine.bulk import BulkRunner
from bimigrate.engine.complexity import effort_hours, score_inventory
from bimigrate.engine.dedup import cluster_inventories, dedup_savings_estimate
from bimigrate.engine.scrub import scrub_inventory
from bimigrate.parsers import TableauParser


def test_bulk_runner_isolation_and_resume(estate_dir: Path, tmp_path: Path):
    job_db = tmp_path / "jobs.db"
    runner = BulkRunner(job_db, workers=2)
    files = runner.discover_files([estate_dir])
    assert len(files) == 6  # incl. corrupt.dxp
    runner.enqueue(files)
    stats = runner.run()
    assert stats["done"] == 5
    assert stats["error"] == 1  # corrupt.dxp isolated, batch survived
    assert runner.errors()[0][0].endswith("corrupt.dxp")

    # resume: nothing pending, nothing reprocessed
    stats2 = runner.run()
    assert stats2["done"] == 0 and stats2["error"] == 0
    inventories = runner.load_inventories()
    assert len(inventories) == 5
    runner.close()


def test_complexity_scoring(estate_dir: Path):
    inv = TableauParser().parse(estate_dir / "sales.twb")
    score_inventory(inv)
    assert inv.complexity_score > 0
    assert inv.complexity_band.value in ("simple", "medium", "complex")
    assert effort_hours(inv) >= 1.0


def test_dedup_clusters_near_duplicates(estate_dir: Path):
    a = score_inventory(TableauParser().parse(estate_dir / "sales.twb"))
    b = score_inventory(TableauParser().parse(estate_dir / "sales_copy.twb"))
    clusters = cluster_inventories([a, b], threshold=0.7)
    assert len(clusters) == 1
    assert set(clusters[0].members) == {a.artifact_path, b.artifact_path}
    assert len(clusters[0].retire_candidates) == 1
    savings = dedup_savings_estimate(clusters, [a, b])
    assert savings["saved_effort_hours"] > 0


def test_scrub_removes_sensitive_metadata(estate_dir: Path):
    inv = TableauParser().parse(estate_dir / "sales.twb")
    assert any(c.server == "db.corp.local" for c in inv.connections)
    scrub_inventory(inv)
    assert all(c.server != "db.corp.local" for c in inv.connections)
    assert all((c.server or "").startswith("server-") for c in inv.connections if c.server)
    assert inv.platform_extras["scrubbed"] is True
    # pseudonyms are stable
    inv2 = scrub_inventory(TableauParser().parse(estate_dir / "sales.twb"))
    assert inv.connections[0].server == inv2.connections[0].server
