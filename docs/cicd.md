# CI/CD

This repository currently has validation-only GitHub Actions. Nothing in this phase creates, updates, deploys, or deletes AWS resources.

## Workflows

### `.github/workflows/ci.yml`

Runs component validation for pull requests, pushes to any branch, and manual `workflow_dispatch` runs.

Permissions are limited to:

```yaml
contents: read
```

The workflow does not request `id-token: write`, does not use secrets, does not configure AWS credentials, does not push Docker images, and does not deploy ECS or AgentCore.

Jobs are path-aware:

| Job | Triggering paths | Validation |
| --- | --- | --- |
| Backend | `backend/**`, `dominos_pakistan_menu_import.json` | Install backend package, run backend tests, build `linux/amd64` backend image, start it, and check `http://localhost:8000/health`. |
| Agent runtime | `agent-runtime/**`, `backend/src/agent/**`, `backend/src/models/**`, `backend/src/services/**`, `backend/src/repositories/**`, `backend/src/infrastructure/**`, `backend/src/agent_client/schemas.py`, `backend/pyproject.toml`, `dominos_pakistan_menu_import.json` | Install backend and runtime packages, run runtime tests, build `linux/arm64` image with `agent-runtime/Dockerfile`, start it through QEMU, and check `http://localhost:8080/ping`. |
| Frontend | `frontend/**`, `amplify.yml` | Use Node 20, run `npm ci`, `npm run typecheck`, `npm run lint`, and `npm run build`. |

Manual `workflow_dispatch` runs all component jobs.

The AgentCore runtime image uses the repository root as the Docker build context and `agent-runtime/Dockerfile`. Docker uses `agent-runtime/Dockerfile.dockerignore` for that Dockerfile-specific build context filtering.

### `.github/workflows/validate-infra.yml`

Runs local infrastructure validation for pull requests, pushes, and manual runs when infrastructure files change.

It validates:

- `infra/phase3-network.yaml`
- `infra/phase7-ecs-api.yaml`
- `infra/phase11-monitoring.yaml`
- `infra/phase13-github-oidc.yaml`

This phase uses local YAML parsing and `cfn-lint`. It does not run `aws cloudformation validate-template`, create change sets, or deploy stacks because no GitHub OIDC role or AWS credentials exist yet.

## GitHub OIDC Preparation

`infra/phase13-github-oidc.yaml` prepares, but does not deploy, GitHub Actions authentication for future deployment workflows.

OIDC is used so GitHub Actions can exchange a short-lived GitHub identity token for an AWS role session. No permanent AWS access keys should be stored in GitHub variables, GitHub secrets, workflow files, Amplify, ECS, Docker images, or `.env` files.

The template defines two separate deployment roles:

- `fyp-github-backend-deployer` for the ECS backend image and service update path.
- `fyp-github-agentcore-deployer` for the AgentCore runtime image and runtime update path.

Both roles trust only the GitHub OIDC provider and require this exact subject:

```text
repo:shaheertariq0111/fyp:environment:staging
```

The audience is:

```text
sts.amazonaws.com
```

The future GitHub `staging` environment must restrict deployments to the `deployment` branch. Because the trust policy uses an environment subject, the branch restriction lives in the GitHub environment rules, not in the AWS OIDC `sub` claim.

### Existing Provider Check

AWS accounts can contain only one IAM OIDC provider for `https://token.actions.githubusercontent.com`. Before deploying the Phase 13 template, check whether the provider already exists:

```powershell
aws iam list-open-id-connect-providers
aws iam get-open-id-connect-provider `
  --open-id-connect-provider-arn arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com
```

The template is intentionally conditional:

- `CreateGitHubOidcProvider=false` reuses `ExistingGitHubOidcProviderArn`.
- `CreateGitHubOidcProvider=true` creates `AWS::IAM::OIDCProvider`.

Use `CreateGitHubOidcProvider=true` only when the account does not already have the GitHub Actions provider. This avoids failed stack creation from trying to create a duplicate provider.

### Current Dev Defaults

The current dev defaults in `infra/phase13-github-oidc.yaml` target:

- AWS account: `352306494518`
- Region: `us-east-1`
- Backend ECR repository: `arn:aws:ecr:us-east-1:352306494518:repository/fyp-dev-backend`
- Agent runtime ECR repository: `arn:aws:ecr:us-east-1:352306494518:repository/fyp-dev-agent-runtime`
- ECS cluster/service/task family: `fyp-dev-backend`
- ECS execution role: `arn:aws:iam::352306494518:role/fyp-dev-ecs-execution`
- ECS task role: `arn:aws:iam::352306494518:role/fyp-dev-ecs-app`
- AgentCore runtime: `arn:aws:bedrock-agentcore:us-east-1:352306494518:runtime/fyp_dev_restaurant_agent-dwLwVnClBF`
- AgentCore execution role: `arn:aws:iam::352306494518:role/fyp-dev-agentcore-execution`

The AgentCore execution role ARN is derived from `infra/phase7-ecs-api.yaml`, where `AgentCoreExecutionRole` uses `RoleName: !Sub ${ProjectName}-agentcore-execution`, and the current dev parameters use `ProjectName=fyp-dev`.

IAM resource-scoping limitations are documented in the template through intentionally small wildcard-resource statements:

- `ecr:GetAuthorizationToken` requires `Resource: "*"`.
- `sts:GetCallerIdentity` uses `Resource: "*"`.
- `ecs:DescribeTaskDefinition` requires `Resource: "*"` because ECS does not support resource-level scoping for that action.
- `ecs:ListTasks` uses `Resource: "*"` and is restricted with `ecs:cluster`; ECS does not support service-resource scoping for that list call. The service itself is still resource-scoped for `ecs:DescribeServices` and `ecs:UpdateService`.

### Local Template Validation

This does not create AWS resources:

```powershell
python -m pip install cfn-lint
cfn-lint `
  infra/phase3-network.yaml `
  infra/phase7-ecs-api.yaml `
  infra/phase11-monitoring.yaml `
  infra/phase13-github-oidc.yaml
```

AWS-authenticated validation and deployment are deferred until the deployment phase:

```powershell
aws cloudformation validate-template `
  --region us-east-1 `
  --template-body file://infra/phase13-github-oidc.yaml
```

Do not run `aws cloudformation deploy` for this template until the user explicitly proceeds to the IAM/OIDC deployment phase.

### Future GitHub Variables

Future deployment workflows should use GitHub environment or repository variables for:

```text
AWS_REGION=us-east-1
BACKEND_DEPLOY_ROLE_ARN=<BackendDeploymentRoleArn output>
AGENTCORE_DEPLOY_ROLE_ARN=<AgentCoreDeploymentRoleArn output>
```

No GitHub secret should contain AWS access keys.

## Future Deployment Phases

Deployment workflows are intentionally not created in this phase.

A later backend deployment workflow should:

- use GitHub OIDC instead of AWS access keys
- authenticate to ECR
- build and push the backend image tagged with the Git commit SHA
- update only the existing ECS backend image
- wait for ECS service stability
- check the API Gateway `/health` endpoint

A later AgentCore deployment workflow should:

- use GitHub OIDC instead of AWS access keys
- build and push the `linux/arm64` AgentCore runtime image tagged with the Git commit SHA
- read the existing AgentCore Runtime configuration
- update only the container image while preserving runtime settings
- wait for the runtime to become `READY`
- run a safe ping or invocation check

When a future deployment job uses the GitHub environment named `staging`, the AWS OIDC trust subject must be:

```text
repo:shaheertariq0111/fyp:environment:staging
```

The GitHub `staging` environment itself must restrict deployments to the `deployment` branch. Do not use branch-based trust and environment-based trust interchangeably; choose the subject that matches the workflow's environment usage.

## Local Commands Matching CI

Backend:

```powershell
cd backend
..\.venv\Scripts\python.exe -m pip install -e ".[dev]"
..\.venv\Scripts\python.exe -m pytest
cd ..
docker build --platform linux/amd64 -t fyp-backend-ci:local backend
docker run --rm -d --name fyp-backend-ci-health -p 8000:8000 `
  -e ENVIRONMENT=test `
  -e AWS_REGION=us-east-1 `
  -e MENU_TABLE_NAME=ci-menu `
  -e CARTS_TABLE_NAME=ci-carts `
  -e ORDERS_TABLE_NAME=ci-orders `
  -e CUSTOMERS_TABLE_NAME=ci-customers `
  -e AGENT_SESSIONS_TABLE_NAME=ci-agent-sessions `
  -e AGENT_REQUESTS_TABLE_NAME=ci-agent-requests `
  -e MENU_SESSIONS_TABLE_NAME=ci-menu-sessions `
  -e AUDIT_TABLE_NAME=ci-audit `
  -e MENU_SITE_BASE_URL=http://localhost:3000/menu `
  -e SESSION_TOKEN_SECRET=ci-session-token-secret `
  -e RESTAURANT_ID=ci-restaurant `
  -e BRANCH_ID=default `
  fyp-backend-ci:local
Invoke-RestMethod http://localhost:8000/health
docker rm -f fyp-backend-ci-health
```

Agent runtime:

```powershell
cd agent-runtime
$env:PYTHONPATH='E:\fyp-agent\agent-runtime\src;E:\fyp-agent\backend'
E:\fyp-agent\.venv\Scripts\python.exe -m pytest tests
cd ..
docker build --platform linux/arm64 -f agent-runtime/Dockerfile -t fyp-agent-runtime-ci:local .
docker run --rm -d --platform linux/arm64 --name fyp-agent-runtime-ci-ping -p 8080:8080 fyp-agent-runtime-ci:local
Invoke-RestMethod http://localhost:8080/ping
docker rm -f fyp-agent-runtime-ci-ping
```

Frontend:

```powershell
cd frontend
npm ci
npm run typecheck
npm run lint
npm run build
```

Infrastructure:

```powershell
python -m pip install cfn-lint
cfn-lint infra/phase3-network.yaml infra/phase7-ecs-api.yaml infra/phase11-monitoring.yaml infra/phase13-github-oidc.yaml
```

Authenticated CloudFormation validation is deferred to a later phase:

```powershell
aws cloudformation validate-template --region us-east-1 --template-body file://infra/phase3-network.yaml
aws cloudformation validate-template --region us-east-1 --template-body file://infra/phase7-ecs-api.yaml
aws cloudformation validate-template --region us-east-1 --template-body file://infra/phase11-monitoring.yaml
aws cloudformation validate-template --region us-east-1 --template-body file://infra/phase13-github-oidc.yaml
```

Do not run those AWS commands in CI until OIDC and least-privilege roles are added.
