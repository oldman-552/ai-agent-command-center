"""WF-17 — the launch scripts: INSTALL, START, STOP, UPDATE, HEALTH-CHECK, RESET, REAL-TESTS.

What this file is and is not:
- **Static checks** of the Windows files (`*.bat`, `scripts/windows/acc.ps1`): they exist, are encoded the way
  Windows needs, RESET asks for a typed confirmation before anything is deleted, and HEALTH-CHECK probes Arena Local
  Bridge from inside the API container. No PowerShell exists on the machine that runs this suite, so **none of the
  Windows scripts is executed here**; `ps_scan` is a small bracket-and-quote scanner, not a PowerShell parser.
- The Linux `health-check.sh` **is executed**, against a stub `docker` and a stub `curl`, to check its control flow.
  That is not a run against Docker either.
A real run needs the owner's Windows machine with Docker Desktop (requirement WF-17 / FI-01).
"""
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from app.connectivity.arena_bridge import probe
from tests import conftest

ROOT = Path(__file__).resolve().parents[2]
PS1 = ROOT / "scripts" / "windows" / "acc.ps1"
pytestmark = pytest.mark.skipif(not PS1.is_file(), reason="the repository root (launch scripts) is not available here")

ACTIONS = {"INSTALL.bat": "install", "START.bat": "start", "STOP.bat": "stop", "UPDATE.bat": "update",
           "HEALTH-CHECK.bat": "health", "RESET.bat": "reset", "RESET-DEV.bat": "reset", "REAL-TESTS.bat": "realtests"}
BOM = b"\xef\xbb\xbf"


def ps_text() -> str:
    return PS1.read_bytes()[len(BOM):].decode("utf-8")


def ps_scan(src: str) -> list[str]:
    """Problems a hurried edit leaves behind: an unclosed string, a bracket that does not match. Understands
    comments, both string kinds, the backtick escape and `$( … )` inside double-quoted strings. Not a parser."""
    problems: list[str] = []
    stack: list[tuple[str, int]] = []
    pairs = {")": "(", "}": "{", "]": "["}
    i, line, n = 0, 1, len(src)

    def double_quoted(j: int) -> int:  # j is just after the opening quote; returns the index after the closing one
        nonlocal line
        while j < n:
            c = src[j]
            if c == "`":
                j += 2
                continue
            if c == '"':
                if src[j + 1:j + 2] == '"':
                    j += 2
                    continue
                return j + 1
            if c == "$" and src[j + 1:j + 2] == "(":
                depth, j = 1, j + 2
                while j < n and depth:
                    if src[j] == '"':
                        j = double_quoted(j + 1)
                        continue
                    if src[j] == "'":
                        j = src.index("'", j + 1) + 1
                        continue
                    depth += {"(": 1, ")": -1}.get(src[j], 0)
                    line += src[j] == "\n"
                    j += 1
                continue
            line += c == "\n"
            j += 1
        problems.append(f"unterminated double-quoted string (opened near line {line})")
        return n

    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
        elif c == "#":
            i = src.find("\n", i)
            if i < 0:
                break
            continue
        elif c == "`":
            i += 2
            continue
        elif c == "'":
            j = i + 1
            while True:
                j = src.find("'", j)
                if j < 0:
                    problems.append(f"unterminated single-quoted string at line {line}")
                    return problems
                if src[j + 1:j + 2] == "'":
                    j += 2
                    continue
                break
            line += src.count("\n", i, j)
            i = j + 1
            continue
        elif c == '"':
            i = double_quoted(i + 1)
            continue
        elif c in "({[":
            stack.append((c, line))
        elif c in ")}]":
            if not stack or stack[-1][0] != pairs[c]:
                problems.append(f"unexpected '{c}' at line {line}")
            else:
                stack.pop()
        i += 1
    problems += [f"'{c}' opened at line {ln} is never closed" for c, ln in stack]
    return problems


def block(src: str, action: str) -> str:
    """The body of one action of the `switch ($Action)` at the end of the script."""
    m = re.search(rf'^  "{action}" \{{\n(.*?)^  \}}\n', src.replace("\r\n", "\n"), re.DOTALL | re.MULTILINE)
    assert m, f"no block for action {action!r}"
    return m.group(1)


def function(src: str, name: str) -> str:
    m = re.search(rf"^function {re.escape(name)}\b.*?^\}}\n", src.replace("\r\n", "\n"), re.DOTALL | re.MULTILINE)
    assert m, f"no function {name}"
    return m.group(0)


# ---------------- the scanner itself (a check that cannot fail proves nothing) ----------------
@pytest.mark.parametrize("bad", [
    'function A { Say "x" ', 'Say "never closed', "Say 'never closed", 'if ($a) { Say "x" ) }',
    'Say "a $(Port "X" "1" b"', 'foo ( bar ] ',
])
def test_the_scanner_catches_broken_scripts(bad):
    assert ps_scan(bad)


@pytest.mark.parametrize("good", [
    'Say "a {0} }" # } not code\n', "Say 'it''s } fine'", 'Say "url: http://localhost:$(Port \'API_PORT\' \'8000\')/x"',
    'Say "quote `" inside" ; if ($a) { $b = @("x", "y") }', '$x = "$($Matches[1])=*"',
])
def test_the_scanner_accepts_valid_fragments(good):
    assert ps_scan(good) == []


# ---------------- the files Windows runs ----------------
def test_every_launcher_exists_and_calls_a_real_action():
    src = ps_text()
    declared = set(re.search(r"ValidateSet\(([^)]*)\)", src).group(1).replace('"', "").replace(" ", "").split(","))
    assert declared == {"install", "start", "stop", "update", "health", "reset", "realtests"}
    for name, action in ACTIONS.items():
        raw = (ROOT / name).read_bytes()
        assert raw.isascii(), f"{name}: cmd.exe reads batch files in the console code page — ASCII only"
        assert b"\n" not in raw.replace(b"\r\n", b""), f"{name}: needs CRLF line endings"
        text = raw.decode("ascii")
        assert f'-File "%~dp0scripts\\windows\\acc.ps1" {action}\r\n' in text, name
        assert "exit /b %code%" in text and "pause" in text, f"{name}: keeps the window open and passes the result on"
        assert action in declared
        block(src, action)  # the action is implemented, not only declared
    assert "*.bat text eol=crlf" in (ROOT / ".gitattributes").read_text()
    assert "*.ps1 text eol=crlf" in (ROOT / ".gitattributes").read_text()


def test_the_powershell_script_is_encoded_for_windows_powershell_5():
    raw = PS1.read_bytes()
    # without the byte-order mark PowerShell 5.1 reads UTF-8 as the ANSI code page, where the last byte of an em dash
    # is a typographic quotation mark — which PowerShell treats as a string delimiter
    assert raw.startswith(BOM)
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert ps_scan(ps_text()) == []
    for name in ("HEALTH-CHECK.ps1", "INSTALL.ps1"):
        small = (ROOT / name).read_bytes()
        assert small.isascii() and b"\n" not in small.replace(b"\r\n", b"") and ps_scan(small.decode()) == []


def test_programs_with_redirected_output_run_only_inside_the_wrappers():
    """Windows PowerShell 5.1 + `$ErrorActionPreference = "Stop"` + a redirected error stream = the script ends at
    the first line docker or git prints there. Redirections are therefore allowed only in the four functions that
    relax the preference for the duration of the call."""
    src = ps_text().replace("\r\n", "\n")
    assert '$ErrorActionPreference = "Stop"' in src
    wrappers = [function(src, n) for n in ("Invoke-Logged", "Invoke-Quiet", "Invoke-Shown", "Invoke-BridgeProbe")]
    for w in wrappers:
        assert '$ErrorActionPreference = "Continue"' in w and "finally { $ErrorActionPreference = $old }" in w
        assert "return" in w and "$LASTEXITCODE" in w
    rest = src
    for w in wrappers:
        rest = rest.replace(w, "")
    code = "\n".join(ln.split(" #")[0] for ln in rest.splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"\*>|2>&1|2>\s", code), "a redirected native call outside the wrappers"
    assert not re.search(r"&\s+docker(\.exe)?\s+(?!@Compose ps)", code), "docker is called through the wrappers"


def test_reset_deletes_nothing_before_the_typed_confirmation():
    src = ps_text()
    reset = block(src, "reset")
    ask, cancel, delete = (reset.index(s) for s in ('Read-Host "Type RESET to continue"', "exit 0", '"down", "--volumes"'))
    assert ask < cancel < delete
    # exact, case-sensitive word; anything else cancels and says so
    assert 'if ($answer -cne "RESET") { Say "Cancelled. Nothing was deleted."; exit 0 }' in reset
    # the warning says what goes and what stays
    for words in ("deletes ALL data", "AI Workforce objectives and results", "bridge settings and stored keys",
                  "files attached to results", ".env (passwords, encryption key) is kept", "cannot be undone"):
        assert words in reset, words
    # no other action removes volumes, and nothing takes the confirmation from an argument or the environment
    for action in ("install", "start", "stop", "update", "health", "realtests"):
        other = block(src, action)
        assert "--volumes" not in other and '"down"' not in other and "prune" not in other, action
    assert "$env:" not in reset and "-Force" not in reset
    assert src.count("--volumes") == 1


def test_health_check_probes_the_bridge_from_inside_the_containers():
    src = ps_text()
    health = block(src, "health")
    assert "Show-Health" in health and "Test-ArenaBridge" in health
    assert health.index("Show-Health") < health.index("Test-ArenaBridge")
    runner = function(src, "Invoke-BridgeProbe")
    assert "& docker.exe @Compose exec -T $Service python -m app.connectivity.arena_bridge.probe" in runner
    test = function(src, "Test-ArenaBridge")
    assert 'Invoke-BridgeProbe "api"' in test and 'Invoke-BridgeProbe "worker"' in test
    # the module the script runs exists, and the script reads its exit codes the way the module defines them
    assert probe.__name__ == "app.connectivity.arena_bridge.probe" and callable(probe.main)
    assert probe.EXIT_BRIDGE_FAILED == 10 and f"$api.Code -eq {probe.EXIT_BRIDGE_FAILED}" in test
    # an unusable bridge is a warning: the health result comes from the core services only
    assert "exit" not in test and "Fail " not in test
    assert "if ($ok) { exit 0 } else { exit 1 }" in health
    # the summary line of /api/health is shown too
    assert "arena_local_bridge" in function(src, "Show-Health")


class ComposeLoader(yaml.SafeLoader):
    """Compose's own tags (`!reset`, `!override`) mean nothing to PyYAML; read the value they decorate."""


def _plain(loader, _suffix, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


ComposeLoader.add_multi_constructor("!", _plain)


def test_compose_lets_the_containers_reach_a_bridge_on_the_host():
    compose = yaml.load((ROOT / "docker-compose.yml").read_text(), Loader=ComposeLoader)  # a SafeLoader subclass
    for name in ("api", "worker"):
        assert "host.docker.internal:host-gateway" in compose["services"][name]["extra_hosts"], name
        assert compose["services"][name]["environment"]["ACC_IN_DOCKER"] == "1"
    # the launcher uses exactly this file (plus the production overrides on request), from the project folder
    src = ps_text()
    assert '(Join-Path $Root "docker-compose.yml")' in src and '"--project-directory", $Root' in src
    # the production overrides keep the mapping: they do not replace extra_hosts
    prod = yaml.load((ROOT / "docker-compose.prod.yml").read_text(), Loader=ComposeLoader)
    for name in ("api", "worker"):
        assert "extra_hosts" not in prod["services"][name] and "volumes" not in prod["services"][name], name


# ---------------- REAL-TESTS: the owner-run tiers ----------------
def test_real_tests_ask_before_anything_is_sent_and_keep_the_keys_out_of_files():
    src = ps_text()
    real = block(src, "realtests")
    run = real.index('Invoke-Shown "docker.exe" ($Compose + @("run", "--rm", "-T", "realtests") + $pytest)')
    # the Arena tier: what will happen is stated, and nothing runs before the typed acknowledgement
    arena = real[real.index('elseif ($choice -eq "2")'):run]
    for words in ("not an official Arena API", "Arena's terms of use apply to you", "5 short prompts"):
        assert words in arena, words
    ack = arena.index('if ($answer -cne "YES") { Say "Cancelled. Nothing was sent."; exit 0 }')
    assert arena.index('Read-Host "Type YES to acknowledge this and continue"') < ack < arena.index("$env:REAL_ARENA_ACCEPT_TERMS")
    assert ack < arena.index("Read-Secret")  # the key is not even asked for before the acknowledgement
    # the provider tier says that it costs money; an empty model or key cancels
    assert "requests that you pay for" in real and real.count('Nothing was sent."; exit 0') >= 4
    # keys: typed hidden, held in this process only, removed afterwards; never written by the script
    assert "Read-Host -AsSecureString" in function(src, "Read-Secret")
    assert real.count("Read-Secret") == 2 and "Read-Host -AsSecureString" not in real
    names = re.search(r"\$names = (.*)\n", real).group(1)
    for var in re.findall(r"\$env:(REAL_[A-Z_]+) =", real):
        assert f'"{var}"' in names, f"{var} is set but not removed afterwards"
    assert 'finally {\n      foreach ($n in $names) { Remove-Item -LiteralPath "Env:\\$n"' in real
    for forbidden in ("Set-IfEmpty", "Add-Content", "Out-File", "Set-Content", "$EnvFile", "Say $key", 'Say "$key'):
        assert forbidden not in real, forbidden
    # a tier is always named, so a run in which every test was skipped cannot end green
    assert '$env:REAL_TIER_REQUIRED = "real_provider"' in real and '$env:REAL_TIER_REQUIRED = "real_arena"' in real
    assert 'if ($code -eq 0) { Say "PASSED.' in real and "else { Fail " in real
    assert '"-m", "real_provider", "tests/test_real_provider.py"' in real and '"-m", "real_arena", "tests/test_real_arena.py"' in real


def test_the_realtests_service_cannot_touch_the_installation():
    compose = yaml.load((ROOT / "docker-compose.yml").read_text(), Loader=ComposeLoader)
    svc = compose["services"]["realtests"]
    assert svc["profiles"] == ["realtests"]  # START (`up`) never starts it
    env = svc["environment"]
    # its own database, with a name the test suite accepts — and no reference to the installation's database
    for key in ("TEST_DATABASE_URL", "DATABASE_URL"):
        assert env[key].endswith("@postgres:5432/acc_owner_test") and "${POSTGRES_DB" not in env[key], key
    assert conftest.database_problem(env["TEST_DATABASE_URL"].replace("${POSTGRES_USER}", "u").replace("${POSTGRES_PASSWORD}", "p")) is None
    assert env["TEST_CREATE_DATABASE"] == "1"
    # neither the broker nor the Redis of the installation: both addresses are dead ends
    assert "127.0.0.1:9" in env["TEST_RABBITMQ_URL"] and "127.0.0.1:9" in env["TEST_REDIS_URL"]
    assert "rabbitmq" not in " ".join(str(v) for v in env.values()) and "redis://redis" not in " ".join(str(v) for v in env.values())
    assert set(svc["depends_on"]) == {"postgres"}
    assert svc["volumes"] == ["./backend/tests:/app/tests:ro"]  # the tests are read, nothing of the installation is mounted
    assert "host.docker.internal:host-gateway" in svc["extra_hosts"]
    # secrets arrive from the caller's environment only; none has a default
    for key in ("REAL_MANAGER_API_KEY", "REAL_ARENA_BRIDGE_KEY", "REAL_ARENA_ACCEPT_TERMS", "REAL_TIER_REQUIRED"):
        assert env[key] == "${" + key + ":-}", key
    # every setting the two test files read is passed through
    for name in ("test_real_provider.py", "test_real_arena.py"):
        for var in re.findall(r'os\.environ\.get\("(REAL_[A-Z_]+)"', (ROOT / "backend" / "tests" / name).read_text()):
            assert var in env, f"{var} (read by {name}) is not passed to the container"
    # the test tools are installed at the locked versions, into a throwaway folder
    script = svc["entrypoint"][2]
    lock = (ROOT / "backend" / "uv.lock").read_text()
    for pkg in ("pytest", "pytest-asyncio"):
        version = re.search(rf'name = "{pkg}"\nversion = "([^"]+)"', lock).group(1)
        assert f"{pkg}=={version}" in script, f"{pkg}: the compose file and uv.lock disagree"
    assert "--target /tmp/testdeps" in script and script.endswith('"$$@"') and svc["entrypoint"][3] == "realtests"
    # and the image has no tests of its own, which is why they are mounted
    assert "tests" in (ROOT / "backend" / ".dockerignore").read_text().split()


@pytest.mark.parametrize(("name", "ok"), [
    ("acc_test", True), ("acc_owner_test", True), ("acc", False), ("acc_e2e", False), ("postgres", False),
    ("test", False), ("acc_test_backup", False), ("Acc_Test", False), ("", False), ("acc-prod_test", False),
])
def test_the_suite_refuses_a_database_that_is_not_a_test_database(name, ok):
    problem = conftest.database_problem(f"postgresql+psycopg://u:p@localhost:5432/{name}")
    assert (problem is None) is ok
    if not ok:
        assert "refusing to run" in problem and "_test" in problem


# ---------------- the Linux health check, executed against stubs ----------------
STUB_DOCKER = """#!/usr/bin/env bash
case "$*" in
  info|"compose version") exit 0 ;;
  *" ps") echo "NAME   STATUS"; exit 0 ;;
  *"exec -T api python -m app.connectivity.arena_bridge.probe") echo "probe-output-from-api"; exit "${STUB_API:-0}" ;;
  *"exec -T worker python -m app.connectivity.arena_bridge.probe") echo "probe-output-from-worker"; exit "${STUB_WORKER:-0}" ;;
esac
echo "unexpected docker call: $*" >&2; exit 97
"""
STUB_CURL = """#!/usr/bin/env bash
if [ -n "${STUB_API_DOWN:-}" ]; then exit 7; fi
printf '%s' "$STUB_HEALTH"
"""
HEALTH = ('{"status": "ok", "services": {"database": "ok", "rabbitmq": "ok", "redis": "ok", '
          '"workers": {"status": "ok", "instances": [{}]}, "browser_bridge": {"status": "not_running"}, '
          '"arena_local_bridge": %s}}')


@pytest.fixture()
def linux_health(tmp_path):
    if not shutil.which("bash") or not shutil.which("python3"):
        pytest.skip("needs bash and python3")
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    shutil.copy(ROOT / "health-check.sh", tmp_path / "health-check.sh")
    shutil.copy(ROOT / "scripts" / "lib" / "acc.sh", tmp_path / "scripts" / "lib" / "acc.sh")
    shutil.copy(ROOT / "docker-compose.yml", tmp_path / "docker-compose.yml")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", STUB_DOCKER), ("curl", STUB_CURL)):
        f = bin_dir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)

    def run(arena: str = '{"status": "not_configured", "configured": 0}', **env: str):
        full = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path), "STUB_HEALTH": HEALTH % arena, **env}
        r = subprocess.run(["bash", str(tmp_path / "health-check.sh")], check=False, capture_output=True, text=True,
                           env=full, timeout=60)
        return r.returncode, r.stdout + r.stderr

    return run


def test_linux_health_check_reports_the_probe_and_stays_green(linux_health):
    code, out = linux_health()
    assert code == 0, out
    assert "arena_local_bridge not configured (optional" in out
    assert "probe-output-from-api" in out and "The worker container reports the same result." in out
    assert "WARNING" not in out and "unexpected docker call" not in out


def test_linux_health_check_warns_about_an_unusable_bridge_without_failing(linux_health):
    arena = '{"status": "unreachable", "configured": 1, "reachable": 0, "ready": 0, "mock": 0}'
    code, out = linux_health(arena, STUB_API="10", STUB_WORKER="10")
    assert code == 0, out  # the bridge is optional: the core services decide the result
    assert "arena_local_bridge unreachable (1 configured, 0 ready when last checked)" in out
    assert "WARNING: an Arena Local Bridge is configured but cannot be used right now" in out
    assert "The worker container reports the same result." in out


def test_linux_health_check_shows_when_the_worker_sees_something_else(linux_health):
    code, out = linux_health(STUB_API="0", STUB_WORKER="10")
    assert code == 0, out
    assert "WARNING: the worker container reports a different result" in out and "probe-output-from-worker" in out


def test_linux_health_check_tells_a_probe_that_did_not_run_from_a_failed_bridge(linux_health):
    # `docker compose exec` answers 1 when the service is stopped: that is not "a bridge is broken"
    code, out = linux_health(STUB_API="1")
    assert code == 0, out
    assert "could not run inside the API container" in out
    assert "configured but cannot be used" not in out and "probe-output-from-worker" not in out


def test_linux_health_check_fails_when_the_api_is_down(linux_health):
    code, out = linux_health(STUB_API_DOWN="1")
    assert code == 1 and "API: DOWN" in out
    assert "probe-output-from-api" not in out  # nothing to probe when the platform is not running
