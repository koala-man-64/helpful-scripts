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
  ADLS_DIRECTORY=optional/path
  ADLS_TARGET_FILENAME=optional.csv
"""

from __future__ import annotations

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


def download_activity_csv(download_dir: Path) -> Path:
    username = require_env("DISCOVER_USERNAME")
    password = require_env("DISCOVER_PASSWORD")

    download_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        try:
            page.goto(DISCOVER_URL, wait_until="domcontentloaded")

            # Top-left log in button in page header.
            page.get_by_role("button", name="Log In").first.click(timeout=20_000)

            page.get_by_label("User ID").fill(username, timeout=20_000)
            page.get_by_label("Password").fill(password, timeout=20_000)

            # Form submit button below textboxes.
            page.get_by_role("button", name="Log In").nth(1).click(timeout=20_000)

            page.get_by_role("link", name="View Activity & Statements").click(timeout=60_000)
            page.get_by_role("link", name="Download").click(timeout=60_000)

            page.get_by_role("radio", name="CSV").check(timeout=20_000)

            with page.expect_download(timeout=30_000) as download_info:
                page.get_by_role("button", name="Download").click(timeout=20_000)

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
    with local_path.open("rb") as f:
        data = f.read()

    file_client.create_file()
    file_client.append_data(data=data, offset=0, length=len(data))
    file_client.flush_data(len(data))

    return remote_path


def main() -> int:
    load_dotenv()

    download_dir = Path("downloads")

    print("Downloading Discover activity CSV...")
    csv_path = download_activity_csv(download_dir)
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
