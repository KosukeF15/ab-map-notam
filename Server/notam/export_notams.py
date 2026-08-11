#!/usr/bin/env python3
"""Convert Japan SWIM Digital NOTAM XML into the native AB MAP feed.

The geometry and field classification intentionally mirror NotamMapVer8.5.py:
polygons, line areas, explicit circles and position points are preserved instead
of reducing every NOTAM to its Q-line centre and radius.
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree


COORDINATE_PATTERN = re.compile(
    r"(\d{4,6})(\.\d+)?\s*([NS])\s*(\d{5,7})(\.\d+)?\s*([EW])",
    re.IGNORECASE,
)
COORDINATE_TEXT_PATTERN = re.compile(
    r"\d{4,6}(?:\.\d+)?\s*[NS]\s*\d{5,7}(?:\.\d+)?\s*[EW]",
    re.IGNORECASE,
)
RADIUS_PATTERN = re.compile(
    r"\b(?:RADIUS|RAD|WI|WITHIN|CIRCLE)\s*(?:A\s+RADIUS\s+OF\s*)?(?:OF\s*)?"
    r"(\d+(?:\.\d+)?)\s*(NM|M|KM)\b|"
    r"\b(\d+(?:\.\d+)?)\s*(NM|M|KM)\s*(?:RADIUS|RAD)\b",
    re.IGNORECASE,
)

TRANSLATIONS = {
    "reason": {
        "DUE TO MAINT": "維持工事・作業のため", "DUE TO CONST": "工事のため",
        "DUE TO SN REMOVAL": "除雪のため", "DUE TO SN": "積雪のため",
        "DUE TO ENG RUNUP": "エンジン試運転のため", "DUE TO EXER": "訓練のため",
        "DUE TO SURVEY": "調査のため", "DUE TO EVENT": "行事のため",
        "DUE TO DISABLED ACFT": "航行不能航空機のため", "DUE TO SWEEPING": "スウィーピング作業のため",
        "DUE TO OIL LEAK": "オイルリークのため", "DUE TO REPAIR": "緊急補修のため",
        "DUE TO RWY CK": "滑走路点検のため", "DUE TO TYPH": "台風のため",
        "DUE TO FLOOD": "冠水のため", "DUE TO TROUBLE": "トラブルのため",
        "DUE TO OBST": "障害物のため", "DUE TO FLTCK": "飛行検査のため",
        "DUE TO BLIZZARD": "吹雪のため",
    },
    "obstacle": {
        "CRANE": "クレーン", "TREE": "樹木", "CRANE SHIP": "クレーン船",
        "SHIP": "船舶", "POLE": "ポール", "BUILDING": "建物", "FENCE": "フェンス",
        "MONUMENT": "モニュメント", "TOWER": "タワー", "ANTENNA": "アンテナ",
        "TETHERED BALLOON": "係留気球", "STACK": "煙突", "SIGN": "看板",
        "BRIDGE": "橋梁", "NATURAL HIGHPOINT": "(地形上の)最高地点", "TANK": "タンク",
        "WINDMILL": "風力発電機", "POWER LINE TOWER": "送電線鉄塔",
        "TRANSMISSION LINE": "送電線",
    },
    "activity": {
        "AIRSHOW": "展示飛行", "AEROBATICS": "曲技飛行", "CAPTIVE BALLOON": "係留気球",
        "KITE": "凧", "BOMB DISPOSAL": "不発弾処理", "SAR TRAINING": "捜索救難訓練",
        "UNLIGHTED ACFT FLT EXERCISE": "無灯火飛行", "CARGO DROP": "カーゴドロップ",
        "SIGNAL FLARE": "信号弾の打ち上げ", "GLIDING": "グライダー", "FIREWORK": "花火の打ち上げ",
        "HOT AIR BALLOON": "熱気球", "BALLOON": "無人自由気球", "ROCKET": "ロケット",
        "FIRING": "射撃", "SPACE FLIGHT": "宇宙飛行活動", "PARACHUTE": "落下傘降下",
        "PARAGLIDER": "パラグライダー", "HANGGLIDING": "ハンググライダー",
        "RADIO ACTIVE CLOUD": "放射性雲", "UAV": "無人航空機", "GROUP FLT": "集団飛行",
        "VOLCANO": "火山活動", "MODEL ACFT": "模型航空機", "MODEL ROCKET": "模型ロケット",
        "ALTRV": "アルトラブ", "JSDF ACT": "自衛隊訓練",
    },
    "lights": {
        "LGT INSTL": "航空障害灯", "LGT AND DAY INSTL": "航空障害灯及び昼間障害標識",
        "LGT AND RED INSTL": "航空障害灯及び赤旗", "DAY INSTL": "昼間障害標識",
        "RED INSTL": "赤旗", "NOT INSTL": "設置なし",
    },
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def first_text(node, names: set[str]) -> str | None:
    for child in node.iter():
        if local_name(child.tag) in names and child.text and child.text.strip():
            return child.text.strip()
    return None


def all_text(node, name: str) -> list[str]:
    return [child.text.strip() for child in node.iter() if local_name(child.tag) == name and child.text and child.text.strip()]


def decimal_coordinate(value: str | None):
    if not value:
        return None
    match = COORDINATE_PATTERN.search(value.replace(" ", "").upper())
    if not match:
        return None
    lat_digits, lat_decimal, lat_direction, lon_digits, lon_decimal, lon_direction = match.groups()
    if len(lat_digits) == 4:
        latitude = int(lat_digits[:2]) + float(lat_digits[2:] + (lat_decimal or "")) / 60
    elif len(lat_digits) == 6:
        latitude = int(lat_digits[:2]) + int(lat_digits[2:4]) / 60 + float(lat_digits[4:] + (lat_decimal or "")) / 3600
    else:
        return None
    if len(lon_digits) == 5:
        longitude = int(lon_digits[:3]) + float(lon_digits[3:] + (lon_decimal or "")) / 60
    elif len(lon_digits) == 7:
        longitude = int(lon_digits[:3]) + int(lon_digits[3:5]) / 60 + float(lon_digits[5:] + (lon_decimal or "")) / 3600
    else:
        return None
    if lat_direction.upper() == "S":
        latitude *= -1
    if lon_direction.upper() == "W":
        longitude *= -1
    return {"latitude": latitude, "longitude": longitude}


def unique_coordinates(items: list[dict]) -> list[dict]:
    output = []
    for item in items:
        if not any(abs(item["latitude"] - seen["latitude"]) < 0.0001 and abs(item["longitude"] - seen["longitude"]) < 0.0001 for seen in output):
            output.append(item)
    return output


def radius_nm(match: re.Match) -> float:
    value = float(match.group(1) or match.group(3))
    unit = (match.group(2) or match.group(4)).upper()
    if unit == "M":
        return value / 1852
    if unit == "KM":
        return value / 1.852
    return value


def notam_field(text: str, field: str) -> str | None:
    match = re.search(rf"\b{field}\)\s*(.*?)(?=\n\s*[A-Z]\)|\Z)", text, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else None


def notam_date(text: str, field: str):
    match = re.search(rf"\b{field}\)\s*(\d{{10}})", text.upper())
    if not match:
        return None
    value = match.group(1)
    try:
        moment = datetime(2000 + int(value[0:2]), int(value[2:4]), int(value[4:6]), int(value[6:8]), int(value[8:10]), tzinfo=timezone.utc)
        return moment
    except ValueError:
        return None


def xml_date(value: str | None):
    if not value or "PERM" in value.upper():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def period_jst(start: datetime | None, end: datetime | None, raw_end: str | None) -> str | None:
    if not start:
        return None
    jst = timezone(timedelta(hours=9))
    start_text = start.astimezone(jst).strftime("%Y/%m/%d %H:%M")
    if raw_end and ("PERM" in raw_end.upper() or "EST" in raw_end.upper()):
        end_text = "EST" if "EST" in raw_end.upper() else "PERM"
    elif end:
        end_text = end.astimezone(jst).strftime("%Y/%m/%d %H:%M")
    else:
        end_text = "Unknown"
    return f"{start_text} - {end_text} (JST)"


def d_item_jst(value: str | None) -> str | None:
    if not value:
        return None
    def replace(match: re.Match) -> str:
        first, second = match.group(1), match.group(2)
        return f"{(int(first[:2]) + 9) % 24:02d}:{first[2:]}-{(int(second[:2]) + 9) % 24:02d}:{second[2:]}"
    converted = re.sub(r"(\d{4})\s*/\s*(?:[A-Z]{3}\s*)?(\d{4})", replace, " ".join(value.split()))
    return converted + " (JST)" if converted != " ".join(value.split()) else converted


def category(target: str) -> str:
    if target == "RJJJ":
        return "rjjj"
    if target == "RJDR":
        return "rjdr"
    if target == "ROBL":
        return "robl"
    if target == "RJTD":
        return "rjtd"
    if target in {"RJJW", "RJJX", "RJJY", "RJJZ"}:
        return "rjjwxyz"
    return "airport"


def extracted_info(text: str) -> dict:
    upper = text.upper()
    status = None
    for pattern, value in [
        (r"\b(CLSD|CLOSED)\b", "閉鎖"), (r"\b(LIM|LIMITED)\b", "制限"),
        (r"\b(PARTLY UNSERVICEABLE|PARTLY U/S|PARTIAL)\b", "一部機能停止"),
        (r"\b(U/S|UNS|UNSERVICEABLE)\b", "機能停止"), (r"\bONTEST\b", "試験電波発射"),
        (r"\b(ACT|ACTIVE)\b", "使用中"),
    ]:
        if re.search(pattern, upper):
            status = value
            break
    reason = None
    match = re.search(r"(DUE TO\s+[A-Z\s]+?)(?=\n|,|\.|RMK|EXC|$)", upper)
    if match:
        raw = match.group(1).strip()
        reason = next((value for key, value in TRANSLATIONS["reason"].items() if key in raw), raw)
    usage_match = re.search(r"\bEXC\b\s*(.*?)(?=\n|\.|RMK|$)", upper)
    type_match = re.search(r"TYPE\s*:\s*([A-Z\s_]+?)(?=\n|,|\.|$)", upper)
    obstacle = None
    if type_match:
        raw = type_match.group(1).strip().replace("OTHER:", "").replace("OTHER :", "").strip()
        obstacle = TRANSLATIONS["obstacle"].get(raw, raw)
    activity = next((value for key, value in TRANSLATIONS["activity"].items() if re.search(r"\b" + re.escape(key).replace(r"\ ", r"[\s_]+") + r"\b", upper)), None)
    lights = next((value for key, value in TRANSLATIONS["lights"].items() if re.search(r"\b" + re.escape(key) + r"\b", upper)), None)
    return {
        "status": status, "reason": reason,
        "usage": "EXC " + usage_match.group(1).strip() if usage_match else None,
        "obstacleType": obstacle, "activity": activity, "obstacleLights": lights,
    }


def q_line(text: str):
    match = re.search(
        r"Q\)\s*([A-Z]{4})/([A-Z]{5})/[^/]+/[^/]+/([A-Z]+)/[^/]+/[^/]+/"
        r"(\d{4,6}[NS]\d{5,7}[EW])(\d{3})",
        text.upper(),
    )
    if not match:
        return None
    return {"fir": match.group(1), "qcode": match.group(2), "scope": match.group(3), "center": decimal_coordinate(match.group(4)), "radius": float(match.group(5))}


def geometry(node, text: str, q: dict | None):
    upper = text.upper()
    body = re.sub(r"Q\).*?(?:\n|$)", "", upper)
    polygons: list[list[dict]] = []
    lines: list[list[dict]] = []
    circles: list[dict] = []
    points: list[dict] = []

    for raw in all_text(node, "posList"):
        try:
            values = [float(item) for item in raw.split()]
        except ValueError:
            continue
        if len(values) >= 6 and len(values) % 2 == 0:
            polygon = []
            for index in range(0, len(values), 2):
                first, second = values[index], values[index + 1]
                latitude, longitude = (first, second) if abs(first) <= 90 else (second, first)
                polygon.append({"latitude": latitude, "longitude": longitude})
            polygons.append(polygon)

    for match in re.finditer(r"(?:BOUNDED BY|WI THE COORD AS FLW|WI THE COORDS AS FLW)(.*?)(?=\n\s*[A-Z0-9.\-]+:|\n\s*\n|\(|F\)|$)", body, re.DOTALL):
        block = re.split(r"(?:TO POINT OF ORIGIN|TO ORIGIN|TO BEG|EXCLUDING|EXCEPT|EXC\b|ATC WILL NOT|PORTION|RMK|THE LINE CONNECTING)", match.group(1))[0]
        polygon = [decimal_coordinate(item.group(0)) for item in COORDINATE_TEXT_PATTERN.finditer(block)]
        polygon = [item for item in polygon if item]
        if len(polygon) >= 3:
            polygons.append(unique_coordinates(polygon))

    # Preserve a complete ordered polyline when LINE CONNECTING contains more
    # than two positions.  The older pair-only expression kept the first leg
    # and downgraded every later vertex to an unrelated point icon.
    for match in re.finditer(
        r"(?:LINE\s+CONNECTING|EITHER\s+SIDE\s+OF\s+A\s+LINE)\s*(.{0,1200}?)(?=\n\s*(?:PSN|POSITION|COORD)\b|\n\s*\n|\n\s*[F-Z]\)|$)",
        body,
        re.DOTALL,
    ):
        vertices = [
            coordinate
            for coordinate_match in COORDINATE_TEXT_PATTERN.finditer(match.group(1))
            if (coordinate := decimal_coordinate(coordinate_match.group(0)))
        ]
        vertices = unique_coordinates(vertices)
        if len(vertices) >= 2:
            lines.append(vertices)

    for match in re.finditer(r"(?:LINE|LINE CONNECTING|EITHER SIDE OF A LINE|BETWEEN)\s*(?:POINT\s*)?(\d{4,6}(?:\.\d+)?\s*[NS]\s*\d{5,7}(?:\.\d+)?\s*[EW])\s*(?:-|TO|AND)\s*(?:POINT\s*)?(\d{4,6}(?:\.\d+)?\s*[NS]\s*\d{5,7}(?:\.\d+)?\s*[EW])", body):
        first, second = decimal_coordinate(match.group(1)), decimal_coordinate(match.group(2))
        if first and second:
            lines.append([first, second])

    unique_lines = []
    for line in lines:
        key = tuple((round(point["latitude"], 5), round(point["longitude"], 5)) for point in line)
        if not any(
            key == tuple((round(point["latitude"], 5), round(point["longitude"], 5)) for point in existing)
            or (len(line) == 2 and all(point in existing for point in line))
            for existing in unique_lines
        ):
            unique_lines.append(line)
    lines = unique_lines

    lines = [
        line for line in lines
        if not any(
            all(
                any(abs(point["latitude"] - vertex["latitude"]) < 0.0001 and abs(point["longitude"] - vertex["longitude"]) < 0.0001 for vertex in polygon)
                for point in line
            )
            for polygon in polygons
        )
    ]

    without_polygons = re.sub(r"(?:BOUNDED BY|WI THE COORD AS FLW|WI THE COORDS AS FLW).*?(?=\n\s*[A-Z0-9.\-]+:|\n\s*\n|\(|F\)|$)", "", body, flags=re.DOTALL)
    psn_points = unique_coordinates([
        coordinate
        for match in re.finditer(r"(?:PSN|POSITION|COORD)\s*[:\s]*([0-9.\s]+[NS]\s*[0-9.\s]+[EW])", without_polygons)
        if (coordinate := decimal_coordinate(match.group(1)))
    ])
    center = q.get("center") if q else None
    for coordinate_match in COORDINATE_TEXT_PATTERN.finditer(without_polygons):
        coordinate = decimal_coordinate(coordinate_match.group(0))
        if not coordinate:
            continue
        context = without_polygons[max(0, coordinate_match.start() - 50):min(len(without_polygons), coordinate_match.end() + 50)]
        nearby = list(RADIUS_PATTERN.finditer(context))
        if nearby:
            selected = min(nearby, key=lambda item: abs(((item.start() + item.end()) / 2) - (coordinate_match.start() - max(0, coordinate_match.start() - 50))))
            circles.append({"center": coordinate, "radiusNM": radius_nm(selected)})
        elif not center or abs(center["latitude"] - coordinate["latitude"]) > 0.0001 or abs(center["longitude"] - coordinate["longitude"]) > 0.0001:
            points.append(coordinate)

    circles_by_key = {}
    for circle in circles:
        key = (round(circle["center"]["latitude"], 5), round(circle["center"]["longitude"], 5), round(circle["radiusNM"], 3))
        circles_by_key[key] = circle
    circles = list(circles_by_key.values())
    structural_points = (
        [point for polygon in polygons for point in polygon]
        + [point for line in lines for point in line]
        + [circle["center"] for circle in circles]
    )
    points = [
        point for point in unique_coordinates(psn_points + points)
        if not any(
            abs(point["latitude"] - structural_point["latitude"]) < 0.0001
            and abs(point["longitude"] - structural_point["longitude"]) < 0.0001
            for structural_point in structural_points
        )
    ]

    explicit = RADIUS_PATTERN.search(body)
    if not polygons and not lines and not circles and explicit and center:
        circles.append({"center": center, "radiusNM": radius_nm(explicit)})
    area_subjects = {"RA", "RD", "RM", "RO", "RP", "RR", "RT", "AC", "AD", "AH", "AL", "AN", "AR", "AT", "AW", "AZ", "WA", "WE", "WF", "WM", "WP", "WV"}
    subject = q["qcode"][:2] if q else ""
    if not polygons and not lines and not circles and q and q["radius"] > 0 and subject in area_subjects and center:
        circles.append({"center": center, "radiusNM": q["radius"]})

    # A single NOTAM may legitimately contain several independent positions,
    # or a mixture of points, lines, circles and polygons.  The web map drew
    # the first PSN coordinate separately from the remaining coordinates, but
    # the JSON exporter used to return early here and silently discard those
    # remaining shapes.  Keep every deduplicated, non-structural point so the
    # native map can render the complete NOTAM.
    return polygons, lines, circles, points


def features_from_zip(zip_path: Path, mapping: dict, airports: dict[str, dict]) -> list[dict]:
    output = []
    with zipfile.ZipFile(zip_path) as archive:
        for filename in archive.namelist():
            if not filename.lower().endswith(".xml"):
                continue
            try:
                root = ElementTree.fromstring(archive.read(filename))
            except ElementTree.ParseError:
                continue
            for node in root.iter():
                if local_name(node.tag) != "NOTAM":
                    continue
                series = first_text(node, {"series"}) or ""
                number = first_text(node, {"number"}) or ""
                year = (first_text(node, {"year"}) or "")[-2:]
                international_id = f"{series}{number}/{year}"
                mapped = mapping.get(international_id, {})
                if not isinstance(mapped, dict):
                    mapped = {"domestic_id": str(mapped)}
                domestic_id = mapped.get("domestic_id") or None
                xml_text = first_text(node, {"text"}) or ""
                text = mapped.get("swim_text") or xml_text
                location = first_text(node, {"location", "locationIndicatorICAO"})
                target = (domestic_id or "")[:4] or location or ""
                if not target:
                    match = re.search(r"^([A-Z]{4})\b", text.strip().upper())
                    target = match.group(1) if match else ""
                q = q_line(text)
                center = q.get("center") if q else decimal_coordinate(first_text(node, {"coordinates"}))
                if not center and location in airports:
                    center = airports[location]
                polygons, lines, circles, points = geometry(node, text, q)
                if not polygons and not lines and not circles and not points and center:
                    points = [center]

                item_b = notam_date(text, "B")
                raw_c = notam_field(text, "C")
                item_c = notam_date(text, "C")
                xml_start = first_text(node, {"start", "beginPosition"})
                xml_end = first_text(node, {"end", "endPosition"})
                starts_at = item_b or xml_date(xml_start)
                ends_at = item_c or xml_date(xml_end)
                issued_at = xml_date(first_text(node, {"issued"}))
                item_d = notam_field(text, "D")
                item_e = notam_field(text, "E")
                item_f = notam_field(text, "F") or first_text(node, {"lowerLimit"})
                item_g = notam_field(text, "G") or first_text(node, {"upperLimit"})
                lower = first_text(node, {"lowerLimit"}) or item_f or "SFC"
                upper = first_text(node, {"upperLimit"}) or item_g or "UNL"
                radius = max((item["radiusNM"] for item in circles), default=None)
                identifier = domestic_id or international_id
                output.append({
                    "id": identifier,
                    "domesticID": domestic_id,
                    "intlID": international_id if domestic_id else None,
                    "category": category(target),
                    "locationCode": location,
                    "center": center,
                    "radiusNM": radius,
                    "polygon": polygons[0] if polygons else [],
                    "polygons": polygons,
                    "lines": lines,
                    "circles": circles,
                    "points": points,
                    "lowerLimit": lower,
                    "upperLimit": upper,
                    "startsAt": iso(starts_at),
                    "endsAt": iso(ends_at),
                    "issuedAt": iso(issued_at),
                    "schedule": " ".join(item_d.split()) if item_d else None,
                    "periodJST": period_jst(starts_at, ends_at, raw_c or xml_end),
                    "itemD": d_item_jst(item_d),
                    "itemE": item_e or text,
                    "itemF": item_f or lower,
                    "itemG": item_g or upper,
                    "extractedInfo": extracted_info(text),
                    "text": text,
                })
    return output


def airport_coordinates(path: Path | None) -> dict[str, dict]:
    if not path or not path.exists():
        return {}
    output = {}
    for item in json.loads(path.read_text(encoding="utf-8")):
        if item.get("icao") and item.get("coordinate"):
            output[item["icao"]] = item["coordinate"]
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("Server/notam/downloads"))
    parser.add_argument("--output", type=Path, default=Path("public/v1/notams.json"))
    parser.add_argument("--airports", type=Path, default=Path("ABMap/Resources/Data/airports.json"))
    args = parser.parse_args()
    mapping_path = args.input / "notam_mapping.json"
    mapping = json.loads(mapping_path.read_text(encoding="utf-8")) if mapping_path.exists() else {}
    airports = airport_coordinates(args.airports)
    by_id = {}
    for zip_path in sorted(args.input.glob("Notam_Batch_*.zip")):
        for feature in features_from_zip(zip_path, mapping, airports):
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
