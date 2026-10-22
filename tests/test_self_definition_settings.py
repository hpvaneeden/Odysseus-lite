"""Slice 1: self-definition settings foundation.

Covers, in order:
- all three switches default OFF (capability inert until explicitly opted in);
- the master switch `self_definition_enabled` gates the two subordinate
  switches regardless of their persisted values;
- with the master on, the subordinate switches operate independently;
- data survives disable/re-enable cycles (nothing is ever deleted by a toggle);
- a missing/unreadable settings file degrades to "all disabled";
- `save_settings` immediately invalidates the settings cache.

Modules are loaded like the other settings tests: monkeypatching
``settings.SETTINGS_FILE`` to a tmp file and invalidating caches.
"""
import json

from src import settings


def _setup(tmp_path, monkeypatch, initial: dict | None = None):
    settings_file = tmp_path / "settings.json"
    if initial is not None:
        settings_file.write_text(json.dumps(initial), encoding="utf-8")
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_file))
    settings._invalidate_caches()
    return settings_file


def test_defaults_all_off(tmp_path, monkeypatch):
    """Fresh install: every self-definition switch is OFF and the gates
    report disabled; nothing is opt-in by default."""
    _setup(tmp_path, monkeypatch, {})

    loaded = settings.load_settings()
    assert loaded["self_definition_enabled"] is False
    assert loaded["autonomous_naming_enabled"] is False
    assert loaded["personality_development_enabled"] is False

    assert settings.self_definition_enabled() is False
    assert settings.autonomous_naming_enabled() is False
    assert settings.personality_development_enabled() is False


def test_missing_settings_file_degrades_to_disabled(tmp_path, monkeypatch):
    """No settings file at all (fresh data dir) must not crash and reads as
    all switches OFF."""
    settings_file = tmp_path / "settings.json"
    # deliberately never created
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_file))
    settings._invalidate_caches()

    assert settings.self_definition_enabled() is False
    assert settings.autonomous_naming_enabled() is False
    assert settings.personality_development_enabled() is False


def test_master_switch_gates_subordinates(tmp_path, monkeypatch):
    """With the master OFF, persisted-ON subordinate switches are treated as
    OFF. With the master ON, subordinates take effect per their own value."""
    _setup(
        tmp_path,
        monkeypatch,
        {
            "self_definition_enabled": False,
            "autonomous_naming_enabled": True,
            "personality_development_enabled": True,
        },
    )

    assert settings.self_definition_enabled() is False
    assert settings.autonomous_naming_enabled() is False  # gated by master
    assert settings.personality_development_enabled() is False  # gated by master

    saved = dict(settings.DEFAULT_SETTINGS)
    saved.update(
        self_definition_enabled=True,
        autonomous_naming_enabled=True,
        personality_development_enabled=False,
    )
    settings.save_settings(saved)

    assert settings.self_definition_enabled() is True
    assert settings.autonomous_naming_enabled() is True  # master ON + own ON
    assert settings.personality_development_enabled() is False  # master ON + own OFF


def test_subordinate_independence(tmp_path, monkeypatch):
    """Master ON: each subordinate switch reflects only its own value."""
    _setup(
        tmp_path,
        monkeypatch,
        {"self_definition_enabled": True},
    )

    saved = dict(settings.DEFAULT_SETTINGS)
    saved.update(
        self_definition_enabled=True,
        autonomous_naming_enabled=True,
        personality_development_enabled=False,
    )
    settings.save_settings(saved)

    assert settings.autonomous_naming_enabled() is True
    assert settings.personality_development_enabled() is False

    # Flip only autonomous_naming; personality stays OFF.
    saved["autonomous_naming_enabled"] = False
    settings.save_settings(saved)
    assert settings.autonomous_naming_enabled() is False
    assert settings.personality_development_enabled() is False


def test_data_survives_disable_reenable(tmp_path, monkeypatch):
    """Data written while enabled persists across master disable/re-enable —
    toggles never delete self-definition data."""
    _setup(
        tmp_path,
        monkeypatch,
        {
            "self_definition_enabled": True,
            "autonomous_naming_enabled": True,
            "personality_development_enabled": True,
        },
    )
    assert settings.autonomous_naming_enabled() is True
    assert settings.personality_development_enabled() is True

    # Disable the master.
    saved = dict(settings.DEFAULT_SETTINGS)
    saved.update(
        self_definition_enabled=False,
        autonomous_naming_enabled=True,
        personality_development_enabled=True,
    )
    settings.save_settings(saved)
    assert settings.self_definition_enabled() is False
    assert settings.autonomous_naming_enabled() is False  # gated
    assert settings.personality_development_enabled() is False  # gated

    # Re-enable the master: subordinate values must still be there.
    saved["self_definition_enabled"] = True
    settings.save_settings(saved)
    assert settings.self_definition_enabled() is True
    assert settings.autonomous_naming_enabled() is True
    assert settings.personality_development_enabled() is True


def test_save_settings_immediately_visible(tmp_path, monkeypatch):
    """save_settings invalidates the cache, so a subsequent load_settings
    reflects the persisted value without waiting for the TTL."""
    _setup(tmp_path, monkeypatch, {})

    assert settings.get_setting("self_definition_enabled") is False

    saved = dict(settings.DEFAULT_SETTINGS)
    saved["self_definition_enabled"] = True
    settings.save_settings(saved)

    assert settings.load_settings()["self_definition_enabled"] is True
    assert settings.self_definition_enabled() is True