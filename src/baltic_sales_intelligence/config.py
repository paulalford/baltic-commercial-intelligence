"""Strict, typed loading for the versioned project configuration bundle."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, model_validator
from yaml.constructor import ConstructorError


class ConfigurationError(ValueError):
    """Raised when the configuration bundle cannot be loaded or validated."""


class StrictModel(BaseModel):
    """Base model that rejects undeclared configuration keys and coercion surprises."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _yaml_sequence_to_tuple(value: object) -> object:
    """Normalise YAML sequences while preserving strict validation of their members."""

    return tuple(value) if isinstance(value, list) else value


Markets = Annotated[
    tuple[Literal["LT", "LV", "EE"], ...], BeforeValidator(_yaml_sequence_to_tuple)
]
StringTuple = Annotated[tuple[str, ...], BeforeValidator(_yaml_sequence_to_tuple)]
DefectCodeTuple = Annotated[tuple[str, ...], BeforeValidator(_yaml_sequence_to_tuple)]
ForecastHorizons = Annotated[
    tuple[Literal[1, 3, 6], ...], BeforeValidator(_yaml_sequence_to_tuple)
]
ForecastIntervals = Annotated[
    tuple[Literal[0.80, 0.95], ...], BeforeValidator(_yaml_sequence_to_tuple)
]


class ProjectIdentity(StrictModel):
    name: Literal["Baltic Sales Intelligence"]
    business_name: Literal["Baltic Trade Supplies UAB"]
    fictional: Literal[True]
    reporting_currency: Literal["EUR"]
    markets: Markets

    @model_validator(mode="after")
    def validate_markets(self) -> ProjectIdentity:
        if self.markets != ("LT", "LV", "EE"):
            raise ValueError("markets must be ordered exactly as LT, LV, EE")
        return self


class ReportingSettings(StrictModel):
    period_start: date
    period_end: date
    extension_end: date
    default_as_of_date: date

    @model_validator(mode="after")
    def validate_dates(self) -> ReportingSettings:
        expected = (
            date(2022, 1, 1),
            date(2026, 9, 30),
            date(2026, 12, 31),
            date(2026, 9, 30),
        )
        actual = (
            self.period_start,
            self.period_end,
            self.extension_end,
            self.default_as_of_date,
        )
        if actual != expected:
            raise ValueError("reporting dates must match the approved specification window")
        return self


class LoggingSettings(StrictModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    format: Literal["json"]


class ApplicationConfiguration(StrictModel):
    schema_version: Literal[1]
    project: ProjectIdentity
    reporting: ReportingSettings
    logging: LoggingSettings


class PopulationSettings(StrictModel):
    customers: Literal[4000]
    products: Literal[750]
    salespeople: Literal[30]
    suppliers: Literal[80]
    subcategories_per_category: Literal[4]
    orders_target: Literal[120000]
    orders_tolerance_fraction: Literal[0.05]
    order_lines_min: Literal[250000]
    order_lines_max: Literal[400000]
    return_events_min: Literal[8000]
    return_events_max: Literal[15000]


class CatalogueSettings(StrictModel):
    categories: StringTuple
    channels: StringTuple
    alternative_supplier_share: Literal[0.20]
    alternative_supplier_tolerance: Literal[0.03]

    @model_validator(mode="after")
    def validate_reference_counts(self) -> CatalogueSettings:
        if len(self.categories) != 12 or len(set(self.categories)) != 12:
            raise ValueError("catalogue must contain 12 unique approved categories")
        if len(self.channels) != 5 or len(set(self.channels)) != 5:
            raise ValueError("catalogue must contain five unique approved channels")
        return self


class ComponentIdentifiers(StrictModel):
    geography: Literal[1]
    customers: Literal[2]
    products: Literal[3]
    salespeople: Literal[4]
    assignments: Literal[5]
    customer_history: Literal[6]
    orders: Literal[7]
    returns: Literal[8]
    targets: Literal[9]
    defects: Literal[10]
    stories: Literal[11]


class OutputSettings(StrictModel):
    encoding: Literal["UTF-8"]
    line_ending: Literal["LF"]
    date_format: Literal["YYYY-MM-DD"]
    stable_row_order: Literal[True]


class GenerationConfiguration(StrictModel):
    schema_version: Literal[1]
    master_seed: Literal[20261010]
    population: PopulationSettings
    catalogue: CatalogueSettings
    component_ids: ComponentIdentifiers
    output: OutputSettings


class InjectionSettings(StrictModel):
    enabled: bool
    minimum_fraction: Literal[0.001]
    maximum_fraction: Literal[0.015]


class SilverSettings(StrictModel):
    fail_on_high_severity: Literal[True]
    require_count_conservation: Literal[True]
    publish_atomically: Literal[True]


class DataQualityConfiguration(StrictModel):
    schema_version: Literal[1]
    injection: InjectionSettings
    defect_codes: DefectCodeTuple
    silver: SilverSettings

    @model_validator(mode="after")
    def validate_defect_codes(self) -> DataQualityConfiguration:
        expected = tuple(f"DQ{number:02d}" for number in range(1, 15))
        if self.defect_codes != expected:
            raise ValueError("defect_codes must contain DQ01 through DQ14 in order")
        return self


class CustomerAnalyticsSettings(StrictModel):
    active_buyer_lookback_months: Literal[12]
    lapse_months: Literal[12]
    reactivation_gap_months: Literal[12]
    rfm_lookback_months: Literal[12]


class ProductAnalyticsSettings(StrictModel):
    abc_basis: Literal["gross_profit"]
    abc_a_cumulative_share: Literal[0.80]
    abc_b_cumulative_share: Literal[0.95]
    xyz_window_months: Literal[12]
    xyz_minimum_history_months: Literal[6]
    xyz_x_max_cv: Literal[0.50]
    xyz_y_max_cv: Literal[1.00]


class ForecastingSettings(StrictModel):
    frequency: Literal["monthly"]
    horizons: ForecastHorizons
    minimum_training_months: Literal[24]
    interval_levels: ForecastIntervals
    evaluation: Literal["expanding_window"]

    @model_validator(mode="after")
    def validate_sequences(self) -> ForecastingSettings:
        if self.horizons != (1, 3, 6):
            raise ValueError("forecast horizons must be ordered as 1, 3, 6")
        if self.interval_levels != (0.80, 0.95):
            raise ValueError("forecast interval levels must be ordered as 0.80, 0.95")
        return self


class OpportunityWeights(StrictModel):
    impact: Literal[0.40]
    confidence: Literal[0.30]
    actionability: Literal[0.20]
    urgency: Literal[0.10]

    @model_validator(mode="after")
    def validate_total(self) -> OpportunityWeights:
        if sum(self.model_dump().values()) != 1.0:
            raise ValueError("opportunity score weights must sum to 1.0")
        return self


class OpportunitySettings(StrictModel):
    score_weights: OpportunityWeights


class AnalyticsConfiguration(StrictModel):
    schema_version: Literal[1]
    customer: CustomerAnalyticsSettings
    product: ProductAnalyticsSettings
    forecasting: ForecastingSettings
    opportunities: OpportunitySettings


class ProjectConfiguration(StrictModel):
    application: ApplicationConfiguration
    generation: GenerationConfiguration
    data_quality: DataQualityConfiguration
    analytics: AnalyticsConfiguration

    def deterministic_hash(self) -> str:
        """Return SHA-256 of the validated, canonical JSON representation."""

        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _read_yaml(path: Path) -> object:
    if not path.is_file():
        raise ConfigurationError(f"required configuration file is missing: {path}")
    try:
        with path.open(encoding="utf-8") as handle:
            content = yaml.load(handle, Loader=_UniqueKeyLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"cannot read valid YAML from {path}: {exc}") from exc
    if not isinstance(content, dict):
        raise ConfigurationError(f"configuration root must be a mapping: {path}")
    return content


def load_configuration(config_directory: str | Path) -> ProjectConfiguration:
    """Load and validate the four-file configuration bundle.

    Validation proves only that these foundation settings match the approved configuration
    schema. It does not prove generated data or any later data-contract invariant.
    """

    directory = Path(config_directory).expanduser()
    if not directory.is_dir():
        raise ConfigurationError(f"configuration directory does not exist: {directory}")

    raw = {
        "application": _read_yaml(directory / "application.yaml"),
        "generation": _read_yaml(directory / "generation.yaml"),
        "data_quality": _read_yaml(directory / "data_quality.yaml"),
        "analytics": _read_yaml(directory / "analytics.yaml"),
    }
    try:
        # YAML sequences are normalised by explicit pre-validators; all scalar and member
        # validation remains strict and no business fallback values are applied.
        return ProjectConfiguration.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(f"configuration validation failed:\n{exc}") from exc
