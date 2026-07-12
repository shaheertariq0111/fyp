# Architecture

## Target Runtime Architecture

```text
Browser
  -> AWS Amplify Hosting
  -> API Gateway HTTP API
  -> VPC Link
  -> AWS Cloud Map SRV service
  -> ECS Fargate FastAPI backend
  -> Amazon Bedrock AgentCore Runtime
  -> Strands restaurant ordering agent
  -> Bedrock model, AgentCore Memory, and DynamoDB-backed tools
```

## Frontend

The frontend is a Next.js 15 application deployed with AWS Amplify Hosting. It uses:

- `NEXT_PUBLIC_API_BASE_URL` for the API Gateway endpoint.
- `NEXT_PUBLIC_BRANCH_ID` for branch selection.

The frontend does not calculate final prices or perform order-state transitions. Those stay in the backend.

## Public API

API Gateway HTTP API is the public backend endpoint. It uses:

- exact Amplify origin CORS
- credentials enabled for admin cookies
- exposed `X-Request-ID` and `X-Agent-Request-ID` response headers
- VPC Link private integration
- Cloud Map service discovery

No Application Load Balancer, Route 53 record, ACM certificate, or custom domain is part of this architecture.

## ECS Backend

The FastAPI backend remains responsible for:

- REST APIs
- admin authentication
- menu, cart, order, customer, and session services
- DynamoDB repositories
- request-status persistence
- AgentCore Runtime invocation
- structured CloudWatch logs

The backend image listens on port `8000` and exposes `/health`.

## Agent Runtime

The Strands reasoning agent is packaged separately under `agent-runtime/` for Amazon Bedrock AgentCore Runtime. AgentCore owns reasoning, tool selection, orchestration, and model invocation. The runtime reuses existing tool and service boundaries rather than duplicating menu, cart, order, or repository logic.

## Persistence

DynamoDB stores application data and async agent request state. Agent request status is not stored only in ECS memory, so ECS task restarts should not erase request status.

## Observability

Logs are JSON structured and safe-field only. Phase 11 monitoring is defined in `infra/phase11-monitoring.yaml` and includes API Gateway, DynamoDB, ECS, backend log-derived, and AgentCore log-derived alarms.

## Security Boundaries

- Secrets are stored in Secrets Manager.
- ECS receives sensitive values through ECS secret injection.
- IAM roles are separated for ECS execution, ECS application runtime, and AgentCore execution.
- AWS access keys are not stored in app configuration, Docker images, Amplify, or Git.
