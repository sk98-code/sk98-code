import pytest
from pydantic import ValidationError

from bimigrate.models.core import (
    ConfidenceTier,
    ConversionRule,
    FeatureMapping,
    tier_for_confidence,
)


def test_tier_policy_boundaries():
    assert tier_for_confidence(0.90) == ConfidenceTier.AUTO
    assert tier_for_confidence(0.899) == ConfidenceTier.ASSISTED
    assert tier_for_confidence(0.60) == ConfidenceTier.ASSISTED
    assert tier_for_confidence(0.599) == ConfidenceTier.MANUAL
    assert tier_for_confidence(0.0) == ConfidenceTier.MANUAL


def test_feature_mapping_requires_fallback_when_no_equivalent():
    with pytest.raises(ValidationError):
        FeatureMapping(
            feature_name="Macros",
            source_tool="qlikview",
            source_layer="automation",
            powerbi_equivalent=None,
            migration_complexity="very_complex",
            automation_possibility="manual",
            fallback_strategy="   ",
            confidence_score=0.1,
        )


def test_feature_mapping_tier_property():
    m = FeatureMapping(
        feature_name="Bar chart",
        source_tool="tableau",
        source_layer="visual",
        powerbi_equivalent="barChart",
        migration_complexity="simple",
        automation_possibility="full",
        fallback_strategy="",
        confidence_score=0.95,
    )
    assert m.tier == ConfidenceTier.AUTO


def test_conversion_rule_tier_consistency_enforced():
    with pytest.raises(ValidationError):
        ConversionRule(
            rule_id="x",
            source_tool="tableau",
            source_pattern="ABS",
            source_example="ABS(x)",
            dax_template="ABS({0})",
            dax_example="ABS(1)",
            confidence=0.95,
            tier="MANUAL",
        )
