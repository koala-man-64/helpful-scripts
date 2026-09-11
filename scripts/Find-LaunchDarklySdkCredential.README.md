# Identify the owner of an exposed LaunchDarkly SDK credential

Use [Find-LaunchDarklySdkCredential.ps1](Find-LaunchDarklySdkCredential.ps1) to find the project, environment, and credential record that owns a known exposed `sdk-...` value. Give those identifiers to the authorized LaunchDarkly owner so they can revoke or rotate the correct credential through the incident process.

The script only discovers information using GET requests. It does **not** revoke, rotate, delete, or test whether the exposed credential still authenticates.

## What the agent needs

- Windows PowerShell 5.1 or PowerShell 7; no additional modules.
- The exact exposed SDK secret, supplied through an approved private channel.
- A **separate LaunchDarkly management REST API access token** for a candidate organization. Its permissions must allow listing projects, environments, and SDK credentials and reading their secret values. The exposed SDK secret and a VIT/incident number do not provide this access.
- Network access to `https://app.launchdarkly.com` and the name of the organization associated with the management token, confirmed by its owner.

This script targets the **US commercial API only**. A token searches its own organization's accessible resources; it cannot search other organizations or reveal resources hidden by permissions. Stop and report the scope mismatch if the candidate organization uses another region or a federal deployment.

## Configure a private copy

1. Copy the script to an approved private working folder outside source control. Keep the distributed/repository copy unchanged.
2. In its **OPERATOR CONFIGURATION** section, replace only the placeholder in:

   ```powershell
   $KnownSdkSecret = 'REPLACE_WITH_EXPOSED_SDK_SECRET'
   ```

   Use the complete exposed value, preserving case and without surrounding whitespace. Do not substitute a credential name or identifying key.
3. Use the management token supplied by the authorized owner. If the process already has `LD_ACCESS_TOKEN` configured through an approved secret-injection mechanism, the script uses it. Otherwise, the script prompts for the token with hidden input. An unattended agent needs that environment variable provisioned before running; an interactive operator can use the prompt.

Never put either credential in a command line, agent message, ticket, output example, or commit. Do not display the configured script, enable tracing, or record a transcript containing credentials. Managed PowerShell script-block logging may capture edited source: use an operator environment approved for this handling, without disabling managed logging. If access or secure credential delivery is unavailable, report that blocker instead of requesting the secret in chat.

## Run

From the folder containing the private script copy, choose one:

```powershell
# Windows PowerShell 5.1
powershell.exe -NoProfile -File .\Find-LaunchDarklySdkCredential.ps1
$scanExitCode = $LASTEXITCODE
```

```powershell
# PowerShell 7
pwsh -NoProfile -File .\Find-LaunchDarklySdkCredential.ps1
$scanExitCode = $LASTEXITCODE
```

Capture the exit code immediately after the process finishes. Read the script's full embedded instructions with:

```powershell
Get-Help .\Find-LaunchDarklySdkCredential.ps1 -Full
```

The scan enumerates projects, their environments, and all returned server-side SDK credentials, including default keys, without an active-only filter. It compares secret values locally using exact, case-sensitive equality. Pagination, request timeouts, retry limits, and bounded rate-limit handling are built in. Let the scan finish: an early `MATCH` does not mean the remaining scan completed.

## Interpret the result

| Exit code | Result | Required next step |
| --- | --- | --- |
| `0` | One or more matches; accessible scan complete | Return **every** match and the owner handoff below. |
| `1` | No match; accessible scan complete | Report the searched organization and scope. Ask the owner to verify permissions and candidate organizations. Do not conclude the credential is revoked or nonexistent. |
| `2` | Incomplete scan; matches may still exist | Return any matches **and** each sanitized failure scope/category. Resolve missing access or API failures and rerun. No-match is inconclusive. |
| `3` | Configuration or setup error | Correct the placeholder/token/setup issue using the script's safe error message, then rerun. |

`HTTP-401` usually requires the owner to check the management token; `HTTP-403` requires checking its permissions. Persistent `HTTP-404`, other API failures, masked/missing values, changing totals, repeated pages, or safety limits require investigation before claiming completeness. Do not expose raw HTTP responses or exception details while troubleshooting.

For another candidate organization, arrange a token for that organization and run a separate scan. Record the organization for each result. Hidden resources, other organizations/regions, and already deleted credentials are outside a complete accessible scan. Avoid concurrent credential changes where possible; pagination is not a consistent snapshot.

## Information the agent must return

Use this template in the approved incident channel. Include only sanitized output; **never include the exposed secret or management token**.

```text
Incident/VIT reference: <reference, if supplied>
Organization: <confirmed by the management-token owner>
API scope: US commercial, accessible resources only
Scan time: <date/time and timezone>
Result: FOUND / NOT FOUND / INCOMPLETE / CONFIGURATION ERROR
Exit code: <0, 1, 2, or 3>
Counts: <projects, environments, SDK credentials, matches from output>

For each MATCH:
  Project name and key: <from output>
  Environment name and key: <from output>
  SDK credential name: <from output>
  SDK credential resource key: <from output; NOT the secret value>
  isDefault: <from output>
  GET resource path: <sanitized path from output>

Incomplete scopes/categories: <each failure, or none>
Scope/access limitations: <known gaps or other candidate organizations>
Next owner/action: <authorized LaunchDarkly owner and requested follow-up>
Credential changes performed: None; discovery only
```

The **resource key** identifies the credential record; the **secret value** authenticates an SDK. They are different fields. A match tells the owner which record needs attention, not whether revocation has already occurred.

The authorized owner should sign in to the confirmed organization, open **Organization settings > Security > SDK keys**, select the reported project and environment, and locate the reported credential name/resource key. Hand off all matches, including default keys. Revocation/rotation and verification of application recovery remain separate authorized incident actions.

After handoff, handle the configured private script copy and any injected management token according to the organization's credential-retention policy. Do not add the configured copy to source control.

## API references

The implementation's API references were verified on September 11, 2026:

- [List projects](https://launchdarkly.com/docs/api/projects/get-projects)
- [List environments](https://launchdarkly.com/docs/api/environments/get-environments-by-project)
- [List environment SDK keys](https://launchdarkly.com/docs/api/sdk-keys-beta/get-sdk-keys)
- [SDK credentials and owner navigation](https://launchdarkly.com/docs/home/account/environment/keys)
