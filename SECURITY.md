# Security Policy

## What this server is, in security terms

The BACnet MCP server is a bridge between a language model and building
automation equipment. It speaks MCP over stdio to its client and BACnet/IP to
the network, which means the trust boundary sits *inside* the server: whatever
the model decides to call, the building is asked to do.

BACnet/IP (BACnet over UDP, ASHRAE 135 Annex J) carries no authentication or
encryption of its own — anyone who can reach the network segment can read and
write objects on any device that accepts it. BACnet Secure Connect (BACnet/SC)
addresses this, but this server does not implement it.

The server also *joins* that network: on startup it runs a bacpypes3 BACnet/IP
stack that binds UDP `BACNET_PORT` (default `47808`) on `BACNET_INTERFACE`
(default `127.0.0.1`) and presents itself as a BACnet device (instance
`BACNET_DEVICE_INSTANCE`, default `1234`). It does not announce itself, but
like any bacpypes3 device it answers requests about its own device and
network-port objects: Who-Is, Who-Has, ReadProperty, ReadPropertyMultiple,
ReadRange and COV subscriptions. It refuses every write to its own objects.

So treat it as equipment on the building automation network, not as an
application on the office network.

## Running it safely

- **Start on loopback, then against the mock.** The defaults keep the server
  on `127.0.0.1`. Exercise an agent end to end against `bacnet-mock-device`
  first, every time, before it gets near a real building.
- **Writes stay off until you mean otherwise.** `BACNET_WRITES_ENABLED`
  defaults to `false`, and the write tools refuse to act until it is `true`.
  The switch, and `OBJECT_MAP_FILE` (which decides where alias writes go),
  are read only from the process environment (e.g. the `env` block of your
  MCP client's config) or from a file you name with
  `bacnet-mcp --env-file PATH`. The server never reads a `.env` from its
  working directory or a parent: for a server started by an MCP client that
  is the client's project, which you may not control. The only implicit
  `.env` is `bacnet-python/.env` of a source checkout, and from it only
  `BACNET_*` connection settings are taken; anything else there is ignored
  with a warning on stderr.
  Writes to a BACnet device move physical equipment (setpoints, outputs,
  schedules), there is no undo, and a language model will call a tool it has
  been given. Once writes are on, the model can write any property the device
  accepts: there is no per-object allow-list yet, so enable writes only for
  sessions that need them, and prefer a command `priority` that your
  operators recognise and can override.
- **A write is sent at most once.** If the device does not answer, the
  server does not retransmit (BACnet clients normally do, and a device whose
  acknowledgement was lost would apply the write again), and the write tools
  are annotated as not idempotent. The tool reports `meta.outcome: "unknown"`
  and asks for a read-back before any retry; treat that result as "may have
  happened". Before every write the server re-checks that the device at the
  address is the one you named. A write the MCP client cancelled, or that
  was still being prepared when the client disconnected, is never sent.
- **Bind to one interface.** To reach real devices, set `BACNET_INTERFACE` to
  the host's address on the BAS network with its prefix length (for example
  `192.168.1.10/24`) rather than `0.0.0.0`, and pick a
  `BACNET_DEVICE_INSTANCE` that does not collide with a real device: the
  server answers Who-Is with that instance.
- **Do not expose the server beyond the host running the client.** It is a
  stdio process meant to run beside the MCP client, not a network service.
- **Segment the network.** The server should sit where a BAS engineering
  workstation would sit, behind whatever separates the building network from
  everything else.
- **Never point it at a life-safety system.** Fire alarm, smoke control and
  other life-safety functions are out of scope for anything in this repository.

## Supported versions

This project is pre-1.0. Security fixes land on `main`. The copy of this server
inside [IndustriConnect](https://github.com/IndustriAgents/IndustriConnect)
(the `BACnet-Project` submodule) picks them up when its submodule pointer is
bumped.

## Reporting a vulnerability

Please report privately: by email to hi@industriagents.com, or through
[GitHub private vulnerability reporting](https://github.com/IndustriAgents/BACnet-MCP/security/advisories/new)
on this repository.
[IndustriConnect's private vulnerability reporting](https://github.com/IndustriAgents/IndustriConnect/security/advisories/new)
covers every server in the suite and works too.

Please do not open a public issue for a vulnerability.

Include the version or commit, what an attacker would gain, and a reproduction
if you have one. We will acknowledge within a week and keep you updated as we
work on a fix.

## Scope

In scope: the MCP server (`bacnet-python`) and the mock device
(`bacnet-mock-device`) in this repository.

Out of scope: vulnerabilities in the BACnet protocol itself (the lack of
authentication in BACnet/IP is a property of BACnet/IP), in third-party
libraries such as bacpypes3 and the MCP SDK — report those upstream — and in vendor
controller firmware.
