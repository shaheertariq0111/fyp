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
docker build --platform linux/arm64 -f agent-runtime/Dockerfile -t fyp-agent-runtime:phase6 .
```

The container serves AgentCore HTTP Runtime traffic on port `8080`:

```text
GET /ping
POST /invocations
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
SESSION_TOKEN_SECRET_ARN=<session-token-secret-arn-if-menu-links-are-enabled>
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

Do not put the `SESSION_TOKEN_SECRET` value in the AgentCore environment JSON. The current runtime still uses the existing menu-link tool path, so when that tool is enabled pass only `SESSION_TOKEN_SECRET_ARN` and allow the AgentCore execution role to read that single secret.

## Manual Deployment Command Placeholder

Do not run this until the AgentCore execution role, memory, image/build artifact, and runtime settings are ready. Create an ignored `agent-runtime/env.agentcore.dev.json` from the example file and put real non-secret configuration plus secret ARNs there. Do not deploy `env.agentcore.example.json`.

The Phase 8 infrastructure template creates an `AgentRuntimeRepositoryUri` output for the ECR repository that should hold this image.

```powershell
# [CREATES OR UPDATES AWS RESOURCE] Deploys the Strands runtime to AgentCore.
aws bedrock-agentcore-control create-agent-runtime `
  --region us-east-1 `
  --agent-runtime-name fyp_dev_restaurant_ordering_agent `
  --agent-runtime-artifact "{`"containerConfiguration`":{`"containerUri`":`"<agent-runtime-ecr-uri>:<tag>`"}}" `
  --role-arn <agentcore-execution-role-arn> `
  --network-configuration "{`"networkMode`":`"PUBLIC`"}" `
  --protocol-configuration serverProtocol=HTTP `
  --environment-variables file://agent-runtime/env.agentcore.dev.json
```

If you choose the AgentCore `codeConfiguration`/S3 artifact path instead of ECR later, remove the AgentCore execution role's ECR image-pull policy before deployment.
