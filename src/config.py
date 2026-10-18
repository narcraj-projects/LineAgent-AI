"""Non-secret scaffold settings; integration configuration belongs to adapters."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    app_name: str = "LineMedic AI"
    data_mode: Literal["synthetic"] = "synthetic"
    milestone: Literal["scaffold"] = "scaffold"
