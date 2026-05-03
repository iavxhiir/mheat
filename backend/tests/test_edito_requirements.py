"""EDITO onboarding self-audit — verifies the guideline §4 / §5 constraints
at test time so a reviewer can run ``pytest -k edito`` and see every
technical requirement asserted automatically.

Each test name maps 1-to-1 to a requirement in ``docs/edito_requirements.md``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "backend" / "app"
REQUIREMENTS = REPO_ROOT / "backend" / "requirements.txt"
CHART_VALUES = REPO_ROOT / "charts" / "mheat" / "values.yaml"
DOCKERFILE = REPO_ROOT / "Dockerfile"


# ---------------------------------------------------------------------------
# §4.1  Containerization
# ---------------------------------------------------------------------------
def test_edito_s4_1_dockerfile_present_and_multi_stage():
    assert DOCKERFILE.is_file(), "Dockerfile missing at repo root"
    text = DOCKERFILE.read_text(encoding="utf-8")
    stages = re.findall(r"(?m)^FROM\s+\S+(?:\s+AS\s+\w+)?", text)
    assert len(stages) >= 2, f"expected a multi-stage build, found {len(stages)} FROM lines"


# ---------------------------------------------------------------------------
# §4.2  Configurable via environment variables
# ---------------------------------------------------------------------------
_TOLERATED_URL_FRAGMENTS: tuple[str, ...] = (
    # Paper / standard references — docstring or schema literals, not service calls.
    "doi.org/",
    "example.com",
    "your-org",
    "localhost",
    "github.com",
    "marine.copernicus.eu",
    "edito.eu",
    "mheat.edito.example",
    "opensource.org/licenses/",  # licence URLs
    "opengis.net/",               # OGC standard URIs (ogcapi, CRS84, conformance)
    "ogcapi.ogc.org/",             # OGC API docs referenced from docstrings
    "api.stacspec.org/",          # STAC API conformance class URIs (spec identifiers)
    "json-schema.org/",           # JSON Schema $schema URIs (spec identifiers, OGC queryables)
    "cdn.jsdelivr.net",           # Swagger UI / ReDoc bundles, allow-listed in /api/docs CSP only
    "fastapi.tiangolo.com",       # FastAPI favicon, allow-listed in /api/docs CSP only
    "tile.openstreetmap.org",      # basemap source in default CSP
    "basemaps.cartocdn.com",       # basemap source in default CSP
    "emodnet-humanactivities.eu", # upstream default, also exposed via Settings
    "emodnet-seabedhabitats.eu",  # idem
    "discomap.eea.europa.eu",     # idem
)


def test_edito_s4_2_no_hard_coded_non_whitelisted_urls():
    """No http(s) URL should appear in app/ outside the tolerated fragments."""
    url_re = re.compile(r"https?://[^\s\"'<>]+")
    offenders: list[str] = []
    for py in APP_DIR.rglob("*.py"):
        for ln, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            for url in url_re.findall(line):
                url = url.rstrip(".,);\"'")
                if any(tag in url for tag in _TOLERATED_URL_FRAGMENTS):
                    continue
                offenders.append(f"{py.relative_to(REPO_ROOT)}:{ln}: {url}")
    assert not offenders, "Hard-coded URLs found (must go through Settings):\n" + "\n".join(offenders)


def test_edito_s4_2_config_exposes_these_urls_as_env_settings():
    from app.config import Settings

    settings = Settings()
    # Each whitelisted URL must be the default of a field on Settings.
    defaults = {
        settings.emodnet_aquaculture_wfs,
        settings.emodnet_seabed_wfs,
        settings.natura2000_wfs.replace("\n", "").replace(" ", ""),
    }
    assert any("emodnet-humanactivities.eu" in u for u in defaults)
    assert any("emodnet-seabedhabitats.eu" in u for u in defaults)
    assert any("europa.eu" in u for u in defaults)


def test_edito_s4_2_no_committed_credentials():
    """No token / password / secret literals in the app package."""
    patterns = [
        re.compile(r"(?i)(password\s*=\s*['\"][^'\"]{3,}['\"])"),
        re.compile(r"(?i)(api[_-]?key\s*=\s*['\"][^'\"]{6,}['\"])"),
        re.compile(r"(?i)(secret\s*=\s*['\"][^'\"]{6,}['\"])"),
        re.compile(r"(?i)(token\s*=\s*['\"][^'\"]{10,}['\"])"),
    ]
    offenders: list[str] = []
    for py in APP_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for rx in patterns:
            for m in rx.finditer(text):
                offenders.append(f"{py.relative_to(REPO_ROOT)}: {m.group(0)[:60]}…")
    assert not offenders, "Possible committed credentials:\n" + "\n".join(offenders)


def test_edito_s4_2_env_example_documents_the_knobs():
    env_example = REPO_ROOT / ".env.example"
    assert env_example.is_file(), ".env.example is required to document the env surface"
    text = env_example.read_text(encoding="utf-8")
    for must_have in ("DEMO_MODE", "CACHE_DIR", "BBOX"):
        assert must_have in text, f"{must_have} missing from .env.example"


# ---------------------------------------------------------------------------
# §4.3  Image size — dry check (full size is asserted in CI via docker)
# ---------------------------------------------------------------------------
def test_edito_s4_3_no_large_data_bundled_in_image():
    """No *.nc / *.zarr / raster larger than 20 MiB under backend/ or frontend/
    that would bloat the image. The fixture cube is small on purpose."""
    too_large: list[str] = []
    for root in ("backend", "frontend"):
        for p in (REPO_ROOT / root).rglob("*"):
            if p.is_file() and p.suffix.lower() in {".nc", ".zarr", ".tif", ".tiff", ".geotiff"}:
                size_mb = p.stat().st_size / (1024 * 1024)
                if size_mb > 20:
                    too_large.append(f"{p.relative_to(REPO_ROOT)}: {size_mb:.1f} MiB")
    assert not too_large, "Dataset files > 20 MiB committed:\n" + "\n".join(too_large)


# ---------------------------------------------------------------------------
# §4.4  External-dependency inventory
# ---------------------------------------------------------------------------
def test_edito_s4_4_dependency_inventory_exists():
    inv = REPO_ROOT / "docs" / "edito_requirements.md"
    assert inv.is_file(), "docs/edito_requirements.md missing"
    text = inv.read_text(encoding="utf-8")
    assert "Declaration of external dependencies" in text
    # Must list the three WFS data providers explicitly.
    for provider in ("Copernicus Marine", "EMODnet", "Natura 2000"):
        assert provider in text, f"{provider} missing from dependency inventory"


def test_edito_s4_4_python_deps_are_pinned():
    assert REQUIREMENTS.is_file()
    lines = [
        ln.strip() for ln in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    unpinned = [
        ln for ln in lines
        if not re.match(r"^[A-Za-z0-9_.\-\[\]]+\s*([=<>~!]=?|@)", ln)
    ]
    assert not unpinned, f"Unpinned requirements lines:\n{unpinned}"


# ---------------------------------------------------------------------------
# §4.5  Resource quota compliance
# ---------------------------------------------------------------------------
def test_edito_s4_5_helm_resources_within_default_quota():
    import yaml

    values = yaml.safe_load(CHART_VALUES.read_text(encoding="utf-8"))
    limits = values["resources"]["limits"]

    def _cpu(v: str) -> float:
        v = str(v)
        return float(v[:-1]) / 1000 if v.endswith("m") else float(v)

    def _gib(v: str) -> float:
        v = str(v)
        if v.endswith("Gi"):
            return float(v[:-2])
        if v.endswith("Mi"):
            return float(v[:-2]) / 1024
        return float(v) / (1024 ** 3)

    assert _cpu(limits["cpu"]) <= 8, f"CPU limit {limits['cpu']} exceeds EDITO 8-core quota"
    assert _gib(limits["memory"]) <= 32, f"Memory limit {limits['memory']} exceeds EDITO 32 GiB quota"


# ---------------------------------------------------------------------------
# §5  ARCO dataset format — script exists and produces Zarr
# ---------------------------------------------------------------------------
def test_edito_s5_arco_export_script_exists():
    exp = REPO_ROOT / "scripts" / "export_arco.py"
    assert exp.is_file(), "scripts/export_arco.py missing — §5 ARCO requirement"
    text = exp.read_text(encoding="utf-8")
    # Must reference Zarr as the on-disk format.
    assert "zarr" in text.lower() or "to_zarr" in text


def test_edito_s5_stac_register_script_exists():
    reg = REPO_ROOT / "scripts" / "register_stac.py"
    assert reg.is_file(), "scripts/register_stac.py missing"


# ---------------------------------------------------------------------------
# §3  Priority to open data / open standards / open source
# ---------------------------------------------------------------------------
def test_licence_is_mit():
    lic = REPO_ROOT / "LICENSE"
    assert lic.is_file()
    text = lic.read_text(encoding="utf-8").upper()
    assert "MIT" in text, "Licence must be MIT per the proposal"


@pytest.mark.parametrize(
    "path", [
        "README.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "docs/reproducibility.md",
        "docs/edito_requirements.md",
    ],
)
def test_key_reviewer_docs_exist(path: str):
    assert (REPO_ROOT / path).is_file(), f"{path} missing — reviewers expect it"


# ---------------------------------------------------------------------------
# §8  Maintenance commitment until 2028-08-31
# ---------------------------------------------------------------------------
def test_sustainability_commitment_until_2028():
    sust = REPO_ROOT / "docs" / "sustainability.md"
    assert sust.is_file(), "docs/sustainability.md missing"
    text = sust.read_text(encoding="utf-8")
    assert "2028-08-31" in text or "31 August 2028" in text, (
        "sustainability.md must state the maintenance commitment until 2028-08-31"
    )
