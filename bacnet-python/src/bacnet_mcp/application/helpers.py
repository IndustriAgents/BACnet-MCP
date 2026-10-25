from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from bacnet_mcp.domain.errors import (
    MAY_HAVE_BEEN_APPLIED,
    NOT_SENT,
    UNKNOWN,
    BACnetClientError,
    WriteError,
)
from bacnet_mcp.domain.model import ArgumentError, Envelope, fail, log, write_outcome


async def guarded(
    tool: str,
    action: Callable[[], Awaitable[Envelope]],
    meta: Mapping[str, Any] | None = None,
    *,
    write: bool = False,
) -> Envelope:
    """Run a tool body, turning every failure into a success:false envelope.

    For write tools the envelope also says whether the request went out. A
    failure whose origin is unclear is reported as possibly sent.
    """
    base = {"tool": tool, **(meta or {})}
    try:
        return await action()
    except WriteError as exc:
        return fail(str(exc), {**base, **write_outcome(exc.outcome, exc.request_sent)})
    except ArgumentError as exc:
        return fail(str(exc), {**base, **(write_outcome(NOT_SENT, False) if write else {})})
    except BACnetClientError as exc:
        if write:
            return fail(
                f"{exc}: {MAY_HAVE_BEEN_APPLIED}",
                {**base, **write_outcome(UNKNOWN, True)},
            )
        return fail(str(exc), base)
    except Exception as exc:
        log.error("%s failed (%s)", tool, type(exc).__name__)
        if write:
            return fail(
                f"Unexpected error while executing tool: {MAY_HAVE_BEEN_APPLIED}",
                {**base, **write_outcome(UNKNOWN, True)},
            )
        return fail("Unexpected error while executing tool", base)
