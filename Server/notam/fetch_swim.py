#!/usr/bin/env python3
"""Fetch Japan SWIM NOTAM ZIP files without embedding credentials.

The selectors follow the supplied NotamAcquisition.py workflow. SWIM is an
interactive service, so selectors and account permissions must be revalidated
whenever its UI changes.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


AIRPORT_GROUPS = [
    ["RJJJ_ENROUTE"],
    ["RJJJ_OTHER"],
    ["RJCC", "RJCJ", "RJCO", "RJCB", "RJCM", "RJCN", "RJCK", "RJCW", "RJCH", "RJEC", "RJEB", "RJEO", "RJER", "RJCR", "RJCT"],
    ["RJAA", "RJSA", "RJSS", "RJSN", "RJSI", "RJSR", "RJSC", "RJSK", "RJSM", "RJSF", "RJSY", "RJSD", "RJST", "RJSU", "RJSH", "RJSO", "RJSP"],
    ["RJTT", "RJTI", "RJTF", "RJTC", "RJTK", "RJTE", "RJTA", "RJTY", "RJTJ", "RJTL", "RJTU", "RJTS", "RJAH", "RJAK", "RJAM", "RJAW", "RJAF", "RJTO", "RJTH", "RJTQ"],
    ["RJGG", "RJNA", "RJNK", "RJNW", "RJNT", "RJNG", "RJNH", "RJNY", "RJOE", "RJNF", "RJAN", "RJAZ", "RJTR", "RJBB", "RJOO", "RJBE", "RJOY", "RJBD", "RJBM", "RJBT"],
    ["RJBK", "RJDC", "RJOA", "RJOB", "RJOC", "RJOF", "RJOH", "RJOI", "RJOK", "RJOM"],
    ["RJOT", "RJOS", "RJOR", "RJOP", "RJOZ", "RJOW", "RJNO", "RJFF", "RJFE", "RJFG"],
    ["RJFK", "RJFM", "RJFN", "RJFO", "RJFR", "RJFS", "RJFT", "RJFU", "RJFY", "RJFA"],
    ["RJFC", "RJFZ", "RJFQ", "RJDK", "RJDA", "RJDO", "RJDB", "RJDT", "ROAH", "ROKD"],
    ["ROTM", "ROAD", "ROIG", "ROIT", "ROKJ", "ROMD", "ROKT", "RORS", "RORH"],
    ["ROKR", "ROYN", "ROMY", "RORE", "RJKA", "RJKB", "RJKI", "RJKN", "RORY"],
]

MAX_LOCATION_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5


class NoDataAvailable(Exception):
    """SWIM reported that the current search has no matching NOTAM."""


class DownloadLimitExceeded(Exception):
    """SWIM refused a bulk download because it exceeded the result limit."""


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


def dismiss_ok_dialog(page: Page) -> None:
    try:
        ok_button = page.locator("button:has-text('OK')").last
        if ok_button.count() and ok_button.is_visible():
            ok_button.click(force=True)
            return
    except Exception:
        pass
    page.keyboard.press("Enter")


def confirm_download_dialog(page: Page) -> None:
    dialog = page.locator("mat-dialog-container:visible, [role='dialog']:visible").last
    if not dialog.count() or not dialog.is_visible():
        return
    for label in ("ダウンロード", "実行", "はい", "OK"):
        button = dialog.get_by_text(label, exact=True).last
        if button.count() and button.is_visible() and button.is_enabled():
            button.click(force=True)
            return


def recover_search_page(page: Page, search_url: str) -> None:
    """Reload a clean SWIM search form after a transient UI failure."""
    page.goto(search_url, wait_until="domcontentloaded", timeout=60_000)
    page.locator("input.mat-chip-input").nth(2).wait_for(
        state="visible", timeout=60_000
    )


def process_group_with_retries(
    page: Page,
    group: list[str],
    output_dir: Path,
    batch: int,
    search_url: str,
    attempts: int,
) -> dict | None:
    """Retry transient SWIM timeouts without publishing a partial feed."""
    for attempt in range(1, attempts + 1):
        try:
            return process_group(page, group, output_dir, batch)
        except (PlaywrightTimeoutError, RuntimeError):
            if attempt >= attempts:
                raise
            print(
                f"Transient SWIM failure for {' '.join(group)} "
                f"(attempt {attempt}/{attempts}); retrying with a fresh page."
            )
            time.sleep(RETRY_DELAY_SECONDS * attempt)
            recover_search_page(page, search_url)

    raise AssertionError("retry loop exited unexpectedly")


def process_group(page: Page, group: list[str], output_dir: Path, batch: int) -> dict | None:
    print(f"Processing batch {batch}: {' '.join(group)}")
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

    # The current SWIM guide specifies that bulk XML is downloaded directly
    # from the search-condition form.  The download button must be pressed
    # before Search; buttons shown in the results area are per-NOTAM GML
    # downloads and follow a different workflow.
    download_button = page.locator("button").filter(
        has_text=re.compile(r"^\s*ダウンロード\s*$")
    ).first
    download_button.wait_for(state="visible", timeout=60_000)
    if not download_button.is_enabled():
        raise RuntimeError(f"XML download action is disabled for batch {batch}")

    zip_payloads: list[bytes] = []
    downloads = []

    def capture_zip_response(response) -> None:
        content_type = response.headers.get("content-type", "").lower()
        disposition = response.headers.get("content-disposition", "").lower()
        try:
            payload = response.body()
            if (
                "zip" in content_type
                or "attachment" in disposition
                or ".zip" in disposition
            ) and payload.startswith(b"PK"):
                zip_payloads.append(payload)
                return
            if "json" in content_type and "download" in response.url.lower():
                document = json.loads(payload)
                encoded = document.get("datas", {}).get("fileData")
                if encoded:
                    decoded = base64.b64decode(encoded)
                    if decoded.startswith(b"PK"):
                        zip_payloads.append(decoded)
        except Exception:
            pass

    def capture_download(download) -> None:
        downloads.append(download)

    page.on("response", capture_zip_response)
    page.on("download", capture_download)
    try:
        download_button.click(force=True)
        deadline = time.monotonic() + 120
        while not downloads and not zip_payloads:
            if page.locator("text='IFUV000M8011'").is_visible() or page.locator("text='検索結果がありません'").is_visible():
                dismiss_ok_dialog(page)
                raise NoDataAvailable
            if page.locator("text='WFUV000M8015'").is_visible() or page.locator("text='上限の1000件'").is_visible():
                dismiss_ok_dialog(page)
                raise DownloadLimitExceeded
            confirm_download_dialog(page)
            if time.monotonic() >= deadline:
                raise PlaywrightTimeoutError(
                    f"No ZIP download or authenticated ZIP response for batch {batch}"
                )
            time.sleep(0.5)

        destination = output_dir / f"Notam_Batch_{batch}.zip"
        if downloads:
            downloads[-1].save_as(destination)
        else:
            destination.write_bytes(zip_payloads[-1])
            print(f"Captured batch {batch} from the authenticated ZIP response.")
    except NoDataAvailable:
        print(f"No current NOTAM data for batch {batch}; skipping it.")
        return None
    except PlaywrightTimeoutError:
        if zip_payloads:
            (output_dir / f"Notam_Batch_{batch}.zip").write_bytes(zip_payloads[-1])
            print(f"Captured batch {batch} from the authenticated ZIP response.")
        else:
            dismiss_ok_dialog(page)
            print(
                f"Download event timed out for batch {batch}; "
                f"button_visible={download_button.is_visible()} "
                f"button_enabled={download_button.is_enabled()}"
            )
            raise
    finally:
        page.remove_listener("response", capture_zip_response)
        page.remove_listener("download", capture_download)

    # Search is a separate action used here only to build the legacy text-ID
    # mapping consumed by the downstream converter.
    page.get_by_text("検索", exact=True).first.click(force=True)
    search_deadline = time.monotonic() + 120
    while time.monotonic() < search_deadline:
        if page.locator("text='IFUV000M8011'").is_visible() or page.locator("text='検索結果がありません'").is_visible():
            dismiss_ok_dialog(page)
            return {}
        if page.locator("text='WFUV000M8015'").is_visible() or page.locator("text='上限の1000件'").is_visible():
            print("Search reached the 1000-item limit; continuing with the available results.")
            dismiss_ok_dialog(page)
            time.sleep(2)
        if page.get_by_text("リスト", exact=True).count():
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"Search results did not become available for batch {batch}")

    page.click("text='リスト'", force=True)
    search_url = page.url
    page.locator("text=/印刷.*全件/").first.click(force=True, timeout=30_000)
    time.sleep(5)
    all_text = ""
    for frame in page.frames:
        try:
            all_text += "\n" + frame.locator("body").inner_text()
        except Exception:
            pass
    page.goto(search_url, wait_until="domcontentloaded", timeout=60_000)
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
        service_url = service_page.url

        mapping = {}
        failed_codes: list[str] = []
        batch = 1
        for group in AIRPORT_GROUPS:
            try:
                result = process_group_with_retries(
                    service_page,
                    group,
                    args.output,
                    batch,
                    service_url,
                    MAX_LOCATION_ATTEMPTS if len(group) == 1 else 1,
                )
            except (PlaywrightTimeoutError, DownloadLimitExceeded, RuntimeError):
                downloaded = args.output / f"Notam_Batch_{batch}.zip"
                if downloaded.exists():
                    print(
                        f"Batch {batch} XML was downloaded before the text-list step failed; "
                        "keeping the XML with an empty domestic-ID mapping."
                    )
                    batch += 1
                    continue
                if len(group) == 1:
                    failed_codes.extend(group)
                    print(f"Giving up this cycle for {' '.join(group)}; continuing with other locations.")
                    try:
                        recover_search_page(service_page, service_url)
                    except Exception:
                        pass
                    continue
                recover_search_page(service_page, service_url)
                print(
                    "Bulk download was not generated; retrying this group "
                    "one location at a time."
                )
                for code in group:
                    try:
                        individual = process_group_with_retries(
                            service_page,
                            [code],
                            args.output,
                            batch,
                            service_url,
                            MAX_LOCATION_ATTEMPTS,
                        )
                    except (PlaywrightTimeoutError, DownloadLimitExceeded, RuntimeError):
                        downloaded = args.output / f"Notam_Batch_{batch}.zip"
                        if downloaded.exists():
                            print(
                                f"Keeping downloaded XML for {code}; its domestic-ID text mapping "
                                "was not available."
                            )
                            batch += 1
                        else:
                            failed_codes.append(code)
                            print(f"Giving up this cycle for {code}; continuing with other locations.")
                        try:
                            recover_search_page(service_page, service_url)
                        except Exception:
                            pass
                        continue
                    if individual is not None:
                        mapping.update(individual)
                        batch += 1
            else:
                if result is not None:
                    mapping.update(result)
                    batch += 1

        zip_count = len(list(args.output.glob("Notam_Batch_*.zip")))
        (args.output / "notam_mapping.json").write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (args.output / "fetch_status.json").write_text(
            json.dumps(
                {
                    "downloadedBatches": zip_count,
                    "failedCodes": failed_codes,
                    "complete": not failed_codes,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        browser.close()
    if zip_count == 0:
        raise RuntimeError("SWIM returned no usable XML batches")
    print(
        f"Fetched {zip_count} XML batches and {len(mapping)} NOTAM text records "
        f"into {args.output}; failed locations: {', '.join(failed_codes) or 'none'}"
    )


if __name__ == "__main__":
    main()
