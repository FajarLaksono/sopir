"""Unit tests for scenario artifact generation.

These are deliberately free of database and SUMO dependencies: the module under
test is imported, but no engine connection and no simulation is performed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scenario_gen.cli import (
    ARTIFACT_SUFFIXES,
    SCENARIO_TEMPLATES,
    ScenarioGenerationError,
    _load_sources,
    _render_sumocfg,
    _sync_artifacts,
    _validate_net_template,
    _validate_route_template,
    templates_dir,
)

SHAPELESS_NET = """<?xml version="1.0" encoding="UTF-8"?>
<net version="1.16">
    <edge id="E0" from="J0" to="J1">
        <lane id="E0_0" index="0" speed="13.89" length="500.00" width="3.50"/>
    </edge>
    <junction id="J0" type="priority" x="0.00" y="250.00"/>
    <junction id="J1" type="priority" x="500.00" y="250.00"/>
</net>
"""

VALID_NET = """<?xml version="1.0" encoding="UTF-8"?>
<net version="1.16">
    <edge id="E0" from="J0" to="J1">
        <lane id="E0_0" index="0" speed="13.89" length="500.00" width="3.50"
              shape="0.00,250.00 500.00,250.00"/>
    </edge>
    <junction id="J0" type="dead_end" x="0.00" y="250.00" incLanes="" intLanes=""
              shape="0.00,244.00 0.00,256.00"/>
    <junction id="J1" type="dead_end" x="500.00" y="250.00" incLanes="E0_0"
              intLanes="" shape="500.00,244.00 500.00,256.00"/>
</net>
"""

VALID_ROUTES = """<?xml version="1.0" encoding="UTF-8"?>
<routes>
    <vType id="car" accel="2.6" decel="4.5" sigma="0.5" length="5" minGap="2.5" maxSpeed="20"/>
    <route id="r0" edges="E0"/>
    <vehicle id="ego" type="car" route="r0" depart="0" departLane="0" departSpeed="10"/>
</routes>
"""

EMPTY_ROUTES = """<?xml version="1.0" encoding="UTF-8"?>
<routes>
    <vType id="car" accel="2.6" decel="4.5" sigma="0.5" length="5" minGap="2.5" maxSpeed="20"/>
</routes>
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_validate_net_rejects_shapeless_lanes(tmp_path: Path) -> None:
    path = _write(tmp_path, "broken.net.xml", SHAPELESS_NET)

    with pytest.raises(ScenarioGenerationError, match="without a shape"):
        _validate_net_template(path)


def test_validate_net_accepts_shaped_lanes(tmp_path: Path) -> None:
    _validate_net_template(_write(tmp_path, "ok.net.xml", VALID_NET))


def test_validate_net_rejects_malformed_xml(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad.net.xml", "<net><edge></net>")

    with pytest.raises(ScenarioGenerationError, match="not valid XML"):
        _validate_net_template(path)


def test_validate_net_rejects_network_without_lanes(tmp_path: Path) -> None:
    path = _write(tmp_path, "empty.net.xml", "<net><junction id='J0'/></net>")

    with pytest.raises(ScenarioGenerationError, match="no lanes"):
        _validate_net_template(path)


def test_validate_routes_rejects_file_without_actors(tmp_path: Path) -> None:
    path = _write(tmp_path, "empty.rou.xml", EMPTY_ROUTES)

    with pytest.raises(ScenarioGenerationError, match="no vehicle, person or flow"):
        _validate_route_template(path)


def test_validate_routes_accepts_person_only_file(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "ped.rou.xml",
        '<routes><person id="p1" depart="0"><walk edges="E0"/></person></routes>',
    )

    _validate_route_template(path)


def test_shipped_templates_are_complete_and_valid() -> None:
    templates = templates_dir()

    assert set(SCENARIO_TEMPLATES) == {
        "intersection",
        "highway_merge",
        "pedestrian_crossing",
        "lane_change",
    }

    for scenario_type, spec in SCENARIO_TEMPLATES.items():
        assert set(spec) == {"description", *ARTIFACT_SUFFIXES}
        for key in ARTIFACT_SUFFIXES:
            assert (templates / spec[key]).is_file(), f"{scenario_type} missing {spec[key]}"
        sources = _load_sources(scenario_type, templates)
        assert set(sources) == set(ARTIFACT_SUFFIXES)


def test_load_sources_reports_missing_template(tmp_path: Path) -> None:
    with pytest.raises(ScenarioGenerationError, match="missing template"):
        _load_sources("intersection", tmp_path)


def test_sync_artifacts_writes_every_artifact(tmp_path: Path) -> None:
    spec = SCENARIO_TEMPLATES["lane_change"]
    sources = {
        "net_file": VALID_NET,
        "route_file": VALID_ROUTES,
        "config_file": "ignored, rendered from spec",
    }

    written = _sync_artifacts(tmp_path / "abc", spec, sources)

    assert set(written) == set(ARTIFACT_SUFFIXES)
    for key in ARTIFACT_SUFFIXES:
        assert (tmp_path / "abc" / spec[key]).is_file()
    assert written["net_file"] == str(tmp_path / "abc" / spec["net_file"])


def test_sync_artifacts_is_idempotent_on_disk(tmp_path: Path) -> None:
    spec = SCENARIO_TEMPLATES["lane_change"]
    sources = {
        "net_file": VALID_NET,
        "route_file": VALID_ROUTES,
        "config_file": "unused",
    }
    scenario_dir = tmp_path / "abc"

    _sync_artifacts(scenario_dir, spec, sources)
    stamps = {key: (scenario_dir / spec[key]).stat().st_mtime_ns for key in ARTIFACT_SUFFIXES}

    _sync_artifacts(scenario_dir, spec, sources)
    after = {key: (scenario_dir / spec[key]).stat().st_mtime_ns for key in ARTIFACT_SUFFIXES}

    assert stamps == after


def test_sync_artifacts_repairs_truncated_file(tmp_path: Path) -> None:
    spec = SCENARIO_TEMPLATES["lane_change"]
    sources = {
        "net_file": VALID_NET,
        "route_file": VALID_ROUTES,
        "config_file": "unused",
    }
    scenario_dir = tmp_path / "abc"
    _sync_artifacts(scenario_dir, spec, sources)
    (scenario_dir / spec["net_file"]).write_text("<net>truncated", encoding="utf-8")

    _sync_artifacts(scenario_dir, spec, sources)

    assert (scenario_dir / spec["net_file"]).read_text(encoding="utf-8") == VALID_NET


def test_rendered_sumocfg_points_at_the_scenario_files() -> None:
    spec = SCENARIO_TEMPLATES["pedestrian_crossing"]
    rendered = _render_sumocfg(spec["net_file"], spec["route_file"])

    assert f'<net-file value="{spec["net_file"]}"/>' in rendered
    assert f'<route-files value="{spec["route_file"]}"/>' in rendered
