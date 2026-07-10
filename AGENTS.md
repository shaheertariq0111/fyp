# Agent Instructions for Codex

## Source of Truth

Before making implementation decisions, read:

- `docs/pizza_restaurant_ordering_agent_prd.md`

Treat this PRD as the primary product and architecture source of truth.

Do not implement features that conflict with the PRD unless explicitly instructed by the user.

## Implementation Priorities

Build the MVP first.

Follow the PRD sections for:

- local development setup
- Strands agent architecture
- 11-tool MVP tool set
- backend service/repository structure
- AWS DynamoDB support
- frontend chat UI
- menu website UI
- no-hardcoding rule
- trusted context injection
- state machines
- testing scenarios

## Hard Rules

- Do not hardcode menu items, prices, pizza options, toppings, table names, model IDs, URLs, or secrets.
- Read menu data, customization options, prices, cart state, and order state from backend/DynamoDB.
- Strands tools must be thin wrappers around services.
- Services own business logic.
- Repositories own DynamoDB access.
- Frontend must not calculate final prices or decide order state.
- Backend must validate all cart and order transitions.
- Use AWS DynamoDB as the development and deployment datastore.

## MVP Tool Set

Implement these 11 Strands tools first:

1. `search_menu`
2. `get_menu_item`
3. `create_menu_session_link`
4. `start_cart_item_customization`
5. `set_customization_mode`
6. `save_customization_choice`
7. `handle_cart_upsell`
8. `create_pending_order_from_cart`
9. `update_order_flow`
10. `get_order_status`
11. `retrieve_restaurant_knowledge`

Do not add optional preference tools until the MVP is working.

## Local Development

The app should run locally with:

- frontend on `http://localhost:3000`
- backend on `http://localhost:8001`
- DynamoDB in the configured AWS region
- Bedrock called remotely through AWS credentials

Use environment variables for all config.

## Testing

After implementing or changing code, run relevant tests.

At minimum, test:

- menu loading
- recommendation flow
- chat customization flow
- 2 identical pizzas
- 2 separately customized pizzas
- upsell flow
- pending order confirmation
- delivery/takeaway flow
- AWS DynamoDB configuration and access behavior

## Progress Tracking

Maintain implementation progress against the PRD phases.

Create and update a file:

- `docs/implementation_status.md`

After completing any meaningful implementation step, update this file with:

- current phase
- completed tasks
- in-progress tasks
- blocked tasks
- next recommended task
- important decisions made
- files changed
- tests added or run

Use the PRD phase names from `docs/pizza_restaurant_ordering_agent_prd.md`.

Do not mark a phase complete unless the code, config, and relevant tests for that phase are implemented.
