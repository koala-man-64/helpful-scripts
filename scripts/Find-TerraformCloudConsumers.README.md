# Find consumers of a Terraform Cloud API token

`Find-TerraformCloudConsumers.ps1` is a read-only PowerShell 7.2+ companion to
`terraform-token-investigator/Investigate-TerraformToken.ps1`. It finds stored
copies and configuration references to a known **HCP Terraform / Terraform Cloud
API token**. Existing credential investigators remain unchanged.

It helps identify applications, repositories, pipelines, workspaces, and variable
sets that need investigation before an update. It does not prove a complete list
of runtime consumers. An accessible workspace or a run created through the API
does not establish that the known token was used for that run.

## What it collects

| Source | Evidence |
| --- | --- |
| Terraform Cloud | Known-token account identity; explicitly scoped organization workspaces, VCS/working-directory hints, workspace variables, variable sets and their application scopes, and run metadata in the observation window |
| Azure | Resources and available ownership tags; all readable Key Vault secret versions, regardless of their names |
| GCP | Cloud Asset resources/labels; all readable global Secret Manager versions, regardless of their names |
| GitHub | Explicit repositories or organizations, Actions secret metadata, environments, default-branch configuration, and accessible static reusable workflow references |
| Azure DevOps | Explicit organization/project repositories, default-branch source, build YAML references, variable groups, and classic release environment/task configuration |

The shared repository scanner recognizes Terraform/HCL files, shell and PowerShell
configuration, `.terraformrc`, `terraform.rc`, JSON credential files in repositories,
`TF_TOKEN_*`, `TFE_TOKEN`, `TFC_TOKEN`, `TERRAFORM_CLOUD_TOKEN`,
`TF_CLI_CONFIG_FILE`, and `hashicorp/setup-terraform` configuration. A name alone
is an unresolved lead. Static secret-resource references can be linked to an
exact cloud match only when the referenced version is explicitly pinned.

Sensitive Terraform variables and masked CI values are recorded as metadata and
coverage gaps. No run, workflow, or secret mutation is used to reveal them.
All readable values are compared locally and withheld from reports. Cloud history
is scanned; Terraform workspace/variable-set variables represent current readable
configuration, not historical versions.

## Authentication and scope

Keep the entry script with both `scripts/terraform-consumers/` and
`scripts/launchdarkly-consumers/`. The latter contains the shared cloud/CI transport
and report implementation so security fixes apply to both companions. No additional
PowerShell module or Terraform CLI installation is needed.

- Enter the known Terraform API token at the hidden prompt, or inject it into the
  process environment as `TFC_KNOWN_TOKEN` using your approved secret-handling tool.
- Optionally inject `TFC_ACCESS_TOKEN` for separate Terraform inventory access.
  Otherwise the known token supplies that access. Identity always uses the known
  token, even when a different inventory token is provided.
- Azure and Azure DevOps use the existing `az` Entra session. A standalone
  `az devops login` PAT is insufficient for this collector.
- GCP uses the existing `gcloud` session; GitHub uses `gh` for github.com.
- The tool never logs in, installs a CLI, enables APIs, switches subscriptions,
  grants permissions, or reads local Terraform CLI credential files automatically.

Use identities permitted to list/read the selected resources. Missing tools,
expired tokens, denied API access, sensitive variables, and inaccessible versions
produce gaps while other scopes continue. A revoked known token can still be
matched against readable cloud secrets; its identity lookup will be a gap.

Copy [config.example.json](terraform-consumers/config.example.json) to private
storage and fill in **non-secret identifiers only**:

```json
{
  "terraformCloud": {
    "hostname": "app.terraform.io",
    "organizations": ["example-org"]
  },
  "azure": { "subscriptionIds": ["00000000-0000-0000-0000-000000000001"] },
  "gcp": { "projectIds": ["example-project"] },
  "azureDevOps": [{ "organization": "example-org", "projects": ["Example Project"] }],
  "github": { "organizations": [], "repositories": ["example-owner/example-repo"] }
}
```

Each Terraform organization opts into its accessible workspaces and variable sets.
An empty scope array skips that inventory; known-token identity is still checked.
A GitHub organization opts into its accessible repositories and organization secret
metadata. Other accounts are never silently added. Unknown fields are rejected.
Supported Terraform hosts are `app.terraform.io` and `app.eu.terraform.io`;
Terraform Enterprise custom hosts and HCP platform service-principal credentials
are outside this version's scope.

## Run

```powershell
pwsh -NoProfile -File ./scripts/Find-TerraformCloudConsumers.ps1 `
  -ConfigPath C:/Private/terraform-scope.json `
  -OutputDirectory C:/Private/terraform-consumers
```

The default run-metadata window is the previous 30 days. Use `-From` and `-To`
with UTC timestamps to choose another range, up to 365 days. Resource/secret
configuration is collected as it exists during the scan, regardless of that window.
Pagination does not assume that provider results are ordered by time.

Credentials must never appear in arguments, configuration files, source control,
transcripts, or tickets. Output inherits its directory's permissions; choose
approved private storage because sanitized identifiers can still be confidential.

## Read the reports

The tool creates `terraform-cloud-consumers.json` and `terraform-cloud-consumers.csv`.
It refuses to overwrite existing reports.

- **AuthenticatedIdentity:** provider-reported identity of the known token; no
  inference of ownership from a synthetic service username.
- **WorkspaceInventory / VariableSetGlobalBinding / VariableSetWorkspaceBinding /
  VariableSetProjectBinding / WorkspaceActivity:** accessible
  configuration or run metadata, with no token-consumption claim.
- **ExactStoredKeyMatch / ExactSourceKeyMatch:** exact readable copies of the token;
  storage or source presence does not prove deployment or execution.
- **ConfigurationReferenceLinkedToMatch:** a static pinned reference to an exactly
  matching cloud secret version; deployed resolution remains to be checked.
- **UnresolvedCandidate / CoverageGap:** an attribution lead or an incomplete part
  of the scan, with a next step.

JSON includes requested/scanned/partial/failed/unsupported scopes and always states
`consumerCompleteness: not-established`. Reports retain earlier findings when later
pages or independent collectors fail. Exit codes: `0` accessible inventory collected,
`2` reports written with gaps, `3` setup/configuration/report-writing failure.

The shared limits are 20 MiB per HTTP response, 1 MiB characters per source file,
1,000 source files per repository, eight reference levels, 100 referenced files per
provider, 10,000 pages per collection, and bounded retries/timeouts. Limits become
gaps. JSON/CSV redact registered values; CSV formula prefixes are neutralized.
Redirects are disabled and requests must stay on approved hosts and scoped paths.

This version does not collect audit trails, Terraform state, plan JSON, run logs,
running-process environments, Kubernetes secrets, VM files, local user credential
stores, or dynamic YAML/HCL evaluation. Regional GCP secrets and inaccessible or
non-default repository branches beyond identified pipeline refs require follow-up.
Variable-set applicability and run activity do not establish effective variable
precedence or exact-token use. Inventory is not an atomic snapshot.

## Validation

```powershell
pwsh -NoProfile -File ./scripts/terraform-consumers/TerraformCloud.Tests.ps1
pwsh -NoProfile -File ./scripts/launchdarkly-consumers/Core.Tests.ps1
pwsh -NoProfile -File ./scripts/launchdarkly-consumers/Cloud.Tests.ps1
pwsh -NoProfile -File ./scripts/launchdarkly-consumers/Repositories.Tests.ps1
pwsh -NoProfile -File ./scripts/launchdarkly-consumers/Repositories.Extended.Tests.ps1
```

These are offline synthetic fixtures. For live qualification, use an explicitly
scoped test organization/repository and approved known token copy. Confirm a known
matching version, a masked/denied case, preserved partial results, and no credential
values in either output. Offline tests do not establish live provider permissions.

## Provider references

- [Terraform account identity](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/account)
- [Workspace variables](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/workspace-variables)
- [Variable sets](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/variable-sets)
- [Workspaces](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/workspaces)
- [Runs](https://developer.hashicorp.com/terraform/cloud-docs/api-docs/run)
- [Terraform CLI environment variables](https://developer.hashicorp.com/terraform/cli/config/environment-variables)
- [Shared cloud and CI collector guide](Find-LaunchDarklyConsumers.README.md)
