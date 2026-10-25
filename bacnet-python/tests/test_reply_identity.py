"""Correct invoke IDs do not excuse mismatched BACnet property/identity echoes."""

import asyncio

import pytest
from bacpypes3.apdu import ReadPropertyACK
from bacpypes3.constructeddata import Any as AnyValue
from bacpypes3.primitivedata import ObjectIdentifier
from helpers import MOCK_DEVICE_ID, mock_address, running_client, running_mock

from bacnet_mcp.bacnet_client import BACnetClientError, WriteError


def alter_replies(mock, alter):
    original = mock.app.response

    async def response(apdu):
        if isinstance(apdu, ReadPropertyACK):
            alter(apdu)
        return await original(apdu)

    mock.app.response = response


@pytest.mark.parametrize(
    "field,value",
    [
        ("objectIdentifier", ("analog-input", 2)),
        ("propertyIdentifier", "reliability"),
        ("propertyArrayIndex", 0),
    ],
)
def test_mismatched_property_ack_over_udp_is_never_a_successful_value(field, value):
    async def scenario():
        async with running_mock() as mock, running_client(apdu_retries=2) as client:
            await client.read_property(
                MOCK_DEVICE_ID, "analog-input", 1, address=mock_address(mock)
            )
            calls = []

            def mutate(apdu):
                calls.append(apdu)
                setattr(apdu, field, value)

            alter_replies(mock, mutate)
            with pytest.raises(BACnetClientError, match="does not match"):
                await client.read_property(MOCK_DEVICE_ID, "analog-input", 1)
            assert len(calls) == 1  # Mismatched data is not a transport timeout to retry.

    asyncio.run(scenario())


def test_missing_array_echo_is_refused():
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            await client.read_property(
                MOCK_DEVICE_ID, "analog-input", 1, address=mock_address(mock)
            )
            alter_replies(mock, lambda apdu: setattr(apdu, "propertyArrayIndex", None))
            with pytest.raises(BACnetClientError, match="does not match"):
                await client.read_property(
                    MOCK_DEVICE_ID, "device", MOCK_DEVICE_ID, "object-list", array_index=0
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("corruption", ["value_type", "echo_object", "echo_property", "echo_index"])
def test_invalid_wildcard_identity_cannot_authorize_a_write(corruption):
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            addr = mock_address(mock)
            await client.read_property(MOCK_DEVICE_ID, "analog-input", 1, address=addr)
            writes = []
            original = mock.app.do_WritePropertyRequest

            async def record(apdu):
                writes.append(apdu)
                return await original(apdu)

            mock.app.do_WritePropertyRequest = record

            def mutate(apdu):
                if str(apdu.propertyIdentifier) != "object-identifier":
                    return
                if corruption == "value_type":
                    apdu.propertyValue = AnyValue(
                        ObjectIdentifier(("analog-input", MOCK_DEVICE_ID))
                    )
                elif corruption == "echo_object":
                    apdu.objectIdentifier = ("device", 4194303)
                elif corruption == "echo_property":
                    apdu.propertyIdentifier = "present-value"
                else:
                    apdu.propertyArrayIndex = 0

            alter_replies(mock, mutate)
            with pytest.raises(WriteError) as error:
                await client.write_property(
                    MOCK_DEVICE_ID, "analog-output", 1, "present-value", 5.0, 8
                )
            assert error.value.outcome == "not_sent" and error.value.request_sent is False
            assert writes == [] and MOCK_DEVICE_ID not in client._addresses

    asyncio.run(scenario())
