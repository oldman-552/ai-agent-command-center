# Skills & MCP registry

## MCP client (MCP-01)
- Register a server (`stdio` command or streamable `http` URL + optional bearer token, stored encrypted).
  Registration runs a **security assessment** and leaves the server **disabled**.
- *Discover* lists tools and resources; new tools start disabled. *Health* runs initialize + ping.
- Agents call MCP tools as runtime tools named `mcp:<server>:<tool>`. A call succeeds only if the server is enabled,
  the tool is enabled, the server is assigned to the agent (`mcp_server_ids`) and the agent holds that permission
  key. Denials and calls are audited.
- stdio servers run with a scrubbed environment (no platform secrets).

## Platform MCP server (MCP-02)
- Endpoint: `POST /api/mcp/` (streamable HTTP, stateless, JSON responses) — also reachable through the web proxy.
- Authentication: `Authorization: Bearer acc_mcp_…` access key, created under *Skills & MCP → Access keys*.
  The token is shown once; only its SHA-256 is stored. A key lists exactly which tools it may call.
- Tools: `list_agents`, `list_tasks`, `get_task`, `search_memory`, `list_pending_approvals` (read-only; MCP cannot
  approve anything) and the Browser Bridge tools (need `browser_bridge.manage` to grant, and the bridge gate).

## Skills (MCP-03, MCP-04)
- Skills are installed from folders under `SKILLS_DIR` on the host or uploaded as files — the platform never
  downloads code by itself.
- *Inspect* runs a static scan (nothing executed): dangerous commands, network access, filesystem access, credential
  access, dependencies and suspicious instructions, with file and line for every finding, and an overall risk.
- Installed skills are disabled. Enabling a HIGH-risk skill or server requires an explicit, audited risk acceptance.
- An enabled skill assigned to an agent adds its instructions to that agent's task context (trusted segment, labelled
  with the skill name).
