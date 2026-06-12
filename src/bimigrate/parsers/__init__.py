from bimigrate.parsers.base import BaseParser, get_parser_for, registered_extensions
from bimigrate.parsers.qliksense import QlikSenseParser
from bimigrate.parsers.qlikview import QlikViewParser
from bimigrate.parsers.spotfire import SpotfireParser
from bimigrate.parsers.tableau import TableauParser

__all__ = [
    "BaseParser",
    "QlikSenseParser",
    "QlikViewParser",
    "SpotfireParser",
    "TableauParser",
    "get_parser_for",
    "registered_extensions",
]
