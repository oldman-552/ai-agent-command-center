"""WF-17 — the probe the health-check scripts run inside the API container
(`python -m app.connectivity.arena_bridge.probe`).

Tier: local integration (FAKE bridge over real HTTP, real PostgreSQL). Not evidence of a real Arena call, and not
evidence that the Windows scripts work: those run only on the owner's machine.
"""
import asyncio
import json
import socket

import app.main  # noqa: F401 — registers the models and the gateway adapters
from app.connectivity.arena_bridge import probe
from tests.fakebridge import ACCOUNTS, KEY
from tests.test_arena_bridge_api import BASE, _ack, _create


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cli(capsys, *args: str) -> tuple[int, str]:
    code = probe.main(list(args))
    return code, capsys.readouterr().out


def test_a_ready_bridge_passes_and_nothing_reaches_arena(admin_client, fake_bridge, capsys):
    b = _ack(admin_client, _create(admin_client, fake_bridge.url))
    code, out = _cli(capsys, "--bridge", str(b["id"]))
    assert code == 0, out
    assert "RESULT: ready" in out and "Status: ok" in out and "FAKE bridge, tests only" in out
    for step in ("address", "dns", "tcp", "health", "key", "ready", "models", "session_registry", "terms"):
        assert f"OK    {step}" in out, (step, out)
    # no chat turn was made, the key-leaking status route was not called, and neither key nor address is printed
    assert not fake_bridge.bridge.turns and fake_bridge.bridge.status_hits == 0
    assert KEY not in out and not any(a in out for a in ACCOUNTS) and "@" not in out


def test_terms_not_acknowledged_is_reported_but_is_not_a_failure(admin_client, fake_bridge, capsys):
    b = _create(admin_client, fake_bridge.url)
    code, out = _cli(capsys, "--bridge", str(b["id"]))
    assert code == 0, out
    assert "the terms are not acknowledged, so nothing is sent to Arena" in out and "FAIL  terms" in out
    assert "RESULT: FAILED" not in out


def test_an_unreachable_bridge_fails_and_names_the_step(admin_client, capsys):
    b = _create(admin_client, f"http://127.0.0.1:{_closed_port()}")
    code, out = _cli(capsys, "--bridge", str(b["id"]))
    assert code == probe.EXIT_BRIDGE_FAILED == 10, out
    assert "FAIL  tcp" in out and "RESULT: FAILED at 'tcp'" in out and "Status: failed" in out
    assert "-> start Arena Local Bridge on the host" in out  # what to do next is printed with the failed step


def test_a_wrong_key_and_no_ready_account(admin_client, fake_bridge, capsys):
    b = _create(admin_client, fake_bridge.url)
    assert admin_client.put(f"{BASE}/{b['id']}/key", json={"api_key": "wrong-key-000000000"}).status_code == 200
    code, out = _cli(capsys, "--bridge", str(b["id"]))
    assert code == 10 and "FAIL  key" in out and "RESULT: FAILED at 'key'" in out and "ARENA_AGENT_BRIDGE_KEY" in out
    assert "wrong-key-000000000" not in out

    fake_bridge.bridge.ready_accounts.clear()
    b = _create(admin_client, fake_bridge.url)
    code, out = _cli(capsys, "--bridge", str(b["id"]))
    assert code == 10 and "RESULT: reachable, but no account is ready" in out and "-> sign in an Arena account" in out


def test_a_disabled_bridge_is_not_checked_and_is_not_a_failure(admin_client, fake_bridge, capsys):
    b = _create(admin_client, f"http://127.0.0.1:{_closed_port()}")
    assert admin_client.patch(f"{BASE}/{b['id']}", json={"enabled": False}).status_code == 200
    code, out = _cli(capsys, "--bridge", str(b["id"]))
    assert code == 0 and "not checked: this bridge is switched off" in out and "Status: disabled" in out


def test_several_bridges_one_broken(admin_client, fake_bridge, capsys):
    good = _ack(admin_client, _create(admin_client, fake_bridge.url))
    bad = _create(admin_client, f"http://127.0.0.1:{_closed_port()}")
    code, out = _cli(capsys, "--bridge", str(good["id"]), "--bridge", str(bad["id"]), "--json")
    report = json.loads(out)
    assert code == 10 and report["status"] == "failed"
    by_id = {b["id"]: b for b in report["bridges"]}
    assert by_id[good["id"]]["ok"] is True and by_id[good["id"]]["ready"] is True
    assert by_id[bad["id"]]["ok"] is False
    assert all("accounts" not in s for b in report["bridges"] for s in b["steps"])  # counts only, even masked ones
    assert KEY not in out and not any(a in out for a in ACCOUNTS)


def test_without_a_configured_bridge_the_default_address_is_tried(fake_bridge):
    # a bridge answers on the address: say so, and that it can be added
    d = asyncio.run(probe.probe_default(fake_bridge.url))
    assert d["ok"] is True and [s["step"] for s in d["steps"]] == ["dns", "tcp", "health"]
    assert "FAKE bridge" in d["steps"][2]["detail"]
    text = probe.render({"in_container": True, "bridges": [], "default": d, "status": "not_configured"})
    assert "checked inside a container" in text and "No bridge is configured (optional" in text
    assert "A bridge answers there" in text and "Status: not_configured" in text
    assert not fake_bridge.bridge.turns  # the default probe sends no key and no chat request

    # nothing listens: reported as expected, not as something to fix
    d = asyncio.run(probe.probe_default(f"http://127.0.0.1:{_closed_port()}"))
    assert d["ok"] is False and d["steps"][-1]["step"] == "tcp" and d["steps"][-1]["ok"] is False
    text = probe.render({"in_container": False, "bridges": [], "default": d, "status": "not_configured"})
    assert "Nothing to fix unless you want Automatic workers." in text and "not in a container" in text
    assert "no    tcp" in text and "FAIL" not in text  # an absent optional bridge is not printed as a failure

    # the name does not resolve (or resolves and refuses) on a machine without Docker Desktop
    d = asyncio.run(probe.probe_default("http://no-such-host.invalid:20140"))
    assert d["ok"] is False and d["steps"][0]["step"] == "dns" and d["steps"][0]["ok"] is False


def test_another_program_on_the_port_is_not_taken_for_a_bridge():
    # a plain TCP listener that answers HTTP with something that is not the bridge
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    import threading

    def serve():
        try:
            c, _ = srv.accept()
            c.recv(4096)
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 13\r\nConnection: close\r\n\r\n<html></html>")
            c.close()
        except OSError:
            pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        d = asyncio.run(probe.probe_default(f"http://127.0.0.1:{port}"))
    finally:
        srv.close()
    assert d["ok"] is False and d["steps"][-1]["step"] == "health" and d["steps"][-1]["ok"] is False


def test_the_probe_reports_when_it_cannot_run(monkeypatch, capsys):
    async def boom(only=None):
        raise RuntimeError("could not connect: password=hunter2hunter2 host=db")

    monkeypatch.setattr(probe, "run", boom)
    assert probe.main([]) == probe.EXIT_PROBE_FAILED == 11
    err = capsys.readouterr().err
    assert "the probe could not run" in err and "hunter2hunter2" not in err
