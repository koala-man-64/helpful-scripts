# JSON Report Schema

Use this schema shape for `report-only`, `ci-health-check`, and automation-friendly output. Add fields only when they are useful and documented in the report.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "RepoOps Custodian Maintenance Report",
  "type": "object",
  "required": ["summary", "findings"],
  "additionalProperties": false,
  "properties": {
    "summary": {
      "type": "object",
      "required": [
        "repo",
        "project",
        "organization",
        "generatedAt",
        "mode",
        "findingCountsByRisk",
        "recommendedActionCountsByRisk"
      ],
      "additionalProperties": true,
      "properties": {
        "repo": { "type": "string" },
        "project": { "type": "string" },
        "organization": { "type": "string" },
        "generatedAt": { "type": "string", "format": "date-time" },
        "mode": {
          "type": "string",
          "enum": ["audit", "dry-run", "interactive-cleanup", "report-only", "ci-health-check"]
        },
        "findingCountsByRisk": {
          "$ref": "#/$defs/riskCounts"
        },
        "recommendedActionCountsByRisk": {
          "$ref": "#/$defs/riskCounts"
        }
      }
    },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "id",
          "category",
          "resourceType",
          "resourceName",
          "risk",
          "currentState",
          "evidence",
          "recommendation",
          "proposedActions",
          "manualReviewRequired"
        ],
        "additionalProperties": true,
        "properties": {
          "id": { "type": "string" },
          "category": { "type": "string" },
          "resourceType": { "type": "string" },
          "resourceName": { "type": "string" },
          "risk": { "$ref": "#/$defs/risk" },
          "currentState": { "type": "string" },
          "evidence": {
            "type": "array",
            "items": {
              "type": "object",
              "additionalProperties": true,
              "properties": {
                "type": { "type": "string" },
                "source": { "type": "string" },
                "observedAt": { "type": "string", "format": "date-time" },
                "value": {}
              }
            }
          },
          "recommendation": { "type": "string" },
          "proposedActions": {
            "type": "array",
            "items": {
              "type": "object",
              "required": [
                "type",
                "description",
                "requiresApproval",
                "reversible",
                "rollback"
              ],
              "additionalProperties": false,
              "properties": {
                "type": { "type": "string" },
                "description": { "type": "string" },
                "command": { "type": "string" },
                "apiCall": { "type": "string" },
                "requiresApproval": { "type": "boolean" },
                "reversible": { "type": "boolean" },
                "rollback": { "type": "string" }
              }
            }
          },
          "manualReviewRequired": { "type": "boolean" }
        }
      }
    }
  },
  "$defs": {
    "risk": {
      "type": "string",
      "enum": ["info", "low", "medium", "high", "critical"]
    },
    "riskCounts": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "info": { "type": "integer", "minimum": 0 },
        "low": { "type": "integer", "minimum": 0 },
        "medium": { "type": "integer", "minimum": 0 },
        "high": { "type": "integer", "minimum": 0 },
        "critical": { "type": "integer", "minimum": 0 }
      }
    }
  }
}
```

When a tool cannot emit strict schema-validated JSON, still preserve the same field names and include enough evidence for downstream automation to classify findings.
