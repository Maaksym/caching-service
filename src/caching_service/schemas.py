# Pydantic models for API input and output validation.
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


class PayloadInput(BaseModel):
    # The POST request must contain two lists of strings.
    model_config = ConfigDict(extra="forbid")

    list_1: list[StrictStr]
    list_2: list[StrictStr]

    # Check that both input lists have the same length.
    @model_validator(mode="after")
    def equal_lengths(self) -> PayloadInput:
        if len(self.list_1) != len(self.list_2):
            raise ValueError("list_1 and list_2 must have the same length")
        return self


class PayloadCreated(BaseModel):
    # POST response: payload ID and whether it was already cached.
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    cached: bool


class PayloadOutput(BaseModel):
    # GET response and stored JSON file format.
    model_config = ConfigDict(extra="forbid")

    output: StrictStr
