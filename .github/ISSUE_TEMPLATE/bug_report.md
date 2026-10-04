---
name: Bug report
about: Report a problem with the BACnet MCP server or the mock device
title: "[Bug] "
labels: bug
assignees: ""
---

**Which part?**
- [ ] MCP server (`bacnet-python`, `bacnet-mcp`)
- [ ] Mock device (`bacnet-mock-device`)

**Describe the bug**
What happens, and what you expected instead.

**To reproduce**
The MCP client you used (Claude Desktop / Claude Code / Cursor / Inspector /
IndustriConnect's mcp-manager-ui), the tool you called, and the arguments you
passed.

1.
2.
3.

**Tool output**
```json
paste the { success, data, error, meta } envelope, or the error
```

**What was on the other end?**
- [ ] The mock device from this repo
- [ ] Real equipment — vendor, model, and BACnet device instance:

**Environment**
- OS:
- Python version:
- Commit SHA:
- `BACNET_*` / `OBJECT_MAP_FILE` settings (no secrets):

**Additional context**
Object identifiers, property IDs, object map entries, stderr logs — whatever
would let someone else reproduce it against the mock.
