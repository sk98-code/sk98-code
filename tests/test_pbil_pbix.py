"""PBIX packaging tests: UTF-16 layout decoding, thin-report detection,
DataMashup extraction, format auto-detection."""

import pytest

from pbi_lineage import detect_format, read_any, read_pbix

from .pbil_fixtures import (
    THIN_CONNECTIONS,
    alias_ref,
    layout,
    make_datamashup,
    section,
    visual_container,
    write_pbix,
)


def _simple_layout() -> dict:
    container = visual_container(
        prototype_query={
            "Version": 2,
            "From": [{"Name": "s", "Entity": "Sales", "Type": 0}],
            "Select": [{"Column": {"Expression": alias_ref("s"), "Property": "Qty"}, "Name": "Sales.Qty"}],
        }
    )
    return layout(sections=[section(visual_containers=[container])])


def test_utf16le_without_bom(tmp_path):
    path = write_pbix(tmp_path / "nobom.pbix", _simple_layout(), bom=False)
    result = read_pbix(path)
    assert result.report is not None
    assert result.report.pages[0].visuals[0].fields() == [("Sales", "Qty", "column")]


def test_utf16le_with_bom(tmp_path):
    path = write_pbix(tmp_path / "bom.pbix", _simple_layout(), bom=True)
    result = read_pbix(path)
    assert result.report is not None
    assert result.report.pages[0].visuals[0].fields() == [("Sales", "Qty", "column")]


def test_utf8_layout_fallback(tmp_path):
    # some third-party writers emit UTF-8
    path = write_pbix(tmp_path / "utf8.pbix", _simple_layout(), encoding="utf-8")
    result = read_pbix(path)
    assert result.report is not None
    assert result.report.pages[0].visuals


def test_thin_report_detection(tmp_path):
    # §5.4.1: no DataModel part + pbiazure connection → thin report
    path = write_pbix(
        tmp_path / "thin.pbix",
        _simple_layout(),
        include_datamodel=False,
        connections=THIN_CONNECTIONS,
    )
    result = read_pbix(path)
    assert result.is_thin
    assert result.connection is not None
    assert result.connection.dataset_id == "11111111-2222-3333-4444-555555555555"


def test_fat_report_is_not_thin_and_warns_about_abf(tmp_path):
    path = write_pbix(tmp_path / "fat.pbix", _simple_layout(), include_datamodel=True)
    result = read_pbix(path)
    assert not result.is_thin
    assert result.model is None
    assert any("DataModel" in w for w in result.warnings)


def test_dataset_id_from_connection_string_when_no_remote_artifacts(tmp_path):
    connections = {
        "Version": 3,
        "Connections": [
            {
                "Name": "EntityDataSource",
                "ConnectionString": "Data Source=pbiazure://api.powerbi.com;Initial Catalog=cafe-babe;",
                "ConnectionType": "pbiServiceLive",
            }
        ],
    }
    path = write_pbix(
        tmp_path / "thin2.pbix", _simple_layout(), include_datamodel=False, connections=connections
    )
    result = read_pbix(path)
    assert result.is_thin
    assert result.connection.dataset_id == "cafe-babe"


def test_datamashup_section1_m_extraction(tmp_path):
    m_code = 'section Section1;\nshared Sales = let Source = Sql.Database("srv", "dw") in Source;'
    path = write_pbix(tmp_path / "m.pbix", _simple_layout(), datamashup=make_datamashup(m_code))
    result = read_pbix(path)
    assert result.m_section == m_code


def test_detect_and_read_any(tmp_path):
    path = write_pbix(tmp_path / "auto.pbix", _simple_layout())
    assert detect_format(path) == "pbix"
    assert read_any(path).source_format == "pbix"
    with pytest.raises(ValueError):
        read_any(tmp_path)  # empty dir: not recognizable
