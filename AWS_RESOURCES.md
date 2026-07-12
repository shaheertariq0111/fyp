# AWS Resources

## Region

Use `us-east-1`.

## Backend Stack

Defined in `infra/phase7-ecs-api.yaml`.

Creates or configures:

- ECR backend repository
- ECR AgentCore runtime repository
- ECS cluster with Container Insights
- ECS Fargate task definition and service
- public-subnet ECS networking for low-cost development
- ECS security group restricted to VPC Link ingress on port `8000`
- API Gateway HTTP API
- API Gateway `$default` stage and access logs
- API Gateway VPC Link
- Cloud Map private namespace and SRV service
- CloudWatch log groups
- optional AgentRequests DynamoDB table
- ECS task execution role
- ECS application task role
- AgentCore execution role

Does not create:

- Application Load Balancer
- Route 53
- ACM certificate
- custom domain
- Amplify app
- AgentCore Runtime

## Secrets

Created manually in Secrets Manager:

- `fyp-dev/session-token-secret`
- `fyp-dev/admin-password`
- `fyp-dev/admin-session-secret`

## Amplify

Created manually in the AWS Amplify console with GitHub connection and monorepo app root `frontend`.

## AgentCore

AgentCore Runtime is created manually after:

- runtime image exists in ECR
- `AgentCoreExecutionRoleArn` is known
- memory exists if using AgentCore Memory
- environment JSON uses ARNs and names only, not secret values

## Monitoring Stack

Defined in `infra/phase11-monitoring.yaml`.

Creates:

- API Gateway 5xx alarm
- API Gateway latency alarm
- DynamoDB throttling alarm
- backend agent request failure metric filter and alarm
- backend AgentCore invocation failure metric filter and alarm
- conditional AgentCore runtime failure metric filter and alarm
- conditional ECS RunningTaskCount alarm

No SNS topic or notification action is configured by default. Alarms will not send notifications unless `AlarmActions` is supplied.

## Cost Categories

Expected recurring cost categories:

- Amplify Hosting build and hosting minutes
- API Gateway HTTP API requests
- VPC Link hourly cost
- ECS Fargate vCPU and memory
- ECR storage
- CloudWatch Logs ingestion and storage
- CloudWatch metrics and alarms
- DynamoDB read/write/storage/TTL-related usage
- Secrets Manager per-secret monthly charge
- Bedrock model usage
- AgentCore Runtime and Memory usage
