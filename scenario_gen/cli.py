"""Materialise SUMO scenario artifacts from the version controlled templates.

Generation is idempotent: one scenario row per scenario type is kept, and the
files on disk are re-synced from the templates on every call so a stale or
truncated artifact is repaired rather than silently reused. Nothing is written to
disk before the corresponding row is committed, and a row is removed again if its
artifacts cannot be written, so the scenario directory never outlives the database
entry that references it.
"""

from __future__ import annotations

import shutil
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database import SessionLocal
from backend.models import Scenario

SCENARIO_TEMPLATES: dict[str, dict[str, str]] = {
    "intersection": {
        "description": "4-way priority intersection, conflicting left and through traffic",
        "net_file": "intersection.net.xml",
        "route_file": "intersection.rou.xml",
        "config_file": "intersection.sumocfg",
    },
    "highway_merge": {
        "description": "3-lane highway with a low-priority on-ramp merging into occupied lane",
        "net_file": "highway_merge.net.xml",
        "route_file": "highway_merge.rou.xml",
        "config_file": "highway_merge.sumocfg",
    },
    "pedestrian_crossing": {
        "description": "Two-lane road with a generated footway and marked pedestrian crossing",
        "net_file": "pedestrian_crossing.net.xml",
        "route_file": "pedestrian_crossing.rou.xml",
        "config_file": "pedestrian_crossing.sumocfg",
    },
    "lane_change": {
        "description": "Two congested lanes against one free lane, forcing a two-stage overtake",
        "net_file": "lane_change.net.xml",
        "route_file": "lane_change.rou.xml",
        "config_file": "lane_change.sumocfg",
    },
}

ARTIFACT_SUFFIXES = ("net_file", "route_file", "config_file")


class ScenarioGenerationError(RuntimeError):
    """Raised when the scenario templates are missing or malformed."""


app = typer.Typer(add_completion=False, help="Generate SUMO scenario artifacts.")


@app.callback()
def main() -> None:
    """Scenario generation entry point."""


def templates_dir() -> Path:
    configured = Path(settings.scenario_templates_dir)
    if configured.is_dir():
        return configured
    return Path(__file__).resolve().parent / "templates"


def _validate_net_template(path: Path) -> None:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ScenarioGenerationError(f"{path.name} is not valid XML: {exc}") from exc

    lanes = root.findall(".//lane")
    if not lanes:
        raise ScenarioGenerationError(f"{path.name} declares no lanes")

    shapeless = sorted(
        lane.get("id", "<unnamed>") for lane in lanes if not (lane.get("shape") or "").strip()
    )
    if shapeless:
        raise ScenarioGenerationError(
            f"{path.name} has lanes without a shape attribute: {', '.join(shapeless)}. "
            "SUMO rejects shapeless lanes; regenerate the network with netconvert."
        )


def _validate_route_template(path: Path) -> None:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ScenarioGenerationError(f"{path.name} is not valid XML: {exc}") from exc

    if not (root.findall(".//vehicle") or root.findall(".//person") or root.findall(".//flow")):
        raise ScenarioGenerationError(f"{path.name} defines no vehicle, person or flow")


def _load_sources(scenario_type: str, templates: Path) -> dict[str, str]:
    spec = SCENARIO_TEMPLATES[scenario_type]
    sources: dict[str, str] = {}

    for key in ARTIFACT_SUFFIXES:
        path = templates / spec[key]
        if not path.is_file():
            raise ScenarioGenerationError(
                f"missing template {spec[key]} for scenario type {scenario_type!r} in {templates}"
            )
        sources[key] = path.read_text(encoding="utf-8")

    net_path = templates / spec["net_file"]
    route_path = templates / spec["route_file"]
    _validate_net_template(net_path)
    _validate_route_template(route_path)
    return sources


def _render_sumocfg(net_file: str, route_file: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<configuration>\n"
        "    <input>\n"
        f'        <net-file value="{net_file}"/>\n'
        f'        <route-files value="{route_file}"/>\n'
        "    </input>\n"
        "    <time>\n"
        '        <begin value="0"/>\n'
        '        <end value="100"/>\n'
        '        <step-length value="0.1"/>\n'
        "    </time>\n"
        "    <processing>\n"
        '        <collision.action value="warn"/>\n'
        "    </processing>\n"
        "</configuration>\n"
    )


def _write_if_changed(path: Path, content: str) -> None:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def _sync_artifacts(
    scenario_dir: Path, spec: dict[str, str], sources: dict[str, str]
) -> dict[str, str]:
    scenario_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    for key in ARTIFACT_SUFFIXES:
        content = sources[key]
        if key == "config_file":
            content = _render_sumocfg(spec["net_file"], spec["route_file"])
        destination = scenario_dir / spec[key]
        _write_if_changed(destination, content)
        written[key] = str(destination)

    return written


def _find_existing(db: Session, scenario_type: str) -> Optional[Scenario]:
    return db.scalars(
        select(Scenario).where(Scenario.type == scenario_type).order_by(Scenario.created_at)
    ).first()


def _scenario_config(spec: dict[str, str]) -> dict[str, str]:
    return {
        "description": spec["description"],
        "net": spec["net_file"],
        "routes": spec["route_file"],
    }


def generate_scenarios(
    db: Optional[Session] = None, base_dir: Optional[Path] = None
) -> list[uuid.UUID]:
    owns_session = db is None
    session = db if db is not None else SessionLocal()
    root = Path(base_dir) if base_dir is not None else Path(settings.scenario_data_dir)
    templates = templates_dir()
    scenario_ids: list[uuid.UUID] = []

    try:
        root.mkdir(parents=True, exist_ok=True)

        for scenario_type, spec in SCENARIO_TEMPLATES.items():
            sources = _load_sources(scenario_type, templates)
            existing = _find_existing(session, scenario_type)

            if existing is not None:
                _sync_artifacts(root / str(existing.id), spec, sources)
                existing.config = _scenario_config(spec)
                session.commit()
                scenario_ids.append(existing.id)
                continue

            scenario_id = uuid.uuid4()
            scenario_dir = root / str(scenario_id)
            planned = {key: str(scenario_dir / spec[key]) for key in ARTIFACT_SUFFIXES}

            scenario = Scenario(
                id=scenario_id,
                type=scenario_type,
                config=_scenario_config(spec),
                net_file_path=planned["net_file"],
                route_file_path=planned["route_file"],
                config_file_path=planned["config_file"],
            )
            session.add(scenario)
            session.commit()

            try:
                _sync_artifacts(scenario_dir, spec, sources)
            except Exception as exc:
                session.delete(scenario)
                session.commit()
                shutil.rmtree(scenario_dir, ignore_errors=True)
                raise ScenarioGenerationError(
                    f"failed to write artifacts for scenario {scenario_id}: {exc}"
                ) from exc

            scenario_ids.append(scenario_id)

        return scenario_ids
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


@app.command()
def generate() -> None:
    ids = generate_scenarios()
    typer.echo(f"Generated {len(ids)} scenarios:")
    for scenario_id in ids:
        typer.echo(f"  {scenario_id}")


if __name__ == "__main__":
    app()
