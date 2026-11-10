"""Command policy for sandboxed shell tools (SEC-05, spec Phase 17 "default deny", ADR-019).

Commands are argv lists executed directly in the container — never through a shell — so pipes, `;`, `&&`, `$()` and
redirections have no meaning. A command is allowed only if its leading words match an allow-listed prefix of the
*tool permission being used* (`shell.test` cannot run a build and vice versa), and its arguments stay inside the
workspace. Everything else is denied with a reason that is shown to the agent and audited.
"""
from __future__ import annotations

from dataclasses import dataclass

# Allow-listed command prefixes per tool permission. Matching is on whole argv words.
ALLOWED: dict[str, tuple[tuple[str, ...], ...]] = {
    "shell.test": (
        ("pytest",), ("python3", "-m", "pytest"), ("python", "-m", "pytest"),
        ("python3", "-m", "unittest"), ("python", "-m", "unittest"),
        ("npm", "test"), ("npm", "run", "test"), ("pnpm", "test"), ("yarn", "test"),
        ("go", "test"), ("cargo", "test"),
    ),
    "shell.build": (
        ("npm", "run", "build"), ("npm", "run", "lint"), ("npm", "run", "check"), ("npx", "tsc", "--noEmit"),
        ("ruff", "check"), ("ruff", "format", "--check"), ("python3", "-m", "compileall"),
        ("python", "-m", "compileall"), ("go", "build"), ("go", "vet"), ("cargo", "build"), ("cargo", "clippy"),
    ),
}
WORKSPACE = "/workspace"
MAX_ARGS = 64
MAX_ARG_LEN = 1024
# Flags that would let an allowed tool load code or configuration from outside the workspace.
_DANGEROUS_FLAGS = ("--rootdir=/", "--confcutdir=/", "-p", "--pyargs", "--prefix", "--global", "-g")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def _escapes_workspace(arg: str) -> bool:
    value = arg.split("=", 1)[1] if arg.startswith("-") and "=" in arg else arg
    parts = value.replace("\\", "/").split("/")
    if ".." in parts:
        return True
    return value.startswith("/") and not (value == WORKSPACE or value.startswith(WORKSPACE + "/"))


def check(tool: str, argv: list[str]) -> Decision:
    prefixes = ALLOWED.get(tool)
    if prefixes is None:
        return Decision(False, f"tool {tool!r} has no command allow-list")
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return Decision(False, "argv must be a non-empty list of strings")
    if len(argv) > MAX_ARGS or any(len(a) > MAX_ARG_LEN for a in argv):
        return Decision(False, "command too long")
    if any("\x00" in a or "\n" in a for a in argv):
        return Decision(False, "control characters in arguments")
    match = next((p for p in prefixes if tuple(argv[:len(p)]) == p), None)
    if match is None:
        return Decision(False, f"command not allowed for {tool}: {argv[0]!r} (default deny)")
    rest = argv[len(match):]
    for a in rest:
        if a in _DANGEROUS_FLAGS or any(a.startswith(f + "=") for f in _DANGEROUS_FLAGS if f.startswith("--")):
            return Decision(False, f"flag not allowed: {a}")
        if _escapes_workspace(a):
            return Decision(False, f"path outside the workspace: {a}")
    return Decision(True, "allowed")
