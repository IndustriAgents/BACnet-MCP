"""Compatibility imports; implementation lives in domain, application and protocol modules."""

import inspect as inspect
import json as json
import logging as logging
import os as os
from collections.abc import Awaitable as Awaitable
from collections.abc import Callable as Callable
from collections.abc import Mapping as Mapping
from dataclasses import dataclass as dataclass
from pathlib import Path as Path
from typing import Any as Any

from bacpypes3.apdu import ErrorRejectAbortNack as ErrorRejectAbortNack
from pydantic import StrictBool as StrictBool
from pydantic import StrictInt as StrictInt

from bacnet_mcp.application.helpers import guarded as guarded
from bacnet_mcp.application.resources import ToolResources as ToolResources
from bacnet_mcp.bacnet_client import BACnetClient as BACnetClient
from bacnet_mcp.domain.errors import MAX_DEVICE_INSTANCE as MAX_DEVICE_INSTANCE
from bacnet_mcp.domain.errors import MAX_INSTANCE as MAX_INSTANCE
from bacnet_mcp.domain.errors import MAY_HAVE_BEEN_APPLIED as MAY_HAVE_BEEN_APPLIED
from bacnet_mcp.domain.errors import NOT_SENT as NOT_SENT
from bacnet_mcp.domain.errors import UNKNOWN as UNKNOWN
from bacnet_mcp.domain.errors import BACnetClientError as BACnetClientError
from bacnet_mcp.domain.errors import ConfigError as ConfigError
from bacnet_mcp.domain.errors import WriteError as WriteError
from bacnet_mcp.domain.model import MAX_TIMEOUT_MS as MAX_TIMEOUT_MS
from bacnet_mcp.domain.model import MIN_TIMEOUT_MS as MIN_TIMEOUT_MS
from bacnet_mcp.domain.model import ArgumentError as ArgumentError
from bacnet_mcp.domain.model import Envelope as Envelope
from bacnet_mcp.domain.model import WriteValue as WriteValue
from bacnet_mcp.domain.model import alias_target as alias_target
from bacnet_mcp.domain.model import check_int as check_int
from bacnet_mcp.domain.model import check_optional_text as check_optional_text
from bacnet_mcp.domain.model import check_priority as check_priority
from bacnet_mcp.domain.model import check_target as check_target
from bacnet_mcp.domain.model import check_text as check_text
from bacnet_mcp.domain.model import check_value as check_value
from bacnet_mcp.domain.model import envelope as envelope
from bacnet_mcp.domain.model import fail as fail
from bacnet_mcp.domain.model import log as log
from bacnet_mcp.domain.model import ok as ok
from bacnet_mcp.domain.model import write_outcome as write_outcome
from bacnet_mcp.infrastructure.maps import ObjectMap as ObjectMap
from bacnet_mcp.infrastructure.tool_config import ToolConfig as ToolConfig

from .protocol.support import LOCAL_ONLY as LOCAL_ONLY
from .protocol.support import READ_ONLY as READ_ONLY
from .protocol.support import WRITES_DEVICE as WRITES_DEVICE
from .protocol.support import FastMCP as FastMCP
from .protocol.support import ToolAnnotations as ToolAnnotations
from .protocol.tools import register_tools as register_tools
