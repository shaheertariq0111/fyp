# Phase 11 logging and monitoring

This document describes the CloudWatch logging fields and alarm targets for the deployed architecture. It does not create AWS resources by itself.

The Phase 11 monitoring resources are defined in:

```text
infra/phase11-monitoring.yaml
```

## Structured log fields

Backend and AgentCore runtime logs are JSON-formatted for CloudWatch Logs. Log records may include:

```text
timestamp
level
logger
message
event
http_request_id
request_id
actor_id
agent_session_id
session_id
route
method
status_code
response_time_ms
agentcore_invocation_status
agent_request_status
tool_name
tool_success
is_write
bedrock_response_time_ms
dynamodb_operation
dynamodb_table
error_code
channel
exception_type
```

Do not log request bodies, passwords, cookies, session tokens, AWS credentials, raw customer addresses, or full private conversation text.

## Existing log groups

The infrastructure template already defines:

```text
/ecs/<project-name>/backend
/aws/apigateway/<project-name>/backend-http-api
```

AgentCore runtime logs should use the AgentCore-managed CloudWatch log group for the deployed runtime. The exact group name must be confirmed after the AgentCore Runtime is created.

## Monitoring template resources

`infra/phase11-monitoring.yaml` creates configurable CloudWatch resources for:

- API Gateway `5XXError` using native `AWS/ApiGateway` metrics.
- API Gateway `Latency` or `IntegrationLatency` using native `AWS/ApiGateway` metrics.
- DynamoDB read/write throttling across the application tables using native `AWS/DynamoDB` metrics.
- Agent request failures using a CloudWatch Logs metric filter on `event = "agent_request_failed"`.
- AgentCore invocation failures using CloudWatch Logs metric filters on `event = "agentcore_invocation_failed"`.
- ECS running task failures using ECS Container Insights `RunningTaskCount`.

The AgentCore log group is parameterized as `AgentCoreLogGroupName` because the exact log group name only exists after the runtime is created. Leave it empty for the first monitoring deployment if AgentCore Runtime does not exist yet. The AgentCore runtime metric filter and runtime alarm both use the `HasAgentCoreLogGroup` CloudFormation condition, so they are not created while `AgentCoreLogGroupName` is empty.

The ECS running-task alarm uses the `HasEcsRunningTaskAlarm` CloudFormation condition. It is not created when `MinimumRunningTaskCount=0`; it is created when `MinimumRunningTaskCount` is greater than zero, and then uses `TreatMissingData=breaching`.

The application code does not call `cloudwatch:PutMetricData`; application-level metrics come from CloudWatch Logs metric filters. The ECS application task role therefore does not need `cloudwatch:PutMetricData`.

The template accepts optional `AlarmActions`, but no SNS topic or other notification action is configured by default. If `AlarmActions` is left empty, the alarms can enter `ALARM` state in CloudWatch but will not send notifications.

## Request ID headers

The backend returns `X-Request-ID` on all HTTP responses and `X-Agent-Request-ID` on chat request responses. API Gateway and FastAPI CORS expose both headers so browser code may read them. The canonical chat request identifier is also returned in the JSON response as `request_id`, which remains the frontend's normal polling identifier.

## Missing data behavior

The monitoring template uses these `TreatMissingData` values:

- ECS `RunningTaskCount`: `breaching` when the alarm is created. The alarm is not created while `MinimumRunningTaskCount=0`.
- API Gateway 5xx and latency: `notBreaching`
- DynamoDB throttling: `notBreaching`
- Log-derived agent and AgentCore failures: `notBreaching`

## ECS task monitoring choice

This project uses ECS Container Insights and alarms on `ECS/ContainerInsights` `RunningTaskCount`.

Behavior:

- The alarm detects when the backend ECS service has fewer running tasks than `MinimumRunningTaskCount`.
- `MinimumRunningTaskCount` is configurable. Use `0` while the ECS service is intentionally deployed with `DesiredCount=0`; the ECS running-task alarm will not be created in that bootstrap state.
- After the ECS service starts, update the monitoring stack with `MinimumRunningTaskCount=1`. That stack update creates the ECS running-task alarm with `TreatMissingData=breaching`.
- It can catch task crashes, failed deployments, and capacity/configuration problems.
- It depends on Container Insights being enabled on the ECS cluster, which is already configured in `infra/phase7-ecs-api.yaml`.

Cost:

- Container Insights publishes additional CloudWatch metrics and logs, so it can increase CloudWatch charges.
- For this low-traffic development deployment, the cost should remain modest, but it is not free.
- If cost becomes a concern, an EventBridge task-stopped rule can replace or supplement this alarm later.

## Recommended alarms

Create these alarms after the backend stack, API Gateway, DynamoDB tables, and AgentCore Runtime exist:

- ECS task failure: alert when running backend task count drops below 1.
- API Gateway 5xx: alert when HTTP API 5xx count is greater than 0 over a short window.
- API Gateway high latency: alert when p95/p99 latency is above the chosen threshold.
- AgentCore invocation failure: alert on JSON logs where `event` is `agentcore_invocation_failed`.
- DynamoDB throttling: alert on table read/write throttle events.
- Agent request failure rate: alert on JSON logs where `agent_request_status` is `failed`.

## Manual deployment command

Run this only when the backend log group, API Gateway, ECS service, and DynamoDB tables exist. Set `AgentCoreLogGroupName` only after the AgentCore Runtime log group exists.

If the backend stack is still intentionally running with `DesiredCount=0`, set `MinimumRunningTaskCount=0` in the parameter overrides. After the ECS service starts, update this monitoring stack with `MinimumRunningTaskCount=1`; that stack update creates the ECS running-task alarm.

`[CREATES AWS RESOURCES]`

```powershell
aws cloudformation deploy `
  --region us-east-1 `
  --stack-name fyp-dev-monitoring `
  --template-file infra/phase11-monitoring.yaml `
  --parameter-overrides `
    ProjectName=fyp-dev `
    HttpApiId=<http-api-id> `
    ApiStageName='$default' `
    EcsClusterName=<ecs-cluster-name> `
    EcsServiceName=<ecs-service-name> `
    MinimumRunningTaskCount=1 `
    BackendLogGroupName=/ecs/fyp-dev/backend `
    AgentCoreLogGroupName= `
    AgentRequestsTableName=fyp-dev-AgentRequests `
    MenuTableName=fyp-dev-Menu `
    CartsTableName=fyp-dev-Carts `
    OrdersTableName=fyp-dev-Orders `
    CustomersTableName=fyp-dev-Customers `
    AgentSessionsTableName=fyp-dev-AgentSessions `
    MenuSessionsTableName=fyp-dev-MenuSessions `
    AuditTableName=fyp-dev-Audit
```

Expected success output:

```text
Successfully created/updated stack - fyp-dev-monitoring
```

The template is the source of truth for Phase 11 monitoring resources. Avoid creating separate ad hoc alarms or metric filters unless the template is updated to match.
