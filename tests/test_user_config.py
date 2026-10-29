"""Tests for user_config.build_config(): the translation from a user's
friendly settings (db.UserSettings) into a concrete advisor.AdvisorConfig.
See user_config.py's module docstring for what each knob is supposed to do.
"""

from training import advisor, db, user_config


def _settings(volume_multiplier=1.0, conditioning_focus=1.0, disabled_styles=None):
    return db.UserSettings(
        user_id=1, volume_multiplier=volume_multiplier, conditioning_focus=conditioning_focus,
        disabled_styles=disabled_styles or [],
    )


def test_default_settings_produce_the_default_targets():
    config = user_config.build_config(_settings())
    assert config.target_rate == advisor.TARGET_RATE
    assert config.pattern_weight == advisor.PATTERN_WEIGHT
    assert config.disabled_styles == frozenset()


def test_volume_multiplier_scales_every_style_uniformly():
    config = user_config.build_config(_settings(volume_multiplier=0.5))
    for style, rate in advisor.TARGET_RATE.items():
        assert config.target_rate[style] == rate * 0.5


def test_conditioning_focus_only_touches_engine():
    config = user_config.build_config(_settings(conditioning_focus=2.0))
    assert config.pattern_weight["engine"] == advisor.PATTERN_WEIGHT["engine"] * 2.0
    for pattern, weight in advisor.PATTERN_WEIGHT.items():
        if pattern != "engine":
            assert config.pattern_weight[pattern] == weight


def test_disabled_styles_pass_through():
    config = user_config.build_config(_settings(disabled_styles=["heavy"]))
    assert config.disabled_styles == frozenset({"heavy"})


def test_build_config_result_is_usable_end_to_end():
    """Not just shape-checking - the resulting config should actually work
    when handed to admissible_cells(), same as any other AdvisorConfig.
    """
    dictionary = advisor.load_dictionary()
    config = user_config.build_config(_settings(disabled_styles=["heavy"]))
    cells = advisor.admissible_cells(dictionary, config)
    assert all(style != "heavy" for _pattern, style in cells)
