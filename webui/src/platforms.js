// Source-platform registry: drives the selection screen, the sidebar steps
// and every platform-scoped API call. Adding a platform = adding an entry.
export const PLATFORMS = {
  tableau: {
    key: "tableau",
    label: "Tableau",
    vendor: "Salesforce · Desktop / Server / Cloud",
    color: "#E97627",
    mono: "T",
    exts: [".twb", ".twbx"],
    hint: "workbooks & packaged workbooks — Server estates via `bimigrate collect tableau`",
    feats: [
      "Dashboards, stories & actions",
      "Calculated fields, LOD & table calcs",
      "User filters → RLS roles",
      "Extracts, custom SQL & schedules",
    ],
    steps: ["upload", "discover", "mappings", "expression", "convert"],
    examples: [
      "{ FIXED [Customer] : SUM([Sales]) }",
      "RUNNING_SUM(SUM([Sales]))",
      "IF [Sales]>100 THEN 'High' ELSEIF [Sales]>50 THEN 'Mid' ELSE 'Low' END",
      "DATEDIFF('month', [Start], [End])",
    ],
  },
  spotfire: {
    key: "spotfire",
    label: "Spotfire",
    vendor: "TIBCO / Cloud Software Group",
    color: "#0E94D2",
    mono: "S",
    exts: [".dxp"],
    hint: "analysis files — library estates via the Library REST API",
    feats: [
      "Pages, visuals & cross tables",
      "OVER expressions & THEN chains",
      "TERR / R / Python data functions",
      "IronPython automation triage",
    ],
    steps: ["upload", "discover", "mappings", "expression", "ironpython", "convert"],
    examples: [
      "Sum([Sales]) OVER (AllPrevious([OrderDate]))",
      "Sum([Sales]) / Sum([Sales]) OVER (All([Axis.X]))",
      "CASE WHEN [Amount] > 100 THEN 'High' ELSE 'Low' END",
    ],
  },
  qlikview: {
    key: "qlikview",
    label: "QlikView",
    vendor: "Qlik · desktop & Publisher",
    color: "#149B5F",
    mono: "Q",
    exts: [".qvw", ".qvs"],
    hint: "documents & load scripts — pair .qvw with its -prj folder for full fidelity",
    feats: [
      "Set analysis & alternate states",
      "Load scripts → Power Query M",
      "Section Access → RLS roles",
      "QVD pipelines → Lakehouse medallion",
    ],
    steps: ["upload", "discover", "mappings", "expression", "script", "convert"],
    examples: [
      "Sum({<Year={2024}>} Sales)",
      "Avg(Aggr(Sum(Sales), Customer))",
      "Sum({State1<Year={2024}>} Sales)",
      "Sum(TOTAL Sales)",
    ],
  },
  qliksense: {
    key: "qliksense",
    label: "Qlik Sense",
    vendor: "Qlik · client-managed & SaaS",
    color: "#54B948",
    mono: "QS",
    exts: [".qvf", ".json"],
    hint: "apps & Engine JSON exports — server estates via `bimigrate collect qliksense`",
    feats: [
      "Master measures & dimensions",
      "Set analysis & variables",
      "Load scripts → Power Query M",
      "Streams & Section Access → workspaces + RLS",
    ],
    steps: ["upload", "discover", "mappings", "expression", "script", "convert"],
    examples: [
      "Count(DISTINCT CustomerID)",
      "Sum({<Region-={'EU'}, Year=>} Amount)",
      "If(Sum(Sales) > 1000, 'High', 'Low')",
    ],
  },
};

export const STEP_META = {
  upload: { title: "Upload estate", nav: "Upload estate" },
  discover: { title: "Discovery & assessment", nav: "Discovery" },
  mappings: { title: "Feature mappings", nav: "Feature mappings" },
  expression: { title: "Expression → DAX", nav: "Expression → DAX" },
  script: { title: "Load script → Power Query M", nav: "Load script → M" },
  ironpython: { title: "IronPython triage", nav: "IronPython triage" },
  convert: { title: "Convert & export", nav: "Convert & export" },
};

export const SAMPLE_LOAD_SCRIPT = `SET vMaxYear = 2024;

MapRegion:
MAPPING LOAD * INLINE [
Code, Region
1, EU
2, US
];

Sales:
LOAD OrderID, CustomerID,
     ApplyMap('MapRegion', RegionCode, 'Other') AS Region,
     Amount, OrderDate
FROM [lib://Data/sales.qvd] (qvd)
WHERE Year(OrderDate) >= 2020;

LEFT JOIN (Sales)
LOAD CustomerID, Segment RESIDENT Customers;

STORE Sales INTO [lib://Data/sales_clean.qvd] (qvd);`;

export const SAMPLE_IRONPYTHON = `from Spotfire.Dxp.Application.Visuals import TablePlot
import smtplib
writer = Document.Data.CreateDataWriter(DataWriterTypeIdentifiers.ExcelXlsDataWriter)
table.ExportDataToFile(stream, writer)
server = smtplib.SMTP('mail.corp.local')
server.SendMail('reports@corp', 'team@corp', msg)
Document.ActivePageReference = Document.Pages[1]`;
