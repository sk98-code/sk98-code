"""Milestones 5–6: VertiPaq size attribution and safe-removal planning."""

import json

import pytest

from pbi_lineage.graph import nid_column, nid_measure
from pbi_lineage.removal import apply_plan, plan_removal
from pbi_lineage.resolve import analyze_model
from pbi_lineage.schema import (
    Column,
    FieldReference,
    Measure,
    Model,
    Page,
    RefKind,
    ReportLayout,
    ReportLevelMeasure,
    Table,
    Visual,
)
from pbi_lineage.size import read_storage_sizes, reclaimable_range


class FakeExecutor:
    def __init__(self, rowsets):
        self.rowsets = rowsets

    def query(self, dmv_query):
        dmv = dmv_query.split("$SYSTEM.")[-1].strip()
        if dmv not in self.rowsets:
            raise RuntimeError(f"unknown DMV {dmv}")
        return self.rowsets[dmv]


# ---------------------------------------------------------------------------
# M5: size attribution
# ---------------------------------------------------------------------------


def _storage_rowsets():
    return {
        "DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS": [
            {"TABLE_ID": "Sales (12)", "COLUMN_ID": "Qty (3)", "USED_SIZE": 1000},
            {"TABLE_ID": "Sales (12)", "COLUMN_ID": "Qty (3)", "USED_SIZE": 500},  # 2nd segment
            {"TABLE_ID": "Sales (12)", "COLUMN_ID": "Region (4)", "USED_SIZE": 200},
            {"TABLE_ID": "H$Sales (12)", "COLUMN_ID": "Qty (3)", "USED_SIZE": 300},  # attr hierarchy
            {"TABLE_ID": "U$Sales (12)", "COLUMN_ID": "Geo Drill (9)", "USED_SIZE": 170},  # user hier
            {"TABLE_ID": "R$Sales (12)", "COLUMN_ID": "Region (4)", "USED_SIZE": 64},  # relationship
        ],
        "DISCOVER_STORAGE_TABLE_COLUMNS": [
            {"TABLE_ID": "Sales (12)", "COLUMN_ID": "Qty (3)", "DICTIONARY_SIZE": 400},
            {"TABLE_ID": "Sales (12)", "COLUMN_ID": "Region (4)", "DICTIONARY_SIZE": 100},
            {"TABLE_ID": "H$Sales (12)", "COLUMN_ID": "Qty (3)", "DICTIONARY_SIZE": 999},  # ignored
        ],
        "DISCOVER_STORAGE_TABLES": [
            {"TABLE_ID": "Sales (12)", "ROWS_COUNT": 12345},
            {"TABLE_ID": "H$Sales (12)", "ROWS_COUNT": 1},
        ],
    }


def test_size_attribution_vertipaq_method():
    report = read_storage_sizes(FakeExecutor(_storage_rowsets()))
    sales = report.tables["Sales"]
    assert sales.row_count == 12345
    assert sales.relationship_bytes == 64
    assert sales.user_hierarchy_bytes == 170

    qty = report.column("Sales", "Qty")
    assert qty.data_bytes == 1500  # segments summed
    assert qty.dictionary_bytes == 400
    assert qty.attribute_hierarchy_bytes == 300
    assert qty.reclaimable_low == 1900  # data + dictionary: the certain floor
    # high bound adds hierarchy structures incl. proportional U$ share
    assert qty.reclaimable_high > qty.reclaimable_low
    # user-hierarchy share is proportional to own size (Qty 1900 of 2200)
    assert qty.user_hierarchy_share_bytes == int(170 * 1900 / 2200)

    region = report.column("Sales", "Region")
    assert (region.data_bytes, region.dictionary_bytes) == (200, 100)


def test_reclaimable_range_never_invents_bytes():
    report = read_storage_sizes(FakeExecutor(_storage_rowsets()))
    low, high = reclaimable_range(report, [("Sales", "Qty"), ("Sales", "NoStorageColumn")])
    assert low == 1900
    assert high >= low


# ---------------------------------------------------------------------------
# M6: removal planning
# ---------------------------------------------------------------------------


def _model():
    return Model(
        name="M",
        tables=[
            Table(
                name="Sales",
                columns=[
                    Column(name="Qty"),
                    Column(name="DiscountCode"),
                    Column(name="Deriv", is_calculated=True, expression="Sales[DiscountCode] * 2"),
                ],
                measures=[Measure(name="Total Sales", expression="SUM(Sales[Qty])")],
            )
        ],
    )


def _report(refs, rl_measures=None):
    return ReportLayout(
        name="R1",
        pages=[Page(name="p1", visuals=[Visual(name="v1", references=refs)])],
        report_level_measures=rl_measures or [],
    )


def _ref(table, name, kind):
    return FieldReference(table=table, name=name, kind=kind, scope="visual:projection", evidence="t")


def test_plan_unused_pair_ordered_dependents_first():
    # Deriv (calc column) depends on DiscountCode; both unused → deletable,
    # Deriv must be scripted before DiscountCode (§7.4 removal order)
    result = analyze_model(_model(), [_report([_ref("Sales", "Total Sales", RefKind.MEASURE)])])
    targets = [nid_column("Sales", "DiscountCode"), nid_column("Sales", "Deriv")]
    plan = plan_removal(result.graph, result.verdicts, targets, model=_model())
    assert not plan.blocked, plan.block_reasons
    assert plan.targets.index(nid_column("Sales", "Deriv")) < plan.targets.index(
        nid_column("Sales", "DiscountCode")
    )
    script = json.loads(plan.tmsl_script)
    operations = script["sequence"]["operations"]
    assert operations[0]["delete"]["object"]["column"] == "Deriv"
    assert operations[1]["delete"]["object"]["column"] == "DiscountCode"
    assert json.loads(plan.backup_script)["backup"]["database"] == "M"
    assert plan.manifest["blocked"] is False


def test_plan_blocks_on_used_target():
    result = analyze_model(_model(), [_report([_ref("Sales", "Total Sales", RefKind.MEASURE)])])
    plan = plan_removal(result.graph, result.verdicts, [nid_column("Sales", "Qty")], model=_model())
    assert plan.blocked
    assert any("Used" in reason for reason in plan.block_reasons)
    # blast radius names the consumers
    kinds = {item.kind for item in plan.impact}
    assert "breaks measure" in kinds and "breaks visual" in kinds


def test_plan_blocks_when_report_level_measure_breaks():
    # §5.4.6: removing a column consumed by a report-level measure is the
    # blast-radius case that must block
    rl = ReportLevelMeasure(name="Adj Margin", table="Sales", expression="SUM(Sales[DiscountCode])")
    result = analyze_model(
        _model(), [_report([_ref("Sales", "Adj Margin", RefKind.MEASURE)], rl_measures=[rl])]
    )
    plan = plan_removal(result.graph, result.verdicts, [nid_column("Sales", "DiscountCode")], model=_model())
    assert plan.blocked
    assert any(item.kind == "breaks report-level measure" for item in plan.impact)
    assert "BLOCKED" in plan.summary()


def test_plan_blocks_incomplete_coverage_targets():
    result = analyze_model(
        _model(),
        [_report([_ref("Sales", "Total Sales", RefKind.MEASURE)])],
        coverage_complete=False,
    )
    plan = plan_removal(result.graph, result.verdicts, [nid_column("Sales", "DiscountCode")], model=_model())
    assert plan.blocked  # §5.4.4 gate reaches the script generator
    assert any("incomplete report coverage" in r for r in plan.block_reasons)


def test_plan_includes_size_range():
    result = analyze_model(_model(), [_report([_ref("Sales", "Total Sales", RefKind.MEASURE)])])
    size_report = read_storage_sizes(
        FakeExecutor(
            {
                "DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS": [
                    {"TABLE_ID": "Sales (1)", "COLUMN_ID": "DiscountCode (2)", "USED_SIZE": 700}
                ],
                "DISCOVER_STORAGE_TABLE_COLUMNS": [
                    {"TABLE_ID": "Sales (1)", "COLUMN_ID": "DiscountCode (2)", "DICTIONARY_SIZE": 300}
                ],
                "DISCOVER_STORAGE_TABLES": [],
            }
        )
    )
    targets = [nid_column("Sales", "DiscountCode"), nid_column("Sales", "Deriv")]
    plan = plan_removal(result.graph, result.verdicts, targets, model=_model(), size_report=size_report)
    assert (plan.reclaimable_low, plan.reclaimable_high) == (1000, 1000)


def test_measures_never_get_bytes():
    # spec: never present "MB saved" for a measure
    result = analyze_model(_model(), [_report([])])
    # Total Sales unused here (no reports reference it)
    plan = plan_removal(
        result.graph,
        result.verdicts,
        [nid_measure("Total Sales")],
        model=_model(),
        size_report=read_storage_sizes(FakeExecutor(_storage_rowsets())),
    )
    assert (plan.reclaimable_low, plan.reclaimable_high) == (0, 0)


def test_apply_refuses_blocked_and_backs_up_first():
    class Recorder:
        def __init__(self):
            self.executed = []

        def execute(self, script):
            self.executed.append(script)

    result = analyze_model(_model(), [_report([_ref("Sales", "Total Sales", RefKind.MEASURE)])])
    good = plan_removal(
        result.graph,
        result.verdicts,
        [nid_column("Sales", "DiscountCode"), nid_column("Sales", "Deriv")],
        model=_model(),
    )
    recorder = Recorder()
    apply_plan(good, recorder)
    assert len(recorder.executed) == 2
    assert '"backup"' in recorder.executed[0]  # backup enforced first

    bad = plan_removal(result.graph, result.verdicts, [nid_column("Sales", "Qty")], model=_model())
    with pytest.raises(ValueError, match="BLOCKED"):
        apply_plan(bad, recorder)
