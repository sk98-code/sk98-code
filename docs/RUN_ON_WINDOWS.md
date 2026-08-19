# Running it on Windows (PowerShell)

Everything runs on your own machine. No data leaves it, and nothing here
needs an internet connection except `pip install`.

## 0. Check you have Python 3.11 or newer

```powershell
py --list
```

If nothing is listed, install Python from
<https://www.python.org/downloads/windows/> and **tick "Add python.exe to
PATH"** in the installer.

Then:

```powershell
py -3.11 --version
```

Any version from 3.11 up is fine — use whatever `py --list` shows.

## 1. Unzip and go into the folder

```powershell
cd $HOME\Downloads
Expand-Archive .\sk98-code.zip -DestinationPath .\sk98-code-app -Force
cd .\sk98-code-app\sk98-code
```

If you cloned from git instead, just `cd` into the repo folder.

## 2. Create a virtual environment

```powershell
py -3.11 -m venv .venv
```

**PowerShell will very likely refuse to activate it.** That is a default
Windows setting, not a problem with this project. Allow scripts for this
window only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Your prompt now starts with `(.venv)`. If you prefer not to change the
policy at all, skip activation and call the venv's Python directly
everywhere below: `.\.venv\Scripts\python.exe -m ...`.

## 3. Install

```powershell
python -m pip install --upgrade pip
pip install -e ".[dev,pbi-file,web]"
```

The quotes around `".[dev,pbi-file,web]"` matter in PowerShell — without
them the square brackets are read as a wildcard.

What each extra gives you:

| Extra | What it adds |
|---|---|
| `web` | the local web UI (`pbi-lineage ui`) |
| `pbi-file` | reads the model out of a `.pbix` file's binary `DataModel` (via **pbixray**). Without it a `.pbix` still gives you the full report analysis, but no tables/columns/measures. PBIP projects always work |
| `dev` | pytest, so you can verify the install |
| `pbi-service` | tenant scanning against the Power BI Service — add later if you need it |
| `pbi-desktop` | attaching to a model open in Power BI Desktop — Windows only, see §7 |

## 4. Verify

```powershell
pytest -q
```

Expect **260 passed**. If that passes, everything is wired correctly.

## 5. Start the UI

```powershell
pbi-lineage ui
```

Your browser opens at <http://127.0.0.1:8777>. Paste a file path into the
box and press **Analyze**. A Windows path works as-is:

```
C:\Users\you\Documents\Sales.pbix
```

Both a `.pbix` file and a PBIP project folder are accepted.

Port already in use, or you want a second instance:

```powershell
pbi-lineage ui --port 8888
pbi-lineage ui --no-open-browser      # don't launch a browser
```

Stop it with **Ctrl+C**.

### No .pbix to hand?

```powershell
python scripts\make_demo.py
```

That writes `demo\RetailDemo` — a small PBIP project built to exercise the
column lineage (a Power Query rename, a computed column, and a column
nothing uses). Paste that path into the UI and press Analyze.

### Trying the estate view without a tenant

Switch the header dropdown to **Power BI Service**, leave the mode on
*Replay a saved scan*, and give it the sample scan that ships with the
repo:

```
demo_estate\sample_tenant_scan.json
```

Then open **Column Lineage** — the view selector will offer
*Graph — whole tenant*, which draws source → dataflow (Gen1/Gen2) →
semantic model → chained model → report.

## 6. The command line, if you prefer it

```powershell
# analyze and write results next to the file
pbi-lineage analyze C:\path\to\Sales.pbix --out .\results

# what would break if this column went away (never deletes anything)
pbi-lineage removal-plan C:\path\to\Sales.pbix --object "Sales[DiscountCode]"

# which models touch a warehouse table
pbi-lineage search-m C:\path\to\Sales.pbix dbo.FactSales

# all commands
pbi-lineage --help
```

`--out` writes `pbi_lineage_export.json` and `pbi_lineage.sqlite`, which
open in any SQLite browser.

## 7. The two Windows-only features

**Live Desktop mode** reads the model straight out of a report open in
Power BI Desktop, which is the only way to get full model metadata out of
an older `.pbix`:

```powershell
pip install -e ".[pbi-desktop]"
pbi-lineage live --pbix C:\path\to\Sales.pbix
```

This needs the **ADOMD.NET client** installed (it ships with SQL Server
Management Studio, or as *SQL_AS_ADOMD.msi* from Microsoft) and Power BI
Desktop running with the file open.

**Tenant scanning** needs an Entra app registration with the Fabric admin
read scopes:

```powershell
pip install -e ".[pbi-service]"
pbi-lineage tenant --client-id <id> --tenant-id <tenant> --out .\scan
```

Fair warning, and it is in the docs too: the Service path has been built
against the documented APIs but has never been run against a real tenant
from here. Replay mode (a saved scan payload) is the exercised path.

## Next time you open PowerShell

The venv is not remembered between windows:

```powershell
cd $HOME\Downloads\sk98-code-app\sk98-code
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pbi-lineage ui
```

## When something goes wrong

| Symptom | Cause and fix |
|---|---|
| `running scripts is disabled on this system` | PowerShell's default policy. Run the `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` line above — it applies to that window only |
| `pbi-lineage : The term 'pbi-lineage' is not recognized` | The venv is not active. Activate it, or call `.\.venv\Scripts\pbi-lineage.exe` |
| `the UI needs the web extra` | Install with the `web` extra: `pip install -e ".[web]"` |
| `No model metadata in this file` | An older `.pbix` whose model is a binary the reader cannot open offline. Install `pbi-file`, or use live Desktop mode (§7) |
| `[Errno 10048] address already in use` | Something is on port 8777. Use `pbi-lineage ui --port 8888` |
| Browser doesn't open | Open <http://127.0.0.1:8777> yourself; the server is already running |
| `Path does not exist` after pasting a path | Drop the surrounding quotes — the box wants the bare path |
