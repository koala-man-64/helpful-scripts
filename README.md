# helpful-scripts

## Discover activity downloader/uploader

`discover_activity_to_adls.py` automates:

1. Open `https://www.discover.com/credit-cards/`
2. Click **Log In**
3. Fill username/password from `.env`
4. Submit login
5. Click **View Activity & Statements**
6. Click **Download**
7. Choose **CSV** in download options
8. Download CSV
9. Upload CSV to ADLS

### Install

```bash
pip install playwright python-dotenv azure-storage-file-datalake
playwright install chromium
```

### `.env` example

```bash
DISCOVER_USERNAME=your_discover_username
DISCOVER_PASSWORD=your_discover_password
ADLS_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net
ADLS_FILE_SYSTEM=your-container
ADLS_DIRECTORY=optional/subfolder
ADLS_TARGET_FILENAME=optional-fixed-name.csv
```

### Run

```bash
python discover_activity_to_adls.py
```

> Note: If Discover prompts for MFA/CAPTCHA, complete it manually in the opened browser window.
