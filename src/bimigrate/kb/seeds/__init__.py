"""Knowledge-base seed data.

Each function entry is a compact tuple:
    (name, signature, category, dax_equivalent, dax_template, confidence, notes)

* dax_equivalent None  -> no native DAX equivalent (MANUAL tier, notes carry
  the fallback strategy).
* dax_template uses {0}, {1}... positional argument placeholders.
* confidence drives the AUTO / ASSISTED / MANUAL tier via the shared policy.

The seeds are the single source of truth: `bimigrate kb init` loads them
into SQLite and exports JSON; converters query the KB at runtime — there
are no hardcoded function lists in converter code.
"""

from bimigrate.kb.seeds.dax_reference import DAX_FUNCTIONS, M_FUNCTIONS
from bimigrate.kb.seeds.qlik import QLIK_FUNCTIONS
from bimigrate.kb.seeds.spotfire import SPOTFIRE_FUNCTIONS
from bimigrate.kb.seeds.tableau import TABLEAU_FUNCTIONS

__all__ = [
    "DAX_FUNCTIONS",
    "M_FUNCTIONS",
    "QLIK_FUNCTIONS",
    "SPOTFIRE_FUNCTIONS",
    "TABLEAU_FUNCTIONS",
]
