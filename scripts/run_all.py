"""Run every MHEAT build / test / audit gate with one command.

Cross-platform: works on Linux, macOS, Windows (PowerShell, cmd, Git-Bash).
Zero deps beyond the stdlib — the script itself never imports anything the
repo doesn't already depend on for its test + build tooling.

Usage::

    python scripts/run_all.py                        # fast gates (< 5 min)
    python scripts/run_all.py --include-slow          # + bench + docker build
    python scripts/run_all.py --only backend-tests    # run a single phase
    python scripts/run_all.py --skip docker           # skip listed phases (CSV)
    python scripts/run_all.py --no-install            # reuse existing env
    python scripts/run_all.py --list                  # print all phase names

Exit code: 0 if every enabled phase passed, otherwise 1.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def _ensure_utf8_stdio() -> None:
    """Force UTF-8 on Windows cp1252 consoles so box-drawing chars don't crash."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


_ensure_utf8_stdio()


def _supports_unicode() -> bool:
    enc = (sys.stdout.encoding or "").lower()
    return enc in {"utf-8", "utf8"} or sys.platform != "win32"


UNICODE = _supports_unicode()
GLYPH_OK = "✓" if UNICODE else "PASS"
GLYPH_FAIL = "✗" if UNICODE else "FAIL"
GLYPH_SKIP = "-" if UNICODE else "skip"
GLYPH_RUN = "▶" if UNICODE else ">"
GLYPH_PIPE = "│" if UNICODE else "|"
GLYPH_LINE = "━" if UNICODE else "-"


# ---- ANSI colours (no-op on legacy Windows consoles) ---------------------
def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.name == "nt" and not os.environ.get("WT_SESSION"):
        # Legacy cmd.exe doesn't do ANSI; Windows Terminal does.
        return False
    return True


C_OK = "\033[1;32m" if _use_color() else ""
C_FAIL = "\033[1;31m" if _use_color() else ""
C_SKIP = "\033[1;33m" if _use_color() else ""
C_HEAD = "\033[1;36m" if _use_color() else ""
C_DIM = "\033[2m" if _use_color() else ""
C_OFF = "\033[0m" if _use_color() else ""


# ---- data ----------------------------------------------------------------
@dataclass
class PhaseResult:
    name: str
    status: str = "pending"   # pending | passed | failed | skipped
    seconds: float = 0.0
    note: str = ""


@dataclass
class Phase:
    name: str
    title: str
    cmd: list[str] | None = None
    cwd: Path = ROOT
    env: dict[str, str] = field(default_factory=dict)
    slow: bool = False
    needs: str | None = None   # name of a binary that must be on PATH
    check_only: str | None = None   # optional path that must exist for the phase to make sense


# ---- phase catalogue -----------------------------------------------------
PHASES: list[Phase] = [
    # --- environment --------------------------------------------------
    Phase(
        name="env-check",
        title="Environment check (python / node / docker)",
        cmd=None,   # custom — handled in run_env_check
    ),
    # --- install (optional via --no-install) --------------------------
    Phase(
        name="install-backend",
        title="pip install backend dev requirements",
        cmd=[sys.executable, "-m", "pip", "install", "--quiet", "-r", "requirements-dev.txt"],
        cwd=BACKEND,
    ),
    Phase(
        name="install-frontend",
        title="npm install frontend dev deps",
        cmd=["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"],
        cwd=FRONTEND,
        needs="npm",
    ),
    # --- backend gates ------------------------------------------------
    Phase(
        name="ruff",
        title="ruff lint (10 rule groups)",
        cmd=[sys.executable, "-m", "ruff", "check", "app/", "tests/"],
        cwd=BACKEND,
    ),
    Phase(
        name="mypy",
        title="mypy type-check",
        cmd=[sys.executable, "-m", "mypy", "app"],
        cwd=BACKEND,
    ),
    Phase(
        name="backend-tests",
        title="pytest (coverage gate)",
        cmd=[sys.executable, "-m", "pytest"],
        cwd=BACKEND,
    ),
    # --- frontend gates ----------------------------------------------
    Phase(
        name="frontend-lint",
        title="ESLint (frontend)",
        cmd=["npm", "run", "lint"],
        cwd=FRONTEND,
        needs="npm",
    ),
    Phase(
        name="frontend-build",
        title="Vite + tsc production bundle",
        cmd=["npm", "run", "build"],
        cwd=FRONTEND,
        needs="npm",
    ),
    Phase(
        name="frontend-tests",
        title="Vitest unit tests + coverage",
        cmd=["npx", "vitest", "run", "--coverage"],
        cwd=FRONTEND,
        needs="npx",
    ),
    # --- security gates ----------------------------------------------
    Phase(
        name="pip-audit",
        title="pip-audit (Python deps, HIGH/CRITICAL)",
        cmd=None,   # custom — strips git+ lines first
    ),
    Phase(
        name="npm-audit",
        title="npm audit (prod deps, HIGH/CRITICAL)",
        cmd=["npm", "audit", "--omit=dev", "--audit-level=high"],
        cwd=FRONTEND,
        needs="npm",
    ),
    # --- reproducibility + artefacts ---------------------------------
    Phase(
        name="reproduce",
        title="Reproducibility manifest (out/)",
        cmd=[sys.executable, "scripts/reproduce.py"],
    ),
    Phase(
        name="arco",
        title="ARCO Zarr export (out/mheat.zarr)",
        cmd=[sys.executable, "scripts/export_arco.py", "--out", str(ROOT / "out" / "mheat.zarr")],
    ),
    Phase(
        name="stac",
        title="STAC Collection + Items dry-run (out/stac)",
        cmd=[sys.executable, "scripts/register_stac.py",
             "--out", str(ROOT / "out" / "stac"),
             "--years", "2022", "2023", "2024"],
    ),
    # --- slow / optional ----------------------------------------------
    Phase(
        name="bench",
        title="In-process latency benchmark (docs/performance.md)",
        cmd=[sys.executable, "scripts/bench_inproc.py"],
        slow=True,
    ),
    Phase(
        name="docker",
        title="Docker image build (requires daemon)",
        cmd=["docker", "build", "-t", "mheat:run-all", "."],
        slow=True,
        needs="docker",
    ),
]


# ---- runners -------------------------------------------------------------
def banner(msg: str, ch: str = GLYPH_LINE) -> None:
    line = ch * min(76, max(20, len(msg) + 4))
    print(f"\n{C_HEAD}{line}{C_OFF}")
    print(f"{C_HEAD}  {msg}{C_OFF}")
    print(f"{C_HEAD}{line}{C_OFF}")


def short(s: str, n: int = 80) -> str:
    s = s.strip().replace("\r", "")
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def run_phase(phase: Phase) -> PhaseResult:
    """Execute one phase. Returns a PhaseResult — never raises."""
    result = PhaseResult(name=phase.name)
    t0 = time.perf_counter()

    if phase.needs and shutil.which(phase.needs) is None:
        result.status = "skipped"
        result.note = f"missing binary: {phase.needs}"
        return result

    if phase.name == "env-check":
        return run_env_check()
    if phase.name == "pip-audit":
        return run_pip_audit()

    try:
        env = {**os.environ, **phase.env}
        assert phase.cmd is not None
        # On Windows, `npm` / `npx` / `node` / `docker` are `.cmd` shims —
        # subprocess can only execute them via the shell. Elsewhere, stay
        # with the direct exec so we don't pull in shell-parsing quirks.
        cmd_list = list(phase.cmd)
        use_shell = (
            os.name == "nt"
            and cmd_list
            and cmd_list[0] in {"npm", "npx", "node", "docker"}
        )
        if use_shell:
            completed = subprocess.run(
                subprocess.list2cmdline(cmd_list),
                cwd=str(phase.cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=60 * 30,
                shell=True,
            )
        else:
            completed = subprocess.run(
                cmd_list,
                cwd=str(phase.cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=60 * 30,
            )
    except FileNotFoundError as e:
        result.status = "skipped"
        result.note = f"command not found: {e.filename}"
        result.seconds = time.perf_counter() - t0
        return result
    except subprocess.TimeoutExpired:
        result.status = "failed"
        result.note = "timeout after 30 min"
        result.seconds = time.perf_counter() - t0
        return result

    result.seconds = time.perf_counter() - t0
    if completed.returncode == 0:
        result.status = "passed"
        # Pull the last meaningful line into the summary note.
        tail = (completed.stdout or completed.stderr).strip().splitlines()
        if tail:
            result.note = short(tail[-1], 60)
    else:
        result.status = "failed"
        tail = (completed.stderr or completed.stdout).strip().splitlines()
        if tail:
            result.note = short(tail[-1], 80)
        # Dump the tail of the output inline so the operator sees what broke.
        sys.stdout.write(C_DIM)
        for line in (completed.stdout or "").splitlines()[-20:]:
            sys.stdout.write(f"  {GLYPH_PIPE} {line}\n")
        for line in (completed.stderr or "").splitlines()[-20:]:
            sys.stdout.write(f"  {GLYPH_PIPE} {line}\n")
        sys.stdout.write(C_OFF)
    return result


def run_env_check() -> PhaseResult:
    result = PhaseResult(name="env-check")
    t0 = time.perf_counter()
    lines = []
    for binary, human in [("python", "Python"), ("node", "Node"), ("npm", "npm"),
                          ("docker", "Docker")]:
        path = shutil.which(binary)
        if path is None:
            lines.append(f"  - {human:<8} missing (OK if you don't need this phase)")
            continue
        try:
            version = subprocess.check_output(
                [path, "--version"], text=True, stderr=subprocess.STDOUT, timeout=10,
            ).strip().splitlines()[0]
        except Exception:  # noqa: BLE001
            version = "(?)"
        lines.append(f"  - {human:<8} {version}")
    print("\n".join(lines))
    result.status = "passed"
    result.seconds = time.perf_counter() - t0
    result.note = f"found: {sum(1 for l in lines if 'missing' not in l)}/4 binaries"
    return result


def run_pip_audit() -> PhaseResult:
    result = PhaseResult(name="pip-audit")
    t0 = time.perf_counter()
    req = BACKEND / "requirements.txt"
    audit_src = ROOT / "out" / "_req_audit.txt"
    audit_src.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        ln for ln in req.read_text(encoding="utf-8").splitlines()
        if "git+" not in ln
    ]
    audit_src.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pip_audit", "--strict", "-r", str(audit_src)],
            capture_output=True, text=True, timeout=60 * 15,
        )
    except FileNotFoundError:
        result.status = "skipped"
        result.note = "pip-audit not installed"
        result.seconds = time.perf_counter() - t0
        return result

    result.seconds = time.perf_counter() - t0
    tail = (completed.stdout or completed.stderr).strip().splitlines()
    if completed.returncode == 0:
        result.status = "passed"
        result.note = tail[-1] if tail else "no known vulnerabilities"
    else:
        result.status = "failed"
        result.note = short(tail[-1] if tail else "pip-audit failed", 80)
        sys.stdout.write(C_DIM)
        for line in (completed.stdout or "").splitlines()[-20:]:
            sys.stdout.write(f"  {GLYPH_PIPE} {line}\n")
        sys.stdout.write(C_OFF)
    return result


def render_summary(results: list[PhaseResult]) -> int:
    banner("Summary")
    passed = failed = skipped = 0
    name_width = max(len(r.name) for r in results)
    for r in results:
        if r.status == "passed":
            tag = f"{C_OK}{GLYPH_OK} pass{C_OFF}"
            passed += 1
        elif r.status == "failed":
            tag = f"{C_FAIL}{GLYPH_FAIL} fail{C_OFF}"
            failed += 1
        elif r.status == "skipped":
            tag = f"{C_SKIP}{GLYPH_SKIP} skip{C_OFF}"
            skipped += 1
        else:
            tag = "? pending"
        dur = f"{r.seconds:6.2f}s"
        print(f"  {tag}  {r.name.ljust(name_width)}  {dur}  {C_DIM}{r.note}{C_OFF}")

    print()
    summary = f"{passed} passed, {failed} failed, {skipped} skipped"
    print(f"  {C_OK if failed == 0 else C_FAIL}{summary}{C_OFF}")
    return 0 if failed == 0 else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    names = [ph.name for ph in PHASES]
    p.add_argument("--only", help="Run only these phases (comma-separated).")
    p.add_argument("--skip", help="Skip these phases (comma-separated).")
    p.add_argument("--include-slow", action="store_true",
                   help=f"Include slow phases: {', '.join(ph.name for ph in PHASES if ph.slow)}.")
    p.add_argument("--no-install", action="store_true",
                   help="Skip the two install-* phases (use the current venv / node_modules).")
    p.add_argument("--list", action="store_true",
                   help="Print the phase catalogue and exit.")
    p.add_argument("--keep-going", action="store_true",
                   help="Do not stop on the first failing phase.")
    p.add_argument("--quick", action="store_true",
                   help="Minimal-but-meaningful set: lint + type + backend tests "
                        "+ frontend tests + reproduce. Skips installs, audits, "
                        "bench, docker, STAC, ARCO.")
    ns = p.parse_args(argv)
    if ns.quick:
        quick_phases = {"env-check", "ruff", "mypy", "backend-tests",
                        "frontend-tests", "reproduce"}
        ns.only = ",".join(n for n in (ph.name for ph in PHASES) if n in quick_phases)
    if ns.list:
        for ph in PHASES:
            flags = " (slow)" if ph.slow else ""
            print(f"  {ph.name:<18} — {ph.title}{flags}")
        sys.exit(0)

    ns.selected = set(ns.only.split(",")) if ns.only else None
    ns.skipped = set(ns.skip.split(",")) if ns.skip else set()
    if ns.no_install:
        ns.skipped |= {"install-backend", "install-frontend"}
    bad = (ns.skipped | (ns.selected or set())) - set(names)
    if bad:
        p.error(f"unknown phase(s): {sorted(bad)}")
    return ns


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results: list[PhaseResult] = []

    for ph in PHASES:
        if args.selected and ph.name not in args.selected:
            continue
        if ph.name in args.skipped:
            results.append(PhaseResult(name=ph.name, status="skipped", note="--skip"))
            continue
        if ph.slow and not args.include_slow and not args.selected:
            results.append(PhaseResult(name=ph.name, status="skipped",
                                       note="slow — pass --include-slow"))
            continue

        banner(f"{GLYPH_RUN} {ph.name} — {ph.title}")
        result = run_phase(ph)
        results.append(result)

        status_tag = {
            "passed":  f"{C_OK}{GLYPH_OK} pass{C_OFF}",
            "failed":  f"{C_FAIL}{GLYPH_FAIL} fail{C_OFF}",
            "skipped": f"{C_SKIP}{GLYPH_SKIP} skip{C_OFF}",
        }.get(result.status, "?")
        print(f"\n  {status_tag} in {result.seconds:.1f}s — {result.note}")
        if result.status == "failed" and not args.keep_going:
            print(f"\n{C_FAIL}Stopping at first failure. Pass --keep-going to run all phases.{C_OFF}")
            break

    return render_summary(results)


if __name__ == "__main__":
    sys.exit(main())
