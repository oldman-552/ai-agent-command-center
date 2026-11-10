"""Existing-repository analysis (OR-09, spec Phase 13 "Existing Project", ADR-027).

Static, read-only inspection of a project repository (nothing is executed): technology detection, architecture
discovery, dependency analysis, test discovery, a codebase map and technical-debt indicators. The result is stored on
the project and fed to the planner for `existing` and `maintenance` projects, so plans start from what is actually
there. Repository content is untrusted: only file names, sizes, counts and manifest fields are read; symlinks are not
followed and `.git` / vendored directories are skipped.
"""
from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "target", ".idea",
             ".vscode", "vendor", ".mypy_cache", ".pytest_cache", ".ruff_cache", "coverage", ".tox"}
LANG = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript",
        ".mjs": "JavaScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".cs": "C#", ".rb": "Ruby",
        ".php": "PHP", ".swift": "Swift", ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++", ".sql": "SQL",
        ".sh": "Shell", ".ps1": "PowerShell", ".bat": "Batch", ".css": "CSS", ".scss": "CSS", ".html": "HTML",
        ".vue": "Vue", ".svelte": "Svelte", ".dart": "Dart"}
FRAMEWORKS = {  # dependency name → framework label
    "fastapi": "FastAPI", "django": "Django", "flask": "Flask", "sqlalchemy": "SQLAlchemy", "alembic": "Alembic",
    "pytest": "pytest", "langgraph": "LangGraph", "celery": "Celery", "pydantic": "Pydantic",
    "next": "Next.js", "react": "React", "vue": "Vue", "svelte": "Svelte", "@angular/core": "Angular",
    "express": "Express", "nestjs": "NestJS", "@nestjs/core": "NestJS", "tailwindcss": "Tailwind CSS",
    "vite": "Vite", "jest": "Jest", "vitest": "Vitest", "@playwright/test": "Playwright", "prisma": "Prisma",
    "typeorm": "TypeORM", "spring-boot": "Spring Boot", "gin-gonic/gin": "Gin", "actix-web": "Actix",
}
ROLE_DIRS = {"frontend": "frontend", "web": "frontend", "client": "frontend", "ui": "frontend", "app": "application",
             "backend": "backend", "server": "backend", "api": "backend", "src": "source", "lib": "library",
             "tests": "tests", "test": "tests", "__tests__": "tests", "e2e": "tests", "docs": "documentation",
             "doc": "documentation", "scripts": "tooling", "infra": "infrastructure", "deploy": "infrastructure",
             "docker": "infrastructure", "k8s": "infrastructure", "helm": "infrastructure", "migrations": "database",
             "alembic": "database", "db": "database", ".github": "ci"}
TEST_RE = re.compile(r"(^test_.*\.py$|_test\.py$|\.test\.[jt]sx?$|\.spec\.[jt]sx?$|_test\.go$|Test\.java$)")
DEBT_RE = re.compile(rb"\b(TODO|FIXME|HACK|XXX)\b")
MAX_FILES, MAX_READ, LARGE_FILE_LINES = 20_000, 256 * 1024, 800


def _walk(root: Path):
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for p in entries:
            if p.is_symlink():
                continue
            if p.is_dir():
                if p.name not in SKIP_DIRS:
                    stack.append(p)
            elif p.is_file():
                yield p


def _deps_python(root: Path) -> dict[str, str]:
    deps: dict[str, str] = {}
    for f in [*root.glob("pyproject.toml"), *root.glob("*/pyproject.toml")]:
        try:
            data = tomllib.loads(f.read_text(errors="replace"))
        except (tomllib.TOMLDecodeError, OSError):
            continue
        for spec in data.get("project", {}).get("dependencies", []):
            m = re.match(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*(.*)$", spec)
            if m:
                deps[m.group(1).split("[")[0].lower()] = m.group(2).strip()
        for name, spec in data.get("tool", {}).get("poetry", {}).get("dependencies", {}).items():
            deps[name.lower()] = spec if isinstance(spec, str) else json.dumps(spec)
    for f in [*root.glob("requirements*.txt"), *root.glob("*/requirements*.txt")]:
        for line in f.read_text(errors="replace").splitlines():
            m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*([<>=!~].*)?$", line.split("#")[0])
            if m:
                deps[m.group(1).lower()] = (m.group(2) or "").strip()
    return deps


def _deps_node(root: Path) -> dict[str, str]:
    deps: dict[str, str] = {}
    for f in [*root.glob("package.json"), *root.glob("*/package.json")]:
        try:
            data = json.loads(f.read_text(errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        for key in ("dependencies", "devDependencies"):
            for name, ver in (data.get(key) or {}).items():
                deps[name] = str(ver)
    return deps


def _deps_go(root: Path) -> dict[str, str]:
    deps = {}
    for f in root.glob("**/go.mod"):
        for m in re.finditer(r"^\s*([\w./\-]+)\s+(v[\w.\-+]+)", f.read_text(errors="replace"), re.MULTILINE):
            deps[m.group(1)] = m.group(2)
        break
    return deps


def analyze(root: Path) -> dict[str, Any]:
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(str(root))
    langs: Counter[str] = Counter()
    lines_by_lang: Counter[str] = Counter()
    dirs: Counter[str] = Counter()
    tests: list[str] = []
    large: list[dict[str, Any]] = []
    debt_markers, files = 0, 0
    for p in _walk(root):
        files += 1
        if files > MAX_FILES:
            break
        rel = p.relative_to(root)
        top = rel.parts[0] if len(rel.parts) > 1 else "."
        dirs[top] += 1
        lang = LANG.get(p.suffix.lower())
        if TEST_RE.search(p.name) or (len(rel.parts) > 1 and rel.parts[0] in ("tests", "test", "e2e", "__tests__")):
            tests.append(str(rel))
        if lang:
            langs[lang] += 1
            try:
                data = p.read_bytes()[:MAX_READ]
            except OSError:
                continue
            n = data.count(b"\n") + 1
            lines_by_lang[lang] += n
            debt_markers += len(DEBT_RE.findall(data))
            if n > LARGE_FILE_LINES:
                large.append({"file": str(rel), "lines": n})
    py, node, go = _deps_python(root), _deps_node(root), _deps_go(root)
    all_deps = {**py, **node, **go}
    frameworks = sorted({label for name, label in FRAMEWORKS.items() if name in all_deps})
    names = {p.name for p in root.iterdir()} if root.exists() else set()
    infra = [x for x in ("Dockerfile", "docker-compose.yml", "compose.yaml", "docker-compose.yaml", "Makefile",
                         ".github", ".gitlab-ci.yml", "Procfile", "vercel.json") if x in names]
    architecture = [{"dir": d, "role": ROLE_DIRS.get(d.lower(), "other"), "files": c}
                    for d, c in sorted(dirs.items(), key=lambda kv: -kv[1]) if d != "."][:40]
    locks = [x for x in ("uv.lock", "poetry.lock", "Pipfile.lock", "package-lock.json", "pnpm-lock.yaml",
                         "yarn.lock", "go.sum", "Cargo.lock") if (root / x).exists() or any(root.glob(f"*/{x}"))]
    debt = []
    if debt_markers:
        debt.append({"kind": "markers", "detail": f"{debt_markers} TODO/FIXME/HACK markers"})
    if large:
        debt.append({"kind": "large_files", "detail": f"{len(large)} files over {LARGE_FILE_LINES} lines"})
    if langs and not tests:
        debt.append({"kind": "no_tests", "detail": "no test files found"})
    if all_deps and not locks:
        debt.append({"kind": "no_lockfile", "detail": "dependencies are declared but not locked"})
    unpinned = [n for n, v in py.items() if not v] + [n for n, v in node.items() if v in ("*", "latest")]
    if unpinned:
        debt.append({"kind": "unpinned", "detail": f"{len(unpinned)} unpinned dependencies", "items": unpinned[:20]})
    if not (root / "README.md").exists() and not (root / "README").exists():
        debt.append({"kind": "no_readme", "detail": "no README"})
    test_frameworks = sorted({FRAMEWORKS[n] for n in ("pytest", "jest", "vitest", "@playwright/test")
                              if n in all_deps})
    return {
        "analyzed_at": datetime.now(UTC).isoformat(), "files": min(files, MAX_FILES), "truncated": files > MAX_FILES,
        "technologies": {"languages": [{"name": k, "files": v, "lines": lines_by_lang[k]} for k, v in
                                       langs.most_common()],
                         "frameworks": frameworks, "infrastructure": infra},
        "architecture": architecture,
        "dependencies": {"python": dict(sorted(py.items())[:300]), "node": dict(sorted(node.items())[:300]),
                         "go": dict(sorted(go.items())[:300]), "lockfiles": locks},
        "tests": {"files": len(tests), "frameworks": test_frameworks, "sample": tests[:30]},
        "large_files": sorted(large, key=lambda x: -x["lines"])[:20],
        "debt": debt,
    }


def summary(a: dict[str, Any]) -> str:
    """A short text map for the planner prompt (data from the repository, so it is passed as untrusted context)."""
    t = a.get("technologies", {})
    langs = ", ".join(f"{x['name']} ({x['files']} files)" for x in t.get("languages", [])[:6]) or "none detected"
    arch = ", ".join(f"{x['dir']}/ ({x['role']})" for x in a.get("architecture", [])[:12]) or "flat"
    debt = "; ".join(d["detail"] for d in a.get("debt", [])) or "none detected"
    return (f"EXISTING REPOSITORY MAP — languages: {langs}. Frameworks: {', '.join(t.get('frameworks', [])) or '—'}. "
            f"Infrastructure: {', '.join(t.get('infrastructure', [])) or '—'}. Structure: {arch}. "
            f"Tests: {a.get('tests', {}).get('files', 0)} files "
            f"({', '.join(a.get('tests', {}).get('frameworks', [])) or 'framework unknown'}). Technical debt: {debt}.")
