from __future__ import annotations

MAX_DEVICE_INSTANCE = 4194302
MAX_INSTANCE = 4194303


class ConfigError(ValueError):
    """Raised when the configuration is unusable."""


class BACnetClientError(RuntimeError):
    """Raised when a BACnet operation fails; the message is shown to the model."""


WRITTEN, REJECTED, UNKNOWN, NOT_SENT = ("written", "rejected", "unknown", "not_sent")
MAY_HAVE_BEEN_APPLIED = "the write may have been applied; read it back before retrying"


class WriteError(BACnetClientError):
    """A write that failed, with what is known about whether it was applied."""

    def __init__(self, message: str, outcome: str, request_sent: bool) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.request_sent = request_sent
