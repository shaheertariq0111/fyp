# Phase 9 secrets and configuration

This file documents the deployment configuration that must be supplied before creating/updating the AWS resources. Do not commit `.env` files, generated tokens, account IDs, or secret values.

## Secrets Manager values

Store these sensitive backend values in AWS Secrets Manager:

- `SESSION_TOKEN_SECRET`
- `AGENTFLO_WHATSAPP_WEBHOOK_SECRET`
- `AGENTFLO_GATEWAY_API_KEY`
- `ADMIN_PASSWORD`
- `ADMIN_SESSION_SECRET`

Use strong values generated locally. Do not paste real values into repository files.

## Manual secret creation commands

Run these only when you are ready to create the secrets in AWS.

`[CREATES AWS RESOURCES]`

```powershell
aws secretsmanager create-secret `
  --region us-east-1 `
  --name fyp-dev/session-token-secret `
  --description "FYP backend session token signing secret" `
  --secret-string "<replace-with-locally-generated-session-token-secret>"

aws secretsmanager create-secret `
  --region us-east-1 `
  --name fyp-dev/agentflo-whatsapp-webhook-secret `
  --description "FYP Agentflo WhatsApp webhook shared secret" `
  --secret-string "<replace-with-locally-generated-webhook-secret>"

aws secretsmanager create-secret `
  --region us-east-1 `
  --name fyp-dev/agentflo-gateway-api-key `
  --description "FYP Agentflo Communication Gateway API key" `
  --secret-string "<replace-with-agentflo-gateway-api-key>"

aws secretsmanager create-secret `
  --region us-east-1 `
  --name fyp-dev/admin-password `
  --description "FYP admin dashboard password" `
  --secret-string "<replace-with-admin-password>"

aws secretsmanager create-secret `
  --region us-east-1 `
  --name fyp-dev/admin-session-secret `
  --description "FYP admin session signing secret" `
  --secret-string "<replace-with-locally-generated-admin-session-secret>"
```

Expected output for each command:

```json
{
  "ARN": "arn:aws:secretsmanager:us-east-1:<account-id>:secret:fyp-dev/...",
  "Name": "fyp-dev/...",
  "VersionId": "..."
}
```

Capture the returned ARNs locally and pass them to the CloudFormation parameters:

- `SessionTokenSecretArn`
- `AgentfloWhatsAppWebhookSecretArn`
- `AgentfloGatewayApiKeySecretArn`
- `AdminPasswordSecretArn`
- `AdminSessionSecretArn`

## Manual secret update commands

Use these if a secret already exists and you need to rotate or correct it.

`[UPDATES AWS SECRET VALUES]`

```powershell
aws secretsmanager put-secret-value `
  --region us-east-1 `
  --secret-id fyp-dev/session-token-secret `
  --secret-string "<replace-with-new-session-token-secret>"

aws secretsmanager put-secret-value `
  --region us-east-1 `
  --secret-id fyp-dev/agentflo-whatsapp-webhook-secret `
  --secret-string "<replace-with-new-webhook-secret>"

aws secretsmanager put-secret-value `
  --region us-east-1 `
  --secret-id fyp-dev/agentflo-gateway-api-key `
  --secret-string "<replace-with-new-agentflo-gateway-api-key>"

aws secretsmanager put-secret-value `
  --region us-east-1 `
  --secret-id fyp-dev/admin-password `
  --secret-string "<replace-with-new-admin-password>"

aws secretsmanager put-secret-value `
  --region us-east-1 `
  --secret-id fyp-dev/admin-session-secret `
  --secret-string "<replace-with-new-admin-session-secret>"
```

Expected output for each command includes `ARN`, `Name`, `VersionId`, and `VersionStages`.

## ECS backend environment variables

Pass these as normal ECS environment variables through the CloudFormation template:

```text
ENVIRONMENT=production
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.amazon.nova-pro-v1:0
BEDROCK_GUARDRAIL_ID=
BEDROCK_GUARDRAIL_VERSION=
KNOWLEDGE_BASE_ID=
AGENTCORE_RUNTIME_ARN=<empty-until-AgentCore-runtime-is-created>
AGENT_REQUESTS_TABLE_NAME=<agent-requests-table>
CONVERSATION_MESSAGES_TABLE_NAME=<conversation-messages-table>
CONVERSATION_MESSAGE_TTL_DAYS=90
AGENTFLO_GATEWAY_BASE_URL=https://communicationgateway.agentflo.com
AGENTFLO_GATEWAY_TENANT_ID=fyp-dev
AGENTFLO_GATEWAY_AGENT_ID=restaurant-agent
AGENTFLO_GATEWAY_ACTOR_ID=restaurant-agent
WHATSAPP_VOICE_ENABLED=false
VOICE_MEDIA_BUCKET_NAME=<temporary-voice-media-bucket>
VOICE_MEDIA_INPUT_PREFIX=voice-input/
VOICE_JOB_QUEUE_URL=<voice-processing-queue-url>
WHATSAPP_VOICE_JOBS_TABLE_NAME=<cloudformation-managed-voice-jobs-table>
VOICE_MEDIA_ALLOWED_HOSTS=
VOICE_TRANSCRIPTION_LANGUAGE_CODE=
VOICE_TRANSCRIPTION_IDENTIFY_LANGUAGE=false
VOICE_SQS_WAIT_TIME_SECONDS=20
VOICE_SQS_VISIBILITY_TIMEOUT_SECONDS=300
VOICE_SQS_HEARTBEAT_SECONDS=60
VOICE_JOB_LEASE_SECONDS=180
VOICE_JOB_TTL_HOURS=24
VOICE_TRANSCRIPTION_JOB_PREFIX=fyp-dev-whatsapp-voice-
VOICE_TRANSCRIBE_ROLE_ARN=
VOICE_MAX_MEDIA_BYTES=10485760
VOICE_DOWNLOAD_TIMEOUT_SECONDS=10
VOICE_TRANSCRIPTION_TIMEOUT_SECONDS=180
MENU_TABLE_NAME=<menu-table>
CARTS_TABLE_NAME=<carts-table>
ORDERS_TABLE_NAME=<orders-table>
CUSTOMERS_TABLE_NAME=<customers-table>
AGENT_SESSIONS_TABLE_NAME=<agent-sessions-table>
MENU_SESSIONS_TABLE_NAME=<menu-sessions-table>
AUDIT_TABLE_NAME=<audit-table>
MENU_SITE_BASE_URL=https://<amplify-app>.amplifyapp.com/menu
RESTAURANT_ID=<restaurant-id>
BRANCH_ID=<branch-id>
LOG_LEVEL=INFO
FRONTEND_CORS_ORIGINS=https://<amplify-app>.amplifyapp.com
ADMIN_USERNAME=<admin-username>
STRANDS_SESSION_STORAGE_DIR=
```

`FRONTEND_CORS_ORIGINS` must be the exact Amplify HTTPS origin. For this deployment, pass one origin only. Do not use `*`, `localhost`, or `127.0.0.1` in production.

The voice settings are non-secret infrastructure references and safety limits.
`VOICE_MEDIA_BUCKET_NAME` points to the dedicated temporary customer-audio
bucket. Later application code must delete `voice-input/` objects promptly after
processing. The one-day S3 lifecycle rule is an asynchronous fallback. The SQS
queue and S3 bucket do not activate voice handling by themselves. Keep
`WHATSAPP_VOICE_ENABLED=false` until the media-validation backend and durable ECS
worker are implemented, and deploy the Phase 7 CloudFormation changes before
enabling any later voice code.

`VOICE_TRANSCRIBE_ROLE_ARN` is optional, non-secret configuration used only by
the standalone voice worker. Empty preserves same-account Transcribe. A
configured ARN must identify an externally created role in the Transcribe
account that trusts only the primary voice-worker task role. The primary stack
does not create resources in another AWS account and stores no permanent
secondary-account credentials. The worker assumes the role through STS once per
transcription. S3 upload and deletion remain in the primary account; the
external role receives only List/Get access for `voice-input/*` through its
identity policy and the primary bucket policy.

## Disabled voice-worker task configuration

Pass B1 defines a separate ECS service that uses the backend image with the
command `python -m src.workers.whatsapp_voice_worker`. Its desired count defaults
to `0`; the web feature flag and worker `WHATSAPP_VOICE_ENABLED` value remain
`false`. The worker receives the existing application table names, the dedicated
voice-jobs table, Standard queue URL, temporary S3 bucket, timing limits,
AgentCore configuration, and Agentflo gateway configuration.
Only the worker receives `VOICE_TRANSCRIBE_ROLE_ARN`; it is not passed to the
web/API container, AgentCore runtime, or an ECS execution role.

Only these secrets are eligible for injection into the worker:

```text
SESSION_TOKEN_SECRET <- SessionTokenSecretArn
AGENTFLO_GATEWAY_API_KEY <- AgentfloGatewayApiKeySecretArn
```

The worker does not receive `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET`, or
`AGENTFLO_WHATSAPP_WEBHOOK_SECRET`. The media hostname and exactly one
transcription language mode must be confirmed before a later controlled
activation. The durable jobs table stores state and leases, not transcripts.
The existing Standard queue, Standard DLQ, and temporary S3 bucket are preserved.

## CORS and admin cookie boundary

API Gateway HTTP API is the public CORS source of truth for the deployed `amplifyapp.com` -> `execute-api.amazonaws.com` architecture. The CloudFormation template configures the same exact single `FrontendCorsOrigins` value with:

- `AllowCredentials: true`
- `AllowOrigins`: the exact Amplify origin only
- `AllowMethods`: `GET`, `POST`, `PUT`, `PATCH`, `OPTIONS`
- `AllowHeaders`: `Content-Type`
- `ExposeHeaders`: `X-Request-ID`, `X-Agent-Request-ID`

FastAPI mirrors those methods, headers, exposed headers, credentials, and origins from `FRONTEND_CORS_ORIGINS` so direct local/backend calls remain consistent with API Gateway. In `staging` and `production`, admin login cookies are `HttpOnly`, `Secure`, and `SameSite=None` for cross-site Amplify/API Gateway requests.

## ECS backend secrets

The ECS task execution role injects these secrets into the container:

```text
SESSION_TOKEN_SECRET <- SessionTokenSecretArn
AGENTFLO_WHATSAPP_WEBHOOK_SECRET <- AgentfloWhatsAppWebhookSecretArn
AGENTFLO_GATEWAY_API_KEY <- AgentfloGatewayApiKeySecretArn
ADMIN_PASSWORD <- AdminPasswordSecretArn
ADMIN_SESSION_SECRET <- AdminSessionSecretArn
```

The ECS application task role does not need direct Secrets Manager read permissions for these values.

Conversation history storage writes WhatsApp customer and agent message text to
`CONVERSATION_MESSAGES_TABLE_NAME` by design. The backend still must not log
message text, full phone numbers, API keys, tokens, webhook secrets, or delivery
addresses.

## Amplify frontend environment variables

Configure these in Amplify Hosting after the backend API endpoint is available:

```text
NEXT_PUBLIC_API_BASE_URL=https://<api-id>.execute-api.us-east-1.amazonaws.com
NEXT_PUBLIC_BRANCH_ID=<branch-id>
```

`NEXT_PUBLIC_MENU_BASE_URL` is not used by the current frontend. Menu-session URLs are generated by the backend from `MENU_SITE_BASE_URL`.

## Deployment bootstrap sequence

Use this order to avoid wildcard CORS and avoid starting ECS tasks before all public URLs are known:

1. Create or update the backend stack with `DesiredCount=0`, `FrontendCorsOrigins=https://bootstrap.invalid`, and a safe placeholder `MenuSiteBaseUrl`.
2. Capture the `BackendApiEndpoint` output from the stack.
3. Deploy Amplify with `NEXT_PUBLIC_API_BASE_URL=<BackendApiEndpoint>` and `NEXT_PUBLIC_BRANCH_ID=<branch-id>`.
4. Capture the generated Amplify URL, for example `https://main.<app-id>.amplifyapp.com`.
5. Update the backend stack with `FrontendCorsOrigins=<exact-amplify-origin>` and `MenuSiteBaseUrl=<exact-amplify-origin>/menu`.
6. Push the backend image, create/update the AgentCore Runtime when its image, role, memory, and configuration are ready, and then update `AgentCoreRuntimeArn`.
7. Update the backend stack with `DesiredCount=1`.

## AgentCore Runtime configuration still pending

Do not create the AgentCore Runtime yet unless these are known:

- `AgentRuntimeRepositoryUri` image URI and pushed image tag
- `AgentCoreExecutionRoleArn`
- `AgentCoreMemoryArn` or memory ID if using AgentCore Memory
- final table names
- final `MENU_SITE_BASE_URL`
- `SESSION_TOKEN_SECRET_ARN` if menu-link tools remain enabled in AgentCore

The runtime creation step remains pending and must be run manually later with `[CREATES AWS RESOURCE]`.

Do not place the `SESSION_TOKEN_SECRET` value in `agent-runtime/env.agentcore.example.json` or any AgentCore environment JSON. The current runtime can still call the existing menu-link tool path, so if that behavior remains enabled AgentCore receives only `SESSION_TOKEN_SECRET_ARN` and the AgentCore execution role reads that single secret from Secrets Manager. A later refactor can move menu-link token creation fully behind ECS and remove this AgentCore secret-read path.
