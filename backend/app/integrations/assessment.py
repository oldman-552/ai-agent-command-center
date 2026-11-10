"""Security assessment of skills and MCP servers before they can be enabled (MCP-03/04, spec Phase 18, ADR-026).

Static inspection only — nothing is executed. Findings are grouped into the categories the spec asks for (dangerous
commands, network access, filesystem access, credential access, dependencies, instructions) with the file and line
that triggered them, and an overall risk: high (anything dangerous or credential-related), medium (network,
filesystem, dependencies, suspicious instructions), low. High risk can only be enabled with an explicit, audited
acceptance by a human; nothing is ever granted automatically.
"""
from __future__ import annotations

import re
from typing import Any

RULES: list[tuple[str, str, re.Pattern[str]]] = [
    # (category, label, pattern)
    ("dangerous_commands", "recursive delete", re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r|shutil\.rmtree")),
    ("dangerous_commands", "pipe download into a shell", re.compile(r"(curl|wget)[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b|iex\s*\(\s*(iwr|irm|New-Object)", re.IGNORECASE)),
    ("dangerous_commands", "privilege escalation", re.compile(r"\bsudo\b|\bsu\s+-|runas\s|--privileged|Set-ExecutionPolicy\s+Bypass", re.IGNORECASE)),
    ("dangerous_commands", "dynamic code execution", re.compile(r"\beval\s*\(|\bexec\s*\(|os\.system\s*\(|subprocess\.(run|Popen|call)\([^)]*shell\s*=\s*True|child_process|Invoke-Expression", re.IGNORECASE)),
    ("dangerous_commands", "world-writable permissions", re.compile(r"chmod\s+(-R\s+)?0?777")),
    ("dangerous_commands", "encoded payload", re.compile(r"base64\s+(-d|--decode)|FromBase64String|atob\s*\(", re.IGNORECASE)),
    ("dangerous_commands", "disk / system modification", re.compile(r"\bmkfs\b|\bdd\s+if=|format\s+[a-z]:|reg\s+(add|delete)\b|crontab\s+-|schtasks", re.IGNORECASE)),
    ("network", "HTTP client / download", re.compile(r"\b(curl|wget|Invoke-WebRequest|iwr|irm)\b|requests\.(get|post)|urllib|httpx|fetch\s*\(|axios", re.IGNORECASE)),
    ("network", "raw sockets / listeners", re.compile(r"\bsocket\.socket|\bnc\s+-l|netcat|\bncat\b|websocket", re.IGNORECASE)),
    ("network", "remote URL", re.compile(r"https?://(?!localhost|127\.0\.0\.1)[\w.-]+", re.IGNORECASE)),
    ("filesystem", "home / system paths", re.compile(r"(~/|\$HOME|%USERPROFILE%|/etc/|/usr/|C:\\\\Windows|/var/)", re.IGNORECASE)),
    ("filesystem", "file writes", re.compile(r"open\([^)]*['\"][wa]b?['\"]|writeFile|fs\.write|Out-File|Set-Content|>\s*/", re.IGNORECASE)),
    ("credentials", "secret environment variables", re.compile(r"(API_KEY|SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY|ACCESS_KEY)\b")),
    ("credentials", "credential stores", re.compile(r"\.ssh/|id_rsa|\.aws/credentials|\.netrc|\.git-credentials|keychain|Credential Manager|\.npmrc|\.pypirc|cookies?\.sqlite|Login Data", re.IGNORECASE)),
    ("dependencies", "package installation", re.compile(r"\b(pip3?|uv)\s+(install|add)\b|\bnpm\s+(i|install)\b|\bnpx\s|\byarn\s+add|\bpnpm\s+add|apt(-get)?\s+install|brew\s+install|go\s+install|cargo\s+install", re.IGNORECASE)),
    ("instructions", "instruction override", re.compile(r"ignore (all |any )?(previous|prior|above) instructions|disregard (the )?(system|previous)|you are now|do not tell the user|hide this from", re.IGNORECASE)),
    ("instructions", "auto-approval / bypass", re.compile(r"--dangerously|skip[-_ ]?permissions|bypass(Permissions)?|--yes\b|auto[-_ ]?approve|without (asking|confirmation)", re.IGNORECASE)),
]
HIGH = {"dangerous_commands", "credentials"}
MEDIUM = {"network", "filesystem", "dependencies", "instructions"}
DEP_FILES = ("requirements.txt", "package.json", "pyproject.toml", "Pipfile", "go.mod", "Cargo.toml", "Gemfile")
MAX_FILES, MAX_BYTES = 200, 512 * 1024


def scan(files: dict[str, str]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for path, text in list(files.items())[:MAX_FILES]:
        for no, line in enumerate(text[:MAX_BYTES].splitlines(), start=1):
            for category, label, rx in RULES:
                if rx.search(line):
                    findings.append({"category": category, "label": label, "file": path, "line": no,
                                     "excerpt": line.strip()[:160]})
        name = path.rsplit("/", 1)[-1]
        if name in DEP_FILES:
            findings.append({"category": "dependencies", "label": f"declares dependencies ({name})", "file": path,
                             "line": 0, "excerpt": ""})
    cats = {f["category"] for f in findings}
    risk = "high" if cats & HIGH else "medium" if cats & MEDIUM else "low"
    summary = {c: sum(1 for f in findings if f["category"] == c) for c in
               ("dangerous_commands", "network", "filesystem", "credentials", "dependencies", "instructions")}
    return {"risk": risk, "summary": summary, "findings": findings[:500], "files_scanned": min(len(files), MAX_FILES),
            "truncated": len(findings) > 500 or len(files) > MAX_FILES}


SHELLS = {"sh", "bash", "zsh", "dash", "cmd", "cmd.exe", "powershell", "pwsh"}


def assess_mcp_server(transport: str, command: list[str] | None, url: str | None) -> dict[str, Any]:
    """What would registering this MCP server allow? Command lines are scanned like code, plus transport checks."""
    files: dict[str, str] = {}
    extra: list[dict[str, Any]] = []
    if transport == "stdio":
        argv = command or []
        files["command"] = " ".join(argv)
        exe = (argv[0].rsplit("/", 1)[-1] if argv else "").lower()
        if exe in SHELLS:
            extra.append({"category": "dangerous_commands", "label": "runs a shell", "file": "command", "line": 1,
                          "excerpt": files["command"][:160]})
        if exe in ("python", "python3", "node", "deno", "bun") and any(a in ("-c", "-e", "--eval") for a in argv):
            extra.append({"category": "dangerous_commands", "label": "inline code", "file": "command", "line": 1,
                          "excerpt": files["command"][:160]})
        if exe in ("npx", "uvx", "pipx", "bunx", "docker"):
            extra.append({"category": "dependencies", "label": f"fetches and runs a package ({exe})",
                          "file": "command", "line": 1, "excerpt": files["command"][:160]})
        extra.append({"category": "filesystem", "label": "local process with the platform user's file access",
                      "file": "command", "line": 1, "excerpt": ""})
    else:
        files["url"] = url or ""
        if url and not re.match(r"https://|http://(localhost|127\.0\.0\.1)(:\d+)?/", url):
            extra.append({"category": "credentials", "label": "credentials would travel over plain HTTP",
                          "file": "url", "line": 1, "excerpt": url[:160]})
        extra.append({"category": "network", "label": "remote server receives tool arguments", "file": "url",
                      "line": 1, "excerpt": (url or "")[:160]})
    r = scan(files)
    r["findings"] = [*extra, *r["findings"]]
    for f in extra:
        r["summary"][f["category"]] += 1
    cats = {f["category"] for f in r["findings"]}
    r["risk"] = "high" if cats & HIGH else "medium" if cats & MEDIUM else "low"
    return r
