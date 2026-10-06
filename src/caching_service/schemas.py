from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


class PayloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    list_1: list[StrictStr]
    list_2: list[StrictStr]

    @model_validator(mode="after")
    def equal_lengths(self) -> PayloadInput:
        if len(self.list_1) != len(self.list_2):
            raise ValueError("list_1 and list_2 must have the same length")
        return self


class PayloadCreated(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    cached: bool


class PayloadOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output: StrictStr
