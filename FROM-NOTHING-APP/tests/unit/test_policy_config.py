"""P01/P02: typed policy register and allowlisted settings with hosting adapters."""
from __future__ import annotations

import json

import pytest

from focus.config import Secret, Settings, SettingsError
from focus.policy import PolicyError, load_policy


def test_policy_loads_with_user_constants_and_authorities(policy) -> None:
    assert policy.id == "rebuild-v1"
    assert len(policy.hash) == 64
    # S09 user requirements recorded verbatim
    assert policy.get("shift.regular_minutes") == {"1": 430, "2": 430, "3": 340}
    assert policy.int("shift.maximum_ot_minutes") == 60
    assert policy.int("shift.no_start_buffer_minutes") == 30
    assert policy.int("activation.fraction_numerator") == 85
    assert policy.int("activation.fraction_denominator") == 100
    assert policy.bool("activation.minimum_one_exception") is False
    assert policy.int("qa.total_ip_minutes") == 45 and policy.int("qa.final_minutes") == 45
    assert policy.int("commitment.horizon_calendar_days") == 3
    assert policy.int("forecast.hysteresis_days") == 2 and policy.int("forecast.bad_news_days") == 7
    assert policy.int("gate.weight") == 35 and policy.int("gate.cap_days") == 5 and policy.int("gate.proxy_grace_days") == 2
    assert policy.int("support.virtual_people_per_team_shift") == 10
    assert policy.seq("support.virtual_teams") == ("MHE", "PSC", "TST")
    assert policy.str("duration.default_statistic") == "P75"
    assert policy.bool("solver.enabled") is False and policy.str("llm.provider") == "none"
    assert len(policy.seq("disruption.causes")) == 8
    assert len(policy.seq("disruption.nominally_excusable")) == 5
    # authority labels distinguish user constants from fixture choices and open decisions
    assert policy.authority_of("shift.maximum_ot_minutes") == "user_requirement"
    assert policy.authority_of("travel.changed_aircraft_minutes") == "fixture_default"
    assert policy.authority_of("duration.total_mechanics_semantics") == "owner_decision"     # OD-01, decided 2026-10-31
    assert policy.authority_of("cs.date_mode") == "open_decision"
    assert policy.authority_of("score.attainment_weight") == "demo_default"
    assert {d["id"] for d in policy.open_decisions} >= {f"OD-{i:02d}" for i in range(2, 13)}
    assert "OD-01" not in {d["id"] for d in policy.open_decisions}


def test_policy_values_are_immutable(policy) -> None:
    with pytest.raises(TypeError):
        policy.values["shift.maximum_ot_minutes"] = 90  # type: ignore[index]
    regular = policy.get("shift.regular_minutes")
    with pytest.raises(TypeError):
        regular["1"] = 999  # type: ignore[index]


def test_statistic_toggle_creates_new_policy_hash(policy) -> None:
    p90 = policy.with_overrides({"duration.default_statistic": "P90"}, new_id="rebuild-v1-p90", reason="A18 toggle")
    assert p90.id == "rebuild-v1-p90" and p90.parent_id == "rebuild-v1"
    assert p90.hash != policy.hash
    assert p90.str("duration.default_statistic") == "P90"
    assert policy.str("duration.default_statistic") == "P75"  # original untouched


def test_user_constants_cannot_be_overridden(policy) -> None:
    with pytest.raises(PolicyError) as exc:
        policy.with_overrides({"shift.maximum_ot_minutes": 90}, new_id="bad")
    assert exc.value.code == "USER_CONSTANT"
    with pytest.raises(PolicyError) as exc:
        policy.with_overrides({"no.such.key": 1}, new_id="bad")
    assert exc.value.code == "UNKNOWN_POLICY_KEY"


def test_policy_file_errors_are_typed(tmp_path) -> None:
    (tmp_path / "x.json").write_text('{"type":"PolicyFile","schema_version":1,"policy_id":"x","revision":1,"scope":"S","mode":"fixture","effective_from_utc":"2026-01-01T00:00:00Z","values":{}}')
    with pytest.raises(PolicyError) as exc:
        load_policy(tmp_path, "x")
    assert exc.value.code == "POLICY_INCOMPLETE"
    (tmp_path / "y.json").write_text('{"type":"PolicyFile","schema_version":1,"policy_id":"y","policy_id":"z"}')
    with pytest.raises(PolicyError) as exc:
        load_policy(tmp_path, "y")
    assert exc.value.code == "POLICY_FILE_INVALID"


def test_settings_allowlist_ignores_unknown_focus_names(app_root) -> None:
    s = Settings.from_env({"FOCUS_MODE": "fixture", "FOCUS_TYPO_SETTING": "x", "FOCUS_APP_ROOT": str(app_root)})
    assert s.ignored_keys == ("FOCUS_TYPO_SETTING",)
    assert s.mode == "fixture" and s.port == 5055 and s.bind == "127.0.0.1"
    assert "FOCUS_TYPO_SETTING" in s.public_view()["ignored_setting_names"]


def test_secrets_never_render(app_root) -> None:
    s = Settings.from_env({"FOCUS_SQL_USER": "u", "FOCUS_SQL_PASSWORD": "hunter2", "FOCUS_SESSION_SECRET": "s3cret", "FOCUS_APP_ROOT": str(app_root)})
    text = repr(s) + str(s.public_view())
    assert "hunter2" not in text and "s3cret" not in text
    assert isinstance(s.sql_password, Secret) and s.sql_password.reveal() == "hunter2"
    assert s.session_secret_ephemeral is False


def test_live_mode_refuses_demo_identity(app_root) -> None:
    with pytest.raises(SettingsError) as exc:
        Settings.from_env({"FOCUS_MODE": "live", "FOCUS_IDENTITY_PROVIDER": "fixture", "FOCUS_APP_ROOT": str(app_root)})
    assert exc.value.code == "FIXTURE_IDENTITY_IN_LIVE"
    live = Settings.from_env({"FOCUS_MODE": "live", "FOCUS_APP_ROOT": str(app_root)})
    assert live.identity_provider == "none" and live.inprocess_worker is False


def test_tanzu_adapter_reads_port_and_vcap_services(app_root) -> None:
    vcap = {"user-provided": [
        {"name": "focus-sql", "credentials": {"host": "sqlhost", "database": "SHARED", "user": "reader", "password": "pw"}},
        {"name": "focus-secrets", "credentials": {"session_secret": "abc", "sign_key": "k", "sign_key_id": "KEY-1"}},
    ]}
    s = Settings.from_env({
        "VCAP_APPLICATION": json.dumps({"application_name": "focus"}),
        "VCAP_SERVICES": json.dumps(vcap),
        "PORT": "8080",
        "FOCUS_MODE": "live",
        "FOCUS_APP_ROOT": str(app_root),
    })
    assert s.platform == "tanzu"
    assert s.port == 8080 and s.bind == "0.0.0.0"
    assert s.sql_host == "sqlhost" and s.sql_user == "reader" and s.sql_configured
    assert s.session_secret_ephemeral is False
    assert s.sign_key_ref == "vcap:focus-secrets/sign_key" and s.sign_key_id == "KEY-1"
    assert s.identity_provider == "trusted_header" and s.trusted_user_header == "X-Forwarded-User"
    assert s.secure_cookies is True and s.proxy_fix == 1


def test_posit_connect_detection(app_root) -> None:
    s = Settings.from_env({"RSTUDIO_PRODUCT": "CONNECT", "FOCUS_MODE": "live", "FOCUS_APP_ROOT": str(app_root)})
    assert s.platform == "posit_connect"
    assert s.identity_provider == "posit_connect"
    assert s.bind == "127.0.0.1"


def test_grants_json_is_strict(app_root) -> None:
    with pytest.raises(SettingsError):
        Settings.from_env({"FOCUS_GRANTS_JSON": '{"subjects": {"a": ["root"]}}', "FOCUS_APP_ROOT": str(app_root)})
    s = Settings.from_env({"FOCUS_GRANTS_JSON": '{"subjects": {"posit:alice": ["reader","manager"]}, "default": ["reader"]}', "FOCUS_APP_ROOT": str(app_root)})
    assert s.grants["subjects"]["posit:alice"] == ("manager", "reader")
