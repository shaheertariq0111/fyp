# Deployment Checklist

## Local Readiness

- [ ] Backend tests pass.
- [ ] AgentCore runtime tests pass.
- [ ] Frontend typecheck passes.
- [ ] Frontend lint has no errors.
- [ ] Frontend production build passes.
- [ ] Backend Docker image builds for `linux/amd64`.
- [ ] Backend container `/health` returns `{"status":"ok"}`.
- [ ] CloudFormation templates validate.

## Manual AWS Setup

- [ ] Secrets Manager secrets created.
- [ ] Backend stack created with `DesiredCount=0`.
- [ ] Backend API endpoint captured.
- [ ] Backend ECR image pushed.
- [ ] Amplify app created from GitHub.
- [ ] Amplify app root is `frontend`.
- [ ] `AMPLIFY_MONOREPO_APP_ROOT=frontend` confirmed.
- [ ] Amplify environment variables configured.
- [ ] Amplify URL captured.
- [ ] Backend stack updated with exact Amplify CORS origin.
- [ ] AgentCore runtime image pushed.
- [ ] AgentCore Memory created if required.
- [ ] AgentCore Runtime created manually.
- [ ] Backend stack updated with `AgentCoreRuntimeArn`.
- [ ] ECS `DesiredCount` updated to `1`.
- [ ] Monitoring stack deployed.
- [ ] Monitoring stack updated with `MinimumRunningTaskCount=1` after ECS starts.

## Application Flow Checks

- [ ] `GET /health`
- [ ] Start conversation
- [ ] Submit chat message with `POST /api/chat`
- [ ] Poll `GET /api/chat/{request_id}`
- [ ] Receive completed agent response
- [ ] View menu
- [ ] Search menu
- [ ] Add item to cart
- [ ] Modify item quantity
- [ ] Remove item
- [ ] Confirm order
- [ ] Check order status
- [ ] Start new conversation
- [ ] Admin login
- [ ] Admin dashboard
- [ ] ECS task restart does not lose request status

## Security Checks

- [ ] No `.env` files committed.
- [ ] No AWS account IDs committed.
- [ ] No secret values committed.
- [ ] No AWS access keys in app config, Docker images, Amplify, or ECS environment variables.
- [ ] CORS uses exact Amplify origin, not `*`.
- [ ] Admin cookies are `HttpOnly`, `Secure`, and `SameSite=None` in staging/production.
- [ ] AgentCore environment JSON contains only names and ARNs, not secret values.
