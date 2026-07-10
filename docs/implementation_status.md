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
- [x] Pending-order creation from a ready cart

### Phase 7: Strands tools

- [x] Exactly 11 MVP Strands tools implemented and registered
- [x] Tools are thin wrappers over services
- [x] Trusted user/session context injection added
- [x] Safe structured fallback for backend failures added
- [x] Optional preference tools intentionally omitted

## In Progress

- None

## Blocked

- AWS DynamoDB provisioning/integration was not executed because AWS credentials, region choice, and final table names are user-owned deployment inputs.
- Real Bedrock/Knowledge Base calls require user-supplied AWS credentials, model access, and resource IDs.
- Menu import execution requires a real normalized restaurant menu data file; no menu data was hardcoded or fabricated.

## Next Task

Obtain an approved normalized menu dataset containing recommendation metadata, seed development AWS DynamoDB, and run repository/service integration checks. Then implement Phase 8 agent orchestration so the runtime agent is forced to call `search_menu` and recommend only returned records, followed by the Phase 5 frontend surfaces.

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
- The MVP contains no preference repository, service, table, tools, or AgentCore Memory.
- Services own business rules; repositories own DynamoDB access; Strands tools only adapt inputs/outputs.
- Cart/order writes use conditional creation and optimistic version checks.
- Menu session tokens are generated securely and only salted hashes are persisted.

## Assumptions

- Input files are already normalized; transforming a restaurant-specific raw export is outside this importer.
- Relationship IDs in `customization_group_ids`, `upsell_group_ids`, and upsell `items` refer to records managed in the same approved menu dataset. Cross-record referential validation is not yet implemented.
- “Local import” means running the importer from a local development machine against configured AWS DynamoDB; no local database emulator is supported.
- Enforcing tool use at model runtime remains Phase 8 work; this change provides the grounded search/ranking service and tool response fields but does not add agent orchestration.
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

## Tests

- `backend/.venv/bin/pytest -q tests/test_import_menu.py tests/test_menu_service.py tests/test_tools.py`: **25 passed**
- `backend/.venv/bin/pytest -q`: **38 passed**
- Importer tests cover metadata defaults and validation in addition to item keys/defaults/timestamps, pricing requirements, option-group validation, upsell validation/defaults, category defaults, invalid entity values, wrapped/raw input shapes, timestamp behavior, and Decimal parsing.
- Menu service tests cover score-based recommendation ordering, tag and `metadata.best_for` matching, default availability exclusion, effective-price fallback, and returned recommendation explanations.
- The full suite also covers AWS configuration, credential-chain construction, resource tags, provisioning authorization, table readiness, repository keys, cart/order services, identical and separate multi-item customization, upsells, pending order creation, fulfillment, idempotency, the 11 tools, trusted context, and safe errors.
- AWS integration remains pending until credentials and resource names are supplied; no external AWS resources were created by this change.
