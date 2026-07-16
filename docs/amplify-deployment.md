# Phase 10 Amplify deployment

This document prepares the Next.js frontend for AWS Amplify Hosting. It does not create AWS resources by itself.

## Build configuration

The root `amplify.yml` is configured for this monorepo layout:

```text
appRoot: frontend
preBuild: npm ci
build: write minimal .env.production, npm run typecheck, npm run lint, npm run build
artifacts: frontend/.next
node: 20
```

The frontend remains a Next.js 15 app. Do not configure static export because the app uses normal Next.js routing and Amplify can host the `.next` build output.

The build spec uses Amplify's monorepo `applications` structure with `appRoot: frontend`, so build commands run from the `frontend` application root and publish that app's `.next` directory.

## Required Amplify environment variables

Configure these in Amplify Hosting branch environment variables:

```text
AMPLIFY_MONOREPO_APP_ROOT=frontend
NEXT_PUBLIC_API_BASE_URL=https://<api-id>.execute-api.us-east-1.amazonaws.com
NEXT_PUBLIC_BRANCH_ID=<branch-id>
```

When you select **My app is a monorepo** and set the app root to `frontend`, Amplify should set `AMPLIFY_MONOREPO_APP_ROOT=frontend` automatically. Confirm it is present in the branch environment variables before deploying.

During the build, `amplify.yml` writes only these public frontend variables into `frontend/.env.production`:

```text
NEXT_PUBLIC_API_BASE_URL
NEXT_PUBLIC_BRANCH_ID
```

It does not copy all Amplify environment variables into the frontend build.

Do not configure `NEXT_PUBLIC_MENU_BASE_URL`; the current frontend does not read it. Generated menu links are produced by the backend from `MENU_SITE_BASE_URL`.

Do not add secrets, AWS access keys, passwords, account IDs, session tokens, or generated values to Amplify environment variables.

## Manual Amplify setup

Use the AWS Amplify console for GitHub-based deployment so Amplify can manage the GitHub connection and automatic rebuilds.

`[CREATES AWS RESOURCES]`

1. Open AWS Amplify Hosting in `us-east-1`.
2. Choose **Create new app**.
3. Choose **GitHub** as the source provider.
4. Connect the repository and branch.
5. Select **My app is a monorepo** and set the app root to `frontend`.
6. Confirm the build spec uses the repository `amplify.yml`.
7. Confirm Amplify sets `AMPLIFY_MONOREPO_APP_ROOT=frontend`; add it manually if it is missing.
8. Set the remaining branch environment variables listed above.
9. Deploy the app.
10. Capture the generated Amplify URL, for example `https://main.<app-id>.amplifyapp.com`.

## Backend CORS follow-up

After Amplify gives you the final URL, update the backend stack parameters:

```text
FrontendCorsOrigins=https://main.<app-id>.amplifyapp.com
MenuSiteBaseUrl=https://main.<app-id>.amplifyapp.com/menu
```

This backend stack update changes AWS resources and must be run manually when you are ready. Keep `FrontendCorsOrigins` as one exact HTTPS origin, not a wildcard and not a comma-separated list.

## Validation before deployment

Run these locally before connecting or redeploying Amplify:

```powershell
cd frontend
npm ci
npm run typecheck
npm run lint
npm run build
```

The build must pass with `NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_BRANCH_ID` set.
