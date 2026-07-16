# Agent Instructions for Codex

## Source of Truth

Before making deployment or infrastructure decisions, read:

* `docs/deployment.md`

Treat this file as the primary deployment and architecture source of truth.

Do not reference or depend on the old PRD unless the user explicitly asks you to.

## Working Method

Work strictly one deployment phase at a time.

* Implement only the current phase.
* Do not begin the next phase until the user explicitly says to proceed.
* Inspect the existing repository before making changes.
* Reuse existing architecture and working code.
* Do not introduce unrelated features or refactors.
* Do not implement future deployment phases early.

## AWS Infrastructure Rules

The user will run all AWS infrastructure commands manually.

Do not run commands that create, update, deploy, replace, or delete AWS resources unless the user explicitly tells you to run them.

This includes:

* AWS CLI
* ECR login and image push
* ECS deployment
* CloudFormation
* CDK
* SAM
* AgentCore deployment
* API Gateway deployment
* Amplify deployment
* DynamoDB creation
* IAM changes
* Secrets Manager operations

When AWS work is required:

1. Provide the exact commands in the correct order.
2. Explain what each command creates or changes.
3. Show the expected output.
4. Clearly warn before any destructive or replacement operation.
5. Stop and wait for the user to run the commands.

## Deployment Architecture

Follow the architecture defined in `docs/deployment.md`.

The intended deployment includes:

* Next.js frontend on AWS Amplify
* FastAPI backend on ECS Fargate
* Backend container images stored in ECR
* API Gateway HTTP API as the public backend endpoint
* API Gateway VPC Link and AWS Cloud Map for ECS integration
* Strands agent deployed to Amazon Bedrock AgentCore Runtime
* AgentCore Memory for short-term conversation memory
* DynamoDB for persistent application and request data
* Secrets Manager for sensitive configuration
* CloudWatch for logs and monitoring

Do not add an Application Load Balancer, Route 53, ACM, or a custom domain unless explicitly instructed.

## Code Architecture Rules

* Do not hardcode table names, model IDs, API URLs, resource ARNs, account IDs, credentials, or secrets.
* Use environment variables for configuration.
* Use IAM roles instead of AWS access keys in deployed services.
* FastAPI route handlers should remain thin.
* Services should own business logic.
* Repositories should own DynamoDB access.
* AWS integrations should be isolated behind service or client classes.
* The frontend must not calculate final prices or control order-state transitions.
* Request status must not be stored only in ECS memory.
* Do not rely on local file-based agent memory in deployed environments.
* Do not expose raw AWS exceptions or internal stack traces to clients.

## Secrets and Security

Never commit or expose:

* `.env` files
* AWS credentials
* access tokens
* passwords
* session secrets
* generated secret values
* private customer data

Do not use broad IAM policies such as:

* `AdministratorAccess`
* `AmazonBedrockFullAccess`
* `AmazonDynamoDBFullAccess`

Use least-privilege, resource-specific permissions.

## Local Development

Keep local development working during deployment changes.

Expected local services:

* frontend on `http://localhost:3000`
* backend on `http://localhost:8001`
* DynamoDB in the configured AWS region
* Bedrock accessed through local AWS credentials

Do not break existing menu, cart, ordering, chat, or admin functionality.

## Testing

After every code change, run the relevant local checks.

At minimum, run where applicable:

* backend tests
* frontend type checking
* frontend linting
* frontend production build
* Docker build
* container health check
* infrastructure template validation
* agent tool tests
* API request-status tests

Do not claim a phase is complete if required tests are failing.

## Phase Completion Report

After completing each phase, stop and report:

* phase completed
* repository findings
* files changed
* changes made
* tests and checks run
* test results
* AWS commands the user must run
* expected command outputs
* remaining blockers
* next phase scope

Do not continue until the user explicitly says:

```text
Proceed to Phase N
```