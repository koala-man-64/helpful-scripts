# Identify a Terraform API credential

Read-only PowerShell investigation of a known HCP Terraform or Terraform Enterprise
API credential so the authorized administrator can revoke the correct credential.
This is for user, team, and organization API tokens, **not agent tokens**. It does
not create, rotate, revoke, delete, or change remote resources. Authentication can
still update server access logs and last-used timestamps.

## Prerequisites and trust boundary

- PowerShell **7.2 or newer** (`pwsh`), with its built-in .NET HTTP client. No
  modules, Terraform CLI, Pester, or package installation is required.
- The known issuing HTTPS origin and credential, supplied locally by an authorized
  operator. Never paste the credential into an agent conversation or commit it.
- Network access and normal certificate trust for that host. Enterprise private
  CAs must be trusted through the approved OS/runtime trust configuration. There
  is no certificate-validation bypass.
- Treat the origin as **trusted administrator configuration**. HTTPS proves the
  connection is to that configured host, not that the host is your Terraform
  instance. Verify it before supplying a credential. No hostname is guessed.

Use a full origin such as `https://terraform.example.test` or an explicit HTTPS
port. Paths, user information, query strings, fragments, and bare hostnames are
rejected. The example domain is a placeholder, not a discovery target.

## Local setup and invocation

From this directory, start PowerShell 7 (`pwsh -NoProfile`). Configure your actual
issuing origin in a local variable and use the hidden prompt:

```powershell
$terraformOrigin = 'https://terraform.example.test' # Replace with known issuer
./Investigate-TerraformToken.ps1 -Origin $terraformOrigin
```

Or pass a local SecureString variable. Keep the input out of saved scripts and
shell history; do not use a literal plaintext argument:

```powershell
$terraformCredential = Read-Host 'Terraform API credential' -AsSecureString
try {
    ./Investigate-TerraformToken.ps1 -Origin $terraformOrigin -Token $terraformCredential
    $investigationExit = $LASTEXITCODE
}
finally {
    $terraformCredential.Dispose()
    Remove-Variable terraformCredential
}
```

For unattended agent execution, have the authorized operator or approved local
secret manager populate the **process** environment beforehand:

- `TERRAFORM_INVESTIGATION_ORIGIN`: full known HTTPS origin.
- `TERRAFORM_INVESTIGATION_TOKEN`: credential, never printed or included in an
  agent tool argument. Avoid machine/user persistent environment configuration.

The agent runs only this command; it must not inspect or echo the environment:

```powershell
pwsh -NoProfile -File ./Investigate-TerraformToken.ps1 -NonInteractive
$investigationExit = $LASTEXITCODE
```

`-Token` takes precedence over the environment credential. The environment origin
is the default for `-Origin`. Without a credential, interactive mode prompts;
`-NonInteractive` returns a configuration error immediately. No `.env` file is
read. Clear the process credential in the supplying session when finished:

```powershell
Remove-Item Env:TERRAFORM_INVESTIGATION_TOKEN -ErrorAction SilentlyContinue
```

Use a controlled session without command tracing, HTTP diagnostic listeners, or
debugger dumps. The tool never writes request headers, credential values, raw
response bodies, or exception text. HTTP authentication necessarily uses a
plaintext string in process memory; SecureString is not a guarantee of memory
erasure, and clearing a child process cannot clear a parent's environment.

## Output and interpretation

One compact JSON document is written to stdout. Example (illustrative IDs):

```json
{
  "host": "https://terraform.example.test",
  "status": "identified",
  "owner": {"type": "teams", "id": "team-Abc", "name": "deployment", "evidence": "authenticated-resource"},
  "token": {"id": "at-Abc", "resourcePath": "/api/v2/authentication-tokens/at-Abc", "description": null, "evidence": "account_auth-token_link"},
  "evidence": [
    {"step": "account", "status": "ok", "httpStatus": 200},
    {"step": "owner_details", "status": "ok", "httpStatus": 200},
    {"step": "token_metadata", "status": "http_404", "httpStatus": 404}
  ],
  "nextSteps": ["Give these findings to the authorized administrator. Identification does not revoke a credential. Do not share the secret.", "Contact an organization owner or authorized team-token administrator with the team and exact token IDs."]
}
```

| Status / exit | Meaning |
| --- | --- |
| `identified` / 0 | Owner type/ID and exact token ID identified. Names or metadata may still be unavailable; inspect per-step evidence. |
| `partial` / 2 | Account responded, but ownership or current token identity is incomplete. Preserve available findings. |
| `unresolved` / 3 | Account request failed or its resource shape was unexpected. No revocation conclusion is possible. |
| `configuration_error`, `internal_error` / 4 | No usable investigation; correct the local setup or investigate the tool without exposing diagnostics containing credentials. |

Missing fields are `null`. `account_auth-token_link` is evidence for the credential
used for that account request. `account_link_and_matching_metadata` means the
detail response also matched that exact ID. Description absence does not negate
identity. Selected names and descriptions are limited to 256 characters, stripped
of control characters, and redact the supplied credential (including its
URL-escaped form), recognizable `atlasv1` values, and any token value repeated in
the metadata description. No other response fields are output. These are still
server-controlled free-text metadata: review them before wider sharing; no
redactor can identify arbitrary unrelated secrets someone stored in a description.

## Request scope and Enterprise compatibility

The first request is always `GET /api/v2/account/details` with a Bearer header.
Ownership comes from `data.relationships.authenticated-resource.data`; a team or
organization's synthetic account username never determines token type. When the
relationship is entirely absent, only an explicit boolean
`is-service-account: false` permits a user-account fallback. Otherwise ownership
remains unknown. Unsupported relationship types (including unhandled HCP group
types) remain partial, without inventing a mapping.

For a known owner, the tool uses only a strict same-origin detail link for
`/api/v2/users/:id`, `/api/v2/teams/:id`, or `/api/v2/organizations/:name` and checks
the returned type/ID. User/team routes can be constructed from their documented
IDs if the related link is absent; organization names are not guessed from IDs.
For the current user, the account response already supplies the name.

`data.links.auth-token` must identify `/api/v2/authentication-tokens/at-…`.
For user/team owners the tool attempts that exact metadata endpoint. The
organization-token documentation does not document a metadata GET; for
organization or unknown owners the tool retains the account link and records
`not_attempted_for_owner_type`. It never substitutes a legacy singleton token,
lists credentials, or enumerates organizations, workspaces, state, or variables.

Current HCP Terraform and Terraform Enterprise account documentation both show
the authenticated-resource and auth-token fields. This is **not** a minimum
Enterprise release guarantee: installed versions, permissions, and API variants
can differ. Missing/unsupported fields remain explicit partial findings. Legacy
numeric token links are not followed or treated as an `at-…` ID. Consult the
installed version's docs if this occurs.

All redirects (including same-origin) are refused. Response links must be
canonical, match an allowed endpoint, and have the exact configured HTTPS origin
and port; encoded paths, queries, fragments, credentials and traversal are
rejected. Each request has a 20-second timeout and 1 MiB response limit. Only
429/502/503/504 responses get retries: at most three attempts per endpoint, with
1/2-second backoff or a valid Retry-After up to 10 seconds. Longer Retry-After
values stop retries for administrator follow-up. Transport failures are not
retried. At most three distinct endpoint requests (nine attempts) occur.

| Evidence | Interpretation and next action |
| --- | --- |
| `http_401` | Authentication rejected: possibly wrong issuer, malformed, expired, disabled or revoked credential. It does not prove revocation. |
| `http_403`, `http_404` | Access denied, hidden/not-found resource, unsupported endpoint/version or entitlement. Terraform may conceal authorization failures as 404. Preserve earlier IDs. |
| `http_3xx` | Redirect refused. Verify the configured issuer with its administrator; do not follow it with credentials. |
| `http_429`, `http_502/503/504` | Bounded retries exhausted or long Retry-After. Retry later under administrator direction. |
| `transport_error` | DNS, TLS, proxy, network, timeout, response-size or other transport failure. Check approved connectivity without dumping sensitive exceptions. |
| `unexpected_*`, `invalid_json`, `resource_mismatch` | Unexpected API/proxy response or mismatched identity. Do not treat that response as exact metadata evidence. |
| `missing_or_unsafe_link`, `missing_or_unsupported_relationship` | Field unavailable or rejected; use available IDs for the administrator's investigation. |

## Administrator handoff

Provide the issuer, owner type/ID/name, exact token ID/path when present, and
per-step evidence via the approved internal channel. **Do not send the credential.**

- **User:** owning user and security administrator locate the exact user token in
  account settings or their approved administrative workflow.
- **Team:** organization owner or authorized team-token administrator locates the
  team and exact token. Modern team tokens can coexist; do not assume a single
  token or replace a legacy token merely to identify it.
- **Organization:** organization owner verifies the organization and current
  credential using installed-version controls.
- **Unknown/partial:** issuing-instance administrator uses version-specific
  documentation and audit evidence; a synthetic username is not a human owner.

Revocation is a separate, explicitly authorized administrator operation. A
successful identification does not remove the exposure. Coordinate replacement
and dependent-client recovery through the incident process; this tool performs
none of those actions. A later authentication failure alone is not proof that the
intended credential was revoked.

## Offline validation

```powershell
pwsh -NoProfile -File ./Test-Investigate-TerraformToken.ps1
```

The standalone tests use fixture data and an in-memory .NET HTTP handler; they
make no network requests and use no real credentials. They cover users, teams,
organizations, missing relationships/links, denied metadata, invalid auth,
unexpected responses, mismatched IDs, redaction, unsafe links/origins, bounded
rate-limit/transient retries and response size. Tested on PowerShell **7.6.5** on
Windows. PowerShell 7.2 is the declared minimum, not a tested runtime here.
Windows PowerShell **5.1 is unsupported**. Live HCP/TFE authentication, TLS/proxy
integration and installed-release behavior have not been tested.

## Official references

Reviewed 2026-09-11:

- [HCP Terraform account API](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/account)
- [Terraform Enterprise account API](https://developer.hashicorp.com/terraform/enterprise/api-docs/account)
- [Enterprise API overview and authentication errors](https://developer.hashicorp.com/terraform/enterprise/api-docs)
- [User detail](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/users), [team detail](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/teams), [organization detail](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/organizations)
- [User token metadata](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/user-tokens)
- [Team tokens, including current and legacy endpoints](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/team-tokens)
- [Organization token API](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/organization-tokens)
