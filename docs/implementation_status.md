# Implementation Status

## Current Phase

Phase 9: Menu website integration (in progress)

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

- [x] MVP Strands tools implemented and registered, including customer profile tools
- [x] Tools are thin wrappers over services
- [x] Trusted user/session context injection added
- [x] Safe structured fallback for backend failures added
- [x] Optional preference tools intentionally omitted

### Phase 8: Agent orchestration

- [x] Restaurant agent system prompt added
- [x] Strands `Agent` factory added
- [x] Bedrock model wrapper configured from runtime settings
- [x] MVP tools registered with the runtime agent, including customer profile tools
- [x] Trusted user/session/branch context injection added around each turn
- [x] Strands file session manager configured per trusted `agent_session_id`
- [x] Agent rules require tool-grounded menu, customization, upsell, order, and status behavior
- [x] Configurable upsell customization rules included in the agent prompt
- [x] Agent prompt explicitly forbids claiming cart/order writes, fulfillment changes, or submitted orders unless backend tools returned success

### Phase 10: API and frontend actions

- [x] FastAPI app added
- [x] `/health` route added
- [x] `POST /api/chat` wired to the Strands restaurant agent
- [x] Agent API responses sanitized with `agent_result_text`
- [x] `POST /api/actions` dispatches supported button/action calls with trusted context
- [x] `GET /api/menu` reads menu data through `MenuService`
- [x] `GET /api/menu/items/{item_id}` reads item details and customization schema
- [x] `GET /api/menu-session` resolves secure menu session tokens
- [x] `POST /api/menu-orders` creates backend-validated pending orders from menu website submissions
- [x] FastAPI route tests added
- [x] Local API server verified on `http://127.0.0.1:8001`
- [x] `POST /api/chat` remains a thin Strands agent endpoint with no API-level chat intent routing
- [x] Agent prompt directs cart/order/status/order-start turns through backend tools instead of chat history
- [x] Agent prompt tells broad order-start turns to check active backend orders before menu search
- [x] Agent prompt now covers the full MVP chat flow: recommendations, website handoff, chat customization, multiple quantities, upsells, pending confirmation, fulfillment, submission, status, ambiguity, and recovery cases
- [x] Tool responses now include an `agent` guidance object with current entity, status, required input, valid next actions, summaries, and instructions for the model
- [x] Cart upsell responses now use `upsell_items` for add-on choices so cart `items` remains the actual cart contents
- [x] Chat menu/recommendation tool calls are capped at five returned options while the website menu API can still load the full menu
- [x] Checkout flow is fulfillment-first: collect delivery/takeaway details before one final confirmation that submits the order
- [x] Checkout/proceed during the upsell step now skips add-ons and creates the backend order instead of forcing an explicit “skip add-ons” turn
- [x] Accepted upsells are one-and-done: after one add-on is added and completed, the cart moves to checkout instead of offering another upsell
- [x] `/api/chat` now returns current-turn `tool_calls`, `write_succeeded`, authoritative `state`, and action `buttons`
- [x] `/api/chat` no longer rewrites assistant text with regex; transactional correctness comes from `tool_calls`, `write_succeeded`, and authoritative state
- [x] Agent prompt forbids placeholder narration before required tool calls; broad order starts must call `get_order_status` before replying
- [x] `/api/chat` re-reads active cart/orders after successful write tools so frontend state reflects persisted backend/DynamoDB state
- [x] Frontend chat renders backend-verified write status and authoritative cart/order state instead of trusting assistant prose
- [x] Frontend New order action rotates session ID and clears visible chat/cart state without changing normal reload persistence
- [x] Customer profiles now include a reusable DynamoDB-backed delivery address book
- [x] `save_customer_address` Strands tool added as a thin wrapper over `CustomerService`
- [x] Delivery flow prompt now checks saved customer addresses before asking for a new address
- [x] New delivery addresses are saved to the customer profile before being copied to the order delivery-address snapshot
- [x] Order responses now expose the order-specific `delivery_address` snapshot for confirmation/status summaries
- [x] DynamoDB provisioning script now creates customer/session tables and ensures TTL on agent sessions
- [x] Development DynamoDB tables `shaheer-fyp-agent-sessions` and `shaheer-fyp-customers` were created
- [x] Admin console MVP added under `/admin` with env-backed login, dashboard analytics, live orders, order details/status updates, menu management, customer search, and monitoring
- [x] Admin menu management can add/edit items, toggle availability, and archive items instead of hard-deleting historical menu references
- [x] Admin backend APIs added under `/api/admin/*` with signed HttpOnly cookie auth
- [x] Frontend downgraded from Next.js 16 to Next.js 15.5.20

## In Progress

- Phase 9 live end-to-end verification: exercise cart creation, checkout/proceed during upsells, takeaway/delivery details, final confirmation, and menu website handoff against the seeded DynamoDB environment.

## Blocked

- Bedrock Knowledge Base calls require a user-supplied `KNOWLEDGE_BASE_ID` and indexed restaurant FAQ/policy content.

## Next Task

Run the live Phase 9 browser/API flow with backend on `http://localhost:8001` and frontend on `http://localhost:3000`; then mark Phase 9 complete if cart/order status transitions and menu handoff succeed.

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
- Menu website order submissions are converted into pending orders by backend services after server-side item, customization, availability, and price validation.
- The chat checkout flow asks fulfillment details before final confirmation; final `confirm` from `pending_confirmation` submits the order. The legacy `ready_for_submission`/`submit` path was removed.
- Customer delivery addresses live on the customer profile for reuse, while each order keeps its own plain `delivery_address` snapshot string.
- Web-collected addresses are stored as unverified MVP address-book entries; no geocoding or delivery-zone validation is implemented yet.
- The newest saved address becomes the default unless a tool caller explicitly passes `make_default=false`.
- Agent sessions use DynamoDB TTL on `expires_at`; frontend localStorage keeps only cached IDs while backend session validity is authoritative.
- Admin deletes are archive operations: archived menu items are unavailable and hidden from customer menu search, while old order snapshots remain readable.
- Admin order status changes are deterministic service transitions and do not invoke the restaurant agent.

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

Latest legacy order-path removal modified:

- `backend/src/services/order_service.py`
- `backend/src/agent/tools.py`
- `backend/src/agent/system_prompt.py`
- `backend/tests/test_order_service.py`
- `docs/pizza_restaurant_ordering_agent_prd.md`
- `docs/implementation_status.md`

Latest frontend hydration warning fix modified:

- `frontend/src/app/layout.tsx`
- `docs/implementation_status.md`

Latest menu search relevance fix modified:

- `backend/src/services/menu_service.py`
- `backend/tests/test_menu_service.py`
- `docs/implementation_status.md`

Latest cart-to-order handoff status fix modified:

- `backend/src/services/cart_service.py`
- `backend/tests/test_cart_service.py`
- `docs/pizza_restaurant_ordering_agent_prd.md`
- `docs/implementation_status.md`

Latest legacy converted-cart routing fix modified:

- `backend/src/services/cart_service.py`
- `backend/src/agent/system_prompt.py`
- `backend/tests/test_cart_service.py`
- `backend/tests/test_restaurant_agent.py`
- `docs/implementation_status.md`

Latest customer identity and backend-owned session implementation modified:

- `backend/src/repositories/customer_repository.py`
- `backend/src/repositories/agent_session_repository.py`
- `backend/src/services/customer_service.py`
- `backend/src/services/agent_session_service.py`
- `backend/src/api/main.py`
- `backend/src/agent/tools.py`
- `frontend/src/lib/session.ts`

Latest customer address book and DynamoDB provisioning implementation modified:

- `backend/src/services/customer_service.py`
- `backend/src/agent/tools.py`
- `backend/src/agent/system_prompt.py`
- `backend/src/services/order_service.py`
- `backend/src/scripts/create_dynamodb_tables.py`
- `backend/tests/test_customer_session_service.py`
- `backend/tests/test_tools.py`
- `backend/tests/test_restaurant_agent.py`
- `backend/tests/test_order_service.py`
- `backend/tests/test_table_definitions.py`
- `frontend/src/types.ts`
- `docs/implementation_status.md`

Latest validation:

- `backend/.venv/bin/pytest -q backend/tests/test_customer_session_service.py backend/tests/test_tools.py backend/tests/test_restaurant_agent.py backend/tests/test_order_service.py backend/tests/test_table_definitions.py` → 27 passed
- `backend/.venv/bin/pytest -q backend/tests` → 83 passed, 1 existing Starlette/httpx deprecation warning
- `cd backend && .venv/bin/python -m src.scripts.create_dynamodb_tables` → created `shaheer-fyp-agent-sessions`, `shaheer-fyp-customers`
- `cd frontend && npm run typecheck` → passed
- `cd frontend && npm run lint` → failed because `next lint` is not valid in the current Next 16 CLI and is interpreted as a project directory
- `cd frontend && npm run build` → passed with existing multiple-lockfile workspace-root warning

Latest admin console MVP implementation modified:

- `backend/src/api/main.py`
- `backend/src/api/schemas.py`
- `backend/src/infrastructure/config.py`
- `backend/src/repositories/menu_repository.py`
- `backend/src/repositories/order_repository.py`
- `backend/src/repositories/customer_repository.py`
- `backend/src/repositories/audit_repository.py`
- `backend/src/services/menu_service.py`
- `backend/src/services/order_service.py`
- `backend/src/services/customer_service.py`
- `backend/src/services/audit_service.py`
- `backend/src/agent/tools.py`
- `backend/tests/test_api.py`
- `backend/tests/test_menu_service.py`
- `backend/tests/test_order_service.py`
- `backend/tests/test_config.py`
- `backend/tests/fakes.py`
- `frontend/src/lib/adminApi.ts`
- `frontend/src/app/admin/*`
- `frontend/src/app/styles.css`
- `docs/implementation_status.md`

Latest admin console validation:

- `backend/.venv/bin/pytest -q backend/tests/test_api.py backend/tests/test_menu_service.py backend/tests/test_order_service.py backend/tests/test_config.py` → 35 passed, 1 existing Starlette/httpx deprecation warning
- `backend/.venv/bin/pytest -q backend/tests` → 91 passed, 1 existing Starlette/httpx deprecation warning
- `cd frontend && npm run typecheck` → passed
- `cd frontend && npm run lint` → failed because `next lint` is not valid in the current Next 16 CLI and is interpreted as a project directory
- `cd frontend && npm run build` → passed with existing multiple-lockfile workspace-root warning

Latest Next.js downgrade modified:

- `frontend/package.json`
- `frontend/package-lock.json`
- `frontend/tsconfig.json`
- `frontend/tsconfig.tsbuildinfo`
- `docs/implementation_status.md`

Latest Next.js downgrade validation:

- `cd frontend && npm install next@^15` → resolved `next@15.5.20`; npm reported 2 moderate vulnerabilities
- `cd frontend && npm ls next react react-dom --depth=0` → `next@15.5.20`, `react@19.2.7`, `react-dom@19.2.7`
- `cd frontend && npm run typecheck` → passed
- `cd frontend && npm run lint` → failed because Next 15 `next lint` is deprecated and prompts to initialize ESLint config interactively
- `cd frontend && npm run build` → passed with existing multiple-lockfile workspace-root warning

Latest Phase 8 agent orchestration change modified:

- `.gitignore`
- `backend/.env.example`
- `backend/src/agent/restaurant_agent.py`
- `backend/src/agent/system_prompt.py`
- `backend/src/agent/tools.py`
- `backend/src/infrastructure/config.py`
- `backend/tests/test_restaurant_agent.py`
- `docs/implementation_status.md`

Latest Phase 10 API wiring change modified:

- `backend/pyproject.toml`
- `backend/src/api/main.py`
- `backend/src/api/schemas.py`
- `backend/src/services/cart_service.py`
- `backend/tests/test_api.py`
- `docs/implementation_status.md`

Latest cart/order chat state hardening modified:

- `backend/src/agent/system_prompt.py`
- `backend/src/api/main.py`
- `backend/src/repositories/cart_repository.py`
- `backend/src/services/cart_service.py`
- `backend/tests/fakes.py`
- `backend/tests/test_api.py`
- `backend/tests/test_cart_service.py`
- `backend/tests/test_restaurant_agent.py`
- `docs/implementation_status.md`

Latest text-only cart flow correction modified:

- `backend/src/api/main.py`
- `backend/src/services/cart_service.py`
- `backend/tests/test_api.py`
- `backend/tests/test_cart_service.py`
- `docs/implementation_status.md`

Latest agent-owned chat routing correction modified:

- `backend/src/agent/system_prompt.py`
- `backend/src/api/main.py`
- `backend/tests/test_api.py`
- `backend/tests/test_restaurant_agent.py`
- `docs/implementation_status.md`

Latest agent system prompt hardening modified:

- `backend/src/agent/system_prompt.py`
- `backend/tests/test_restaurant_agent.py`
- `docs/implementation_status.md`

Latest comprehensive prompt flow hardening modified:

- `backend/src/agent/system_prompt.py`
- `backend/tests/test_restaurant_agent.py`
- `docs/implementation_status.md`

Latest tool response contract hardening modified:

- `backend/src/models/tool_responses.py`
- `backend/src/services/cart_service.py`
- `backend/src/services/order_service.py`
- `backend/src/agent/system_prompt.py`
- `backend/tests/test_cart_service.py`
- `backend/tests/test_order_service.py`
- `backend/tests/test_restaurant_agent.py`
- `docs/implementation_status.md`

Latest transactional chat/frontend correctness fix modified:

- `backend/src/agent/context.py`
- `backend/src/agent/restaurant_agent.py`
- `backend/src/agent/system_prompt.py`
- `backend/src/agent/tools.py`
- `backend/src/api/main.py`
- `backend/src/api/schemas.py`
- `backend/src/models/tool_responses.py`
- `backend/src/services/cart_service.py`
- `backend/src/services/order_service.py`
- `backend/tests/test_api.py`
- `backend/tests/test_cart_service.py`
- `backend/tests/test_order_service.py`
- `backend/tests/test_restaurant_agent.py`
- `backend/tests/test_tools.py`
- `frontend/src/app/chat/page.tsx`
- `frontend/src/app/styles.css`
- `frontend/src/lib/session.ts`
- `frontend/src/types.ts`
- `docs/implementation_status.md`

Latest checkout choreography correction modified:

- `backend/src/services/order_service.py`
- `backend/src/services/cart_service.py`
- `backend/src/agent/system_prompt.py`
- `backend/tests/test_order_service.py`
- `backend/tests/test_cart_service.py`
- `backend/tests/test_restaurant_agent.py`
- `docs/pizza_restaurant_ordering_agent_prd.md`
- `docs/implementation_status.md`

Latest prompt cleanup modified:

- `backend/src/agent/system_prompt.py`
- `backend/tests/test_restaurant_agent.py`
- `backend/tests/test_order_service.py`
- `docs/implementation_status.md`

Latest order-start tool-call prompting fix modified:

- `backend/src/agent/system_prompt.py`
- `backend/tests/test_restaurant_agent.py`
- `docs/implementation_status.md`

Latest recommendation result cap modified:

- `backend/src/services/menu_service.py`
- `backend/src/agent/tools.py`
- `backend/src/api/main.py`
- `backend/src/agent/system_prompt.py`
- `backend/tests/test_menu_service.py`
- `backend/tests/test_tools.py`
- `docs/implementation_status.md`

Latest Phase 5 frontend work modified:

- `.gitignore`
- `frontend/.env.local`
- `frontend/package.json`
- `frontend/next.config.js`
- `frontend/next-env.d.ts`
- `frontend/tsconfig.json`
- `frontend/src/app/layout.tsx`
- `frontend/src/app/page.tsx`
- `frontend/src/app/styles.css`
- `frontend/src/app/chat/page.tsx`
- `frontend/src/app/menu/page.tsx`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/session.ts`
- `frontend/src/types.ts`
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
- Phase 8 tests cover system-prompt grounding rules, Bedrock model configuration, registration of the MVP tools, per-session Strands session-manager wiring, and trusted request-context injection.
- Live Bedrock smoke test through `invoke_restaurant_agent`: configured model `us.amazon.nova-pro-v1:0` used `search_menu` against DynamoDB and returned a user-facing Legend Ranch recommendation with PKR 750/1500/2100 size prices.
- Agent response extraction now uses `agent_result_text(...)` to remove provider-emitted `<thinking>` blocks before returning text to users.
- `pip install -e .[dev]`: installed FastAPI/httpx test dependencies in the local Python 3.10 environment.
- `pytest -q`: **55 passed** after API route wiring.
- Live local API checks passed:
  - `GET http://127.0.0.1:8001/health` returned `{"status":"ok"}`.
  - `GET http://127.0.0.1:8001/api/menu?query=chicken` returned DynamoDB-backed menu results.
  - `POST http://127.0.0.1:8001/api/chat` returned a Bedrock/DynamoDB-grounded Legend Ranch recommendation.
- `pytest -q`: **55 passed** after CORS/frontend-start support changes.
- Frontend dependencies, build/type checks, and local dev server were verified by user on `http://localhost:3000`.
- `backend/.venv/bin/pytest -q backend/tests/test_api.py backend/tests/test_restaurant_agent.py backend/tests/test_cart_service.py backend/tests/test_order_service.py`: **26 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **61 passed**
- Cart/order chat state tests cover cart status from backend state, place-order text creating pending orders only through `CartService`, incomplete carts refusing placement, confirm updating pending backend orders, and pickup requiring/updating backend fulfillment state.
- `backend/.venv/bin/pytest -q backend/tests/test_api.py backend/tests/test_cart_service.py backend/tests/test_restaurant_agent.py backend/tests/test_order_service.py`: **31 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **66 passed**
- Text-only chat tool-surface tests cover backend menu search for item terms, exact item text adding to the backend cart, active customization text saving the backend choice, and appending another item to an existing active cart.
- `backend/.venv/bin/pytest -q backend/tests/test_restaurant_agent.py`: **8 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **66 passed** after system prompt hardening for tool-first routing, text-only chat behavior, active customization choices, fulfillment sequencing, and no pickup location/time collection in MVP.
- `backend/.venv/bin/pytest -q backend/tests/test_api.py backend/tests/test_cart_service.py`: **24 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **69 passed**
- Text-only cart flow tests cover blocking add-item requests during active customization, returning the current backend customization prompt/options, and preventing `ok` from triggering menu search.
- `backend/.venv/bin/pytest -q backend/tests/test_api.py`: **20 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **72 passed**
- Order-start routing tests cover `i want to order` opening menu choices without an active order, preserving active `awaiting_fulfillment_method` orders, and letting recommendation questions reach the agent.
- `backend/.venv/bin/pytest -q backend/tests/test_api.py backend/tests/test_restaurant_agent.py`: **15 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **59 passed**
- API chat guard logic was removed; route tests now verify chat is delegated to the Strands agent, while prompt tests cover broad order-start and active-order guidance.
- `backend/.venv/bin/pytest -q backend/tests/test_restaurant_agent.py`: **8 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **59 passed**
- Comprehensive prompt tests now cover tool-source grounding, text-only choices, broad order starts, recommendations, chat customization, upsells, pending confirmation, fulfillment/submission, cart status limitations, ambiguity, and safety.
- `backend/.venv/bin/pytest -q backend/tests/test_cart_service.py backend/tests/test_order_service.py`: **11 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **61 passed**
- Tool response contract tests cover top-level `agent` guidance for cart customization, upsells, pending orders, fulfillment, submission, active order status, and unambiguous `upsell_items` add-on payloads.
- `backend/.venv/bin/pytest -q backend/tests/test_restaurant_agent.py backend/tests/test_cart_service.py backend/tests/test_order_service.py`: **19 passed**
- `backend/.venv/bin/pytest -q backend/tests/test_api.py backend/tests/test_tools.py backend/tests/test_restaurant_agent.py backend/tests/test_cart_service.py backend/tests/test_order_service.py`: **34 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **66 passed**
- Chat correctness tests cover write metadata/state, structured failed-write errors, informational responses, trusted active-cart lookup, and exception logging.
- `backend/.venv/bin/pytest -q backend/tests/test_api.py backend/tests/test_tools.py backend/tests/test_cart_service.py backend/tests/test_order_service.py`: **26 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **66 passed**
- Post-write chat state tests now verify `/api/chat` refreshes state from the backend service after a successful write instead of only returning the tool payload.
- `backend/.venv/bin/pytest -q backend/tests/test_order_service.py backend/tests/test_cart_service.py backend/tests/test_restaurant_agent.py`: **20 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **67 passed**
- Checkout choreography tests cover fulfillment-first order creation, takeaway/delivery reaching final confirmation, final confirm submitting the order, checkout auto-skipping a pending upsell decision, and one-and-done accepted upsells.
- `backend/.venv/bin/pytest -q backend/tests/test_restaurant_agent.py backend/tests/test_order_service.py`: **11 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **67 passed**
- `backend/.venv/bin/pytest -q backend/tests/test_restaurant_agent.py`: **8 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **68 passed**
- `backend/.venv/bin/pytest -q backend/tests/test_menu_service.py backend/tests/test_tools.py backend/tests/test_restaurant_agent.py`: **18 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **70 passed**
- `npm run typecheck`: **passed**
- `npm run lint`: **failed** because the configured `next lint` script is incompatible with the installed Next 16 CLI and treats `lint` as a project directory.
- `npm run build`: **passed** with the existing multiple-lockfile workspace-root warning.
- `backend/.venv/bin/pytest -q backend/tests/test_order_service.py backend/tests/test_restaurant_agent.py`: **12 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **71 passed**, 1 existing Starlette/httpx deprecation warning
- `npm run typecheck`: **passed** after adding root hydration warning suppression for browser-extension HTML attributes
- `backend/.venv/bin/pytest -q backend/tests/test_menu_service.py`: **7 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **73 passed**, 1 existing Starlette/httpx deprecation warning
- `backend/.venv/bin/pytest -q backend/tests/test_menu_service.py`: **8 passed** after replacing hardcoded query stop words with data-driven menu-corpus token relevance
- `backend/.venv/bin/pytest -q backend/tests`: **74 passed**, 1 existing Starlette/httpx deprecation warning
- `backend/.venv/bin/pytest -q backend/tests/test_cart_service.py`: **9 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **74 passed**, 1 existing Starlette/httpx deprecation warning
- `backend/.venv/bin/pytest -q backend/tests/test_cart_service.py backend/tests/test_restaurant_agent.py`: **18 passed**
- `backend/.venv/bin/pytest -q backend/tests`: **75 passed**, 1 existing Starlette/httpx deprecation warning
- `backend/.venv/bin/pytest -q backend/tests/test_customer_session_service.py backend/tests/test_api.py backend/tests/test_tools.py backend/tests/test_cart_service.py backend/tests/test_restaurant_agent.py backend/tests/test_config.py backend/tests/test_table_definitions.py backend/tests/test_dynamodb.py`: **46 passed**, 1 existing Starlette/httpx deprecation warning
- `backend/.venv/bin/pytest -q backend/tests`: **80 passed**, 1 existing Starlette/httpx deprecation warning
- `npm run typecheck`: **passed**
- `npm run build`: **passed** with the existing multiple-lockfile workspace-root warning
- `npm run lint`: **failed** because the configured `next lint` script is incompatible with the installed Next 16 CLI and treats `lint` as a project directory
