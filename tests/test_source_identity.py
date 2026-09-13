from basketball_miner.source_identity import IdentityDecision, compare_titles, normalize_title


def test_normalize_title_collapses_unicode_case_and_punctuation():
    observed = 'Basketball—Jump Shot: “Biomechanics”'
    canonical = 'basketball jump shot biomechanics'

    assert normalize_title(observed) == normalize_title(canonical)
    assert compare_titles(observed, canonical).decision is IdentityDecision.EXACT_MATCH


def test_long_title_with_subtitle_is_high_confidence_variant():
    observed = 'Effects of defensive pressure on basketball passing'
    canonical = (
        'Effects of defensive pressure on basketball passing: '
        'accuracy under fatigue'
    )

    assert compare_titles(observed, canonical).decision is IdentityDecision.HIGH_CONFIDENCE_VARIANT


def test_short_title_uses_strict_threshold_and_stays_ambiguous():
    observed = 'Elite Basketball Passing'
    canonical = 'Elite Basketball Pass'

    assert compare_titles(observed, canonical).decision is IdentityDecision.AMBIGUOUS


def test_materially_different_titles_are_mismatch():
    observed = 'Basketball shooting biomechanics'
    canonical = 'Nutrition in soccer players'

    assert compare_titles(observed, canonical).decision is IdentityDecision.MISMATCH


def test_middle_similarity_band_is_ambiguous():
    observed = 'Effects of defensive pressure on basketball passing'
    canonical = 'Effects of defensive pressure during basketball passing practice'

    result = compare_titles(observed, canonical)

    assert result.decision is IdentityDecision.AMBIGUOUS
    assert 0.75 <= result.sequence_ratio < 0.94
