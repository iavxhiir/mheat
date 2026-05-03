"""Application settings, all sourced from environment variables (12-factor).

The values here are the single source of truth for the service's runtime
configuration. Never hardcode credentials, paths, or endpoints elsewhere.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env against the project root (mheat/) so the file is found
# regardless of the caller's CWD (backend/, mheat/, container, CI).
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILES = (
    str(_PROJECT_ROOT / ".env"),
    str(_PROJECT_ROOT / "backend" / ".env"),
    ".env",
)


class Settings(BaseSettings):
    """Runtime configuration loaded from the process environment.

    Attributes:
        cms_username / cms_password: Copernicus Marine credentials. Required
            for live fetches; the service still boots without them but every
            request that needs an uncached date will return 503.
        bbox: Study area as (lon_min, lat_min, lon_max, lat_max).
        clim_start / clim_end: Inclusive reference years for the climatology.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Runtime
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    log_level: str = Field(default="info")
    log_format: str = Field(default="json")  # json|text

    # CMS
    cms_username: str | None = Field(
        default=None, validation_alias="COPERNICUSMARINE_SERVICE_USERNAME"
    )
    cms_password: str | None = Field(
        default=None, validation_alias="COPERNICUSMARINE_SERVICE_PASSWORD"
    )

    # CORS
    cors_origins: str = Field(default="http://localhost:8000,http://localhost:5173")

    # Data
    cache_dir: Path = Field(default=Path("/data/cache"))
    zarr_store: Path = Field(default=Path("/data/cache/sst.zarr"))
    climatology_store: Path = Field(
        default=Path("/data/cache/climatology.zarr"),
        validation_alias="CLIMATOLOGY_STORE",
    )
    frontend_dir: Path = Field(default=Path("/srv/frontend"))

    # Study area & climatology
    bbox: str = Field(default="-6.0,30.0,36.5,46.0")
    clim_start: int = Field(default=1991)
    clim_end: int = Field(default=2020)

    # Overlays — endpoints verified against the live catalogues 2026-04.
    # Aquaculture: GeoServer WFS, typeNames `emodnet:aquaculture` (the
    # `*_points` suffix used in earlier passes returns 400).
    # Seagrass: the `emodnet_open` namespace under `geoserver` (the older
    # `emodnet_open_maplibrary` namespace only carries Article-17 reporting
    # data, no Mediterranean Posidonia coverage).
    # Natura 2000: EEA ArcGIS REST (the legacy WFSServer at the same path
    # returns 400 — `Natura2000Sites/MapServer` is the live REST endpoint).
    emodnet_aquaculture_wfs: str = Field(
        default="https://ows.emodnet-humanactivities.eu/wfs"
    )
    emodnet_seabed_wfs: str = Field(
        default="https://ows.emodnet-seabedhabitats.eu/geoserver/emodnet_open/wfs"
    )
    natura2000_wfs: str = Field(
        default="https://bio.discomap.eea.europa.eu/arcgis/rest/services/"
        "ProtectedSites/Natura2000Sites/MapServer"
    )

    # Copernicus dataset IDs (overridable). These are dataset-level IDs used by
    # copernicusmarine.subset(), not the parent product IDs shown on the portal.
    cms_nrt_product: str = Field(default="SST_MED_SST_L4_NRT_OBSERVATIONS_010_004_a_V2")
    cms_reanalysis_product: str = Field(default="cmems_mod_med_phy-temp_my_4.2km_P1D-m")
    cms_forecast_product: str = Field(default="cmems_mod_med_phy-tem_anfc_4.2km_P1D-m")

    @field_validator("cors_origins")
    @classmethod
    def _normalize_cors(cls, v: str) -> str:
        return v.strip()

    @field_validator("bbox")
    @classmethod
    def _validate_bbox_shape_and_range(cls, v: str) -> str:
        """``bbox`` must be 4 CSV floats within valid lon/lat ranges and non-degenerate."""
        try:
            parts = [float(p.strip()) for p in v.split(",")]
        except ValueError as e:
            raise ValueError(f"BBOX must be 4 comma-separated floats, got {v!r}") from e
        if len(parts) != 4:
            raise ValueError(f"BBOX must have exactly 4 values, got {len(parts)} in {v!r}")
        lon_min, lat_min, lon_max, lat_max = parts
        if not (-180.0 <= lon_min <= 180.0 and -180.0 <= lon_max <= 180.0):
            raise ValueError(f"BBOX longitudes must be within [-180, 180]: got {v!r}")
        if not (-90.0 <= lat_min <= 90.0 and -90.0 <= lat_max <= 90.0):
            raise ValueError(f"BBOX latitudes must be within [-90, 90]: got {v!r}")
        if lon_min >= lon_max or lat_min >= lat_max:
            raise ValueError(f"BBOX is degenerate (min >= max on an axis): {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_climatology_window(self) -> Settings:
        """Climatology must be a non-degenerate span of ≥ 10 years, aligned with WMO practice."""
        if self.clim_start >= self.clim_end:
            raise ValueError(
                f"CLIM_START ({self.clim_start}) must be strictly less than "
                f"CLIM_END ({self.clim_end})",
            )
        if self.clim_end - self.clim_start < 10:
            raise ValueError(
                "Climatology window must span at least 10 years "
                f"(got {self.clim_end - self.clim_start})",
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a clean list of strings."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def bbox_tuple(self) -> tuple[float, float, float, float]:
        """Study area as (lon_min, lat_min, lon_max, lat_max)."""
        parts = [float(p) for p in self.bbox.split(",")]
        if len(parts) != 4:
            raise ValueError(f"BBOX must have 4 comma-separated floats, got {self.bbox!r}")
        return (parts[0], parts[1], parts[2], parts[3])

    def credentials_present(self) -> bool:
        """True if CMS username and password are both set."""
        return bool(self.cms_username and self.cms_password)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the memoized Settings singleton."""
    return Settings()
