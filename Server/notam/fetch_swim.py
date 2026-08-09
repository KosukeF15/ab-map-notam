#!/usr/bin/env python3
"""Fetch Japan SWIM NOTAM ZIP files without embedding credentials.

The selectors follow the supplied NotamAcquisition.py workflow. SWIM is an
interactive service, so selectors and account permissions must be revalidated
whenever its UI changes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright


AIRPORT_GROUPS = [
    ["RJJJ_ENROUTE"],
    ["RJJJ_OTHER"],
    ["RJCC", "RJCJ", "RJCO", "RJCB", "RJCM", "RJCN", "RJCK", "RJCW", "RJCH", "RJEC", "RJEB", "RJEO", "RJER", "RJCR", "RJCT"],
    ["RJAA", "RJSA", "RJSS", "RJSN", "RJSI", "RJSR", "RJSC", "RJSK", "RJSM", "RJSF", "RJSY", "RJSD", "RJST", "RJSU", "RJSH", "RJSO", "RJSP"],
    ["RJTT", "RJTI", "RJTF", "RJTC", "RJTK", "RJTE", "RJTA", "RJTY", "RJTJ", "RJTL", "RJTU", "RJTS", "RJAH", "RJAK", "RJAM", "RJAW", "RJAF", "RJTO", "RJTH", "RJTQ"],
    ["RJGG", "RJNA", "RJNK", "RJNW", "RJNT", "RJNG", "RJNH", "RJNY", "RJOE", "RJNF", "RJAN", "RJAZ", "RJTR", "RJBB", "RJOO", "RJBE", "RJOY", "RJBD", "RJBM", "RJBT"],
    ["RJBK", "RJDC", "RJOA", "RJOB", "RJOC", "RJOF", "RJOH", "RJOI", "RJOK", "RJOM", "RJOT", "RJOS", "RJOR", "RJOP", "RJOZ", "RJOW", "RJNO", "RJFF", "RJFE", "RJFG"],
    ["RJFK", "RJFM", "RJFN", "RJFO", "RJFR", "RJFS", "RJFT", "RJFU", "RJFY", "RJFA", "RJFC", "RJFZ", "RJFQ", "RJDK", "RJDA", "RJDO", "RJDB", "RJDT", "ROAH", "ROKD"],
    ["ROTM", "ROAD", "ROIG", "ROIT", "ROKJ", "ROMD", "ROKT", "RORS", "RORH", "ROKR", "ROYN", "ROMY", "RORE", "RJKA", "RJKB", "RJKI", "RJKN", "RORY"],
]


def extract_notam_mapping(text: str) -> dict:
    mapping = {}
    for block in re.split(r"(?=\([A-Z]\d{4}/\d{2}\s+NOTAM[NRC])", text):
        block = block.strip()
        identifier = re.search(r"^\(([A-Z]\d{4}/\d{2})\s+NOTAM[NRC]", block)
        if not identifier:
            continue
        close_parenthesis = block.rfind(")")
        swim_text = block[: close_parenthesis + 1].strip() if close_parenthesis >= 0 else block
        after_text = block[close_parenthesis + 1 :] if close_parenthesis >= 0 else ""
        domestic = re.search(r"([A-Z]{4}\s+\d{1,4}/\d{2})", after_text)
        mapping[identifier.group(1)] = {
            "domestic_id": domestic.group(1) if domestic else "",
            "swim_text": swim_text,
            "special_data": None,
        }
    return mapping


def set_checkbox(page: Page, label: str, target_checked: bool) -> None:
    checkbox = page.locator(f"mat-checkbox:has-text('{label}')").first
    if checkbox.count() == 0:
        return
    classes = checkbox.get_attribute("class") or ""
    if ("mat-checkbox-checked" in classes) != target_checked:
        checkbox.click(force=True)


def configure_scope(page: Page, enroute_only: bool, other_only: bool) -> None:
    set_checkbox(page, "飛行場", not enroute_only)
    set_checkbox(page, "ワーニング", not enroute_only)
    set_checkbox(page, "エンルート", not other_only)


def process_group(page: Page, group: list[str], output_dir: Path, batch: int) -> dict | None:
    try:
        clear_button = page.locator("text='クリア'")
        if clear_button.is_visible():
            clear_button.click(force=True)
    except Exception:
        pass

    enroute_only = group == ["RJJJ_ENROUTE"]
    other_only = group == ["RJJJ_OTHER"]
    actual_codes = ["RJJJ" if code.startswith("RJJJ_") else code for code in group]
    location_input = page.locator("input.mat-chip-input").nth(2)
    location_input.fill(" ".join(actual_codes), force=True)
    page.keyboard.press("Enter")
    configure_scope(page, enroute_only, other_only)
    page.click("text='検索'", force=True)
    time.sleep(3)

    if page.locator("text='IFUV000M8011'").is_visible() or page.locator("text='検索結果がありません'").is_visible():
        try:
            page.click("button:has-text('OK')", force=True, timeout=3_000)
        except Exception:
            page.keyboard.press("Enter")
        return None

    with page.expect_download(timeout=60_000) as download_info:
        page.click("text='ダウンロード'", force=True)
    download_info.value.save_as(output_dir / f"Notam_Batch_{batch}.zip")

    page.click("text='リスト'", force=True)
    page.locator("text=/印刷.*全件/").first.click(force=True, timeout=30_000)
    time.sleep(5)
    all_text = ""
    for frame in page.frames:
        try:
            all_text += "\n" + frame.locator("body").inner_text()
        except Exception:
            pass
    page.click("text='戻る'", force=True)
    location_input.wait_for(state="visible", timeout=60_000)
    return extract_notam_mapping(all_text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("Server/notam/downloads"))
    args = parser.parse_args()
    user_id = os.environ.get("SWIM_USER_ID")
    password = os.environ.get("SWIM_PASSWORD")
    login_url = os.environ.get("SWIM_LOGIN_URL", "https://top.swim.mlit.go.jp/swim/login")
    if not user_id or not password:
        raise SystemExit("SWIM_USER_ID and SWIM_PASSWORD must be set as secret environment variables")

    args.output.mkdir(parents=True, exist_ok=True)
    for old_file in args.output.glob("Notam_Batch_*.zip"):
        old_file.unlink()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        context.set_default_timeout(300_000)
        login_page = context.new_page()
        login_page.goto(login_url, wait_until="domcontentloaded")
        login_page.fill("#swm-login-email", user_id)
        login_page.fill("#swm-login-password", password)
        login_page.click("text='ログイン'")
        target = login_page.locator("a:has-text('デジタルノータムリクエストサービス'), mat-list-item:has-text('デジタルノータムリクエストサービス')").first
        with login_page.expect_popup(timeout=60_000) as popup:
            target.click(force=True)
        service_page = popup.value
        service_page.wait_for_load_state("domcontentloaded")

        mapping = {}
        batch = 1
        for group in AIRPORT_GROUPS:
            result = process_group(service_page, group, args.output, batch)
            if result is None and len(group) > 1:
                for code in group:
                    individual = process_group(service_page, [code], args.output, batch)
                    if individual:
                        mapping.update(individual)
                        batch += 1
            elif result:
                mapping.update(result)
                batch += 1

        (args.output / "notam_mapping.json").write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        browser.close()
    print(f"Fetched {len(mapping)} NOTAM text records into {args.output}")


if __name__ == "__main__":
    main()

