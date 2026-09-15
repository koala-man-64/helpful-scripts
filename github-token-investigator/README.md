# GitHub personal access token investigator

Read-only PowerShell 7.2+ utility for identifying the account behind a GitHub PAT,
following the repository's Terraform and LaunchDarkly investigation conventions.
No modules or runtime dependencies. Windows PowerShell 5.1 is unsupported.

## Run

From the repository root:

```powershell
pwsh -NoProfile -File ./github-token-investigator/Investigate-GitHubToken.ps1
```

Paste the PAT into the hidden prompt. Do not put it into a command, transcript,
ticket, chat, or source file. The result is one compact JSON document.

Optional named repository metadata check:

```powershell
pwsh -NoProfile -File ./github-token-investigator/Investigate-GitHubToken.ps1 -Repository owner/repository
```

GitHub Enterprise Server (verify the issuing instance with its administrator):

```powershell
pwsh -NoProfile -File ./github-token-investigator/Investigate-GitHubToken.ps1 -ApiBaseUrl https://github.example.com/api/v3 -AllowEnterpriseHost
```

The default destination is `https://api.github.com`. Any custom HTTPS destination
requires `-AllowEnterpriseHost`; this explicitly authorizes sending the credential
to that host. Data-residency API hosts can use an HTTPS root URL. Only a root URL
or `/api/v3` is accepted, with optional port; credentials, query strings, fragments,
and other paths are rejected. The REST API version is pinned to `2022-11-28`;
the installed Enterprise Server version must support it.

For automation, supply `-Token` as an existing `Security.SecureString` in a
PowerShell session, or inject the dedicated process environment variable
`GITHUB_INVESTIGATION_TOKEN` through your secret manager and use `-NonInteractive`.
An explicit SecureString takes precedence. The script deliberately does not read
`GH_TOKEN` or `GITHUB_TOKEN`, which could identify the investigator's own account.
Remove the dedicated environment value after use. It remains in the calling
process environment until you remove it.

## Evidence and limits

- Calls authenticated `GET /user` once. Successful output identifies the account
  login and numeric ID, with optional account type and display name. It does not
  identify the exact token record, its label, its creator, or historical users.
- Reports `X-OAuth-Scopes` and `GitHub-Authentication-Token-Expiration` when returned.
  Classic OAuth scopes are not a complete effective permissions inventory.
  Fine-grained PAT permissions cannot be inferred from a missing scopes header.
  An empty scopes header is preserved separately from an absent header.
- `ghp_` and `github_pat_` provide format hints only. Legacy credentials with no
  recognized prefix may still authenticate. Success does not certify PAT type.
- `-Repository` adds one `GET /repos/owner/name`, only after identity succeeds.
  It reports whether matching repository metadata was readable. Public metadata
  access does not establish private content, write, or administrator privileges.
  Repository `permissions` fields are intentionally not presented as PAT grants.
- A failed repository check preserves the confirmed account and returns `partial`.
  A 404 can mean a missing or hidden resource. A 401 does not prove revocation;
  a wrong issuing host or invalid credential is also possible. A 403 may indicate
  policy, SSO, permissions or rate limiting. Selected SSO/rate-limit headers help
  distinguish cases; absence of an SSO header does not establish SSO authorization.
- Token ID, token name, creation time and last-used time remain null. Use the
  identified account's token settings and authorized administrator audit records
  for further investigation. No audit history or complete access inventory is
  collected. This utility does not revoke or rotate credentials.

## Security and operation

All requests are GET-only, with redirects and cookies disabled, normal TLS
certificate validation, a 20-second timeout per request, and a 1 MiB response cap.
There are at most two requests and no automatic retries; follow reported
`Retry-After`/rate-limit information before running again. Returned links are never
followed. SSO authorization URLs, raw bodies, raw exception messages and arbitrary
response headers are not emitted. Selected text is redacted for the supplied
credential, URL-encoded form, recognized GitHub credential prefixes and control
characters, then truncated to 256 characters.

The investigation itself uses the credential and may affect audit or last-used
records. HTTPS still requires trusting the configured host and local proxy/TLS
environment. A SecureString reduces accidental exposure; HttpClient necessarily
uses a managed plaintext string in memory, which cannot be reliably zeroed. The
temporary BSTR is zeroed and clients are disposed. Protect the resulting account
and repository evidence appropriately.

| Exit | Status | Meaning |
| --- | --- | --- |
| 0 | `identified` | Account identified; any requested repository check succeeded |
| 2 | `partial` | Account identified; requested repository evidence incomplete |
| 3 | `unresolved` | Account identity could not be established |
| 4 | `configuration_error` / `internal_error` | Invalid setup or safe internal stop |

Exit 0 does not mean complete token metadata or a clean security investigation.
PowerShell host startup and parameter-binding errors occur before this JSON contract.

## Offline validation

```powershell
pwsh -NoProfile -File ./github-token-investigator/Test-Investigate-GitHubToken.ps1
```

The suite uses synthetic credentials and an in-memory HTTP handler. It checks
identity shape, redaction, header evidence, repository mismatch, partial results,
SSO, URL restrictions, custom-host opt-in, HTTP errors, timeouts, response size,
client security settings, SecureString input and the actual missing-input CLI exit.
No real PAT or network is used. For an authorized live check, run the hidden-prompt
command against the known issuing host and compare the login/ID to account settings.

## Provider references

- [Get the authenticated user](https://docs.github.com/en/rest/users/users#get-the-authenticated-user)
- [Get a repository](https://docs.github.com/en/rest/repos/repos#get-a-repository)
- [OAuth scopes and response headers](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)
- [REST API troubleshooting](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api)
- [Managing personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
