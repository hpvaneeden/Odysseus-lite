"""Slice 2: grounded self-model.

Covers, in order:
- the three mutually-exclusive categories (verified / hypothesis / aspiration);
- verified claims carry provenance (evidence, source_type, verification_status);
- tool registration alone does not imply successful tool execution
  (no verified execution claim; an explicit unverified hypothesis);
- hypotheses are not represented as verified facts;
- aspirations are not represented as verified facts;
- persistence + reload round trip;
- unchanged evidence does not rewrite the store;
- master switch OFF: no create / update / delete, existing data preserved;
- master re-ON: existing data remains, updated conservatively;
- memory.json is never touched by self-model writes;
- missing / incomplete evidence fails safely without fabricating claims.

Modules are loaded like the settings tests: monkeypatching
``settings.SETTINGS_FILE`` (to drive the master gate) and
``self_model.SELF_MODEL_FILE`` (to isolate the store), then invalidating
caches.
"""
import json
import os

from src import settings
import src.self_model as sm


def _write_settings(settings_file, master_on: bool) -> None:
    settings_file.write_text(
        json.dumps({"self_definition_enabled": master_on}), encoding="utf-8"
    )
    settings._invalidate_caches()


def _setup(tmp_path, monkeypatch, master_on: bool = False):
    settings_file = tmp_path / "settings.json"
    _write_settings(settings_file, master_on)
    model_file = tmp_path / "self_model.json"
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(sm, "SELF_MODEL_FILE", str(model_file))
    return settings_file, model_file


def _refresh(model_on: bool = True):
    return sm.refresh()


def _categories(model):
    return model["categories"]


def test_three_categories_present(tmp_path, monkeypatch):
    """A built model always exposes exactly the three categories as lists."""
    _setup(tmp_path, monkeypatch, master_on=True)
    model = sm.refresh()
    assert model is not None
    cats = _categories(model)
    assert set(cats.keys()) == {
        sm.CAT_VERIFIED,
        sm.CAT_HYPOTHESIS,
        sm.CAT_ASPIRATION,
    }
    for cat in sm.CATEGORIES:
        assert isinstance(cats[cat], list)


def test_verified_claims_have_full_provenance(tmp_path, monkeypatch):
    """Every verified claim carries non-empty evidence, source_type, and a
    'verified' status, plus a confidence and timestamp."""
    _setup(tmp_path, monkeypatch, master_on=True)
    model = sm.refresh()
    for claim in _categories(model)[sm.CAT_VERIFIED]:
        assert isinstance(claim["claim"], str) and claim["claim"].strip()
        assert isinstance(claim["evidence"], str) and claim["evidence"].strip()
        assert isinstance(claim["source_type"], str) and claim["source_type"].strip()
        assert claim["verification_status"] == sm.STATUS_VERIFIED
        assert 0.0 <= claim["confidence"] <= 1.0
        assert isinstance(claim["timestamp"], int)
        assert isinstance(claim["id"], str) and claim["id"]
        assert claim["area"] in {
            sm.AREA_ROLE, sm.AREA_CAPABILITY, sm.AREA_TOOL,
            sm.AREA_LIMITATION, sm.AREA_PRINCIPLE, sm.AREA_SECURITY,
        }


def test_tool_registration_does_not_imply_successful_execution(
    tmp_path, monkeypatch
):
    """Registered tools are listed as *registration* facts only; no claim
    asserts successful execution as verified, and an explicit unverified
    hypothesis records that registration does not establish execution."""
    _setup(tmp_path, monkeypatch, master_on=True)
    model = sm.refresh()
    verified = _categories(model)[sm.CAT_VERIFIED]
    hypotheses = _categories(model)[sm.CAT_HYPOTHESIS]

    # No verified claim says a tool actually executes / works.
    for claim in verified:
        text = claim["claim"].lower()
        assert "executes successfully" not in text
        assert "successful execution" not in text
        # Registration-backed tool claims are about registration, not execution.
        if claim["source_type"] == sm.SOURCE_IMPLEMENTATION and claim["area"] == sm.AREA_TOOL:
            assert "registered" in text

    # An explicit hypothesis records that registration does not imply execution.
    assert any(
        "registration alone does not establish successful tool execution" in c["claim"]
        and c["verification_status"] == sm.STATUS_UNVERIFIED
        for c in hypotheses
    )


def test_hypotheses_are_not_verified(tmp_path, monkeypatch):
    """Hypotheses are recorded with 'unverified' status, never 'verified'."""
    _setup(tmp_path, monkeypatch, master_on=True)
    model = sm.refresh()
    for claim in _categories(model)[sm.CAT_HYPOTHESIS]:
        assert claim["verification_status"] == sm.STATUS_UNVERIFIED


def test_aspirations_are_not_verified(tmp_path, monkeypatch):
    """Aspirations are recorded with 'aspirational' status, never 'verified'."""
    _setup(tmp_path, monkeypatch, master_on=True)
    model = sm.refresh()
    for claim in _categories(model)[sm.CAT_ASPIRATION]:
        assert claim["verification_status"] == sm.STATUS_ASPIRATIONAL


def test_persistence_and_reload_round_trip(tmp_path, monkeypatch):
    """refresh() persists the model and load_model() reads it back."""
    _, model_file = _setup(tmp_path, monkeypatch, master_on=True)
    assert not os.path.exists(model_file)

    model = sm.refresh()
    assert model is not None
    assert os.path.exists(model_file)

    reloaded = sm.load_model()
    assert reloaded is not None
    # Content matches (ignoring volatile ids/timestamps written at build time).
    assert sm._strip_volatile(reloaded) == sm._strip_volatile(model)


def test_unchanged_evidence_does_not_rewrite_store(tmp_path, monkeypatch):
    """When evidence is unchanged, a second refresh() does not rewrite the file."""
    _, model_file = _setup(tmp_path, monkeypatch, master_on=True)

    sm.refresh()
    with open(model_file, "rb") as f:
        first_bytes = f.read()
    mtime_first = os.path.getmtime(model_file)

    # Second refresh with identical evidence -> file must not be rewritten.
    sm.refresh()
    with open(model_file, "rb") as f:
        second_bytes = f.read()
    mtime_second = os.path.getmtime(model_file)

    assert second_bytes == first_bytes
    assert mtime_second == mtime_first


def test_master_off_prevents_create_update_delete(tmp_path, monkeypatch):
    """With the master OFF, refresh() returns None and never creates or
    modifies the store; an existing store is left byte-identical."""
    _, model_file = _setup(tmp_path, monkeypatch, master_on=False)

    # Nothing exists yet: must not be created.
    assert sm.refresh() is None
    assert not os.path.exists(model_file)

    # Seed a store while enabled, then disable the master.
    _write_settings(tmp_path / "settings.json", master_on=True)
    sm.refresh()
    with open(model_file, "rb") as f:
        seeded = f.read()

    _write_settings(tmp_path / "settings.json", master_on=False)
    assert sm.refresh() is None
    with open(model_file, "rb") as f:
        after_disable = f.read()
    assert after_disable == seeded  # untouched


def test_data_survives_disable_reenable_cycle(tmp_path, monkeypatch):
    """Data written while enabled persists across master disable / re-enable;
    toggles never delete self-model data."""
    _, model_file = _setup(tmp_path, monkeypatch, master_on=True)

    model_enabled = sm.refresh()
    assert model_enabled is not None
    verified_count = len(_categories(model_enabled)[sm.CAT_VERIFIED])
    assert verified_count > 0

    # Disable the master: data must remain on disk.
    _write_settings(tmp_path / "settings.json", master_on=False)
    assert sm.refresh() is None
    reloaded_while_off = sm.load_model()
    assert reloaded_while_off is not None
    assert len(_categories(reloaded_while_off)[sm.CAT_VERIFIED]) == verified_count

    # Re-enable the master: existing data remains and is updated conservatively.
    _write_settings(tmp_path / "settings.json", master_on=True)
    model_again = sm.refresh()
    assert model_again is not None
    assert len(_categories(model_again)[sm.CAT_VERIFIED]) == verified_count


def test_memory_json_never_touched(tmp_path, monkeypatch):
    """The self-model writes only to its own store (data/self_model.json) and
    never to memory.json, so memory / reflection / experience are unaffected."""
    from src.constants import MEMORY_FILE

    # self_model uses a dedicated file distinct from the general memory store.
    assert sm.SELF_MODEL_FILE != MEMORY_FILE
    assert os.path.basename(sm.SELF_MODEL_FILE) == "self_model.json"

    _, model_file = _setup(tmp_path, monkeypatch, master_on=True)

    # Capture memory.json before refresh; self-model must not modify it.
    memory_before = None
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "rb") as f:
            memory_before = f.read()

    sm.refresh()
    assert os.path.exists(model_file)

    memory_after = None
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "rb") as f:
            memory_after = f.read()
    assert memory_after == memory_before


def test_missing_incomplete_evidence_fails_safely(tmp_path, monkeypatch):
    """If an evidence source cannot be introspected, the model still builds
    without fabricating execution claims, and the gap is recorded as a
    hypothesis rather than a verified fact."""
    _setup(tmp_path, monkeypatch, master_on=True)

    # Make the tool-schema source unavailable.
    monkeypatch.setattr(
        sm, "_read_tool_schema_names", lambda: ([], False)
    )

    model = sm.refresh()
    assert model is not None
    cats = _categories(model)
    assert isinstance(cats[sm.CAT_VERIFIED], list)
    assert isinstance(cats[sm.CAT_HYPOTHESIS], list)
    assert isinstance(cats[sm.CAT_ASPIRATION], list)

    # No verified claim about tools since the registry is unavailable.
    for claim in cats[sm.CAT_VERIFIED]:
        assert claim["area"] != sm.AREA_TOOL or "registered" not in claim["claim"]

    # The gap itself is recorded as an unverified hypothesis.
    assert any(
        "could not be introspected" in c["claim"]
        and c["verification_status"] == sm.STATUS_UNVERIFIED
        for c in cats[sm.CAT_HYPOTHESIS]
    )
