#!/usr/bin/env python3
"""Convert SWIM Digital NOTAM ZIP/XML data to the compact iOS feed schema."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree


COORDINATE_PATTERN = re.compile(r"(\d{4,6})(?:\.\d+)?([NS])(\d{5,7})(?:\.\d+)?([EW])")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def first_text(node, names: set[str]) -> str | None:
    for child in node.iter():
        if local_name(child.tag) in names and child.text and child.text.strip():
            return child.text.strip()
    return None


def decimal_coordinate(value: str):
    match = COORDINATE_PATTERN.search(value.replace(" ", "").upper())
    if not match:
        return None
    latitude_digits, latitude_direction, longitude_digits, longitude_direction = match.groups()
    if len(latitude_digits) == 4:
        latitude = int(latitude_digits[:2]) + int(latitude_digits[2:]) / 60
    else:
        latitude = int(latitude_digits[:2]) + int(latitude_digits[2:4]) / 60 + int(latitude_digits[4:6]) / 3600
    if len(longitude_digits) == 5:
        longitude = int(longitude_digits[:3]) + int(longitude_digits[3:]) / 60
    else:
        longitude = int(longitude_digits[:3]) + int(longitude_digits[3:5]) / 60 + int(longitude_digits[5:7]) / 3600
    if latitude_direction == "S":
        latitude *= -1
    if longitude_direction == "W":
        longitude *= -1
    return {"latitude": latitude, "longitude": longitude}


def date_from_notam_field(text: str, field: str):
    match = re.search(rf"\b{field}\)\s*(\d{{10}})", text.upper())
    if not match:
        return None
    value = match.group(1)
    moment = datetime(
        2000 + int(value[0:2]), int(value[2:4]), int(value[4:6]),
        int(value[6:8]), int(value[8:10]), tzinfo=timezone.utc,
    )
    return moment.isoformat().replace("+00:00", "Z")


def category(text: str, location: str | None) -> str:
    upper = text.upper()
    if "UNMANNED" in upper or "DRONE" in upper:
        return "rjdr"
    if "OBST" in upper or "OBSTACLE" in upper:
        return "obstacle"
    if location == "RJJJ":
        return "rjjj"
    return "airport"


def polygon_from_node(node) -> list[dict]:
    raw = first_text(node, {"posList"})
    if not raw:
        return []
    values = [float(item) for item in raw.split()]
    if len(values) < 6 or len(values) % 2:
        return []
    output = []
    for index in range(0, len(values), 2):
        first, second = values[index], values[index + 1]
        latitude, longitude = (first, second) if abs(first) <= 90 else (second, first)
        output.append({"latitude": latitude, "longitude": longitude})
    return output


def features_from_zip(zip_path: Path, mapping: dict) -> list[dict]:
    output = []
    with zipfile.ZipFile(zip_path) as archive:
        for filename in archive.namelist():
            if not filename.lower().endswith(".xml"):
                continue
            root = ElementTree.fromstring(archive.read(filename))
            for node in root.iter():
                if local_name(node.tag) != "NOTAM":
                    continue
                series = first_text(node, {"series"}) or ""
                number = first_text(node, {"number"}) or ""
                year = (first_text(node, {"year"}) or "")[-2:]
                identifier = f"{series}{number}/{year}"
                mapped = mapping.get(identifier, {})
                text = mapped.get("swim_text") or first_text(node, {"text"}) or ""
                location = first_text(node, {"location", "locationIndicatorICAO"})
                center = decimal_coordinate(text)
                radius_match = re.search(r"(?:RADIUS|WI)\s+([0-9]+(?:\.[0-9]+)?)\s*NM", text.upper())
                schedule_match = re.search(r"\bD\)\s*(.*?)(?=\n\s*[E-Z]\)|$)", text.upper(), re.DOTALL)
                output.append({
                    "id": identifier,
                    "domesticID": mapped.get("domestic_id") or None,
                    "category": category(text, location),
                    "locationCode": location,
                    "center": center,
                    "radiusNM": float(radius_match.group(1)) if radius_match else None,
                    "polygon": polygon_from_node(node),
                    "lowerLimit": first_text(node, {"lowerLimit"}),
                    "upperLimit": first_text(node, {"upperLimit"}),
                    "startsAt": date_from_notam_field(text, "B"),
                    "endsAt": date_from_notam_field(text, "C"),
                    "schedule": " ".join(schedule_match.group(1).split()) if schedule_match else None,
                    "text": text,
                })
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("Server/notam/downloads"))
    parser.add_argument("--output", type=Path, default=Path("public/v1/notams.json"))
    args = parser.parse_args()
    mapping_path = args.input / "notam_mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8")) if mapping_path.exists() else {}
    by_id = {}
    for zip_path in sorted(args.input.glob("Notam_Batch_*.zip")):
        for feature in features_from_zip(zip_path, mapping):
            by_id[feature["id"]] = feature
    feed = {
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": sorted(by_id.values(), key=lambda item: item["id"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(by_id)} NOTAM records to {args.output}")


if __name__ == "__main__":
    main()

