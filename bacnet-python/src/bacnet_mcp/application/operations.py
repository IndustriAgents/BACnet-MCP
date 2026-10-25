"""Compose the protocol use cases over one instance-owned resource set."""

from .browse import BrowseOperations
from .common import CommonOperations
from .diagnostics import DiagnosticsOperations
from .discovery import DiscoveryOperations
from .metadata import MetadataOperations
from .read import ReadOperations
from .write import WriteOperations


class Operations(
    CommonOperations,
    BrowseOperations,
    ReadOperations,
    WriteOperations,
    DiscoveryOperations,
    DiagnosticsOperations,
    MetadataOperations,
):
    pass
