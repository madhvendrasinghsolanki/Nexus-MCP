"""Structured, validated query inputs. Clients never send raw PostgREST strings."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nexus_mcp.core.errors import ValidationFailed
from nexus_mcp.core.validation import validate_identifier

# Column names that PostgREST treats as control parameters, not filterable columns.
RESERVED_PARAMS = frozenset(
    {"select", "order", "limit", "offset", "on_conflict", "columns", "and", "or", "not"}
)
MAX_IN_VALUES = 100

Scalar = str | int | float | bool


def check_column(name: str) -> str:
    validate_identifier(name, "column name")
    return name


def quote(value: str) -> str:
    """Quote a value for use inside a PostgREST list/logic expression."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fmt(value: Scalar) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class FilterOp(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    LIKE = "like"
    ILIKE = "ilike"
    IN = "in"
    IS = "is"


class Filter(BaseModel):
    """One condition. Multiple filters are combined with AND."""

    model_config = ConfigDict(extra="forbid")

    column: str = Field(description="Column name")
    op: FilterOp = Field(
        FilterOp.EQ,
        description=(
            "eq, neq, gt, gte, lt, lte, like/ilike (use * as wildcard), "
            "in (value is a list), is (value is null, true or false)"
        ),
    )
    value: Scalar | list[Scalar] | None = Field(
        None, description="Comparison value; a list for `in`; null/true/false for `is`"
    )

    @field_validator("column")
    @classmethod
    def _column(cls, v: str) -> str:
        try:
            check_column(v)
        except ValidationFailed as exc:
            raise ValueError(exc.message) from None
        if v.lower() in RESERVED_PARAMS:
            raise ValueError(f"Column name {v!r} is reserved and cannot be filtered")
        return v

    @model_validator(mode="after")
    def _shape(self) -> Filter:
        v, op = self.value, self.op
        if op is FilterOp.IN:
            if not isinstance(v, list) or not v or len(v) > MAX_IN_VALUES:
                raise ValueError(f"`in` needs a list of 1-{MAX_IN_VALUES} values")
        elif op is FilterOp.IS:
            if v is not None and not isinstance(v, bool):
                raise ValueError("`is` accepts only null, true or false")
        elif isinstance(v, list) or v is None:
            raise ValueError(f"`{op.value}` needs a single non-null value")
        elif op in (FilterOp.LIKE, FilterOp.ILIKE) and not isinstance(v, str):
            raise ValueError(f"`{op.value}` needs a string pattern")
        return self

    def to_param(self) -> tuple[str, str]:
        v = self.value
        if self.op is FilterOp.IN:
            assert isinstance(v, list)
            return self.column, "in.(" + ",".join(quote(fmt(x)) for x in v) + ")"
        if self.op is FilterOp.IS:
            return self.column, "is." + ("null" if v is None else fmt(v))  # type: ignore[arg-type]
        assert v is not None and not isinstance(v, list)
        return self.column, f"{self.op.value}.{fmt(v)}"


class OrderBy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    direction: Literal["asc", "desc"] = "asc"

    @field_validator("column")
    @classmethod
    def _column(cls, v: str) -> str:
        try:
            return check_column(v)
        except ValidationFailed as exc:
            raise ValueError(exc.message) from None


ColumnName = Annotated[str, Field(description="Column name")]


def build_filter_params(filters: list[Filter] | None) -> list[tuple[str, str]]:
    return [f.to_param() for f in filters or []]


def escape_like_term(term: str) -> str:
    """Make free text safe inside a quoted ilike pattern (no accidental wildcards/quotes)."""
    cleaned = "".join(ch for ch in term if ch.isprintable()).replace("*", "")
    for ch in ("\\", "%", "_"):
        cleaned = cleaned.replace(ch, "\\" + ch)
    return cleaned
