"""Compatibility import; implementation lives in adapters.bacnet_client."""

import sys

from .adapters import bacnet_client as _implementation

sys.modules[__name__] = _implementation
