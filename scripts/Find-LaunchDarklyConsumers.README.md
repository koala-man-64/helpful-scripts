# Find applications that may consume a LaunchDarkly SDK credential

`Find-LaunchDarklyConsumers.ps1` is a **read-only PowerShell 7 inventory tool**.
Use it after `Find-LaunchDarklySdkCredential.ps1` has identified the credential's
project, environment, and resource key. The existing investigator is unchanged.

The companion collects LaunchDarkly environment activity, cloud resource and
secret-version inventory, and GitHub/Azure DevOps configuration references.
It does **not** establish a complete runtime consumer list, rotate a key, run a
pipeline, modify a secret, or inspect processes, Kubernetes secrets, or VM files.

## Prepare

Keep the script and its `launchdarkly-consumers/` directory together. The private
implementation files are part of the tool; copying only the entry script will
not work. No PowerShell modules or Python packages are required.

Use an operator environment approved to read the specified secrets. Install and
sign in to the applicable CLIs separately before running:

| Platform | Authentication | Required read access |
| --- | --- | --- |
| LaunchDarkly | `LD_ACCESS_TOKEN` or hidden prompt | Applications and service-connection usage for the target organization |
| Azure | Existing `az` session | Resource inventory; Key Vault secret list, versions, and get permissions |
| GCP | Existing `gcloud` session | Project metadata, Cloud Asset search, Secret Manager list/version metadata and version access |
| GitHub | Existing `gh` session for github.com | Repository contents, workflows, environments, and relevant repository/organization secret metadata |
| Azure DevOps | Existing Azure CLI Entra session | Repositories/code, build/release definitions, and variable groups in the specified projects |

Azure DevOps authentication uses an Entra access token obtained from `az` for the
Azure DevOps audience. A separate `az devops login` PAT alone is insufficient for
this tool. Missing commands or denied API requests produce coverage gaps; the
tool never installs extensions, signs in, changes the active subscription,
enables cloud APIs, or grants permissions.

The LaunchDarkly SDK secret and management token are different credentials.
Supply the SDK value through the hidden prompt or an approved process environment
injection named `LD_KNOWN_SDK_SECRET`. Never put either value in a command argument,
the JSON configuration, a ticket, a transcript, or source control.

## Configure explicit scopes

Copy `launchdarkly-consumers/config.example.json` to approved private storage.
Populate it with non-secret identifiers:

```json
{
  "launchDarkly": {
    "projectKey": "example-project",
    "environmentKey": "production",
    "credentialResourceKey": "example-credential"
  },
  "azure": {
    "subscriptionIds": ["00000000-0000-0000-0000-000000000001"]
  },
  "gcp": { "projectIds": ["example-project"] },
  "azureDevOps": [
    { "organization": "example-org", "projects": ["Example Project"] }
  ],
  "github": {
    "organizations": ["example-org"],
    "repositories": ["another-owner/example-repository"]
  }
}
```

An empty array skips that platform scope. A GitHub organization explicitly opts
into its accessible repositories; an explicit repository opts into that repository
only. Other accessible accounts, subscriptions, projects, and external template
repositories are not automatically added. Unknown configuration fields are errors,
so a spelling mistake cannot silently remove intended scope.

The credential resource key is the non-secret record identifier from the original
investigator, **not** the `sdk-...` authentication value. This tool preserves the
operator-supplied identity; it does not independently revalidate that mapping.

## Run

Run from the repository root, or adjust the script path:

```powershell
pwsh -NoProfile -File ./scripts/Find-LaunchDarklyConsumers.ps1 `
  -ConfigPath C:/Private/ld-scope.json `
  -OutputDirectory C:/Private/ld-inventory
```

The default LaunchDarkly window is the previous 30 days. Use explicit timestamps
to choose another window, up to 365 days:

```powershell
pwsh -NoProfile -File ./scripts/Find-LaunchDarklyConsumers.ps1 `
  -ConfigPath C:/Private/ld-scope.json `
  -OutputDirectory C:/Private/ld-inventory-september `
  -From 2026-09-01T00:00:00Z -To 2026-09-14T00:00:00Z
```

Select an output folder with appropriate access controls outside source control.
The tool uses the folder's existing/inherited permissions, and refuses to overwrite
either report. Resource identifiers and ownership metadata can still be confidential.
Follow your organization's retention policy for the reports.

## Read the evidence

The tool writes `launchdarkly-consumers.json` and `launchdarkly-consumers.csv`.
JSON contains scope coverage, the requested observation window, findings, and
limitations. CSV provides one review row per finding and neutralizes spreadsheet
formula prefixes. Provider bodies, secret values, and authentication headers are
not included. Failures use fixed categories instead of provider error bodies.

| Classification | What it establishes |
| --- | --- |
| `EnvironmentActivity` | LaunchDarkly reported service-connection activity for the environment; exact credential attribution is unavailable |
| `ExactStoredKeyMatch` | An accessible stored value exactly matched the known key at scan time |
| `ExactSourceKeyMatch` | The exact key appeared as a bounded literal in inspected source; deployment and runtime use are unproven |
| `ConfigurationReferenceLinkedToMatch` | A static, pinned configuration reference names a matching stored version; deployment use remains unproven |
| `UnresolvedCandidate` | A configuration name, SDK initialization, secret reference, or other lead needs attribution |
| `CoverageGap` | An inaccessible, unsupported, malformed, dynamic, or bounded-out part of the scan needs follow-up |
| Other inventory classifications | Resource, pipeline, environment, credential target, and secret metadata only |

Application and owner fields come from available identifiers/labels. They are
leads for routing, not verified ownership assignments. First/last observation
fields are **daily activity bucket timestamps**, not exact connect/disconnect
times. Projected usage is excluded. Missing application IDs stay unidentified.

All readable versions of cloud secrets are compared, including unrelated names
and historical versions. Disabled/destroyed/inaccessible versions remain gaps;
the tool does not enable them. A historical match does not establish that `latest`
or an unversioned reference currently resolves to that key. Only an explicit
matching version is automatically linked.

GitHub secret values are unavailable through its API. Azure DevOps masked values
also cannot be verified as exact matches. The tool does not execute a workflow
to reveal them. Source inspection is bounded static analysis: dynamic templates,
runtime substitutions, non-default branches beyond identified pipeline refs,
skipped content, regional Secret Manager resources, and workload internals require
additional investigation. Missing permissions do not prove absence.

GitHub scanning includes repository, environment, and explicitly scoped organization
secret metadata, default-branch configuration, and static reusable workflows. Azure
DevOps scanning includes default-branch source, identified build YAML, variable groups,
and classic release environment/task references. Static local YAML templates and
Azure Repos aliases are followed within configured projects; dynamic expressions,
YAML aliases/flow-style constructs, external repository types, and inaccessible refs
need manual inspection. The scanner does not evaluate YAML or execute source code.
Source inspection is capped at 1,000 eligible files per repository, 1 MiB characters
per file, eight reference levels, and 100 referenced files per provider scan.

| Exit code | Meaning |
| --- | --- |
| `0` | Accessible inventory collected within the implemented boundaries; runtime consumer completeness is still not established |
| `2` | Reports were written with coverage gaps or collection failures; earlier findings were retained |
| `3` | Configuration, setup, or report-writing failure; inspect the fixed console category and any report files already written |

Collections have page limits and bounded HTTP retries. Native authentication
helpers have time and output limits and are disposed/terminated when necessary.
Individual HTTP responses are capped at 20 MiB; the in-memory redaction set is
capped at 33,554,432 characters. Reaching a collection limit produces a gap,
not a claim that the remaining resources were scanned.
HTTP redirects are disabled and requests must stay within approved hosts and
configured scopes. A scan is not an atomic snapshot: configuration may change
during collection. Rerun relevant scopes after changes.

## Validate and hand off

Offline tests use synthetic credentials and mocked transport:

```powershell
pwsh -NoProfile -File ./scripts/launchdarkly-consumers/Core.Tests.ps1
pwsh -NoProfile -File ./scripts/launchdarkly-consumers/Cloud.Tests.ps1
pwsh -NoProfile -File ./scripts/launchdarkly-consumers/Repositories.Tests.ps1
pwsh -NoProfile -File ./scripts/launchdarkly-consumers/Repositories.Extended.Tests.ps1
```

For live validation, use a deliberately scoped project/repository and an approved
known test secret version. Verify that the report identifies that version, lists
an accessible reference, preserves an inaccessible or masked case as a gap, and
contains no credential values. Local test success does not establish provider
permission coverage or live consumer attribution.

Use the inventory to choose the next workload collectors and assign each candidate
to an application owner. Before rotating, separately verify deployed configuration,
refresh/restart behavior, dormant/rollback dependencies, and application health.

## Provider references

- [LaunchDarkly service-connection usage](https://launchdarkly.com/docs/api/account-usage-beta/get-service-connections-usage)
- [LaunchDarkly applications](https://launchdarkly.com/docs/api/applications-beta/get-applications)
- [Azure resource inventory](https://learn.microsoft.com/en-us/rest/api/resources/resources/list)
- [Azure Key Vault secret versions](https://learn.microsoft.com/en-us/rest/api/keyvault/secrets/get-secret-versions/get-secret-versions?view=rest-keyvault-secrets-7.4)
- [GCP Cloud Asset search](https://docs.cloud.google.com/asset-inventory/docs/reference/rest/v1/TopLevel/searchAllResources)
- [GCP Secret Manager](https://docs.cloud.google.com/secret-manager/docs/reference/rest)
- [GitHub Actions secret metadata](https://docs.github.com/en/rest/actions/secrets)
- [Azure DevOps variable groups](https://learn.microsoft.com/en-us/rest/api/azure/devops/distributedtask/variablegroups/get?view=azure-devops-rest-7.1)
