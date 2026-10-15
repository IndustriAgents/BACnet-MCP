# 0.3.0 local release verification

Verified on 2026-10-15. Package: `industriagents-bacnet-mcp`.

This records local build verification, not registry publication. Check the
GitHub Actions runs and registry version pages for publication status. Registry
authentication/trusted publishers must be configured before release; GitHub
repository access alone does not authorize registry publication.

## Checks

- Full Python regression run: 288 passed.
- Final architecture/contract tests: 15 passed (a separate run, not additional unique tests).
- Ruff lint and formatting checks passed for source and tests.
- `uv lock --check` and contract/package version parity passed.
- Three npm launcher tests passed.
- Installed wheel and installed npm tarball both initialized over stdio outside
  the checkout, reported version 0.3.0 and listed 5 canonical tools.
  Their schemas matched the contract; invalid mode calls returned MCP errors.
- `twine check --strict` passed for the wheel and source distribution.
- The npm tarball contains exactly the same wheel bytes as the PyPI artifact.
- `git diff --check` passed.

No tests were skipped in the full regression run.

These are software and mock/loopback checks, not physical-device certification.
See [protocol capability limits](architecture.md#protocol-capability-limits).

## Artifact SHA-256

Artifacts are local build outputs in `dist/` and `npm/`, excluded from Git.
Rebuilding can change archive hashes; these identify the verified files.

| Artifact | SHA-256 |
|---|---|
| `industriagents_bacnet_mcp-0.3.0-py3-none-any.whl` | `4c7f76dfa0befc2fb63daa06c242a35cc65d2cbd1b24d06fbd85644b4e599e09` |
| `industriagents_bacnet_mcp-0.3.0.tar.gz` | `4683f2172ea4daa71d00d86a174ea3dc70b0d6e3d8f669e3a46d27e69f89cf5c` |
| `industriagents-bacnet-mcp-0.3.0.tgz` | `0a9d913695b6c366ff489eed252ee006123d9da6abfd030fad6771e60f0c3663` |

See [release instructions](release.md) for independent registry publication.
