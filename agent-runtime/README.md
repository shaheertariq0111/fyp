# AgentCore Runtime Package

This package prepares the Strands restaurant ordering agent for Amazon Bedrock AgentCore Runtime.

It intentionally does not include the normal FastAPI application routes. The ECS backend remains responsible for REST APIs, admin auth, menu/cart/order/customer/session validation, and request-status APIs.

## Runtime Entry Point

The runtime entry point is:

```text
agent_runtime.handler:invoke
```

## Build Context

Build runtime artifacts from the repository root so the runtime package can install the shared backend package without duplicating menu, cart, order, customer, repository, or tool logic:

```powershell
docker build --platform linux/amd64 -f agent-runtime/Dockerfile -t fyp-agent-runtime:phase6 .
```

The handler accepts the same trusted invocation fields used by the ECS backend agent client:

```json
{
  "message": "Add a large pizza",
  "user_id": "cust-123",
  "agent_session_id": "web-session-123",
  "branch_id": "default",
  "customer_id": "cust-123",
  "customer_name": "Ava",
  "customer_phone": "+923001234567",
  "channel": "web"
}
```

## Required Environment

Configure these through AgentCore Runtime environment variables and IAM role permissions:

```text
ENVIRONMENT=production
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=<model-id>
BEDROCK_GUARDRAIL_ID=<optional>
BEDROCK_GUARDRAIL_VERSION=<optional>
KNOWLEDGE_BASE_ID=<optional>
KNOWLEDGE_BASE_MAX_RESULTS=5
MENU_TABLE_NAME=<table>
CARTS_TABLE_NAME=<table>
ORDERS_TABLE_NAME=<table>
CUSTOMERS_TABLE_NAME=<table>
AGENT_SESSIONS_TABLE_NAME=<table>
AGENT_REQUESTS_TABLE_NAME=<table>
MENU_SESSIONS_TABLE_NAME=<table>
AUDIT_TABLE_NAME=<table>
MENU_SITE_BASE_URL=<frontend-menu-url>
SESSION_TOKEN_SECRET=<from-secrets-manager>
SESSION_TOKEN_TTL_MINUTES=60
AGENT_SESSION_TTL_HOURS=24
AGENT_REQUEST_TTL_HOURS=24
STRANDS_SESSION_STORAGE_DIR=
RESTAURANT_ID=<restaurant-id>
BRANCH_ID=<branch-id>
LOG_LEVEL=INFO
AGENTCORE_MEMORY_ID=<memory-id>
```

`STRANDS_SESSION_STORAGE_DIR` must be empty or omitted in AgentCore so the runtime does not rely on local file-based Strands session storage.

## Manual Deployment Command Placeholder

Do not run this until the AgentCore execution role, memory, image/build artifact, and runtime settings are ready:

```powershell
# [CREATES OR UPDATES AWS RESOURCE] Deploys the Strands runtime to AgentCore.
aws bedrock-agentcore-control create-agent-runtime `
  --region us-east-1 `
  --agent-runtime-name fyp-dev-restaurant-ordering-agent `
  --agent-runtime-artifact <artifact-reference> `
  --role-arn <agentcore-execution-role-arn> `
  --environment-variables file://agent-runtime/env.agentcore.example.json
```

The exact artifact argument may differ depending on the AgentCore CLI/SDK version available in your AWS account. Keep this command as a deployment note until Phase 7/8 IAM and artifact packaging are finalized.
