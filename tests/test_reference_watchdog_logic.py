#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pytest",
#   "pytest-cov",
#   "ruff",
#   "mypy",
#   "PyYAML>=6",
#   "Jinja2>=3",
# ]
# ///
# This is AI generated code
"""Tests for reference_watchdog logic module."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

sys.path.insert(0, str(REPO_ROOT))

from conftest import CodeQualityBase  # noqa: E402

from custom_components.blueprint_toolkit.reference_watchdog.logic import (  # noqa: E402, E501
    SEED_DOMAINS,
    Config,
    Finding,
    Owner,
    OwnerResult,
    Ref,
    RegistryEntry,
    SourceInput,
    SourceOrphan,
    TruthSet,
    _build_notification_body,
    _build_owner_result,
    _build_source_orphans_notification,
    _collect_findings,
    _discover_yaml_sources,
    _enumerate_json_sources,
    _enumerate_storage_helpers,
    _evaluate_sources,
    _extract_includes_from_text,
    _extract_refs_from_template,
    _find_source_orphans,
    _is_entity_excluded,
    _is_integration_excluded,
    _is_path_excluded,
    _looks_like_entity_id,
    _orphan_url,
    _owner_display_name,
    _OwnerStats,
    _read_json_file,
    _read_yaml_file,
    _sanitize_notification_id,
    _scan_automations,
    _scan_config_entries,
    _scan_customize,
    _scan_generic_yaml,
    _scan_lovelace,
    _scan_scripts,
    _scan_template,
    _source_orphans_notification_id,
    _walk_tree,
    run_evaluation,
)

# -- Helpers ---------------------------------------------


def _config(**overrides: object) -> Config:
    defaults: dict[str, object] = {
        "exclude_paths": [],
        "exclude_integrations": [],
        "exclude_entities": [],
        "exclude_entity_id_regex": "",
        "check_disabled_entities": True,
        "notification_prefix": "reference_watchdog_test__",
    }
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def _reg_entry(
    entity_id: str,
    platform: str = "shelly",
    unique_id: str = "",
    config_entry_id: str | None = "abc",
    disabled: bool = False,
    name: str | None = None,
    original_name: str | None = None,
) -> RegistryEntry:
    return RegistryEntry(
        entity_id=entity_id,
        platform=platform,
        unique_id=unique_id or f"{entity_id}_uid",
        config_entry_id=config_entry_id,
        disabled=disabled,
        name=name,
        original_name=original_name,
    )


def _ts(
    entity_ids: set[str] | None = None,
    device_ids: set[str] | None = None,
    service_names: set[str] | None = None,
    disabled_entity_ids: set[str] | None = None,
    registry: dict[str, RegistryEntry] | None = None,
    entity_by_unique_id: dict[tuple[str, str], str] | None = None,
    extra_domains: set[str] | None = None,
    config_entries_with_entities: set[str] | None = None,
) -> TruthSet:
    # TruthSet is frozen, so build staging collections
    # and construct once.
    ents: set[str] = set(entity_ids or set())
    disabled: set[str] = set(disabled_entity_ids or set())
    ents.update(disabled)
    doms: set[str] = set(SEED_DOMAINS)
    for eid in ents:
        doms.add(eid.split(".", 1)[0])
    if extra_domains:
        doms.update(extra_domains)
    # Auto-derive config_entries_with_entities from the
    # registry so tests that don't pass it explicitly
    # still get a realistic value.
    derived_ceids: set[str] = set()
    if registry:
        for reg_entry in registry.values():
            if reg_entry.config_entry_id:
                derived_ceids.add(reg_entry.config_entry_id)
    if config_entries_with_entities:
        derived_ceids.update(config_entries_with_entities)
    return TruthSet(
        entity_ids=frozenset(ents),
        disabled_entity_ids=frozenset(disabled),
        device_ids=frozenset(device_ids or set()),
        service_names=frozenset(service_names or set()),
        domains=frozenset(doms),
        registry=dict(registry or {}),
        entity_by_unique_id=dict(entity_by_unique_id or {}),
        config_entries_with_entities=frozenset(derived_ceids),
    )


def _source(
    source_type: str,
    path: str,
    parsed: object,
    **extra: str,
) -> SourceInput:
    return SourceInput(
        source_type=source_type,
        path=path,
        parsed=parsed,
        extra=dict(extra),
    )


# -- Detection primitives -------------------------------


class TestLooksLikeEntityId:
    def test_valid_entity_id(self) -> None:
        assert _looks_like_entity_id(
            "sensor.foo",
            {"sensor", "light"},
        )

    def test_unknown_domain(self) -> None:
        assert not _looks_like_entity_id(
            "widget.foo",
            {"sensor", "light"},
        )

    def test_no_period(self) -> None:
        assert not _looks_like_entity_id("sensorfoo", {"sensor"})

    def test_uppercase_rejected(self) -> None:
        assert not _looks_like_entity_id("Sensor.Foo", {"sensor"})

    def test_numeric_object_id(self) -> None:
        assert _looks_like_entity_id("sensor.foo_01", {"sensor"})

    def test_multi_dot_rejected(self) -> None:
        assert not _looks_like_entity_id("sensor.foo.bar", {"sensor"})


class TestExtractRefsFromTemplate:
    def test_plain_states_call(self) -> None:
        refs = _extract_refs_from_template(
            "{{ states('sensor.foo') }}",
            {"sensor"},
        )
        assert refs == ["sensor.foo"]

    def test_state_attr_call(self) -> None:
        refs = _extract_refs_from_template(
            "{{ state_attr('climate.bar', 'temperature') }}",
            {"climate"},
        )
        assert "climate.bar" in refs

    def test_getattr_chain(self) -> None:
        refs = _extract_refs_from_template(
            "{{ states.sensor.foo.state }}",
            {"sensor"},
        )
        assert "sensor.foo" in refs

    def test_concatenation_not_extracted(self) -> None:
        refs = _extract_refs_from_template(
            "{{ states('sensor.' ~ name) }}",
            {"sensor"},
        )
        assert refs == []

    def test_unknown_domain_ignored(self) -> None:
        refs = _extract_refs_from_template(
            "{{ states('widget.foo') }}",
            {"sensor"},
        )
        assert refs == []

    def test_no_jinja_markers_empty(self) -> None:
        assert (
            _extract_refs_from_template(
                "sensor.foo",
                {"sensor"},
            )
            == []
        )

    def test_invalid_jinja_no_crash(self) -> None:
        assert (
            _extract_refs_from_template(
                "{{ unclosed",
                {"sensor"},
            )
            == []
        )

    def test_block_syntax(self) -> None:
        refs = _extract_refs_from_template(
            "{% if is_state('binary_sensor.door', 'on') %}y{% endif %}",
            {"binary_sensor"},
        )
        assert "binary_sensor.door" in refs


class TestWalkTree:
    def test_structural_entity_id_key(self) -> None:
        tree: dict[str, object] = {
            "entity_id": "light.foo",
        }
        refs = list(_walk_tree(tree, [], {"light"}))
        assert len(refs) == 1
        assert refs[0].value == "light.foo"
        assert refs[0].kind == "entity"
        assert "entity_id" in refs[0].context

    def test_structural_entities_list(self) -> None:
        tree: dict[str, object] = {
            "entities": ["light.a", "light.b"],
        }
        refs = list(_walk_tree(tree, [], {"light"}))
        values = sorted(r.value for r in refs)
        assert values == ["light.a", "light.b"]

    def test_nested_target_block(self) -> None:
        tree: dict[str, object] = {
            "action": "light.turn_on",
            "target": {"entity_id": "light.foo"},
        }
        refs = list(_walk_tree(tree, [], {"light"}))
        values = [r.value for r in refs]
        # Sniff is disabled under `action:` so the service
        # name should never appear.
        assert "light.turn_on" not in values
        # Structural walk emits light.foo from target
        assert "light.foo" in values

    def test_sniff_finds_blueprint_input(self) -> None:
        # A key name that isn't in _ENTITY_KEYS but holds a
        # bare entity-id value -- the sniff should find it.
        tree: dict[str, object] = {
            "controlled_entities": ["light.foo", "light.bar"],
        }
        refs = list(_walk_tree(tree, [], {"light"}))
        values = sorted(r.value for r in refs)
        assert values == ["light.bar", "light.foo"]
        for r in refs:
            assert r.context.startswith("sniff:")

    def test_jinja_in_non_ref_key(self) -> None:
        tree: dict[str, object] = {
            "value_template": "{{ states('sensor.foo') }}",
        }
        refs = list(_walk_tree(tree, [], {"sensor"}))
        values = [r.value for r in refs]
        assert "sensor.foo" in values
        jinja_refs = [r for r in refs if r.context.startswith("jinja:")]
        assert len(jinja_refs) == 1

    def test_sniff_disabled_under_service_key(self) -> None:
        tree: dict[str, object] = {
            "service": "light.turn_on",
            "target": {"entity_id": "light.foo"},
        }
        refs = list(_walk_tree(tree, [], {"light"}))
        values = [r.value for r in refs]
        assert "light.turn_on" not in values
        assert "light.foo" in values

    def test_sniff_disabled_under_ref_key_subtree(self) -> None:
        # Entity at an _ENTITY_KEYS value should emit once
        # (structural), not twice (structural + sniff).
        tree: dict[str, object] = {
            "entity_id": ["light.foo"],
        }
        refs = list(_walk_tree(tree, [], {"light"}))
        assert len(refs) == 1
        assert not refs[0].context.startswith("sniff:")

    def test_device_id_structural(self) -> None:
        tree: dict[str, object] = {
            "device_id": "a" * 32,
        }
        refs = list(_walk_tree(tree, [], {"light"}))
        assert len(refs) == 1
        assert refs[0].kind == "device"
        assert refs[0].value == "a" * 32

    def test_device_id_rejects_non_hex(self) -> None:
        tree: dict[str, object] = {
            "device_id": "uuid:12345678",
        }
        refs = list(_walk_tree(tree, [], {"light"}))
        assert refs == []

    def test_device_id_rejects_wrong_length(self) -> None:
        tree: dict[str, object] = {
            "device_id": "a" * 31,
        }
        refs = list(_walk_tree(tree, [], {"light"}))
        assert refs == []


# -- Exclusion helpers ----------------------------------


class TestExclusionHelpers:
    def test_path_excluded_simple_match(self) -> None:
        assert _is_path_excluded("plants.yaml", ["plants.yaml"])

    def test_path_excluded_glob(self) -> None:
        assert _is_path_excluded(
            ".storage/lovelace.old_dashboard",
            [".storage/lovelace.*"],
        )

    def test_path_not_excluded(self) -> None:
        assert not _is_path_excluded(
            "automations.yaml",
            ["plants.yaml"],
        )

    def test_path_empty_patterns(self) -> None:
        assert not _is_path_excluded("anything.yaml", [])

    def test_integration_excluded(self) -> None:
        assert _is_integration_excluded("shelly", ["shelly", "hue"])

    def test_integration_none_never_excluded(self) -> None:
        assert not _is_integration_excluded(None, ["shelly"])

    def test_integration_not_in_list(self) -> None:
        assert not _is_integration_excluded("zha", ["shelly"])

    def test_entity_excluded_by_list(self) -> None:
        assert _is_entity_excluded(
            "sensor.foo",
            ["sensor.foo"],
            "",
        )

    def test_entity_excluded_by_regex(self) -> None:
        assert _is_entity_excluded(
            "sensor.legacy_temp",
            [],
            r"^sensor\.legacy_",
        )

    def test_entity_not_excluded(self) -> None:
        assert not _is_entity_excluded(
            "sensor.foo",
            ["sensor.bar"],
            r"^other\.",
        )


# -- Per-source adapters --------------------------------


class TestScanAutomations:
    def test_single_automation_with_refs(self) -> None:
        parsed = [
            {
                "id": "1234",
                "alias": "Test Auto",
                "triggers": [
                    {
                        "trigger": "state",
                        "entity_id": "binary_sensor.door",
                    },
                ],
            },
        ]
        source = _source("automations", "automations.yaml", parsed)
        ts = _ts(
            entity_by_unique_id={
                ("automation", "1234"): "automation.test_auto",
            },
            registry={
                "automation.test_auto": _reg_entry(
                    "automation.test_auto",
                    platform="automation",
                    config_entry_id=None,
                ),
            },
            entity_ids={"automation.test_auto"},
        )
        owners = _scan_automations(source, ts)
        assert len(owners) == 1
        owner, tree = owners[0]
        assert owner.integration == "automation"
        assert owner.block_path == "config-block[0]"
        assert owner.friendly_name == "Test Auto"
        assert owner.entity_id == "automation.test_auto"
        assert owner.url_path == "/config/automation/edit/1234"
        assert owner.yaml_only is True

    def test_missing_id_still_produces_owner(self) -> None:
        parsed = [{"alias": "Orphan"}]
        source = _source("automations", "automations.yaml", parsed)
        owners = _scan_automations(source, _ts())
        assert len(owners) == 1
        owner, _ = owners[0]
        assert owner.friendly_name == "Orphan"
        assert owner.block_path == "config-block[0]"
        assert owner.entity_id is None
        assert owner.url_path is None

    def test_missing_alias_leaves_friendly_name_none(self) -> None:
        # Unnamed automation -- display label falls back to
        # "automation - config-block[0]" via
        # _owner_display_name.
        parsed = [{"id": "xyz"}]
        source = _source("automations", "automations.yaml", parsed)
        owners = _scan_automations(source, _ts())
        assert len(owners) == 1
        owner, _ = owners[0]
        assert owner.friendly_name is None
        assert owner.block_path == "config-block[0]"
        assert _owner_display_name(owner) == "automation - config-block[0]"


class TestScanScripts:
    def test_owner_from_script_key(self) -> None:
        parsed = {
            "foo": {"alias": "Foo Script", "sequence": []},
        }
        source = _source("scripts", "scripts.yaml", parsed)
        ts = _ts(
            entity_ids={"script.foo"},
            registry={
                "script.foo": _reg_entry(
                    "script.foo",
                    platform="script",
                    config_entry_id=None,
                ),
            },
        )
        owners = _scan_scripts(source, ts)
        assert len(owners) == 1
        owner, _ = owners[0]
        assert owner.integration == "script"
        assert owner.block_path == "config-block[0]"
        assert owner.friendly_name == "Foo Script"
        assert owner.entity_id == "script.foo"
        assert owner.url_path == "/config/script/edit/foo"
        assert owner.yaml_only is True

    def test_missing_alias_uses_key(self) -> None:
        parsed = {"bar": {"sequence": []}}
        source = _source("scripts", "scripts.yaml", parsed)
        owners = _scan_scripts(source, _ts())
        assert owners[0][0].friendly_name == "bar"


class TestScanTemplate:
    def test_sensor_list_form(self) -> None:
        parsed = [
            {
                "sensor": [
                    {"name": "Foo", "unique_id": "foo_uid"},
                    {"name": "Bar", "unique_id": "bar_uid"},
                ],
            },
        ]
        source = _source("template", "template.yaml", parsed)
        ts = _ts(
            entity_by_unique_id={
                ("template", "foo_uid"): "sensor.foo",
            },
            entity_ids={"sensor.foo"},
            registry={
                "sensor.foo": _reg_entry(
                    "sensor.foo",
                    platform="template",
                    config_entry_id=None,
                ),
            },
        )
        owners = _scan_template(source, ts)
        by_friendly = {o.friendly_name: o for o, _ in owners}
        assert "Foo" in by_friendly
        assert "Bar" in by_friendly
        assert by_friendly["Foo"].block_path == "config-block[0].sensor[0]"
        assert by_friendly["Bar"].block_path == "config-block[0].sensor[1]"

    def test_sensor_dict_form_is_wrapped(self) -> None:
        # A single sensor defined as `sensor: {name: ...}`
        # rather than `sensor: [{name: ...}]` must still
        # produce an owner.
        parsed = [
            {
                "sensor": {
                    "name": "Grid Import Power",
                    "state": "{{ states('sensor.envoy_prod') }}",
                },
            },
        ]
        source = _source("template", "template.yaml", parsed)
        owners = _scan_template(source, _ts())
        friendlies = [o.friendly_name for o, _ in owners]
        assert "Grid Import Power" in friendlies

    def test_trigger_list_becomes_per_item_owners(self) -> None:
        parsed = [
            {
                "trigger": [
                    {"platform": "state"},
                    {"platform": "time"},
                ],
                "sensor": [{"name": "Foo"}],
            },
        ]
        source = _source("template", "template.yaml", parsed)
        owners = _scan_template(source, _ts())
        paths = [o.block_path for o, _ in owners]
        assert "config-block[0].sensor[0]" in paths
        assert "config-block[0].trigger[0]" in paths
        assert "config-block[0].trigger[1]" in paths

    def test_variables_becomes_single_owner(self) -> None:
        parsed = [
            {
                "variables": {
                    "foo": "{{ states('sensor.a') }}",
                    "bar": "{{ states('sensor.b') }}",
                },
                "sensor": [{"name": "X"}],
            },
        ]
        source = _source("template", "template.yaml", parsed)
        owners = _scan_template(source, _ts())
        var_owners = [
            o for o, _ in owners if o.block_path == "config-block[0].variables"
        ]
        assert len(var_owners) == 1
        assert var_owners[0].friendly_name is None
        assert var_owners[0].integration == "template"

    def test_multiple_config_blocks_get_distinct_owners(
        self,
    ) -> None:
        # Each config block must produce block-level
        # owners with distinct identity tuples
        # (source_file, block_path, friendly_name) so that
        # _build_owner_result generates distinct
        # notification IDs and HA's
        # persistent_notification doesn't collapse them.
        parsed = [
            {
                "trigger": [
                    {
                        "platform": "state",
                        "entity_id": "sensor.dead_a",
                    },
                ],
                "sensor": [{"name": "Alpha"}],
            },
            {
                "trigger": [
                    {
                        "platform": "state",
                        "entity_id": "sensor.dead_b",
                    },
                ],
                "binary_sensor": [{"name": "Beta"}],
            },
        ]
        source = _source("template", "template.yaml", parsed)
        owners = _scan_template(source, _ts())

        trigger_paths = [
            o.block_path
            for o, _ in owners
            if o.block_path and ".trigger[" in o.block_path
        ]
        assert "config-block[0].trigger[0]" in trigger_paths
        assert "config-block[1].trigger[0]" in trigger_paths

        identities = {
            (o.source_file, o.block_path, o.friendly_name) for o, _ in owners
        }
        assert len(identities) == len(owners), (
            "owners collide on identity tuple;"
            f" got {len(owners)} owners, {len(identities)} identities"
        )

    def test_light_and_weather_platforms_recognized(self) -> None:
        # Template integration supports more than the
        # common sensor/binary_sensor platforms.
        parsed = [
            {
                "light": [{"name": "Studio Light"}],
                "weather": [{"name": "Backyard"}],
                "lock": [{"name": "Front"}],
            },
        ]
        source = _source("template", "template.yaml", parsed)
        owners = _scan_template(source, _ts())
        paths = {o.block_path for o, _ in owners}
        assert "config-block[0].light[0]" in paths
        assert "config-block[0].weather[0]" in paths
        assert "config-block[0].lock[0]" in paths


class TestScanCustomize:
    def test_owner_per_entity(self) -> None:
        parsed = {
            "sensor.valid": {"friendly_name": "Valid"},
            "sensor.dead": {"icon": "mdi:skull"},
        }
        source = _source("customize", "customize.yaml", parsed)
        owners = _scan_customize(source, _ts())
        assert len(owners) == 2
        by_friendly = {o.friendly_name: (o, t) for o, t in owners}
        assert set(by_friendly) == {"sensor.valid", "sensor.dead"}
        for eid, (owner, tree) in by_friendly.items():
            assert owner.integration == "customize"
            assert owner.source_file == "customize.yaml"
            assert owner.block_path is not None
            assert owner.block_path.startswith("config-block[")
            # Subtree is a one-key dict so the collector's
            # integration=="customize" branch validates
            # just this entity's key.
            assert tree == {eid: parsed[eid]}
        # Insertion order drives the block index.
        assert by_friendly["sensor.valid"][0].block_path == "config-block[0]"
        assert by_friendly["sensor.dead"][0].block_path == "config-block[1]"

    def test_not_a_dict_returns_empty(self) -> None:
        source = _source("customize", "customize.yaml", [])
        owners = _scan_customize(source, _ts())
        assert owners == []


class TestScanConfigEntries:
    def test_helper_entry_gets_url(self) -> None:
        parsed = {
            "data": {
                "entries": [
                    {
                        "entry_id": "abc123",
                        "domain": "group",
                        "title": "Downstairs Lights",
                        "options": {
                            "entities": ["light.a", "light.b"],
                        },
                    },
                ],
            },
        }
        source = _source(
            "config_entries",
            ".storage/core.config_entries",
            parsed,
        )
        ts = _ts(
            registry={
                "light.a": _reg_entry(
                    "light.a",
                    config_entry_id="abc123",
                ),
            },
        )
        owners = _scan_config_entries(source, ts)
        assert len(owners) == 1
        owner, _ = owners[0]
        assert owner.integration == "group"
        assert owner.friendly_name == "Downstairs Lights"
        assert owner.block_path is None
        assert owner.url_path == ("/config/entities/?config_entry=abc123")

    def test_entry_without_entities_has_no_url(self) -> None:
        parsed = {
            "data": {
                "entries": [
                    {
                        "entry_id": "hk123",
                        "domain": "homekit",
                        "title": "HomeKit Bridge",
                        "options": {
                            "filter": {
                                "include_entities": [
                                    "light.foo",
                                ],
                            },
                        },
                    },
                ],
            },
        }
        source = _source(
            "config_entries",
            ".storage/core.config_entries",
            parsed,
        )
        owners = _scan_config_entries(source, _ts())
        assert owners[0][0].url_path is None

    def test_url_driven_by_precomputed_index(self) -> None:
        # _scan_config_entries consults
        # truth_set.config_entries_with_entities to decide
        # whether to emit the entities-page URL. Passing
        # an empty registry but seeding the precomputed
        # set directly must still produce the URL.
        parsed = {
            "data": {
                "entries": [
                    {
                        "entry_id": "via_index",
                        "domain": "group",
                        "title": "Group via index",
                    },
                ],
            },
        }
        source = _source(
            "config_entries",
            ".storage/core.config_entries",
            parsed,
        )
        ts = _ts(config_entries_with_entities={"via_index"})
        owners = _scan_config_entries(source, ts)
        assert owners[0][0].url_path == (
            "/config/entities/?config_entry=via_index"
        )


class TestScanLovelace:
    def test_dashboard_owner_with_url_from_extra(self) -> None:
        parsed = {
            "key": "lovelace.doors",
            "data": {
                "config": {
                    "views": [
                        {
                            "cards": [
                                {"type": "entity", "entity": "light.foo"},
                            ],
                        },
                    ],
                },
            },
        }
        source = _source(
            "lovelace",
            ".storage/lovelace.doors",
            parsed,
            title="Doors",
            url_path="/doors",
        )
        owners = _scan_lovelace(source, _ts())
        assert len(owners) == 1
        owner, _ = owners[0]
        assert owner.integration == "lovelace"
        assert owner.friendly_name == "Doors"
        assert owner.block_path is None
        assert owner.url_path == "/doors"


class TestScanGenericYaml:
    def test_dict_top_level_one_owner_per_key(self) -> None:
        parsed = {
            "plant_01": {"sensors": {"moisture": "sensor.a"}},
            "plant_02": {"sensors": {"moisture": "sensor.b"}},
        }
        source = _source("generic_yaml", "plants.yaml", parsed)
        owners = _scan_generic_yaml(source, _ts())
        friendlies = sorted(o[0].friendly_name or "" for o in owners)
        assert friendlies == ["plant_01", "plant_02"]
        paths = [o[0].block_path for o in owners]
        assert paths == ["config-block[0]", "config-block[1]"]
        for o in owners:
            assert o[0].integration is None

    def test_list_top_level_named_from_item(self) -> None:
        parsed = [
            {"name": "Grid Import Energy", "source": "sensor.foo"},
            {"name": "Grid Export Energy", "source": "sensor.bar"},
        ]
        source = _source("generic_yaml", "sensor.yaml", parsed)
        owners = _scan_generic_yaml(source, _ts())
        friendlies = [o[0].friendly_name for o in owners]
        assert friendlies == ["Grid Import Energy", "Grid Export Energy"]
        paths = [o[0].block_path for o in owners]
        assert paths == ["config-block[0]", "config-block[1]"]

    def test_list_top_level_falls_back_to_none_friendly(self) -> None:
        # List items with no name/alias/id leave
        # friendly_name None -- display label falls back
        # to "$file - $block_path".
        parsed = [{"platform": "smtp"}, {"platform": "telegram"}]
        source = _source(
            "generic_yaml",
            "notifications.yaml",
            parsed,
        )
        owners = _scan_generic_yaml(source, _ts())
        for o, _ in owners:
            assert o.friendly_name is None
        paths = [o[0].block_path for o in owners]
        assert paths == ["config-block[0]", "config-block[1]"]
        # display name stays locatable via source_file + block_path
        assert (
            _owner_display_name(owners[0][0])
            == "notifications.yaml - config-block[0]"
        )

    def test_scalar_produces_single_file_owner(self) -> None:
        parsed: object = None
        source = _source("generic_yaml", "empty.yaml", parsed)
        owners = _scan_generic_yaml(source, _ts())
        assert len(owners) == 1
        owner, _ = owners[0]
        assert owner.block_path is None
        assert owner.friendly_name is None
        assert owner.integration is None


# -- Collection + validation ----------------------------


class TestCollectFindings:
    def test_broken_entity_emits_finding(self) -> None:
        tree: dict[str, object] = {
            "entity_id": "sensor.dead",
        }
        owner = Owner(source_file="x.yaml", friendly_name="t")
        ts = _ts(entity_ids={"sensor.alive"})
        findings, stats = _collect_findings(
            _config(),
            owner,
            tree,
            ts,
        )
        assert len(findings) == 1
        assert findings[0].ref.value == "sensor.dead"
        assert findings[0].disabled is False
        assert stats.refs_broken == 1

    def test_valid_entity_no_finding(self) -> None:
        tree: dict[str, object] = {
            "entity_id": "sensor.alive",
        }
        owner = Owner(source_file="x.yaml", friendly_name="t")
        ts = _ts(entity_ids={"sensor.alive"})
        findings, stats = _collect_findings(_config(), owner, tree, ts)
        assert findings == []
        assert stats.refs_valid == 1

    def test_disabled_entity_flagged_when_enabled(self) -> None:
        tree: dict[str, object] = {
            "entity_id": "sensor.disabled_one",
        }
        owner = Owner(source_file="x.yaml", friendly_name="t")
        ts = _ts(disabled_entity_ids={"sensor.disabled_one"})
        findings, stats = _collect_findings(
            _config(check_disabled_entities=True),
            owner,
            tree,
            ts,
        )
        assert len(findings) == 1
        assert findings[0].disabled is True
        assert stats.refs_disabled == 1

    def test_disabled_entity_not_flagged_when_off(self) -> None:
        tree: dict[str, object] = {
            "entity_id": "sensor.disabled_one",
        }
        owner = Owner(source_file="x.yaml", friendly_name="t")
        ts = _ts(disabled_entity_ids={"sensor.disabled_one"})
        findings, stats = _collect_findings(
            _config(check_disabled_entities=False),
            owner,
            tree,
            ts,
        )
        assert findings == []
        assert stats.refs_disabled == 1

    def test_service_name_filter_skips_sniff_hit(self) -> None:
        # light.turn_on looks like an entity id via sniff,
        # but is in service_names -- should be dropped.
        tree: dict[str, object] = {
            "custom_input": "light.turn_on",
        }
        owner = Owner(source_file="x.yaml", friendly_name="t")
        ts = _ts(
            service_names={"light.turn_on"},
            extra_domains={"light"},
        )
        findings, stats = _collect_findings(_config(), owner, tree, ts)
        assert findings == []
        assert stats.refs_service_skipped == 1
        assert stats.refs_total == 0

    def test_target_exclude_regex_suppresses(self) -> None:
        tree: dict[str, object] = {
            "entity_id": "sensor.legacy_thing",
        }
        owner = Owner(source_file="x.yaml", friendly_name="t")
        ts = _ts()  # sensor.legacy_thing not in truth set -> broken
        findings, _ = _collect_findings(
            _config(exclude_entity_id_regex=r"^sensor\.legacy_"),
            owner,
            tree,
            ts,
        )
        assert findings == []

    def test_customize_broken_entity(self) -> None:
        owner = Owner(
            source_file="customize.yaml",
            integration="customize",
            friendly_name="sensor.dead",
        )
        tree: dict[str, object] = {
            "sensor.dead": {"friendly_name": "B"},
        }
        ts = _ts(entity_ids={"sensor.valid"})
        findings, stats = _collect_findings(_config(), owner, tree, ts)
        assert len(findings) == 1
        assert findings[0].ref.value == "sensor.dead"
        assert stats.refs_total == 1
        assert stats.refs_broken == 1

    def test_customize_valid_entity(self) -> None:
        owner = Owner(
            source_file="customize.yaml",
            integration="customize",
            friendly_name="sensor.valid",
        )
        tree: dict[str, object] = {
            "sensor.valid": {"friendly_name": "A"},
        }
        ts = _ts(entity_ids={"sensor.valid"})
        findings, stats = _collect_findings(_config(), owner, tree, ts)
        assert findings == []
        assert stats.refs_total == 1
        assert stats.refs_valid == 1


# -- Notification body formatting -----------------------


class TestNotificationBody:
    def test_yaml_only_note_included(self) -> None:
        # No url_path -> the owner is not UI-editable, so
        # the "edit the YAML file" note appears.
        owner = Owner(
            source_file="utility_meters.yaml",
            block_path="config-block[0]",
            friendly_name="daily_power",
            entity_id="sensor.daily_power",
            yaml_only=True,
        )
        findings = [
            Finding(
                ref=Ref(
                    kind="entity",
                    value="sensor.dead",
                    context="source",
                ),
            ),
        ]
        body = _build_notification_body(owner, findings)
        assert "sensor.daily_power" in body
        assert "YAML-only" in body
        assert "utility_meters.yaml" in body
        assert "sensor.dead" in body

    def test_yaml_only_note_suppressed_when_url_path_set(self) -> None:
        # yaml_only can be True for automations/scripts
        # because their registry entries have no config
        # entry -- but if HA's UI can edit them (url_path
        # set) the note is misleading and is suppressed.
        owner = Owner(
            source_file="automations.yaml",
            integration="automation",
            block_path="config-block[0]",
            friendly_name="Front Door Auto Lock",
            entity_id="automation.front_door_auto_lock",
            url_path="/config/automation/edit/1234",
            yaml_only=True,
        )
        findings = [
            Finding(
                ref=Ref(
                    kind="entity",
                    value="automation.lock_front_door",
                    context="actions.[1].target.entity_id",
                ),
            ),
        ]
        body = _build_notification_body(owner, findings)
        assert "automation.front_door_auto_lock" in body
        assert "YAML-only" not in body
        # File: line still shows the path for YAML editors.
        assert "File: `automations.yaml`" in body

    def test_yaml_only_note_suppressed_when_url_path_set_no_entity(
        self,
    ) -> None:
        # Parallel branch: no entity_id but url_path set
        # (unlikely in practice, but the elif branch
        # should stay consistent).
        owner = Owner(
            source_file="automations.yaml",
            integration="automation",
            block_path="config-block[0]",
            friendly_name="Unregistered",
            url_path="/config/automation/edit/abc",
            yaml_only=True,
        )
        body = _build_notification_body(owner, [])
        assert "YAML-only" not in body

    def test_header_shows_block_path_and_name(self) -> None:
        # The Owner: line combines block_path and
        # friendly_name so users can locate the owner in
        # the file by position or by identifier.
        owner = Owner(
            source_file="automations.yaml",
            integration="automation",
            block_path="config-block[2]",
            friendly_name="My Motion Lights",
        )
        body = _build_notification_body(owner, [])
        # Square brackets in the YAML path are escaped so
        # they don't form a bogus markdown link.
        assert "Owner: config-block\\[2\\] - My Motion Lights" in body

    def test_integration_line_present_when_set(self) -> None:
        owner = Owner(
            source_file="automations.yaml",
            integration="automation",
            block_path="config-block[0]",
            friendly_name="X",
        )
        body = _build_notification_body(owner, [])
        assert "Integration: [automation]" in body
        assert "/config/integrations/integration/automation" in body

    def test_integration_line_omitted_when_none(self) -> None:
        # Generic YAML owners have integration=None.
        # Their notification must not advertise an
        # Integration: line -- exclude_integrations can't
        # filter these.
        owner = Owner(
            source_file="plants.yaml",
            block_path="config-block[0]",
            friendly_name="plant_01",
        )
        body = _build_notification_body(owner, [])
        assert "Integration:" not in body

    def test_disabled_section_rendered(self) -> None:
        owner = Owner(
            source_file="automations.yaml",
            integration="automation",
            block_path="config-block[0]",
            friendly_name="Test",
        )
        findings = [
            Finding(
                ref=Ref(
                    kind="entity",
                    value="sensor.dis",
                    context="entity_id",
                ),
                disabled=True,
            ),
        ]
        body = _build_notification_body(owner, findings)
        assert "Disabled-but-existing" in body
        assert "sensor.dis" in body

    def test_broken_and_disabled_mixed(self) -> None:
        owner = Owner(
            source_file="automations.yaml",
            integration="automation",
            block_path="config-block[0]",
            friendly_name="Test",
        )
        findings = [
            Finding(
                ref=Ref(
                    kind="entity",
                    value="sensor.broken",
                    context="a",
                ),
            ),
            Finding(
                ref=Ref(
                    kind="entity",
                    value="sensor.disabled",
                    context="b",
                ),
                disabled=True,
            ),
        ]
        body = _build_notification_body(owner, findings)
        assert "Broken references (1)" in body
        assert "Disabled-but-existing references (1)" in body

    def test_url_path_renders_as_markdown_link(self) -> None:
        owner = Owner(
            source_file=".storage/core.config_entries",
            integration="group",
            friendly_name="Upstairs Lights",
            url_path="/config/entities/?config_entry=abc",
        )
        findings = [
            Finding(
                ref=Ref(
                    kind="entity",
                    value="light.dead",
                    context="options.entities",
                ),
            ),
        ]
        body = _build_notification_body(owner, findings)
        assert "Owner: [Upstairs Lights](/config/entities/" in body

    def test_friendly_name_with_brackets_is_escaped(self) -> None:
        owner = Owner(
            source_file=".storage/core.config_entries",
            integration="group",
            friendly_name="Lights [zone 1]",
            url_path="/config/entities/?config_entry=abc",
        )
        body = _build_notification_body(owner, [])
        assert "Owner: [Lights \\[zone 1\\]](" in body
        assert "Owner: [Lights [zone 1]](" not in body

    def test_integration_name_escaped_in_link_text(self) -> None:
        # Integration IDs are slug-style under HA's current
        # charset, but the notification body interpolates
        # them as link text -- escape so a future HA release
        # loosening the charset can't corrupt the rendered
        # link. URL target keeps the raw value (URL targets
        # don't render markdown).
        owner = Owner(
            source_file="automations.yaml",
            integration="bad[plat]",
            friendly_name="X",
            url_path="/config/automation/edit/abc",
        )
        body = _build_notification_body(owner, [])
        assert (
            "Integration: [bad\\[plat\\]]"
            "(/config/integrations/integration/bad[plat])" in body
        )

    def test_no_url_path_renders_plain_text(self) -> None:
        owner = Owner(
            source_file="plants.yaml",
            block_path="config-block[0]",
            friendly_name="plant_01",
        )
        findings = [
            Finding(
                ref=Ref(
                    kind="entity",
                    value="sensor.dead",
                    context="moisture",
                ),
            ),
        ]
        body = _build_notification_body(owner, findings)
        assert "Owner: config-block\\[0\\] - plant_01" in body
        assert "](/)" not in body


# -- End-to-end -----------------------------------------


class TestEvaluateSources:
    def test_broken_ref_surfaces_as_finding(self) -> None:
        sources = [
            _source(
                "automations",
                "automations.yaml",
                [
                    {
                        "id": "1",
                        "alias": "A",
                        "triggers": [
                            {
                                "trigger": "state",
                                "entity_id": "sensor.dead",
                            },
                        ],
                    },
                ],
            ),
        ]
        ts = _ts(entity_ids={"sensor.alive"})
        results = _evaluate_sources(_config(), sources, ts)
        assert len(results) == 1
        result = results[0]
        assert result.has_issue is True
        assert len(result.findings) == 1
        assert result.findings[0].ref.value == "sensor.dead"
        assert result.notification_title  # category set; dispatcher prepends

    def test_clean_owner_emits_zero_finding_result(self) -> None:
        sources = [
            _source(
                "automations",
                "automations.yaml",
                [
                    {
                        "id": "1",
                        "alias": "A",
                        "triggers": [
                            {
                                "trigger": "state",
                                "entity_id": "sensor.alive",
                            },
                        ],
                    },
                ],
            ),
        ]
        ts = _ts(entity_ids={"sensor.alive"})
        results = _evaluate_sources(_config(), sources, ts)
        assert len(results) == 1
        assert results[0].has_issue is False
        assert results[0].refs_total == 1
        assert results[0].refs_valid == 1

    def test_exclude_integration_drops_entries(self) -> None:
        sources = [
            _source(
                "config_entries",
                ".storage/core.config_entries",
                {
                    "data": {
                        "entries": [
                            {
                                "entry_id": "a",
                                "domain": "shelly",
                                "title": "Shelly",
                            },
                        ],
                    },
                },
            ),
        ]
        results = _evaluate_sources(
            _config(exclude_integrations=["shelly"]),
            sources,
            _ts(),
        )
        assert results == []

    def test_exclude_source_entity_drops_owner(self) -> None:
        sources = [
            _source(
                "automations",
                "automations.yaml",
                [{"id": "legacy", "alias": "Legacy"}],
            ),
        ]
        ts = _ts(
            entity_by_unique_id={
                ("automation", "legacy"): "automation.legacy",
            },
            entity_ids={"automation.legacy"},
        )
        results = _evaluate_sources(
            _config(exclude_entities=["automation.legacy"]),
            sources,
            ts,
        )
        assert results == []

    def test_yaml_only_owner_via_registry(self) -> None:
        # Automation in automations.yaml with an `id` is
        # UI-editable -- _scan_automations emits a url_path,
        # so the YAML-only note is suppressed in the
        # notification body even though the registry has
        # config_entry_id=None (which still flips the
        # yaml_only flag itself).
        sources = [
            _source(
                "automations",
                "automations.yaml",
                [
                    {
                        "id": "1",
                        "alias": "YAMLo",
                        "triggers": [
                            {
                                "trigger": "state",
                                "entity_id": "sensor.dead",
                            },
                        ],
                    },
                ],
            ),
        ]
        ts = _ts(
            entity_by_unique_id={("automation", "1"): "automation.yamlo"},
            entity_ids={"automation.yamlo"},
            registry={
                "automation.yamlo": _reg_entry(
                    "automation.yamlo",
                    platform="automation",
                    config_entry_id=None,
                ),
            },
        )
        results = _evaluate_sources(_config(), sources, ts)
        assert len(results) == 1
        assert results[0].owner.yaml_only is True
        assert results[0].owner.url_path == "/config/automation/edit/1"
        assert "YAML-only" not in results[0].notification_message

    def test_results_returned_unsorted(self) -> None:
        # _evaluate_sources no longer sorts -- that
        # responsibility lives in helpers.prepare_notifications
        # which sorts by (notification_title, notification_id)
        # before applying the notification cap. Sort-order
        # behavior is covered in tests/test_helpers.py.
        sources = [
            _source(
                "automations",
                "automations.yaml",
                [
                    {"id": "b", "alias": "B"},
                    {"id": "a", "alias": "A"},
                ],
            ),
        ]
        results = _evaluate_sources(_config(), sources, _ts())
        names = {r.owner.friendly_name for r in results}
        assert names == {"A", "B"}


class TestOwnerResultNotification:
    def test_to_notification_active_on_issue(self) -> None:
        owner = Owner(source_file="x.yaml", friendly_name="t")
        result = OwnerResult(
            owner=owner,
            has_issue=True,
            notification_id="test_id",
            notification_title="title",
            notification_message="msg",
            findings=[],
            refs_total=0,
            refs_structural=0,
            refs_jinja=0,
            refs_sniff=0,
            refs_valid=0,
            refs_disabled=0,
            refs_broken=0,
            refs_service_skipped=0,
        )
        notif = result.to_notification()
        assert notif.active is True
        assert notif.notification_id == "test_id"

    def test_to_notification_suppress_dismisses(self) -> None:
        owner = Owner(source_file="x.yaml", friendly_name="t")
        result = OwnerResult(
            owner=owner,
            has_issue=True,
            notification_id="test_id",
            notification_title="title",
            notification_message="msg",
            findings=[],
            refs_total=0,
            refs_structural=0,
            refs_jinja=0,
            refs_sniff=0,
            refs_valid=0,
            refs_disabled=0,
            refs_broken=0,
            refs_service_skipped=0,
        )
        notif = result.to_notification(suppress=True)
        assert notif.active is False


# -- Sanitize notification ID --------------------------


class TestSanitizeNotificationId:
    def test_lowercase_and_underscores_preserved(self) -> None:
        assert _sanitize_notification_id("hello_world") == "hello_world"

    def test_uppercase_lowered(self) -> None:
        assert _sanitize_notification_id("Hello") == "hello"

    def test_spaces_replaced(self) -> None:
        assert _sanitize_notification_id("my automation") == "my_automation"

    def test_special_chars_replaced(self) -> None:
        assert (
            _sanitize_notification_id("auto:lights/kitchen")
            == "auto_lights_kitchen"
        )

    def test_empty_string(self) -> None:
        assert _sanitize_notification_id("") == ""

    def test_digits_preserved(self) -> None:
        assert _sanitize_notification_id("test_123") == "test_123"


# -- Broken device findings ----------------------------


class TestCollectFindingsDevice:
    def test_broken_device_emits_finding(self) -> None:
        tree: dict[str, object] = {
            "device_id": "a" * 32,
        }
        owner = Owner(source_file="x.yaml", friendly_name="t")
        ts = _ts(device_ids={"b" * 32})
        findings, stats = _collect_findings(
            _config(),
            owner,
            tree,
            ts,
        )
        assert len(findings) == 1
        assert findings[0].ref.value == "a" * 32
        assert findings[0].ref.kind == "device"
        assert stats.refs_broken == 1

    def test_valid_device_no_finding(self) -> None:
        tree: dict[str, object] = {
            "device_id": "a" * 32,
        }
        owner = Owner(source_file="x.yaml", friendly_name="t")
        ts = _ts(device_ids={"a" * 32})
        findings, stats = _collect_findings(
            _config(),
            owner,
            tree,
            ts,
        )
        assert findings == []
        assert stats.refs_valid == 1


# -- _build_owner_result -------------------------------


class TestBuildOwnerResult:
    def test_no_issue_has_empty_title_and_message(self) -> None:
        owner = Owner(
            source_file="automations.yaml",
            integration="automation",
            block_path="config-block[0]",
            friendly_name="Clean",
        )
        result = _build_owner_result(_config(), owner, [], _OwnerStats())
        assert result.has_issue is False
        assert result.notification_title == ""
        assert result.notification_message == ""
        assert result.notification_id.startswith(
            "reference_watchdog_test__owner_",
        )

    def test_with_findings_has_title_and_message(self) -> None:
        owner = Owner(
            source_file="automations.yaml",
            integration="automation",
            block_path="config-block[0]",
            friendly_name="Broken Auto",
        )
        findings = [
            Finding(
                ref=Ref(
                    kind="entity",
                    value="sensor.dead",
                    context="entity_id",
                ),
            ),
        ]
        stats = _OwnerStats(
            refs_total=1,
            refs_structural=1,
            refs_broken=1,
        )
        result = _build_owner_result(_config(), owner, findings, stats)
        assert result.has_issue is True
        assert result.notification_title == "Broken Auto"
        assert "sensor.dead" in result.notification_message
        assert result.refs_total == 1
        assert result.refs_broken == 1

    def test_notification_id_sanitized(self) -> None:
        owner = Owner(
            source_file=".storage/core.config_entries",
            integration="group",
            friendly_name="Downstairs Lights",
        )
        result = _build_owner_result(_config(), owner, [], _OwnerStats())
        # Colons, spaces, dots replaced with underscores.
        # The 8-char hex hash suffix that guards against
        # sanitize-collisions is still lowercase a-f0-9
        # so it contains no disallowed characters.
        assert ":" not in result.notification_id
        assert " " not in result.notification_id
        assert "." not in result.notification_id

    def test_notification_id_collision_resistant(self) -> None:
        # Two owners whose raw identities collapse to the
        # same sanitized string under [^a-z0-9_] -> "_"
        # must still produce distinct notification IDs so
        # HA's persistent_notification doesn't merge them.
        a = Owner(
            source_file="plants.yaml",
            block_path="config-block[0]",
            friendly_name="Living Room",
        )
        b = Owner(
            source_file="plants.yaml",
            block_path="config-block[0]",
            friendly_name="Living-Room",
        )
        ra = _build_owner_result(_config(), a, [], _OwnerStats())
        rb = _build_owner_result(_config(), b, [], _OwnerStats())
        assert ra.notification_id != rb.notification_id

    def test_stats_propagated(self) -> None:
        owner = Owner(source_file="x.yaml", friendly_name="t")
        stats = _OwnerStats(
            refs_total=10,
            refs_structural=5,
            refs_jinja=3,
            refs_sniff=2,
            refs_valid=8,
            refs_disabled=1,
            refs_broken=1,
            refs_service_skipped=4,
        )
        result = _build_owner_result(_config(), owner, [], stats)
        assert result.refs_total == 10
        assert result.refs_structural == 5
        assert result.refs_jinja == 3
        assert result.refs_sniff == 2
        assert result.refs_valid == 8
        assert result.refs_disabled == 1
        assert result.refs_broken == 1
        assert result.refs_service_skipped == 4


# -- I/O and source discovery --------------------------


class TestReadYamlFile:
    def test_reads_valid_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "test.yaml"
        f.write_text("key: value\n")
        result = _read_yaml_file(str(f))
        assert result == {"key": "value"}

    def test_returns_none_for_missing_file(self) -> None:
        assert _read_yaml_file("/nonexistent/path.yaml") is None

    def test_returns_none_for_invalid_yaml(
        self,
        tmp_path: Path,
    ) -> None:
        f = tmp_path / "bad.yaml"
        f.write_text(":\n  - :\n    bad: [")
        assert _read_yaml_file(str(f)) is None

    def test_include_tags_become_placeholders(
        self,
        tmp_path: Path,
    ) -> None:
        f = tmp_path / "test.yaml"
        f.write_text("automation: !include automations.yaml\n")
        result = _read_yaml_file(str(f))
        assert isinstance(result, dict)
        assert result["automation"] == ("<!include:automations.yaml>")

    def test_secret_tag_becomes_placeholder(
        self,
        tmp_path: Path,
    ) -> None:
        f = tmp_path / "test.yaml"
        f.write_text("password: !secret my_pass\n")
        result = _read_yaml_file(str(f))
        assert isinstance(result, dict)
        assert result["password"] == "<!secret:my_pass>"

    def test_public_safeloader_not_mutated(
        self,
        tmp_path: Path,
    ) -> None:
        # RW's tag constructors must live on a private subclass,
        # not on yaml.SafeLoader -- otherwise every yaml.safe_load
        # call anywhere in the HA process inherits them.
        import yaml

        f = tmp_path / "test.yaml"
        f.write_text("password: !secret my_pass\n")
        _read_yaml_file(str(f))

        public_ctors = yaml.SafeLoader.yaml_constructors
        for tag in (
            "!include",
            "!include_dir_list",
            "!include_dir_named",
            "!include_dir_merge_list",
            "!include_dir_merge_named",
            "!secret",
            "!env_var",
        ):
            assert tag not in public_ctors, (
                f"yaml.SafeLoader.yaml_constructors carries {tag} -- "
                "RW leaked tag constructors onto the public loader."
            )


class TestReadJsonFile:
    def test_reads_valid_json(self, tmp_path: Path) -> None:
        f = tmp_path / "test.json"
        f.write_text('{"key": "value"}')
        result = _read_json_file(str(f))
        assert result == {"key": "value"}

    def test_returns_none_for_missing_file(self) -> None:
        assert _read_json_file("/nonexistent/path.json") is None

    def test_returns_none_for_invalid_json(
        self,
        tmp_path: Path,
    ) -> None:
        f = tmp_path / "bad.json"
        f.write_text("{bad json")
        assert _read_json_file(str(f)) is None


class TestExtractIncludesFromText:
    def test_single_include(self, tmp_path: Path) -> None:
        text = "automation: !include automations.yaml\n"
        result = _extract_includes_from_text(
            text,
            "configuration.yaml",
            str(tmp_path),
        )
        assert result == ["automations.yaml"]

    def test_multiple_includes(self, tmp_path: Path) -> None:
        text = (
            "automation: !include automations.yaml\n"
            "script: !include scripts.yaml\n"
        )
        result = _extract_includes_from_text(
            text,
            "configuration.yaml",
            str(tmp_path),
        )
        assert result == ["automations.yaml", "scripts.yaml"]

    def test_include_dir_expands_files(
        self,
        tmp_path: Path,
    ) -> None:
        themes_dir = tmp_path / "themes"
        themes_dir.mkdir()
        (themes_dir / "dark.yaml").write_text("name: dark\n")
        (themes_dir / "light.yaml").write_text("name: light\n")
        (themes_dir / "readme.txt").write_text("skip me\n")

        text = "frontend:\n  themes: !include_dir_merge_named themes\n"
        result = _extract_includes_from_text(
            text,
            "configuration.yaml",
            str(tmp_path),
        )
        assert sorted(result) == [
            "themes/dark.yaml",
            "themes/light.yaml",
        ]

    def test_no_includes_returns_empty(
        self,
        tmp_path: Path,
    ) -> None:
        text = "key: value\n"
        result = _extract_includes_from_text(
            text,
            "configuration.yaml",
            str(tmp_path),
        )
        assert result == []

    def test_relative_path_resolved(
        self,
        tmp_path: Path,
    ) -> None:
        text = "extra: !include ../shared/common.yaml\n"
        result = _extract_includes_from_text(
            text,
            "subdir/config.yaml",
            str(tmp_path),
        )
        assert result == ["shared/common.yaml"]

    def test_missing_dir_treated_as_file(
        self,
        tmp_path: Path,
    ) -> None:
        # When the target directory doesn't exist,
        # isdir() returns False and the target is treated
        # as a file path (which _discover_yaml_sources
        # will skip via isfile()).
        text = "themes: !include_dir_list nonexistent/\n"
        result = _extract_includes_from_text(
            text,
            "configuration.yaml",
            str(tmp_path),
        )
        assert result == ["nonexistent"]

    def test_commented_include_ignored(
        self,
        tmp_path: Path,
    ) -> None:
        text = (
            "# automation: !include automations.yaml\n"
            "script: !include scripts.yaml\n"
        )
        result = _extract_includes_from_text(
            text,
            "configuration.yaml",
            str(tmp_path),
        )
        assert result == ["scripts.yaml"]

    def test_quoted_include_ignored(
        self,
        tmp_path: Path,
    ) -> None:
        # `!include` appearing inside a quoted string
        # value is a documentation artifact, not a
        # directive -- don't queue the target for scanning.
        text = (
            'description: "see !include other.yaml for details"\n'
            "script: !include scripts.yaml\n"
            "note: 'also !include ignored.yaml here'\n"
        )
        result = _extract_includes_from_text(
            text,
            "configuration.yaml",
            str(tmp_path),
        )
        assert result == ["scripts.yaml"]


class TestDiscoverYamlSources:
    def test_discovers_configuration_yaml(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "configuration.yaml").write_text("homeassistant:\n")
        result = _discover_yaml_sources(str(tmp_path))
        assert len(result) == 1
        assert result[0][0] == "configuration.yaml"
        assert result[0][1] == "generic_yaml"

    def test_follows_includes_recursively(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "configuration.yaml").write_text(
            "sensor: !include sensor.yaml\n"
        )
        (tmp_path / "sensor.yaml").write_text("- platform: template\n")
        result = _discover_yaml_sources(str(tmp_path))
        paths = [r[0] for r in result]
        assert "configuration.yaml" in paths
        assert "sensor.yaml" in paths

    def test_dedicated_source_gets_correct_type(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "configuration.yaml").write_text(
            "automation: !include automations.yaml\n"
            "script: !include scripts.yaml\n"
        )
        (tmp_path / "automations.yaml").write_text("[]\n")
        (tmp_path / "scripts.yaml").write_text("{}\n")
        result = _discover_yaml_sources(str(tmp_path))
        type_map = {r[0]: r[1] for r in result}
        assert type_map["automations.yaml"] == "automations"
        assert type_map["scripts.yaml"] == "scripts"
        assert type_map["configuration.yaml"] == "generic_yaml"

    def test_skips_missing_includes(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "configuration.yaml").write_text(
            "missing: !include does_not_exist.yaml\n"
        )
        result = _discover_yaml_sources(str(tmp_path))
        assert len(result) == 1

    def test_skips_empty_files(
        self,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "configuration.yaml").write_text(
            "extra: !include empty.yaml\n"
        )
        (tmp_path / "empty.yaml").write_text("")
        result = _discover_yaml_sources(str(tmp_path))
        assert len(result) == 1

    def test_no_cycles(self, tmp_path: Path) -> None:
        (tmp_path / "configuration.yaml").write_text("a: !include a.yaml\n")
        (tmp_path / "a.yaml").write_text("b: !include configuration.yaml\n")
        result = _discover_yaml_sources(str(tmp_path))
        paths = [r[0] for r in result]
        assert paths.count("configuration.yaml") == 1
        assert paths.count("a.yaml") == 1

    def test_include_dir_expansion(
        self,
        tmp_path: Path,
    ) -> None:
        pkg_dir = tmp_path / "packages"
        pkg_dir.mkdir()
        (pkg_dir / "garden.yaml").write_text("sensor: []\n")
        (pkg_dir / "hvac.yaml").write_text("climate: []\n")
        (tmp_path / "configuration.yaml").write_text(
            "homeassistant:\n  packages: !include_dir_named packages\n"
        )
        result = _discover_yaml_sources(str(tmp_path))
        paths = sorted(r[0] for r in result)
        assert "packages/garden.yaml" in paths
        assert "packages/hvac.yaml" in paths

    def test_returns_no_config_yaml(
        self,
        tmp_path: Path,
    ) -> None:
        result = _discover_yaml_sources(str(tmp_path))
        assert result == []


class TestEnumerateJsonSources:
    def test_finds_config_entries(
        self,
        tmp_path: Path,
    ) -> None:
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "core.config_entries").write_text(
            '{"data": {"entries": []}}'
        )
        result = _enumerate_json_sources(str(tmp_path))
        paths = [s.path for s in result]
        assert ".storage/core.config_entries" in paths

    def test_finds_lovelace_dashboards(
        self,
        tmp_path: Path,
    ) -> None:
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "lovelace.my_dash").write_text('{"data": {"config": {}}}')
        (storage / "lovelace_dashboards").write_text(
            '{"data": {"items": [{"id": "my_dash",'
            ' "title": "My Dash", "url_path": "my-dash"}]}}'
        )
        result = _enumerate_json_sources(str(tmp_path))
        lv = [s for s in result if s.source_type == "lovelace"]
        assert len(lv) == 1
        assert lv[0].extra["title"] == "My Dash"
        assert lv[0].extra["url_path"] == "/my-dash"

    def test_no_storage_dir(self, tmp_path: Path) -> None:
        result = _enumerate_json_sources(str(tmp_path))
        assert result == []


class TestTruthSet:
    def test_set_fields_are_frozenset(self) -> None:
        ts = _ts(entity_ids={"light.foo"})
        assert isinstance(ts.entity_ids, frozenset)
        assert isinstance(ts.domains, frozenset)
        assert isinstance(ts.service_names, frozenset)
        assert isinstance(ts.config_entries_with_entities, frozenset)

    def test_is_frozen(self) -> None:
        # Reassigning a field on a frozen dataclass
        # raises FrozenInstanceError (subclass of
        # AttributeError).
        import dataclasses

        ts = _ts()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ts.entity_ids = frozenset({"light.foo"})  # type: ignore[misc]


class TestRunEvaluation:
    def _write_config(self, tmp_path: Path) -> None:
        (tmp_path / "configuration.yaml").write_text(
            "automation: !include automations.yaml\n"
        )
        (tmp_path / "automations.yaml").write_text(
            "- id: '1'\n"
            "  alias: Broken\n"
            "  triggers:\n"
            "    - trigger: state\n"
            "      entity_id: sensor.does_not_exist\n"
        )

    def test_end_to_end_surfaces_broken_ref(
        self,
        tmp_path: Path,
    ) -> None:
        self._write_config(tmp_path)
        ev = run_evaluation(
            str(tmp_path),
            _config(),
            _ts(),
            [],
            0,
        )
        # One automation owner with a broken ref.
        issue_owners = [r for r in ev.results if r.has_issue]
        assert len(issue_owners) == 1
        issue = issue_owners[0]
        assert issue.owner.integration == "automation"
        assert issue.owner.friendly_name == "Broken"
        assert any(
            f.ref.value == "sensor.does_not_exist" for f in issue.findings
        )
        assert ev.broken_entity_count == 1


class TestFindSourceOrphans:
    def test_utility_meter_defined_in_generic_yaml(self) -> None:
        # YAML-only registry entry (config_entry_id=None)
        # whose object_id appears as a dict key in a
        # generic YAML file is not an orphan.
        reg = _reg_entry(
            "sensor.central_heater_energy_daily",
            platform="utility_meter",
            unique_id="central_heater_energy_daily_single_tariff",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        yaml_sources: list[tuple[str, object]] = [
            (
                "utility_meters.yaml",
                {
                    "central_heater_energy_daily": {
                        "source": "sensor.central_heater_energy",
                        "cycle": "daily",
                    },
                },
            ),
        ]
        orphans = _find_source_orphans(_config(), ts, yaml_sources, [])
        assert orphans == []

    def test_prefix_match_does_not_mask_orphan(self) -> None:
        # Regression: naive substring match wrongly
        # considered sensor.central_heater_energy_daily
        # "defined" because its object_id was a suffix of
        # a longer YAML key (garage_central_heater_energy_daily).
        # Exact-string matching fixes this.
        reg = _reg_entry(
            "sensor.central_heater_energy_daily",
            platform="utility_meter",
            unique_id="central_heater_energy_daily_single_tariff",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        yaml_sources: list[tuple[str, object]] = [
            (
                "utility_meters.yaml",
                {
                    "garage_central_heater_energy_daily": {
                        "unique_id": "garage_central_heater_energy_daily",
                        "source": ("sensor.garage_central_heater_em0_energy"),
                        "cycle": "daily",
                    },
                },
            ),
        ]
        orphans = _find_source_orphans(_config(), ts, yaml_sources, [])
        assert len(orphans) == 1
        assert orphans[0].entity_id == ("sensor.central_heater_energy_daily")

    def test_utility_meter_missing_is_orphan(self) -> None:
        # Same registry entry but utility_meters.yaml has
        # no matching key -- orphan.
        reg = _reg_entry(
            "sensor.hottub_energy_daily",
            platform="utility_meter",
            unique_id="hottub_energy_daily_single_tariff",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        yaml_sources: list[tuple[str, object]] = [
            (
                "utility_meters.yaml",
                {
                    "central_heater_energy_daily": {
                        "source": "sensor.central_heater_energy",
                    },
                },
            ),
        ]
        orphans = _find_source_orphans(_config(), ts, yaml_sources, [])
        assert len(orphans) == 1
        assert orphans[0].entity_id == "sensor.hottub_energy_daily"
        assert orphans[0].platform == "utility_meter"

    def test_config_entry_managed_never_orphan(self) -> None:
        # A registry entry with a config_entry_id is UI-
        # managed via the config flow. It is never a
        # source orphan regardless of YAML contents.
        reg = _reg_entry(
            "sensor.shelly_power",
            platform="shelly",
            unique_id="uid_xyz",
            config_entry_id="abc",
        )
        ts = _ts(registry={reg.entity_id: reg})
        orphans = _find_source_orphans(_config(), ts, [], [])
        assert orphans == []

    def test_runtime_platform_excluded(self) -> None:
        # pyscript-created entities live in runtime state,
        # never in YAML. They must not be flagged.
        reg = _reg_entry(
            "sensor.foo",
            platform="pyscript",
            unique_id="pyscript_foo",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        orphans = _find_source_orphans(_config(), ts, [], [])
        assert orphans == []

    def test_automation_pool_is_automations_yaml_only(self) -> None:
        # An automation's unique_id must match in
        # automations.yaml specifically. A stray mention in
        # another file (e.g. a dashboard that happens to
        # contain the id) is not enough to call it defined.
        reg = _reg_entry(
            "automation.foo",
            platform="automation",
            unique_id="1683070666795",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        # Stray mention in a generic YAML ("notes.yaml")
        # with the id buried in a free-text value. Under
        # structural harvest, ``note`` is not in
        # _DEFINER_ID_KEYS, so the value is not pooled.
        notes_sources: list[tuple[str, object]] = [
            ("notes.yaml", {"note": "old id: 1683070666795"}),
        ]
        orphans = _find_source_orphans(_config(), ts, notes_sources, [])
        assert len(orphans) == 1

        # But when it IS in automations.yaml under ``id:``,
        # harvest picks it up.
        auto_sources: list[tuple[str, object]] = [
            (
                "automations.yaml",
                [{"id": "1683070666795", "alias": "Foo"}],
            ),
        ]
        orphans2 = _find_source_orphans(_config(), ts, auto_sources, [])
        assert orphans2 == []

    def test_template_pool_includes_generic(self) -> None:
        # Some users put template blocks in a file other
        # than template.yaml (e.g. inside configuration.yaml
        # or a sub-included file). Generic YAML counts as
        # a template definer.
        reg = _reg_entry(
            "sensor.humidifiers_energy",
            platform="template",
            unique_id="humidifiers_energy",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        yaml_sources: list[tuple[str, object]] = [
            (
                "configuration.yaml",
                {
                    "template": [
                        {
                            "sensor": [
                                {
                                    "name": "Humidifiers Energy",
                                    "unique_id": "humidifiers_energy",
                                    "state": "0",
                                },
                            ],
                        },
                    ],
                },
            ),
        ]
        orphans = _find_source_orphans(_config(), ts, yaml_sources, [])
        assert orphans == []

    def test_consumer_side_reference_does_not_mask_orphan(self) -> None:
        # A utility_meter that is still referenced by an
        # automation but no longer defined in any YAML
        # must be flagged. automations.yaml must not leak
        # into the utility_meter pool.
        reg = _reg_entry(
            "sensor.hottub_energy_daily",
            platform="utility_meter",
            unique_id="hottub_energy_daily_single_tariff",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        yaml_sources: list[tuple[str, object]] = [
            (
                "automations.yaml",
                [
                    {
                        "alias": "Notify",
                        "triggers": [
                            {
                                "trigger": "state",
                                "entity_id": ("sensor.hottub_energy_daily"),
                            },
                        ],
                    },
                ],
            ),
        ]
        orphans = _find_source_orphans(_config(), ts, yaml_sources, [])
        assert len(orphans) == 1
        assert orphans[0].entity_id == "sensor.hottub_energy_daily"

    def test_customize_yaml_is_not_a_definer(self) -> None:
        # A dead entity still customized in customize.yaml
        # remains an orphan -- customize is an overlay, not
        # a definer.
        reg = _reg_entry(
            "input_boolean.dead",
            platform="input_boolean",
            unique_id="dead",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        yaml_sources: list[tuple[str, object]] = [
            (
                "customize.yaml",
                {"input_boolean.dead": {"friendly_name": "Dead"}},
            ),
        ]
        orphans = _find_source_orphans(_config(), ts, yaml_sources, [])
        assert len(orphans) == 1

    def test_storage_helper_defines_input_boolean(self) -> None:
        # A UI-created input_boolean lives in
        # .storage/input_boolean, not in any YAML file.
        # Its registry entry is YAML-only
        # (config_entry_id=None). Must be recognized as
        # defined.
        reg = _reg_entry(
            "input_boolean.garage_occupied",
            platform="input_boolean",
            unique_id="working_in_the_garage",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        storage_sources: list[tuple[str, object]] = [
            (
                ".storage/input_boolean",
                {
                    "data": {
                        "items": [
                            {
                                "id": "working_in_the_garage",
                                "name": "Garage Occupied",
                            },
                        ],
                    },
                },
            ),
        ]
        orphans = _find_source_orphans(_config(), ts, [], storage_sources)
        assert orphans == []

    def test_exclude_entities_suppresses_orphan(self) -> None:
        # User-supplied exclusion suppresses the orphan.
        reg = _reg_entry(
            "sensor.legacy",
            platform="utility_meter",
            unique_id="legacy",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        orphans = _find_source_orphans(
            _config(exclude_entities=["sensor.legacy"]),
            ts,
            [],
            [],
        )
        assert orphans == []

    def test_exclude_entity_id_regex_suppresses_orphan(self) -> None:
        reg = _reg_entry(
            "sensor.legacy_one",
            platform="utility_meter",
            unique_id="legacy_one",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        orphans = _find_source_orphans(
            _config(exclude_entity_id_regex=r"^sensor\.legacy_"),
            ts,
            [],
            [],
        )
        assert orphans == []

    def test_disabled_flag_propagated(self) -> None:
        # Orphan detection is independent of disabled
        # state but the flag rides along for the UI.
        reg = _reg_entry(
            "input_boolean.unused",
            platform="input_boolean",
            unique_id="unused",
            config_entry_id=None,
            disabled=True,
        )
        ts = _ts(
            registry={reg.entity_id: reg},
            disabled_entity_ids={"input_boolean.unused"},
        )
        orphans = _find_source_orphans(_config(), ts, [], [])
        assert len(orphans) == 1
        assert orphans[0].disabled is True

    def test_comment_does_not_mask_orphan(self) -> None:
        # A YAML comment mentioning the orphan's id is
        # stripped by the YAML parser before we harvest,
        # so it cannot contribute to the definer pool.
        # End-to-end test uses yaml.safe_load so the real
        # strip happens.
        import yaml

        parsed = yaml.safe_load(
            "# This file used to define hottub_energy_daily.\n"
            "other_key: other_value\n"
        )
        reg = _reg_entry(
            "sensor.hottub_energy_daily",
            platform="utility_meter",
            unique_id="hottub_energy_daily_single_tariff",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        yaml_sources: list[tuple[str, object]] = [("notes.yaml", parsed)]
        orphans = _find_source_orphans(_config(), ts, yaml_sources, [])
        assert len(orphans) == 1

    def test_description_string_does_not_mask_orphan(self) -> None:
        # A free-text ``description`` / ``alias`` string
        # that happens to mention the orphan's id must not
        # mark it as defined. Values are harvested only
        # when their key is in _DEFINER_ID_KEYS.
        reg = _reg_entry(
            "sensor.hottub_energy_daily",
            platform="utility_meter",
            unique_id="hottub_energy_daily_single_tariff",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        yaml_sources: list[tuple[str, object]] = [
            (
                "automations.yaml",
                [
                    {
                        "id": "new_id",
                        "alias": "replaces old hottub_energy_daily meter",
                        "description": (
                            "related: hottub_energy_daily_single_tariff"
                        ),
                    },
                ],
            ),
        ]
        orphans = _find_source_orphans(_config(), ts, yaml_sources, [])
        assert len(orphans) == 1
        assert orphans[0].entity_id == "sensor.hottub_energy_daily"

    def test_non_slug_unique_id_matches_via_storage_id(self) -> None:
        # Some integrations store colon- or dash-bearing
        # unique_ids (e.g. MAC-like). Structural harvest
        # captures the full string value, so exact match
        # works even when the identifier contains non-
        # ``[a-z0-9_]`` characters.
        reg = _reg_entry(
            "person.alice",
            platform="person",
            unique_id="aa:bb:cc:dd:ee:ff",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})
        storage_sources: list[tuple[str, object]] = [
            (
                ".storage/person",
                {
                    "data": {
                        "items": [
                            {
                                "id": "aa:bb:cc:dd:ee:ff",
                                "name": "Alice",
                            },
                        ],
                    },
                },
            ),
        ]
        orphans = _find_source_orphans(_config(), ts, [], storage_sources)
        assert orphans == []


class TestOrphanUrl:
    def test_platform_becomes_domain_filter(self) -> None:
        assert _orphan_url("utility_meter") == (
            "/config/entities?domain=utility_meter"
        )
        assert _orphan_url("input_boolean") == (
            "/config/entities?domain=input_boolean"
        )

    def test_empty_platform_falls_back_to_entities_page(self) -> None:
        assert _orphan_url("") == "/config/entities"


class TestBuildSourceOrphansNotification:
    def test_empty_returns_inactive(self) -> None:
        notif = _build_source_orphans_notification(_config(), [])
        assert notif.active is False
        assert notif.notification_id == _source_orphans_notification_id(
            _config(),
        )

    def test_groups_by_platform_and_links(self) -> None:
        orphans = [
            SourceOrphan(
                entity_id="sensor.hottub_energy_daily",
                platform="utility_meter",
                unique_id="hottub_energy_daily_single_tariff",
                disabled=False,
            ),
            SourceOrphan(
                entity_id="input_boolean.tec_test_light",
                platform="input_boolean",
                unique_id="tec_test_light",
                disabled=False,
            ),
            SourceOrphan(
                entity_id="input_boolean.tec_test_disable",
                platform="input_boolean",
                unique_id="tec_test_disable",
                disabled=False,
            ),
        ]
        notif = _build_source_orphans_notification(_config(), orphans)
        assert notif.active is True
        assert "source orphans (3)" in notif.title.lower()
        # Larger group (input_boolean=2) ordered before
        # smaller (utility_meter=1); within-group sorted
        # by entity_id.
        ib_idx = notif.message.find("**input_boolean**")
        um_idx = notif.message.find("**utility_meter**")
        assert ib_idx != -1 and um_idx != -1
        assert ib_idx < um_idx
        # Per-platform filter URL -- HA's entities page
        # accepts ?domain=<integration> but not
        # ?search=<entity_id>, so we land the user on the
        # narrowed integration list.
        assert "/config/entities?domain=input_boolean" in notif.message
        assert "/config/entities?domain=utility_meter" in notif.message
        # Entity_id itself still appears in the link text
        # so the user can spot it in the filtered list.
        assert "`input_boolean.tec_test_disable`" in notif.message
        assert "`sensor.hottub_energy_daily`" in notif.message

    def test_disabled_tag_in_body(self) -> None:
        orphans = [
            SourceOrphan(
                entity_id="input_boolean.unused",
                platform="input_boolean",
                unique_id="unused",
                disabled=True,
            ),
        ]
        notif = _build_source_orphans_notification(_config(), orphans)
        assert "*(disabled)*" in notif.message


class TestEnumerateStorageHelpers:
    def test_returns_known_helper_files(self, tmp_path: Path) -> None:
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "input_boolean").write_text('{"data": {"items": []}}')
        (storage / "person").write_text('{"data": {"items": []}}')
        # Unknown filenames must be skipped -- only
        # curated definer files contribute to the pool.
        (storage / "core.entity_registry").write_text('{"data": {}}')
        (storage / "core.restore_state").write_text('{"data": {}}')
        result = _enumerate_storage_helpers(str(tmp_path))
        rels = [r for r, _ in result]
        assert ".storage/input_boolean" in rels
        assert ".storage/person" in rels
        assert ".storage/core.entity_registry" not in rels
        assert ".storage/core.restore_state" not in rels

    def test_returns_parsed_json(self, tmp_path: Path) -> None:
        # Helper now returns parsed objects so downstream
        # code can walk the structure rather than re-parse.
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "input_boolean").write_text(
            '{"data": {"items": [{"id": "foo"}]}}'
        )
        result = _enumerate_storage_helpers(str(tmp_path))
        assert len(result) == 1
        rel, parsed = result[0]
        assert rel == ".storage/input_boolean"
        assert isinstance(parsed, dict)
        assert parsed == {"data": {"items": [{"id": "foo"}]}}

    def test_invalid_json_is_skipped(self, tmp_path: Path) -> None:
        storage = tmp_path / ".storage"
        storage.mkdir()
        (storage / "input_boolean").write_text("not json at all")
        assert _enumerate_storage_helpers(str(tmp_path)) == []

    def test_missing_files_are_ok(self, tmp_path: Path) -> None:
        storage = tmp_path / ".storage"
        storage.mkdir()
        # No helpers present.
        assert _enumerate_storage_helpers(str(tmp_path)) == []

    def test_no_storage_dir(self, tmp_path: Path) -> None:
        assert _enumerate_storage_helpers(str(tmp_path)) == []


class TestRunEvaluationOrphans:
    def test_orphan_feeds_summary_notification(
        self,
        tmp_path: Path,
    ) -> None:
        # Minimum config: configuration.yaml, empty
        # automations.yaml, and a registry entry for a
        # utility_meter that isn't declared anywhere.
        (tmp_path / "configuration.yaml").write_text(
            "automation: !include automations.yaml\n"
        )
        (tmp_path / "automations.yaml").write_text("")
        storage = tmp_path / ".storage"
        storage.mkdir()

        reg = _reg_entry(
            "sensor.dead_meter_daily",
            platform="utility_meter",
            unique_id="dead_meter_daily_single_tariff",
            config_entry_id=None,
        )
        ts = _ts(registry={reg.entity_id: reg})

        ev = run_evaluation(
            str(tmp_path),
            _config(),
            ts,
            [],
            0,
        )
        assert ev.source_orphan_count == 1
        assert ev.source_orphan_candidates == 1
        # Summary notification must be present and active.
        summary = [
            n
            for n in ev.notifications
            if n.notification_id.endswith("source_orphans")
        ]
        assert len(summary) == 1
        assert summary[0].active is True
        assert "sensor.dead_meter_daily" in summary[0].message


class TestIntegrationUxInvariant:
    """UX contract: rendered Integration: <=> filterable.

    Every owner whose notification body shows an
    ``Integration:`` line must have ``owner.integration``
    set to the same value -- and that value must be what
    the user would paste into the blueprint's
    ``exclude_integrations`` input to suppress the owner.
    """

    def test_render_presence_matches_exclude_filter(self) -> None:
        # An owner with integration=None has no
        # Integration: line and can't be filtered.
        no_int = Owner(
            source_file="plants.yaml",
            block_path="config-block[0]",
            friendly_name="plant_01",
        )
        body = _build_notification_body(no_int, [])
        assert "Integration:" not in body
        assert not _is_integration_excluded(
            no_int.integration,
            ["anything"],
        )

        # An owner with integration="automation" shows
        # the Integration: line AND is filtered when
        # "automation" is in exclude_integrations.
        has_int = Owner(
            source_file="automations.yaml",
            integration="automation",
            block_path="config-block[0]",
            friendly_name="X",
        )
        body = _build_notification_body(has_int, [])
        assert "Integration: [automation]" in body
        assert _is_integration_excluded(
            has_int.integration,
            ["automation"],
        )

    def test_customize_integration_filters(self) -> None:
        # Customize uses integration="customize" so the
        # value the user sees in the notification is the
        # value they paste into exclude_integrations.
        owner = Owner(
            source_file="customize.yaml",
            integration="customize",
            block_path="config-block[0]",
            friendly_name="sensor.dead",
        )
        body = _build_notification_body(owner, [])
        assert "Integration: [customize]" in body
        assert _is_integration_excluded(
            owner.integration,
            ["customize"],
        )


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "custom_components/blueprint_toolkit/reference_watchdog/__init__.py",
        "custom_components/blueprint_toolkit/reference_watchdog/handler.py",
        "custom_components/blueprint_toolkit/reference_watchdog/logic.py",
        "tests/test_reference_watchdog_logic.py",
    ]
    mypy_targets = [
        "custom_components/blueprint_toolkit/reference_watchdog/logic.py",
        "custom_components/blueprint_toolkit/reference_watchdog/handler.py",
    ]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
