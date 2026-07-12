# Deployment Guide

This guide is the manual deployment sequence for the final architecture:

```text
Amplify frontend -> API Gateway HTTP API -> VPC Link -> Cloud Map -> ECS Fargate backend -> AgentCore Runtime
```

Do not paste secrets, account IDs, generated tokens, real `.env` files, or secret values into the repository.

## 1. Local Validation

Run from the repository root unless a command changes directory.

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest

cd ..\agent-runtime
$env:PYTHONPATH='E:\fyp-agent\agent-runtime\src;E:\fyp-agent\backend'
E:\fyp-agent\.venv\Scripts\python.exe -m pytest tests

cd ..\frontend
npm ci
npm run typecheck
npm run lint
npm run build

cd ..
aws cloudformation validate-template --region us-east-1 --template-body file://infra/phase7-ecs-api.yaml
aws cloudformation validate-template --region us-east-1 --template-body file://infra/phase11-monitoring.yaml
```

## 2. Pre-Deployment DynamoDB Verification

Before creating ECS or AgentCore resources, verify that every required application table exists and is `ACTIVE`. Replace table names with the actual names you created or will pass to CloudFormation.

```powershell
$MenuTableName = "<actual-menu-table>"
$CartsTableName = "<actual-carts-table>"
$OrdersTableName = "<actual-orders-table>"
$CustomersTableName = "<actual-customers-table>"
$AgentSessionsTableName = "<actual-agent-sessions-table>"
$AgentRequestsTableName = "<actual-agent-requests-table>"
$MenuSessionsTableName = "<actual-menu-sessions-table>"
$AuditTableName = "<actual-audit-table>"

$tables = @(
  $MenuTableName,
  $CartsTableName,
  $OrdersTableName,
  $CustomersTableName,
  $AgentSessionsTableName,
  $AgentRequestsTableName,
  $MenuSessionsTableName,
  $AuditTableName
)

foreach ($table in $tables) {
  aws dynamodb describe-table `
    --region us-east-1 `
    --table-name $table `
    --query "Table.{TableName:TableName,Status:TableStatus,ItemCount:ItemCount}" `
    --output table
}
```

Expected result: every table reports `ACTIVE`.

Verify menu seed data exists before deploying user-facing flows:

```powershell
aws dynamodb scan `
  --region us-east-1 `
  --table-name $MenuTableName `
  --select COUNT `
  --query "{Table:'$MenuTableName',Count:Count,ScannedCount:ScannedCount}" `
  --output table
```

Expected result: `Count` is greater than `0`.

## 3. Backend Docker Image

The ECS backend image is `linux/amd64`.

```powershell
docker build --platform linux/amd64 -t fyp-dev-backend:phase12 backend
```

Container health smoke test:

```powershell
docker run --rm -d --name fyp-dev-backend-health -p 8000:8000 fyp-dev-backend:phase12
Start-Sleep -Seconds 20
Invoke-RestMethod http://localhost:8000/health
docker stop fyp-dev-backend-health
```

Expected health output:

```json
{"status":"ok"}
```

## 4. Backend Secrets

Run only when you are ready to create secrets.

`[CREATES AWS RESOURCES]`

```powershell
aws secretsmanager create-secret --region us-east-1 --name fyp-dev/session-token-secret --description "FYP backend session token signing secret" --secret-string "<replace-with-locally-generated-session-token-secret>"
aws secretsmanager create-secret --region us-east-1 --name fyp-dev/admin-password --description "FYP admin dashboard password" --secret-string "<replace-with-admin-password>"
aws secretsmanager create-secret --region us-east-1 --name fyp-dev/admin-session-secret --description "FYP admin session signing secret" --secret-string "<replace-with-locally-generated-admin-session-secret>"
```

Expected output includes each secret `ARN`, `Name`, and `VersionId`. Store the ARNs locally.

## 5. Initial Backend Infrastructure

Create the backend stack first with `BackendImageUri=` and `DesiredCount=0`. The CloudFormation template conditionally skips the ECS task definition and ECS service while `BackendImageUri` is empty, so the bootstrap stack cannot create a task definition with an empty or fake image.

`[CREATES OR UPDATES AWS RESOURCES]`

```powershell
aws cloudformation deploy `
  --region us-east-1 `
  --stack-name fyp-dev-backend-api `
  --template-file infra/phase7-ecs-api.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    ProjectName=fyp-dev `
    VpcId=<vpc-id> `
    PublicSubnetIds=<public-subnet-a>,<public-subnet-b> `
    BackendImageUri= `
    DesiredCount=0 `
    FrontendCorsOrigins=https://bootstrap.invalid `
    MenuSiteBaseUrl=https://bootstrap.invalid/menu `
    BedrockModelId=us.amazon.nova-pro-v1:0 `
    MenuTableName=$MenuTableName `
    CartsTableName=$CartsTableName `
    OrdersTableName=$OrdersTableName `
    CustomersTableName=$CustomersTableName `
    AgentSessionsTableName=$AgentSessionsTableName `
    AgentRequestsTableName=$AgentRequestsTableName `
    MenuSessionsTableName=$MenuSessionsTableName `
    AuditTableName=$AuditTableName `
    AgentCoreRuntimeArn= `
    AgentCoreMemoryArn= `
    AgentCoreSessionTokenSecretArn= `
    SessionTokenSecretArn=<session-token-secret-arn> `
    AdminPasswordSecretArn=<admin-password-secret-arn> `
    AdminSessionSecretArn=<admin-session-secret-arn>
```

Expected output:

```text
Successfully created/updated stack - fyp-dev-backend-api
```

Capture actual CloudFormation outputs:

```powershell
$StackName = "fyp-dev-backend-api"
$outputs = aws cloudformation describe-stacks `
  --region us-east-1 `
  --stack-name $StackName `
  --query "Stacks[0].Outputs" `
  --output json | ConvertFrom-Json

function Get-StackOutput($key) {
  ($outputs | Where-Object { $_.OutputKey -eq $key }).OutputValue
}

$BackendRepositoryUri = Get-StackOutput "BackendRepositoryUri"
$AgentRuntimeRepositoryUri = Get-StackOutput "AgentRuntimeRepositoryUri"
$BackendApiEndpoint = Get-StackOutput "BackendApiEndpoint"
$EcsClusterName = Get-StackOutput "EcsClusterName"
$BackendCloudMapServiceId = Get-StackOutput "BackendCloudMapServiceId"
$AgentCoreExecutionRoleArn = Get-StackOutput "AgentCoreExecutionRoleArn"
$AgentRequestsTableName = Get-StackOutput "AgentRequestsTableName"
$MenuTableName = Get-StackOutput "MenuTableName"
$CartsTableName = Get-StackOutput "CartsTableName"
$OrdersTableName = Get-StackOutput "OrdersTableName"
$CustomersTableName = Get-StackOutput "CustomersTableName"
$AgentSessionsTableName = Get-StackOutput "AgentSessionsTableName"
$MenuSessionsTableName = Get-StackOutput "MenuSessionsTableName"
$AuditTableName = Get-StackOutput "AuditTableName"
```

`BackendServiceName` is not output during the empty-image bootstrap. It appears after the stack is updated with a real `BackendImageUri`.

## 6. Push Backend Image To ECR

`[AUTHENTICATES TO AWS ECR]`

```powershell
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com
```

```powershell
docker tag fyp-dev-backend:phase12 ${BackendRepositoryUri}:phase12
```

`[PUSHES IMAGE TO AWS ECR]`

```powershell
docker push ${BackendRepositoryUri}:phase12
```

Expected output ends with an image digest.

## 7. Amplify Frontend

Amplify setup is manual in the AWS console.

`[CREATES AWS RESOURCES]`

1. Open AWS Amplify Hosting in `us-east-1`.
2. Create a new app from GitHub.
3. Select the repository and Git branch to deploy.
4. Select **My app is a monorepo** and set app root to `frontend`.
5. Confirm `AMPLIFY_MONOREPO_APP_ROOT=frontend`.
6. Set:

```text
NEXT_PUBLIC_API_BASE_URL=<BackendApiEndpoint>
NEXT_PUBLIC_BRANCH_ID=default
```

`NEXT_PUBLIC_BRANCH_ID` is the restaurant branch ID used by the application, likely `default`; it is not the Git branch name.

Deploy and capture the generated Amplify URL:

```text
https://main.<app-id>.amplifyapp.com
```

## 8. Update Backend CORS And Menu URL

`[UPDATES AWS RESOURCES]`

```powershell
aws cloudformation deploy `
  --region us-east-1 `
  --stack-name fyp-dev-backend-api `
  --template-file infra/phase7-ecs-api.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    ProjectName=fyp-dev `
    VpcId=<vpc-id> `
    PublicSubnetIds=<public-subnet-a>,<public-subnet-b> `
    BackendImageUri=${BackendRepositoryUri}:phase12 `
    DesiredCount=0 `
    FrontendCorsOrigins=https://main.<app-id>.amplifyapp.com `
    MenuSiteBaseUrl=https://main.<app-id>.amplifyapp.com/menu `
    BedrockModelId=us.amazon.nova-pro-v1:0 `
    MenuTableName=$MenuTableName `
    CartsTableName=$CartsTableName `
    OrdersTableName=$OrdersTableName `
    CustomersTableName=$CustomersTableName `
    AgentSessionsTableName=$AgentSessionsTableName `
    AgentRequestsTableName=$AgentRequestsTableName `
    MenuSessionsTableName=$MenuSessionsTableName `
    AuditTableName=$AuditTableName `
    AgentCoreRuntimeArn= `
    AgentCoreMemoryArn= `
    AgentCoreSessionTokenSecretArn=<session-token-secret-arn-if-agentcore-menu-links-are-enabled> `
    SessionTokenSecretArn=<session-token-secret-arn> `
    AdminPasswordSecretArn=<admin-password-secret-arn> `
    AdminSessionSecretArn=<admin-session-secret-arn>
```

Refresh stack outputs after this update:

```powershell
$outputs = aws cloudformation describe-stacks --region us-east-1 --stack-name $StackName --query "Stacks[0].Outputs" --output json | ConvertFrom-Json
$BackendServiceName = Get-StackOutput "BackendServiceName"
```

## 9. AgentCore Short-Term Memory

Create the short-term AgentCore Memory before creating the runtime. This project uses short-term event memory only; do not configure long-term profiling strategies.

`[CREATES AWS RESOURCE]`

```powershell
aws bedrock-agentcore-control create-memory `
  --region us-east-1 `
  --name fyp_dev_restaurant_ordering_memory `
  --description "Short-term memory for the FYP restaurant ordering agent" `
  --event-expiry-duration 30 `
  --query "memory.{id:id,arn:arn,status:status}" `
  --output json
```

Capture the returned `id` and `arn` locally:

```powershell
$AgentCoreMemoryId = "<memory-id-from-create-memory>"
$AgentCoreMemoryArn = "<memory-arn-from-create-memory>"
```

Wait until memory is `ACTIVE`:

```powershell
aws bedrock-agentcore-control wait memory-created `
  --region us-east-1 `
  --memory-id $AgentCoreMemoryId

aws bedrock-agentcore-control get-memory `
  --region us-east-1 `
  --memory-id $AgentCoreMemoryId `
  --query "memory.{id:id,arn:arn,status:status}" `
  --output table
```

Expected status: `ACTIVE`.

## 10. AgentCore Runtime

Verify the AgentCore runtime container contract:

- build platform: `linux/arm64`
- container port: `8080`
- health route: `GET /ping`
- invocation route: `POST /invocations`
- Dockerfile command: `uvicorn agent_runtime.server:app --host 0.0.0.0 --port 8080`

Build the AgentCore runtime image from the repository root:

```powershell
docker build --platform linux/arm64 -f agent-runtime/Dockerfile -t fyp-dev-agent-runtime:phase12 .
docker tag fyp-dev-agent-runtime:phase12 ${AgentRuntimeRepositoryUri}:phase12
```

`[PUSHES IMAGE TO AWS ECR]`

```powershell
docker push ${AgentRuntimeRepositoryUri}:phase12
```

Create an ignored runtime environment file. Do not deploy `agent-runtime/env.agentcore.example.json`; it contains placeholders only.

```powershell
Copy-Item agent-runtime\env.agentcore.example.json agent-runtime\env.agentcore.dev.json
```

Edit `agent-runtime/env.agentcore.dev.json` locally. Use real non-secret configuration, table names, and secret ARNs only. Do not put `SESSION_TOKEN_SECRET`, passwords, generated tokens, AWS access keys, or account IDs in the file except where an ARN inherently contains your account ID.

Required values include:

```text
MENU_TABLE_NAME=$MenuTableName
CARTS_TABLE_NAME=$CartsTableName
ORDERS_TABLE_NAME=$OrdersTableName
CUSTOMERS_TABLE_NAME=$CustomersTableName
AGENT_SESSIONS_TABLE_NAME=$AgentSessionsTableName
AGENT_REQUESTS_TABLE_NAME=$AgentRequestsTableName
MENU_SESSIONS_TABLE_NAME=$MenuSessionsTableName
AUDIT_TABLE_NAME=$AuditTableName
MENU_SITE_BASE_URL=https://main.<app-id>.amplifyapp.com/menu
SESSION_TOKEN_SECRET_ARN=<session-token-secret-arn-if-agentcore-menu-links-are-enabled>
AGENTCORE_MEMORY_ID=$AgentCoreMemoryId
```

Create the AgentCore Runtime with a valid name. Hyphens are not allowed by the current AWS CLI pattern; use letters, numbers, and underscores.

`[CREATES AWS RESOURCE]`

```powershell
aws bedrock-agentcore-control create-agent-runtime `
  --region us-east-1 `
  --agent-runtime-name fyp_dev_restaurant_ordering_agent `
  --agent-runtime-artifact "{`"containerConfiguration`":{`"containerUri`":`"${AgentRuntimeRepositoryUri}:phase12`"}}" `
  --role-arn $AgentCoreExecutionRoleArn `
  --network-configuration "{`"networkMode`":`"PUBLIC`"}" `
  --protocol-configuration serverProtocol=HTTP `
  --environment-variables file://agent-runtime/env.agentcore.dev.json `
  --query "{arn:agentRuntimeArn,id:agentRuntimeId,version:agentRuntimeVersion,status:status}" `
  --output json
```

Capture the returned runtime values:

```powershell
$AgentCoreRuntimeArn = "<agent-runtime-arn>"
$AgentCoreRuntimeId = "<agent-runtime-id>"
```

There is no runtime-ready waiter in the current AWS CLI. Poll until status is `READY`; do not start ECS before this passes.

```powershell
do {
  $runtimeStatus = aws bedrock-agentcore-control get-agent-runtime `
    --region us-east-1 `
    --agent-runtime-id $AgentCoreRuntimeId `
    --query "status" `
    --output text

  Write-Host "AgentCore runtime status: $runtimeStatus"
  if ($runtimeStatus -in @("CREATE_FAILED", "UPDATE_FAILED", "DELETING")) {
    throw "AgentCore runtime is not deployable: $runtimeStatus"
  }
  if ($runtimeStatus -ne "READY") {
    Start-Sleep -Seconds 20
  }
} while ($runtimeStatus -ne "READY")
```

Smoke-test direct AgentCore invocation:

```powershell
'{"message":"hello","user_id":"smoke-user","agent_session_id":"smoke-session-123456789012345678901","branch_id":"default","channel":"web"}' |
  Set-Content -NoNewline -Path .\agentcore-smoke-request.json

aws bedrock-agentcore invoke-agent-runtime `
  --region us-east-1 `
  --agent-runtime-arn $AgentCoreRuntimeArn `
  --runtime-session-id smoke-session-123456789012345678901 `
  --content-type application/json `
  --accept application/json `
  --payload fileb://agentcore-smoke-request.json `
  agentcore-smoke-response.json

Get-Content .\agentcore-smoke-response.json
```

## 11. Start ECS Backend

Only start ECS after AgentCore Runtime is `READY`.

`[UPDATES AWS RESOURCES]`

```powershell
aws cloudformation deploy `
  --region us-east-1 `
  --stack-name fyp-dev-backend-api `
  --template-file infra/phase7-ecs-api.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    ProjectName=fyp-dev `
    VpcId=<vpc-id> `
    PublicSubnetIds=<public-subnet-a>,<public-subnet-b> `
    BackendImageUri=${BackendRepositoryUri}:phase12 `
    DesiredCount=1 `
    FrontendCorsOrigins=https://main.<app-id>.amplifyapp.com `
    MenuSiteBaseUrl=https://main.<app-id>.amplifyapp.com/menu `
    BedrockModelId=us.amazon.nova-pro-v1:0 `
    MenuTableName=$MenuTableName `
    CartsTableName=$CartsTableName `
    OrdersTableName=$OrdersTableName `
    CustomersTableName=$CustomersTableName `
    AgentSessionsTableName=$AgentSessionsTableName `
    AgentRequestsTableName=$AgentRequestsTableName `
    MenuSessionsTableName=$MenuSessionsTableName `
    AuditTableName=$AuditTableName `
    AgentCoreRuntimeArn=$AgentCoreRuntimeArn `
    AgentCoreMemoryArn=$AgentCoreMemoryArn `
    AgentCoreSessionTokenSecretArn=<session-token-secret-arn-if-agentcore-menu-links-are-enabled> `
    SessionTokenSecretArn=<session-token-secret-arn> `
    AdminPasswordSecretArn=<admin-password-secret-arn> `
    AdminSessionSecretArn=<admin-session-secret-arn>
```

Refresh outputs:

```powershell
$outputs = aws cloudformation describe-stacks --region us-east-1 --stack-name $StackName --query "Stacks[0].Outputs" --output json | ConvertFrom-Json
$BackendServiceName = Get-StackOutput "BackendServiceName"
$BackendApiEndpoint = Get-StackOutput "BackendApiEndpoint"
$BackendCloudMapServiceId = Get-StackOutput "BackendCloudMapServiceId"
```

## 12. Monitoring Stack

Use actual outputs and table names from the backend stack. Use `MinimumRunningTaskCount=0` while ECS is intentionally stopped. After ECS starts, use `MinimumRunningTaskCount=1`; that stack update creates the ECS running-task alarm.

```powershell
$HttpApiId = ($BackendApiEndpoint -replace '^https://', '' -replace '\.execute-api\..+$', '')
```

`[CREATES OR UPDATES AWS RESOURCES]`

```powershell
aws cloudformation deploy `
  --region us-east-1 `
  --stack-name fyp-dev-monitoring `
  --template-file infra/phase11-monitoring.yaml `
  --parameter-overrides `
    ProjectName=fyp-dev `
    HttpApiId=$HttpApiId `
    ApiStageName='$default' `
    EcsClusterName=$EcsClusterName `
    EcsServiceName=$BackendServiceName `
    MinimumRunningTaskCount=1 `
    BackendLogGroupName=/ecs/fyp-dev/backend `
    AgentCoreLogGroupName=<actual-agentcore-log-group-or-empty> `
    AgentRequestsTableName=$AgentRequestsTableName `
    MenuTableName=$MenuTableName `
    CartsTableName=$CartsTableName `
    OrdersTableName=$OrdersTableName `
    CustomersTableName=$CustomersTableName `
    AgentSessionsTableName=$AgentSessionsTableName `
    MenuSessionsTableName=$MenuSessionsTableName `
    AuditTableName=$AuditTableName
```

Expected output:

```text
Successfully created/updated stack - fyp-dev-monitoring
```

## 13. Final Post-Deployment Checks

API health:

```powershell
Invoke-RestMethod "$BackendApiEndpoint/health"
```

ECS service:

```powershell
aws ecs describe-services `
  --region us-east-1 `
  --cluster $EcsClusterName `
  --services $BackendServiceName `
  --query "services[0].{status:status,desired:desiredCount,running:runningCount,pending:pendingCount,deployments:deployments[*].rolloutState}" `
  --output table
```

Cloud Map service instances:

```powershell
aws servicediscovery list-instances `
  --region us-east-1 `
  --service-id $BackendCloudMapServiceId `
  --query "Instances[*].{Id:Id,Attributes:Attributes}" `
  --output table
```

AgentCore runtime:

```powershell
aws bedrock-agentcore-control get-agent-runtime `
  --region us-east-1 `
  --agent-runtime-id $AgentCoreRuntimeId `
  --query "{id:agentRuntimeId,status:status,arn:agentRuntimeArn}" `
  --output table
```

Async chat:

```powershell
$chat = Invoke-RestMethod `
  -Method Post `
  -Uri "$BackendApiEndpoint/api/chat" `
  -ContentType "application/json" `
  -Body '{"message":"show me the menu","session_id":"smoke-session","user_id":"smoke-user"}'

$chat
Invoke-RestMethod "$BackendApiEndpoint/api/chat/$($chat.request_id)"
```

Menu/cart/order flows:

```powershell
Invoke-RestMethod "$BackendApiEndpoint/api/menu"
```

Then verify from the Amplify UI:

- view menu
- search menu
- add item to cart
- modify quantity
- remove item
- confirm order
- check order status
- start new conversation

Amplify CORS:

```powershell
curl.exe -i `
  -H "Origin: https://main.<app-id>.amplifyapp.com" `
  "$BackendApiEndpoint/health"
```

Expected headers include the exact Amplify origin and exposed request ID headers.

CloudWatch logs:

```powershell
aws logs tail /ecs/fyp-dev/backend --region us-east-1 --since 15m
```

Expected backend logs are JSON and include safe fields such as `event`, `http_request_id`, `route`, `status_code`, and `response_time_ms`.

Cross-site admin authentication:

1. Open `https://main.<app-id>.amplifyapp.com/admin/login`.
2. Log in with the configured admin credentials.
3. Confirm authenticated admin pages load.
4. In browser dev tools, verify the admin cookie is `HttpOnly`, `Secure`, and `SameSite=None`.

## 14. Rollback

To roll back a failed backend deployment, redeploy the previous known-good image tag.

`[UPDATES AWS RESOURCES]`

```powershell
aws cloudformation deploy `
  --region us-east-1 `
  --stack-name fyp-dev-backend-api `
  --template-file infra/phase7-ecs-api.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --parameter-overrides `
    ProjectName=fyp-dev `
    VpcId=<vpc-id> `
    PublicSubnetIds=<public-subnet-a>,<public-subnet-b> `
    BackendImageUri=${BackendRepositoryUri}:<previous-good-tag> `
    DesiredCount=1 `
    FrontendCorsOrigins=https://main.<app-id>.amplifyapp.com `
    MenuSiteBaseUrl=https://main.<app-id>.amplifyapp.com/menu `
    BedrockModelId=us.amazon.nova-pro-v1:0 `
    MenuTableName=$MenuTableName `
    CartsTableName=$CartsTableName `
    OrdersTableName=$OrdersTableName `
    CustomersTableName=$CustomersTableName `
    AgentSessionsTableName=$AgentSessionsTableName `
    AgentRequestsTableName=$AgentRequestsTableName `
    MenuSessionsTableName=$MenuSessionsTableName `
    AuditTableName=$AuditTableName `
    AgentCoreRuntimeArn=$AgentCoreRuntimeArn `
    AgentCoreMemoryArn=$AgentCoreMemoryArn `
    AgentCoreSessionTokenSecretArn=<session-token-secret-arn-if-agentcore-menu-links-are-enabled> `
    SessionTokenSecretArn=<session-token-secret-arn> `
    AdminPasswordSecretArn=<admin-password-secret-arn> `
    AdminSessionSecretArn=<admin-session-secret-arn>
```

To pause the backend while investigating, redeploy the full parameter set with `DesiredCount=0`. Do not use a short partial command unless your deployment process preserves all previous parameters.
