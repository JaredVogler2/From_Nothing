"""One immutable policy registry (S09, P01).

A policy bundle is ``config/policies/<policy_id>.json`` plus its ``includes``.
Every value carries an authority label (user_requirement, manual_specified,
fixture_default, demo_default, open_decision, owner_decision). Values are deep-frozen and the
bundle hash covers id, revision, scope, mode, values and authorities. Any
override (for example a duration-statistic toggle) produces a *new* policy id
and hash; accepted artifacts keep the hash they were built with.
"""
from __future__ import annotations

import types
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .codec import CodecError, content_hash, parse_utc, strict_loads

AUTHORITIES = frozenset({"user_requirement", "manual_specified", "fixture_default", "demo_default", "open_decision", "owner_decision"})

REQUIRED_KEYS = (
    "scope.all_wip",
    "commitment.enabled",
    "commitment.horizon_calendar_days",
    "forecast.hysteresis_days",
    "forecast.bad_news_days",
    "forecast.confirmation_runs",
    "forecast.tolerance_days",
    "activation.fraction_numerator",
    "activation.fraction_denominator",
    "activation.minimum_one_exception",
    "shift.timezone",
    "shift.start_local",
    "shift.wall_minutes",
    "shift.regular_minutes",
    "shift.maximum_ot_minutes",
    "shift.no_start_buffer_minutes",
    "shift.protected_windows",
    "shift.ot_window",
    "travel.same_aircraft_minutes",
    "travel.changed_aircraft_minutes",
    "qa.total_ip_minutes",
    "qa.final_minutes",
    "duration.default_statistic",
    "duration.minimum_fixture_sample",
    "gate.weight",
    "gate.cap_days",
    "gate.proxy_grace_days",
    "support.virtual_people_per_team_shift",
    "support.virtual_teams",
    "solver.enabled",
    "llm.provider",
    "score.attainment_weight",
    "score.adherence_weight",
    "score.oos_rate",
    "disruption.causes",
    "disruption.nominally_excusable",
)


class PolicyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return types.MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, (dict, types.MappingProxyType)):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class Policy:
    id: str
    version: int
    scope: str
    mode: str
    values: Mapping[str, Any]
    authority: Mapping[str, str]
    refs: Mapping[str, str]
    effective_from: datetime
    effective_to: datetime | None
    open_decisions: tuple[Mapping[str, Any], ...]
    parent_id: str | None
    hash: str

    # --- typed accessors -------------------------------------------------
    def get(self, key: str) -> Any:
        if key not in self.values:
            raise PolicyError("POLICY_KEY_MISSING", f"policy {self.id} has no value for {key!r}")
        return self.values[key]

    def int(self, key: str) -> int:
        value = self.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise PolicyError("POLICY_TYPE", f"{key} must be an integer")
        return value

    def str(self, key: str) -> str:
        value = self.get(key)
        if not isinstance(value, str):
            raise PolicyError("POLICY_TYPE", f"{key} must be a string")
        return value

    def bool(self, key: str) -> bool:
        value = self.get(key)
        if not isinstance(value, bool):
            raise PolicyError("POLICY_TYPE", f"{key} must be a boolean")
        return value

    def seq(self, key: str) -> tuple:
        value = self.get(key)
        if not isinstance(value, tuple):
            raise PolicyError("POLICY_TYPE", f"{key} must be a list")
        return value

    def map(self, key: str) -> Mapping[str, Any]:
        value = self.get(key)
        if not isinstance(value, types.MappingProxyType):
            raise PolicyError("POLICY_TYPE", f"{key} must be an object")
        return value

    def optional(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def authority_of(self, key: str) -> str:
        return self.authority.get(key, "unknown")

    def describe(self) -> dict[str, Any]:
        return {
            "policy_id": self.id,
            "version": self.version,
            "scope": self.scope,
            "mode": self.mode,
            "hash": self.hash,
            "parent_id": self.parent_id,
            "values": {k: {"value": _thaw(v), "authority": self.authority.get(k), "ref": self.refs.get(k)} for k, v in sorted(self.values.items())},
            "open_decisions": [_thaw(d) for d in self.open_decisions],
        }

    def with_overrides(self, overrides: Mapping[str, Any], *, new_id: str, authority: str = "fixture_default", reason: str = "") -> "Policy":
        """Return a new, separately hashed policy (S08 TOGGLE)."""
        if not overrides:
            raise PolicyError("EMPTY_OVERRIDE", "an override policy must change at least one value")
        if authority not in AUTHORITIES:
            raise PolicyError("BAD_AUTHORITY", f"unknown authority {authority!r}")
        values = dict(self.values)
        auth = dict(self.authority)
        refs = dict(self.refs)
        for key, value in overrides.items():
            if key not in values:
                raise PolicyError("UNKNOWN_POLICY_KEY", f"cannot override unknown key {key!r}")
            if auth.get(key) == "user_requirement" and key not in _TOGGLEABLE_USER_KEYS:
                raise PolicyError("USER_CONSTANT", f"{key} is a user requirement and cannot be overridden")
            values[key] = _freeze(value)
            auth[key] = authority
            refs[key] = f"override of {self.id}: {reason}".strip()
        return _build(
            policy_id=new_id,
            version=self.version,
            scope=self.scope,
            mode=self.mode,
            values=values,
            authority=auth,
            refs=refs,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
            open_decisions=self.open_decisions,
            parent_id=self.id,
        )


# Statistic selection is explicitly a future policy toggle (S01/S08); the user
# asked for the ability to switch it, so it is the one user value that may be
# overridden -- always into a new policy id/hash, never in place.
_TOGGLEABLE_USER_KEYS = frozenset({"duration.default_statistic"})


def _build(*, policy_id: str, version: int, scope: str, mode: str, values: dict[str, Any], authority: dict[str, str],
           refs: dict[str, str], effective_from: datetime, effective_to: datetime | None,
           open_decisions: tuple[Mapping[str, Any], ...], parent_id: str | None) -> Policy:
    frozen_values = types.MappingProxyType({k: _freeze(_thaw(v)) for k, v in sorted(values.items())})
    frozen_auth = types.MappingProxyType(dict(sorted(authority.items())))
    frozen_refs = types.MappingProxyType(dict(sorted(refs.items())))
    digest = content_hash({
        "id": policy_id,
        "version": version,
        "scope": scope,
        "mode": mode,
        "values": {k: _thaw(v) for k, v in frozen_values.items()},
        "authority": dict(frozen_auth),
        "effective_from": effective_from,
        "effective_to": effective_to,
        "parent_id": parent_id,
    })
    return Policy(
        id=policy_id,
        version=version,
        scope=scope,
        mode=mode,
        values=frozen_values,
        authority=frozen_auth,
        refs=frozen_refs,
        effective_from=effective_from,
        effective_to=effective_to,
        open_decisions=tuple(_freeze(d) for d in open_decisions),
        parent_id=parent_id,
        hash=digest,
    )


def _read(path: Path) -> dict[str, Any]:
    try:
        data = strict_loads(path.read_bytes())
    except FileNotFoundError as exc:
        raise PolicyError("POLICY_FILE_MISSING", f"policy file {path} not found") from exc
    except CodecError as exc:
        raise PolicyError("POLICY_FILE_INVALID", f"{path.name}: {exc}") from exc
    if not isinstance(data, dict) or data.get("type") != "PolicyFile" or data.get("schema_version") != 1:
        raise PolicyError("POLICY_FILE_INVALID", f"{path.name} is not a schema_version 1 PolicyFile")
    return data


def load_policy(policy_dir: Path, policy_id: str = "rebuild-v1") -> Policy:
    root_path = policy_dir / f"{policy_id}.json"
    root = _read(root_path)
    if root.get("policy_id") != policy_id:
        raise PolicyError("POLICY_ID_MISMATCH", f"{root_path.name} declares {root.get('policy_id')!r}")
    files = [root] + [_read(policy_dir / name) for name in root.get("includes", [])]
    values: dict[str, Any] = {}
    authority: dict[str, str] = {}
    refs: dict[str, str] = {}
    for data in files:
        if data.get("policy_id") != policy_id:
            raise PolicyError("POLICY_ID_MISMATCH", "included policy file belongs to another policy")
        for key, spec in data.get("values", {}).items():
            if key in values:
                raise PolicyError("DUPLICATE_POLICY_KEY", f"{key} defined twice in policy {policy_id}")
            if not isinstance(spec, dict) or set(spec) - {"value", "authority", "ref"} or "value" not in spec:
                raise PolicyError("POLICY_VALUE_INVALID", f"{key} must be {{value, authority, ref}}")
            if spec.get("authority") not in AUTHORITIES:
                raise PolicyError("BAD_AUTHORITY", f"{key} has unknown authority {spec.get('authority')!r}")
            values[key] = spec["value"]
            authority[key] = spec["authority"]
            refs[key] = str(spec.get("ref", ""))
    missing = [k for k in REQUIRED_KEYS if k not in values]
    if missing:
        raise PolicyError("POLICY_INCOMPLETE", f"policy {policy_id} is missing {missing}")
    effective_to = root.get("effective_to_utc")
    return _build(
        policy_id=policy_id,
        version=int(root["revision"]),
        scope=str(root["scope"]),
        mode=str(root["mode"]),
        values=values,
        authority=authority,
        refs=refs,
        effective_from=parse_utc(root["effective_from_utc"]),
        effective_to=parse_utc(effective_to) if effective_to else None,
        open_decisions=tuple(root.get("open_decisions", [])),
        parent_id=None,
    )
