# BACnet protocol research and capability assessment

Reviewed 2026-10-25 against this repository, bacpypes3 **0.0.110**, and the
OPCUA-MCP execution/feature-module reference. This is an implementation support
assessment, not an ASHRAE or BTL conformance claim.

## Evidence and design decisions

| Primary source | Sections used | Consequence for this MCP |
|---|---|---|
| [BACnet Committee](https://bacnet.org/about-bacnet-committee/) | Standards and certification responsibilities | A Python library and mock test are not product certification; published standards and device PICS remain the conformance authority. The complete purchased standard was not available during this review. |
| [BACpypes3 object/property identifiers](https://bacpypes3.readthedocs.io/en/latest/gettingstarted/objectidentifiers.html) | Object identifiers; property identifiers | Preserve object type, instance, property and array index separately; do not guess device addresses or units. |
| [BACpypes3 0.0.110 object services](https://github.com/JoelBender/BACpypes3/blob/v0.0.110/bacpypes3/service/object.py) | ReadProperty and WriteProperty encoding/decoding | Reuse the pinned native read path for array-indexed object-list traversal; retain protocol-native priority and relinquishment behavior. |
| [BACpypes3 samples](https://bacpypes3.readthedocs.io/en/latest/samples/index.html) | Client samples and network configuration | A client is itself a bound BACnet/IP application. Startup bind failures and explicit target identity belong to the adapter. |

Links accessed 2026-10-25. Source claims were checked against the pinned installed
library and the repository's actual UDP fixture. Device support for an object or
property must still be established by its reply or PICS.

BACnet commonly exposes building automation as objects with properties. An agent
needs an inventory before choosing an object, and metadata before assigning a
physical interpretation to its value. `list_objects` lists configured aliases;
it never enumerated a device. The new `browse_objects` closes that workflow gap
without pretending that a configuration map is network discovery.

## Capability matrix

| Workflow / tool | Implementation and limits | Verification / remaining work |
|---|---|---|
| Discover device identity / `discover_devices` | Existing Who-Is/I-Am and optional details; explicit address or configured subnet broadcast | Existing local UDP and stdio tests; no production scan performed |
| Enumerate device objects / `browse_objects` | New ReadProperty of object-list[0], then 1–100 indexed entries; shared 100–60000 ms deadline, continuation index and partial failure | `test_browse.py`: normal page, continuation, failed entry, deadline, validation and real UDP mock |
| Read value and metadata / `read_property` | Existing address/alias routes; native type conversion, property and array access | Existing codec/native and MCP tests; use units, status-flags, priority-array and range properties where the device implements them |
| Write/relinquish / `write_property` | Existing explicit write gate, identity recheck, datatype preparation, priority rules and one write attempt | Existing lost-ACK, cancellation, priority/null and oversized APDU tests |
| Inspect aliases / `list_objects` | Local configuration, including malformed entries for diagnosis | Existing map tests; no device capability inference |
| Inspect backend / `ping` | New typed capabilities inventory plus existing local status | Contract validation and installed wheel/npm smoke; device_verified remains false |
| COV, events/alarms, history | Not exposed by this server | Separate feature work is still needed for subscription ownership, renewal, bounded buffers, event loss and cancellation; repeated reads are observations, not COV |
| BACnet/SC, direct MS/TP, foreign-device/BBMD management | Not exposed; this deployment is BACnet/IP | Requires transport design and the relevant authorized infrastructure; no support claim from library availability |
| Physical-device/BTL conformance | Not performed | Requires an authorized device/network and appropriate conformance procedures |

## Architecture and acceptance

`application/browse.py` depends on the normalized read port, not bacpypes3 or MCP.
The existing native adapter retains identity, retry and cancellation ownership.
The new tool is read-only and never implicitly creates aliases or writes values.
Every canonical tool now declares its data shape in `contract/tools.json`; actual
MCP results, including compatibility routes, are validated against those shapes.

The new capability object describes implementation support. `enabled` reflects
the native write configuration only, not policy authorization, reachability or
device permission. Both text and structured MCP results carry the same envelope.
Unexpected exception payloads are omitted from responses and generic error logs.

Run `python3 scripts/mcp_workspace.py verify BACnet-MCP` from the workspace, or
use the repository's CI commands independently. Package smoke checks remove
inherited deployment settings, bind only loopback, compare complete tool metadata
and call only local diagnostics plus an invalid selector. No live device is used.
