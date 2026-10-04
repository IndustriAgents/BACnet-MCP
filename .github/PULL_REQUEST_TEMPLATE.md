## Summary

<!-- What does this PR do, and why? -->

## Part(s) affected

- [ ] MCP server (`bacnet-python`)
- [ ] Mock device (`bacnet-mock-device`)
- [ ] Docs / CI only

## Type of change

- [ ] Bug fix
- [ ] New tool
- [ ] Documentation
- [ ] Refactor / chore

## Does this change what can be written to a device?

- [ ] No
- [ ] Yes — describe the new capability and how it is gated (`BACNET_WRITES_ENABLED`):

## Checklist

- [ ] Tools still return the shared `{ success, data, error, meta }` envelope, and nothing reports success for data that did not come from a device.
- [ ] The CI checks pass locally (see [CONTRIBUTING.md](https://github.com/IndustriAgents/BACnet-MCP/blob/main/CONTRIBUTING.md#checks)).
- [ ] The mock device can exercise the change and I tested against it (if it cannot, say below why and how you tested).
- [ ] Nothing was tested against production equipment.
- [ ] Nothing new is written to `stdout` — all logging goes to `stderr`.
- [ ] I updated the README(s) where relevant.

## How to test

<!-- The exact commands and tool calls a reviewer can run, against the mock where it can exercise the change. -->
