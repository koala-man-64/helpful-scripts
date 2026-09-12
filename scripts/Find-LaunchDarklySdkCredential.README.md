# Identify the owner of an exposed LaunchDarkly SDK credential

Use [Find-LaunchDarklySdkCredential.ps1](Find-LaunchDarklySdkCredential.ps1) to find the project, environment, and credential record that owns a known exposed `sdk-...` value. Give those identifiers to the authorized LaunchDarkly owner so they can revoke or rotate the correct credential through the incident process.

The script first requests caller identity with the exposed SDK credential, then uses a separate management token to discover the exact SDK credential record. It only uses GET requests and does **not** revoke, rotate, delete, or initialize an SDK. A successful identity request shows that the endpoint accepted the credential at that time; it does not verify application health.

## What the agent needs

- Windows PowerShell 5.1 or PowerShell 7; no additional modules.
- The exact exposed SDK secret, supplied through an approved private channel.
- For the full scan: a **separate LaunchDarkly management REST API access token** for a candidate organization. Its permissions must allow listing projects, environments, and SDK credentials and reading their secret values. The exposed SDK secret and a VIT/incident number do not provide this access. `-IdentityOnly` does not need a management token.
- Network access to `https://app.launchdarkly.com` and the name of the organization associated with the management token, confirmed by its owner.

The expanded full scan also uses permission to read the creator's member record and resource-scoped audit logs. If these are denied, the script preserves the match and reports supplementary details as incomplete.

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

To start with only the exposed SDK credential, run:

```powershell
powershell.exe -NoProfile -File .\Find-LaunchDarklySdkCredential.ps1 -IdentityOnly
$scanExitCode = $LASTEXITCODE
```

Use `pwsh` instead of `powershell.exe` for PowerShell 7. This mode never prompts for a management token or enumerates resources. It sends the exposed value in the Authorization header to `/api/v2/caller-identity` on the fixed US commercial host, with redirects disabled. It prints only recognized, sanitized identity fields. Available fields may include account ID, project/environment names and IDs, authentication kind, and token identifiers. Fields are optional, and returned IDs must not be mistaken for resource keys. Even successful identity metadata may leave the SDK credential name/resource key unresolved.

Exit `4` means identity metadata was returned and **no full scan was performed**. Exit `2` means the lookup was inconclusive; a `401` does not identify the owner or prove revocation. Retain the identity result and use the full scan below when the exact credential record is still needed. The script still expects an exposed `sdk-...` value, not a management API token, in `KnownSdkSecret`.

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

The default run performs identity lookup before prompting for a management token, then scans even if the lookup fails. Identity lookup status does not determine full-scan completeness. The scan enumerates projects, their environments, and all returned server-side SDK credentials, including default keys, without an active-only filter. It compares secret values locally using exact, case-sensitive equality. Pagination, request timeouts, retry limits, and bounded rate-limit handling are built in. Let the scan finish: an early `MATCH` does not mean the remaining scan completed.

## Interpret the result

| Exit code | Result | Required next step |
| --- | --- | --- |
| `0` | One or more matches; accessible scan complete | Return **every** match and the owner handoff below. |
| `1` | No match; accessible scan complete | Report the searched organization and scope. Ask the owner to verify permissions and candidate organizations. Do not conclude the credential is revoked or nonexistent. |
| `2` | Incomplete full scan, or inconclusive lookup in `-IdentityOnly` mode | Return any matches **and** each sanitized failure scope/category. Resolve missing access or API failures and rerun. No-match is inconclusive. |
| `3` | Configuration or setup error | Correct the placeholder/token/setup issue using the script's safe error message, then rerun. |
| `4` | Identity metadata only; full scan not performed | Return the sanitized identity fields. Run the full scan if credential-record identifiers remain unresolved. |
| `5` | Match found, credential scan complete, supplementary details/history incomplete | Return the match and every `DETAILS INCOMPLETE` reason. Resolve access or pagination limits as needed; do not discard the confirmed match. |

`HTTP-401` usually requires the owner to check the management token; `HTTP-403` requires checking its permissions. Persistent `HTTP-404`, other API failures, masked/missing values, changing totals, repeated pages, or safety limits require investigation before claiming completeness. Do not expose raw HTTP responses or exception details while troubleshooting.

For another candidate organization, arrange a token for that organization and run a separate scan. Record the organization for each result. Hidden resources, other organizations/regions, and already deleted credentials are outside a complete accessible scan. Avoid concurrent credential changes where possible; pagination is not a consistent snapshot.

## Additional credential evidence

Every full-scan match now collects:

- SDK description, record version, creator member ID, creation/update timestamps and expiration value, alongside the existing credential name/key/default status.
- Project ID and tags; environment ID, tags, critical flag, color, cache TTL, secure-mode flag, event-tracking default, and comment/confirmation settings where returned.
- Creator name, email, base/custom roles and invitation/verification status, when the member record is readable. A historical creator may no longer operate the credential. Their account roles do not describe the SDK key's permissions.
- Retained audit events filtered to `proj/<project>:env/<environment>:sdk-key/<credential-key>`, including event ID/time/type, action, actor name/email/ID, and acting management-token or app identifiers. The acting management token is distinct from the exposed SDK key; no token value or token suffix is printed.
- Identity lookup now also includes client ID, service-token flag, and scopes when returned. These optional identity fields do not establish the full flag/view payload accessible through the SDK credential.

Fields that are absent are labeled **not returned**; an explicit empty list is labeled **empty list**. SDK timestamp output retains the raw API number and adds a UTC interpretation assuming Unix milliseconds. A missing or zero expiration value is not used to declare the credential expired, unexpired, or revoked.

Audit scanning requests records after Unix epoch zero and before the scan's initial timestamp, limited by the organization's retention and token access. It follows validated continuation cursors, with **100 pages of 20 events per match** by default. Adjust `$MaxAuditPages` in the private copy to at most 1000 if necessary. A remaining next page at the limit, malformed pagination, repeated event, or permission/API failure produces `DETAILS INCOMPLETE` and exit `5` when the credential scan itself succeeded. Empty audit results mean no records returned in that accessible retained scope, not that the key was never used or changed. Missing creator IDs also produce an explicit detail limitation.

The script does **not** provide per-key last-use time, originating IPs, a list of consuming applications/deployments/repositories, SDK request logs, proof of compromise, or complete effective flag/view payload scope. Audit logs describe management changes, not feature evaluations or credential use. Investigate deployment configuration, repository history, service logs, SDK-key settings, and available organization telemetry separately for those questions. It also excludes raw audit comments, descriptions, change payloads, arbitrary response fields, and flag values to avoid disclosing unrelated secrets.

An exit `0` means the accessible credential scan and attempted supplemental requests completed; it does not mean every possible field or all historical evidence exists. Include the printed limitations in the handoff.

## Information the agent must return

Use this template in the approved incident channel. Include only sanitized output; **never include the exposed secret or management token**.

```text
Incident/VIT reference: <reference, if supplied>
Organization: <confirmed by the management-token owner>
API scope: US commercial, accessible resources only
Scan time: <date/time and timezone>
Result: FOUND / NOT FOUND / INCOMPLETE / CONFIGURATION ERROR / IDENTITY METADATA ONLY
Exit code: <0, 1, 2, 3, 4, or 5>
Identity lookup: <sanitized fields or inconclusive category from output>
Counts: <projects, environments, SDK credentials, matches; or not scanned>

For each MATCH:
  Project name and key: <from output>
  Environment name and key: <from output>
  SDK credential name: <from output>
  SDK credential resource key: <from output; NOT the secret value>
  isDefault: <from output>
  GET resource path: <sanitized path from output>
  Credential metadata: <description/version/raw timestamps/expiry>
  Creator: <member ID and available name/email; distinguish from current owner>
  Project/environment context: <available metadata from output>
  Audit evidence: <resource specifier, time bounds, event IDs/times/actions/actors>
  Detail limitations: <missing fields, inaccessible member/history, pagination limits>

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
- [Identify the caller](https://launchdarkly.com/docs/api/other/get-caller-identity)
- [SDK credential metadata](https://launchdarkly.com/docs/api/sdk-keys-beta/get-sdk-key-by-key)
- [Creator member lookup](https://launchdarkly.com/docs/api/account-members/get-member)
- [Audit log queries](https://launchdarkly.com/docs/api/audit-log/get-audit-log-entries)
- [Resource specifiers](https://launchdarkly.com/docs/home/account/roles/role-resources)
