"""WF-19 — the documents name things that exist.

Checks that can be made by a machine: every script a guide tells the owner to double-click is in the repository,
every link between documents resolves (file and heading), and no guide sends anyone to the Arena site that was
removed from the Browser Bridge (ADR-032). Whether a sentence is *true* still needs a reader.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
pytestmark = pytest.mark.skipif(not (DOCS / "AI_WORKFORCE.md").is_file(), reason="the repository's docs are not available here")

GUIDES = ["README.md", "docs/AI_WORKFORCE.md", "docs/ARENA_LOCAL_BRIDGE.md", "docs/WINDOWS_SETUP.md",
          "docs/TROUBLESHOOTING.md", "docs/SECURITY.md", "docs/DEPLOYMENT.md", "docs/DEVELOPMENT.md",
          "docs/HANDOFF.md", "docs/RELEASE_READINESS.md", "docs/WORKFORCE_ASSESSMENT.md", "docs/BROWSER_BRIDGE.md"]
# historical documents: kept as they were written, not instructions for today
ARCHIVE = {"MASTER_PROMPT.v1.md", "IMPLEMENTATION_PLAN.v1.md", "acceptance.v1.json", "CHANGELOG.md",
           "ARCHITECTURE_DECISIONS.md", "REQUIREMENTS.md", "requirements.json", "WORKFORCE_ASSESSMENT.md"}


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", path.read_text(), re.MULTILINE)}


@pytest.mark.parametrize("guide", GUIDES)
def test_links_between_documents_resolve(guide):
    path = ROOT / guide
    text = re.sub(r"```.*?```", "", path.read_text(), flags=re.DOTALL)
    problems = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if re.match(r"^[a-z]+://|^mailto:", target):
            continue
        file_part, _, anchor = target.partition("#")
        dest = (path.parent / file_part).resolve() if file_part else path
        if not dest.exists():
            problems.append(f"{target}: no such file")
        elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
            problems.append(f"{target}: no heading with that anchor in {dest.name}")
    assert not problems, f"{guide}: {problems}"


@pytest.mark.parametrize("guide", GUIDES)
def test_every_script_a_guide_names_exists(guide):
    text = (ROOT / guide).read_text()
    names = set(re.findall(r"`((?:[A-Za-z][\w-]*)\.(?:bat|ps1|sh))`", text))
    # scripts of other programs, and names used only as examples of a pattern
    names -= {"start-gui.bat", "real-tests.sh"}
    missing = sorted(n for n in names if not any((ROOT / d / n).is_file() for d in (".", "scripts", "scripts/dev",
                                                                                   "scripts/dev/no-docker",
                                                                                   "scripts/windows", "scripts/lib")))
    assert not missing, f"{guide} names scripts that are not in the repository: {missing}"


def test_the_guides_agree_with_the_code_on_names_that_must_match():
    from app.security import rbac
    from app.workforce import human

    guide = (DOCS / "AI_WORKFORCE.md").read_text()
    security = (DOCS / "SECURITY.md").read_text()
    # limits a person meets when submitting a result
    assert f"{human.MAX_CONTENT_CHARS:,} characters" in guide and f"{human.MAX_NOTES_CHARS:,} characters" in guide
    assert f"| Files per submission | {human.MAX_ATTACHMENTS} |" in guide
    # permissions named in the documents exist, and the human-only ones are human-only in the code
    for perm in ("workforce.read", "workforce.run", "workforce.work", "workforce.review", "workforce.manage"):
        assert perm in rbac.PERMISSIONS, perm
    for perm in ("workforce.manage",):
        assert perm in rbac.HUMAN_ONLY and f"`{perm}`" in security
    # the role table of the guide: who may review, and who may only read
    assert "workforce.review" in rbac.role_grants("REVIEWER") and "workforce.run" not in rbac.role_grants("REVIEWER")
    assert "workforce.review" not in rbac.role_grants("DEVELOPER") and "workforce.work" in rbac.role_grants("DEVELOPER")
    assert rbac.role_grants("VIEWER") & {p for p in rbac.PERMISSIONS if p.startswith("workforce.")} == {"workforce.read"}
    # every report class the guide explains is a class the platform computes, and none is left out
    from app.workforce import report
    assert set(report.CLASSES) == {"verified", "human_approved", "worker_proposal", "unverified_claim", "failed", "incomplete"}


def test_no_guide_sends_anyone_to_the_removed_arena_site_of_the_browser_bridge():
    offenders = []
    for path in [ROOT / "README.md", *sorted(DOCS.glob("*.md"))]:
        if path.name in ARCHIVE:
            continue
        for n, line in enumerate(path.read_text().splitlines(), start=1):
            low = line.lower()
            if "arena" not in low:
                continue
            # The Browser Bridge document may mention Arena only to say that it is not there: in the removal note at
            # its top (a quoted block), where it says addresses are refused, in the test table, and in the
            # description of a third-party tool that happens to list Arena among its sites.
            if path.name == "BROWSER_BRIDGE.md":
                if line.lstrip().startswith(">"):
                    continue
                if not any(w in low for w in ("refused", "retired", "duck.ai", "test_arena")):
                    offenders.append(f"{path.name}:{n}: {line.strip()[:120]}")
            elif re.search(r"browser bridge.{0,80}arena (site|adapter)|built-in `?arena`? (site|adapter)|add (the )?arena (site|as a site)", low) \
                    and not any(w in low for w in ("no arena site", "removed", "gone", "not part", "retired")):
                offenders.append(f"{path.name}:{n}: {line.strip()[:120]}")
    assert not offenders, offenders
    # and the Browser Bridge document states the removal where a reader starts
    head = "\n".join((DOCS / "BROWSER_BRIDGE.md").read_text().splitlines()[:16])
    assert "Arena is not part of the Browser Bridge" in head and "ARENA_LOCAL_BRIDGE.md" in head
