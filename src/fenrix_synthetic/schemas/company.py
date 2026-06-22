"""Company configuration schema."""

from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class CompanyConfig(BaseModel):
    """Company identity configuration mapping internal ID to source identity."""

    company_id: str = Field(..., description="Internal company ID, e.g., C001")
    source_identity: str = Field(..., description="Private source identity, e.g., CHC")
    ticker: str = Field(default="", description="Stock ticker symbol for SEC (private)")
    cik: str = Field(default="", description="10-digit SEC CIK (private, auto-resolvable)")
    data_root: Path = Field(default=Path("data"), description="Base data directory")
    raw_dir: Path = Field(default=Path("data/raw"), description="Raw artifact directory")
    bronze_dir: Path = Field(default=Path("data/bronze"), description="Bronze artifact directory")

    @field_validator("data_root", "raw_dir", "bronze_dir", mode="before")
    @classmethod
    def resolve_path(cls, v: str | Path) -> Path:
        if isinstance(v, str):
            return Path(v)
        return v

    @field_validator("data_root", "raw_dir", "bronze_dir")
    @classmethod
    def ensure_absolute(cls, v: Path) -> Path:
        return v.resolve()
