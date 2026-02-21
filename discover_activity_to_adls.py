#!/usr/bin/env python3
"""Automate Discover activity CSV download and upload it to ADLS.

Requirements:
  pip install playwright python-dotenv azure-storage-file-datalake
  playwright install chromium

Expected .env keys:
  DISCOVER_USERNAME=...
  DISCOVER_PASSWORD=...
  ADLS_CONNECTION_STRING=...
  ADLS_FILE_SYSTEM=...

Optional .env keys:
  ADLS_DIRECTORY=optional/path
  ADLS_TARGET_FILENAME=optional.csv
  DISCOVER_HEADLESS=false
  DISCOVER_TIMEOUT_MS=60000
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from azure.storage.filedatalake import DataLakeServiceClient
from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

DISCOVER_URL = "https://www.discover.com/credit-cards/"


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def click_login_entry(page, timeout_ms: int) -> None:
    # Try common variants on Discover's top navigation.
    for role in ("button", "link"):
        locator = page.get_by_role(role, name="Log In")
        if locator.count() > 0:
            locator.first.click(timeout=timeout_ms)
            return
    raise RuntimeError('Could not find top-level "Log In" control.')


def click_login_submit(page, timeout_ms: int) -> None:
    button = page.get_by_role("button", name="Log In")
    count = button.count()
    if count == 0:
        raise RuntimeError('Could not find login submit button labeled "Log In".')
    # Usually second "Log In" control is the form submit; fallback to first.
    index = 1 if count > 1 else 0
    button.nth(index).click(timeout=timeout_ms)


def download_activity_csv(download_dir: Path, *, headless: bool, timeout_ms: int) -> Path:
    username = require_env("DISCOVER_USERNAME")
    password = require_env("DISCOVER_PASSWORD")

    download_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        try:
            page.goto(DISCOVER_URL, wait_until="domcontentloaded")
            click_login_entry(page, timeout_ms)

            page.get_by_label("User ID").fill(username, timeout=timeout_ms)
            page.get_by_label("Password").fill(password, timeout=timeout_ms)
            click_login_submit(page, timeout_ms)

            page.get_by_role("link", name="View Activity & Statements").click(timeout=timeout_ms)
            page.get_by_role("link", name="Download").click(timeout=timeout_ms)
            page.get_by_role("radio", name="CSV").check(timeout=timeout_ms)

            with page.expect_download(timeout=timeout_ms) as download_info:
                page.get_by_role("button", name="Download").click(timeout=timeout_ms)

            download = download_info.value
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            default_name = f"discover_activity_{timestamp}.csv"
            filename = os.getenv("ADLS_TARGET_FILENAME", default_name)
            path = download_dir / filename
            download.save_as(str(path))
            return path

        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                "Timed out while interacting with Discover. The site may require MFA/CAPTCHA "
                "or selectors may have changed."
            ) from exc
        finally:
            context.close()
            browser.close()


def upload_to_adls(local_path: Path) -> str:
    connection_string = require_env("ADLS_CONNECTION_STRING")
    file_system_name = require_env("ADLS_FILE_SYSTEM")
    adls_directory = os.getenv("ADLS_DIRECTORY", "").strip("/")

    service_client = DataLakeServiceClient.from_connection_string(connection_string)
    fs_client = service_client.get_file_system_client(file_system=file_system_name)

    remote_name = local_path.name
    remote_path = f"{adls_directory}/{remote_name}" if adls_directory else remote_name

    file_client = fs_client.get_file_client(remote_path)
    with local_path.open("rb") as handle:
        data = handle.read()

    # Overwrite safely if file already exists.
    file_client.upload_data(data, overwrite=True)
    return remote_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--download-dir",
        default="downloads",
        help="Local directory to store the CSV download (default: downloads)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (overrides DISCOVER_HEADLESS in .env)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=None,
        help="Action timeout in milliseconds (overrides DISCOVER_TIMEOUT_MS in .env)",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    headless = args.headless or env_bool("DISCOVER_HEADLESS", default=False)
    timeout_ms = args.timeout_ms or int(os.getenv("DISCOVER_TIMEOUT_MS", "60000"))
    download_dir = Path(args.download_dir)

    print("Downloading Discover activity CSV...")
    csv_path = download_activity_csv(download_dir, headless=headless, timeout_ms=timeout_ms)
    print(f"Saved download to {csv_path}")

    print("Uploading CSV to ADLS...")
    remote_path = upload_to_adls(csv_path)
    print(f"Uploaded to ADLS path: {remote_path}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
