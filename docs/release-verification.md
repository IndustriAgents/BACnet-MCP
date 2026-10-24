# 0.3.0 local release verification

Verified on 2026-10-24. Package: `industriagents-bacnet-mcp`.

This records local build verification after the OPCUA architecture review and
boundary hardening. It does not record a registry publication. The current
package version is retained; no tag, push or publication was performed.

## Checks

- Full Python regression run: 317 passed.
- Mock BACnet device suite: 24 passed.
- The full suite includes request depth/size bounds before dispatch, legacy
  unknown-argument rejection, cancellation admission release, text/structured
  audit consistency, and unexpected post-dispatch control failures.
- Ruff lint and formatting checks passed for source and tests.
- `uv lock --check` and contract/package version parity passed.
- Three npm launcher tests passed.
- Installed wheel and installed npm tarball initialized over stdio outside the
  checkout, reported the manifest version 0.3.0 and listed 5 canonical tools.
  Schemas matched the reviewed contract and invalid modes returned MCP errors.
  Smoke tests select exact manifest-version filenames, so stale archives cannot
  accidentally satisfy a later version's checks.
- `twine check --strict` passed for the wheel and source distribution.
- Workspace artifact inspection verified the current Python sources and
  contract inside the wheel and identical wheel bytes in the npm tarball.
- `git diff --check` passed.

Skipped tests: None.

These are software and mock/loopback checks, not physical-device certification.
See [protocol capability limits and architecture differences](architecture.md).

## Second architecture review

- Added a native operation adapter that normalizes bacpypes3 error PDUs before
  application dispatch; application/domain import checks enforce the boundary.
- Generic client errors after a write preserve uncertainty. Invalid alias maps
  report `not_sent` and never invoke the device client.
- Shared policy and audit checks reject FIFOs/special files without blocking,
  oversized/deep policy documents and duplicate keys.
- All verification commands, rebuilt wheel/source/npm artifacts, installed
  stdio smokes and strict package metadata checks passed again after these fixes.

## Artifact SHA-256

Artifacts are local build outputs in `dist/` and `npm/`, excluded from Git.
Rebuilding can change archive hashes; these identify the verified files.

| Artifact | SHA-256 |
|---|---|
| `industriagents_bacnet_mcp-0.3.0-py3-none-any.whl` | `35740a6b98467cffd9c22a8ea7ea72d4d2cf53461a0a0a8fb7e5b964872653af` |
| `industriagents_bacnet_mcp-0.3.0.tar.gz` | `6ad5a83fe5767b011478fa0a680c1d33e71652a460bf7edf010d3346a485e4cd` |
| `industriagents-bacnet-mcp-0.3.0.tgz` | `11dcb246968c9bc868f778d659858d2f5742629587b02c680b2576064f04320c` |

See [release instructions](release.md) for independent registry publication.
