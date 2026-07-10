# Implementation Status

## Current Phase

Phase 5: Frontend (next phase; not started)

## Completed

### Phase 1: Project structure

- [x] Backend layered folder structure created
- [x] Frontend route/component/lib/type folder structure created
- [x] Validated backend environment config loader added
- [x] Frontend API URL config added
- [x] AWS DynamoDB resource factory using the standard AWS credential chain added
- [x] Bedrock runtime and agent-runtime client factories added
- [x] Shared menu, cart, order, button, and tool-response models added
- [x] Backend and frontend `.env.example` files added

### Phase 2: Data layer

- [x] AWS table creation definitions for `RestaurantMenu`, `MenuSessions`, `Carts`, `Orders`, and `AgentAuditEvents`
- [x] Order GSI definition added
- [x] Idempotent AWS table creation script added
- [x] Explicit AWS resource-creation authorization guard added
- [x] AWS table tags, deletion protection, and point-in-time recovery configuration added
- [x] Configured table readiness verification added
- [x] Data-driven normalized menu import script added
- [x] Import support added for menu items, categories, option groups, and upsell groups
- [x] Normalized import validation added for entity types, pricing, option-group types, and list fields
- [x] Normalized defaults added for menu items, option groups, upsell groups, and categories
- [x] UTC creation/update timestamps and Decimal JSON parsing added
- [x] PRD normalized menu import contract aligned with the implemented DynamoDB item design
- [x] Menu recommendation metadata defaults and validation added to normalized imports
- [x] Recommendation metadata added to the typed menu data model
- [x] Optional `UserPreferences` table intentionally omitted

### Phase 3: Repositories

- [x] `MenuRepository` implemented
- [x] `MenuSessionRepository` implemented
- [x] `CartRepository` implemented
- [x] `OrderRepository` implemented
- [x] `AuditRepository` implemented
- [x] Conditional creates and optimistic cart/order writes added
- [x] Optional `PreferenceRepository` intentionally omitted

### Phase 4: Services

- [x] `MenuService` implemented
- [x] `MenuSessionService` implemented with secure random tokens and stored hashes
- [x] `CartService` implemented
- [x] `OrderService` implemented
- [x] `KnowledgeService` implemented with safe disabled/error behavior
- [x] `AuditService` implemented with sensitive-field redaction
- [x] `MenuService.search_menu` metadata matching, effective-price filtering, and recommendation ranking implemented
- [x] Optional `PreferenceService` intentionally omitted

### Phase 6: Cart service

- [x] Cart creation and item customization start
- [x] Same/separate multiple-quantity customization modes
- [x] Separate cart items with quantity 1 and sequential labels
- [x] Required-field and active-item tracking
- [x] Backend option validation and server-side price recalculation
- [x] Data-driven upsell retrieval, add, and skip
- [x] Required customization flow for configurable upsell items
- [x] Pending-order creation from a ready cart

### Phase 7: Strands tools

- [x] Exactly 11 MVP Strands tools implemented and registered
- [x] Tools are thin wrappers over services
- [x] Trusted user/session context injection added
- [x] Safe structured fallback for backend failures added
- [x] Optional preference tools intentionally omitted

### Phase 8: Agent orchestration

- [x] Restaurant agent system prompt added
- [x] Strands `Agent` factory added
- [x] Bedrock model wrapper configured from runtime settings
- [x] Exactly 11 MVP tools registered with the runtime agent
- [x] Trusted user/session/branch context injection added around each turn
- [x] Strands file session manager configured per trusted `agent_session_id`
- [x] Agent rules require tool-grounded menu, customization, upsell, order, and status behavior
- [x] Configurable upsell customization rules included in the agent prompt

## In Progress

- None

## Blocked

- Bedrock Knowledge Base calls require a user-supplied `KNOWLEDGE_BASE_ID` and indexed restaurant FAQ/policy content.

## Next Task

Implement API routes for chat/actions/menu if backend HTTP wiring is next, or start the Phase 5 frontend surfaces if UI work should lead. The agent runtime is now ready for API integration.

## Important Decisions

- The PRD remains the source of truth.
- Runtime configuration supplies all table names, model/Knowledge Base IDs, URLs, restaurant/branch IDs, and secrets.
- AWS DynamoDB is the only supported development/runtime datastore.
- AWS authentication uses the boto3 credential provider chain; long-lived access keys are not stored in project configuration.
- AWS table creation requires the explicit `ALLOW_AWS_RESOURCE_CREATION=true` opt-in.
- Menu items, prices, customization choices, and upsells must come from imported DynamoDB records.
- Recommendation ranking, popularity, audience fit, and display reasons come from each menu item's `metadata` object.
- `search_menu` consistently ranks filtered results by record-provided score, popularity, availability, and effective price; neither the prompt nor frontend selects recommended products.
- Conversational intent interpretation remains in the agent layer; `MenuService` contains no recommendation-phrase or stop-word dictionaries.
- The menu importer accepts either `{ "records": [...] }` or the equivalent raw record list.
- Locally executed and production imports use the same normalized schema but separate approved datasets and AWS configurations.
- Production DynamoDB must be seeded separately; test data must not be promoted as production menu data.
- Development AWS DynamoDB tables were provisioned in account `352306494518`, region `us-east-1`, using `shaheer-fyp-*` table names.
- The verified Domino's Pakistan normalized menu import was seeded into the development menu table with 102 records.
- Bedrock model invocation is connected in account `352306494518`, region `us-east-1`, using local `BEDROCK_MODEL_ID=us.amazon.nova-pro-v1:0`.
- The previous local model ID `anthropic.claude-3-5-sonnet-20241022-v2:0` is end-of-life in `us-east-1`; Anthropic profiles require account use-case details before regular use.
- DynamoDB scan-based cart lookups must paginate until they find a matching cart/cart item or exhaust the table; `Limit=1` before filter evaluation is unsafe for filtered scans.
- MVP upsell options may include configurable add-ons; when selected, `handle_cart_upsell(add_item)` returns the next required customization question and the agent continues with `save_customization_choice`.
- Strands conversation state is scoped by trusted `agent_session_id` through `FileSessionManager`; operational state still comes from DynamoDB-backed tools.
- The MVP contains no preference repository, service, table, tools, or AgentCore Memory.
- Services own business rules; repositories own DynamoDB access; Strands tools only adapt inputs/outputs.
- Cart/order writes use conditional creation and optimistic version checks.
- Menu session tokens are generated securely and only salted hashes are persisted.

## Assumptions

- Input files are already normalized; transforming a restaurant-specific raw export is outside this importer.
- Relationship IDs in `customization_group_ids`, `upsell_group_ids`, and upsell `items` refer to records managed in the same approved menu dataset. Cross-record referential validation is not yet implemented.
- “Local import” means running the importer from a local development machine against configured AWS DynamoDB; no local database emulator is supported.
- Unit tests do not invoke Bedrock; live smoke tests validate model connectivity separately to avoid routine model charges.
- Search `query` values are expected to be concise terms produced by the agent; category, tags, and price should be passed through their dedicated arguments.

## Files Changed

- `.gitignore`
- `backend/.env.example`
- `backend/pyproject.toml`
- `backend/src/agent/*`
- `backend/src/infrastructure/*`
- `backend/src/models/*`
- `backend/src/repositories/*`
- `backend/src/scripts/*`
- `backend/src/services/*`
- `backend/tests/*`
- `frontend/.env.example`
- `frontend/src/lib/api.ts`
- `docs/implementation_status.md`
- `docs/pizza_restaurant_ordering_agent_prd.md`
- `AGENTS.md`

Latest recommendation-metadata change modified:

- `backend/src/scripts/import_menu.py`
- `backend/src/models/menu.py`
- `backend/src/services/menu_service.py`
- `backend/src/agent/tools.py`
- `backend/tests/test_import_menu.py`
- `backend/tests/test_menu_service.py`
- `docs/pizza_restaurant_ordering_agent_prd.md`
- `docs/implementation_status.md`

Latest AWS DynamoDB provisioning modified local configuration only:

- `backend/.env` (gitignored local config)
- `docs/implementation_status.md`

Latest live tool validation fixes modified:

- `backend/src/repositories/cart_repository.py`
- `backend/src/repositories/order_repository.py`
- `backend/src/services/cart_service.py`
- `backend/tests/test_cart_service.py`
- `backend/tests/test_repositories.py`
- `docs/implementation_status.md`

Latest configurable upsell MVP change modified:

- `backend/src/services/cart_service.py`
- `backend/tests/test_cart_service.py`
- `docs/pizza_restaurant_ordering_agent_prd.md`
- `docs/implementation_status.md`

Latest Phase 8 agent orchestration change modified:

- `.gitignore`
- `backend/.env.example`
- `backend/src/agent/restaurant_agent.py`
- `backend/src/agent/system_prompt.py`
- `backend/src/agent/tools.py`
- `backend/src/infrastructure/config.py`
- `backend/tests/test_restaurant_agent.py`
- `docs/implementation_status.md`

## Tests

- `backend/.venv/bin/pytest -q tests/test_import_menu.py tests/test_menu_service.py tests/test_tools.py`: **25 passed**
- `backend/.venv/bin/pytest -q`: **38 passed**
- Importer tests cover metadata defaults and validation in addition to item keys/defaults/timestamps, pricing requirements, option-group validation, upsell validation/defaults, category defaults, invalid entity values, wrapped/raw input shapes, timestamp behavior, and Decimal parsing.
- Menu service tests cover score-based recommendation ordering, tag and `metadata.best_for` matching, default availability exclusion, effective-price fallback, and returned recommendation explanations.
- The full suite also covers AWS configuration, credential-chain construction, resource tags, provisioning authorization, table readiness, repository keys, cart/order services, identical and separate multi-item customization, upsells, pending order creation, fulfillment, idempotency, the 11 tools, trusted context, and safe errors.
- `python -m src.scripts.create_dynamodb_tables`: created `shaheer-fyp-restaurant-menu`, `shaheer-fyp-menu-sessions`, `shaheer-fyp-carts`, `shaheer-fyp-agent-audit-events`, and `shaheer-fyp-orders` in AWS account `352306494518`, region `us-east-1`.
- `python -m src.scripts.import_menu ..\dominos_pakistan_menu_import.json`: imported 102 menu records.
- `aws dynamodb scan --table-name shaheer-fyp-restaurant-menu --select COUNT --region us-east-1`: confirmed `Count=102`.
- `pytest -q`: **41 passed**
- Live AWS tool smoke test against seeded DynamoDB: search, item lookup, menu session link, chat customization, upsell skip, pending order creation, confirmation, takeaway submission, and order status succeeded. `retrieve_restaurant_knowledge` returned expected `KNOWLEDGE_BASE_UNAVAILABLE` because `KNOWLEDGE_BASE_ID` is not configured.
- Live AWS two-pizza same customization test succeeded: one order line with quantity `2`, then cancelled.
- Live AWS two-pizza separate customization test succeeded: separate pizza lines plus one directly addable upsell, delivery address collection, and final status `submitted_to_restaurant`.
- Live AWS configurable upsell test succeeded: `PEPSI` was offered as a configurable upsell, `pepsi-size=500ml` was saved through `save_customization_choice`, the line repriced to PKR 230, and the test takeaway order reached `submitted_to_restaurant`.
- `pytest -q`: **47 passed** after Phase 8 agent orchestration wiring.
- Phase 8 tests cover system-prompt grounding rules, Bedrock model configuration, registration of the 11 MVP tools, per-session Strands session-manager wiring, and trusted request-context injection.
- Live Bedrock smoke test through `invoke_restaurant_agent`: configured model `us.amazon.nova-pro-v1:0` used `search_menu` against DynamoDB and returned a user-facing Legend Ranch recommendation with PKR 750/1500/2100 size prices.
- Agent response extraction now uses `agent_result_text(...)` to remove provider-emitted `<thinking>` blocks before returning text to users.
