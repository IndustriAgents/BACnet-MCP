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

The server also *joins* that network: on startup it runs a BAC0 stack that
binds UDP `BACNET_PORT` (default `47808`) on `BACNET_INTERFACE` (default
`0.0.0.0`, i.e. every interface) and presents itself as a BACnet device
(instance `BACNET_DEVICE_INSTANCE`, default `1234`).

So treat it as equipment on the building automation network, not as an
application on the office network.

## Running it safely

- **Start on loopback, then against the mock.** The server does not talk to
  `bacnet-mock-device` yet (see [Status](README.md#status)), so for now run it
  with `BACNET_INTERFACE=127.0.0.1`. Once it does, exercise an agent end to end
  against the mock first, every time, before it gets near a real building.
- **Turn writes off until you mean otherwise.** `BACNET_WRITES_ENABLED`
  defaults to `true`; set it to `false` for anything you only want to observe.
  Writes to a BACnet device move physical equipment (setpoints, outputs,
  schedules), there is no undo, and a language model will call a tool it has
  been given. The current write tools do not send anything to a device yet
  (see [Status](README.md#status)), but configure them as if they did — the
  gate is meant to be in place before the bindings are.
- **Bind to one interface.** Set `BACNET_INTERFACE` to the address on the BAS
  network rather than leaving it on `0.0.0.0`, and pick a `BACNET_DEVICE_INSTANCE`
  that does not collide with a real device: the server announces itself with
  an I-Am broadcast at startup. Set both in the process environment; a `.env`
  file is read too late for them (see [Configuration](README.md#configuration)).
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
on this repository. If that link offers no report form, use
[IndustriConnect's private vulnerability reporting](https://github.com/IndustriAgents/IndustriConnect/security/advisories/new),
which covers every server in the suite, or the email address.

Please do not open a public issue for a vulnerability.

Include the version or commit, what an attacker would gain, and a reproduction
if you have one. We will acknowledge within a week and keep you updated as we
work on a fix.

## Scope

In scope: the MCP server (`bacnet-python`) and the mock device
(`bacnet-mock-device`) in this repository.

Out of scope: vulnerabilities in the BACnet protocol itself (the lack of
authentication in BACnet/IP is a property of BACnet/IP), in third-party
libraries such as BAC0 and bacpypes3 — report those upstream — and in vendor
controller firmware.
