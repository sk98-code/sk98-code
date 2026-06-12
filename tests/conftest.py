import json
import zipfile
from pathlib import Path

import pytest

TWB_XML = """<?xml version='1.0' encoding='utf-8' ?>
<workbook version='18.1'>
  <datasources>
    <datasource caption='Sales' name='federated.0abc'>
      <connection class='federated'>
        <named-connections>
          <named-connection name='sqlserver.1'>
            <connection class='sqlserver' server='db.corp.local' dbname='SalesDW' username='svc_tableau'/>
          </named-connection>
        </named-connections>
        <relation name='Custom SQL Query' type='text'>SELECT * FROM dbo.FactSales WHERE Year &gt;= 2020</relation>
      </connection>
      <column caption='Sales' datatype='real' name='[Sales]' role='measure'/>
      <column caption='Region' datatype='string' name='[Region]' role='dimension'/>
      <column caption='Profit Ratio' datatype='real' name='[Calculation_1]' role='measure'>
        <calculation class='tableau' formula='SUM([Profit])/SUM([Sales])'/>
      </column>
      <column caption='Sales per Customer' datatype='real' name='[Calculation_2]' role='measure'>
        <calculation class='tableau' formula='{ FIXED [Customer] : SUM([Sales]) }'/>
      </column>
      <column caption='Running Sales' datatype='real' name='[Calculation_3]' role='measure'>
        <calculation class='tableau' formula='RUNNING_SUM(SUM([Sales]))'/>
      </column>
      <filter class='categorical' column='[Region]'>
        <groupfilter function='filter' expression='USERNAME() = [Owner]'/>
      </filter>
    </datasource>
    <datasource name='Parameters'>
      <column caption='Top N' name='[Parameter 1]' datatype='integer'/>
    </datasource>
  </datasources>
  <worksheets>
    <worksheet name='Sales by Region'>
      <table>
        <panes>
          <pane id='1'>
            <mark class='Bar'/>
            <encodings>
              <color column='[Sales].[Region]'/>
            </encodings>
          </pane>
        </panes>
      </table>
    </worksheet>
    <worksheet name='Trend'>
      <table>
        <panes>
          <pane id='1'>
            <mark class='Line'/>
          </pane>
        </panes>
      </table>
    </worksheet>
  </worksheets>
  <dashboards>
    <dashboard name='Exec Dashboard'>
      <zones/>
    </dashboard>
  </dashboards>
  <actions>
    <action caption='Filter to Region' name='[Action1]'>
      <activation type='on-select'/>
      <source worksheet='Sales by Region'/>
      <filter/>
    </action>
  </actions>
</workbook>
"""

QVS_SCRIPT = """SET ThousandSep=',';
SET DecimalSep='.';
LET vMaxYear = Year(Today());

SECTION ACCESS;
LOAD * INLINE [
ACCESS, USERID, REGION
USER, DOMAIN\\ALICE, EU
USER, DOMAIN\\BOB, US
];
SECTION APPLICATION;

MapRegion:
MAPPING LOAD * INLINE [
Code, Region
1, EU
2, US
];

Sales:
LOAD
    OrderID,
    CustomerID,
    ApplyMap('MapRegion', RegionCode, 'Other') AS Region,
    Amount,
    OrderDate
FROM [lib://Data/sales.qvd] (qvd)
WHERE Year(OrderDate) >= 2020;

Customers:
LOAD CustomerID,
     CustomerName
FROM [customers.csv] (txt, codepage is 65001, embedded labels, delimiter is ',');

LEFT JOIN (Sales)
LOAD CustomerID, Segment RESIDENT Customers;

STORE Sales INTO [lib://Data/sales_clean.qvd] (qvd);
DROP TABLE Customers;
"""


def qliksense_app() -> dict:
    return {
        "properties": {"qTitle": "Sales Sense App"},
        "loadScript": QVS_SCRIPT,
        "objects": [
            {"qInfo": {"qId": "sheet1", "qType": "sheet"}, "qMetaDef": {"title": "Overview"}},
            {
                "qInfo": {"qId": "obj1", "qType": "barchart"},
                "qMetaDef": {"title": "Sales by Region"},
                "qHyperCubeDef": {
                    "qDimensions": [{"qDef": {"qFieldDefs": ["Region"]}}],
                    "qMeasures": [{"qDef": {"qDef": "Sum({<Year={2024}>} Amount)", "qLabel": "Sales 2024"}}],
                },
            },
            {
                "qInfo": {"qId": "m1", "qType": "measure"},
                "qMetaDef": {"title": "Total Sales"},
                "qMeasure": {"qDef": "Sum(Amount)", "qLabel": "Total Sales"},
            },
            {
                "qInfo": {"qId": "d1", "qType": "dimension"},
                "qMetaDef": {"title": "Region Dim"},
                "qDim": {"qFieldDefs": ["Region"]},
            },
        ],
        "variables": [{"qName": "vDiscount", "qDefinition": "0.1"}],
    }


SPOTFIRE_ANALYSIS_XML = """<?xml version="1.0" encoding="utf-8"?>
<AnalysisDocument>
  <Pages>
    <Page Title="Overview">
      <Visualization Type="Spotfire.BarChart" Title="Sales by Region"/>
      <Visualization Type="Spotfire.CrossTablePlot" Title="Detail"/>
    </Page>
    <Page Title="Trends">
      <Visualization Type="Spotfire.LineChart" Title="Monthly Trend"/>
    </Page>
  </Pages>
  <Data>
    <DataTable Name="SalesData">
      <Column Name="Region"/>
      <Column Name="Amount"/>
      <Column Name="OrderDate"/>
    </DataTable>
    <InformationLink Name="SalesIL" Path="/corp/sales"/>
    <CalculatedColumn Name="RunningSales"
        Expression="Sum([Amount]) OVER (AllPrevious([Axis.X]))"/>
  </Data>
  <Scripts>
    <Script Name="ExportButton" Language="IronPython">
      <ScriptCode>from Spotfire.Dxp.Application.Visuals import TablePlot
print("export")</ScriptCode>
    </Script>
    <DataFunction Name="Forecast" FunctionType="TERR">
      <Script>fit &lt;- lm(y ~ x, data=df)</Script>
    </DataFunction>
  </Scripts>
  <Properties>
    <DocumentProperty Name="MaxYear"/>
  </Properties>
</AnalysisDocument>
"""


@pytest.fixture()
def estate_dir(tmp_path: Path) -> Path:
    root = tmp_path / "estate"
    root.mkdir()
    (root / "sales.twb").write_text(TWB_XML, encoding="utf-8")
    (root / "sales_copy.twb").write_text(
        TWB_XML.replace("Exec Dashboard", "Exec Dashboard v2"), encoding="utf-8"
    )
    (root / "reload.qvs").write_text(QVS_SCRIPT, encoding="utf-8")
    (root / "sense_app.json").write_text(json.dumps(qliksense_app()), encoding="utf-8")
    with zipfile.ZipFile(root / "analysis.dxp", "w") as zf:
        zf.writestr("AnalysisDocument.xml", SPOTFIRE_ANALYSIS_XML)
        zf.writestr("binary/part1.bin", b"\x00\x01\x02IronPython\x00\x03")
    (root / "corrupt.dxp").write_bytes(b"not a zip at all")
    return root
