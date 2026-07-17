"use client";

import { FormEvent, RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AdminShell, money } from "@/app/admin/AdminShell";
import { adminGet, adminPatch, adminPost, adminPut } from "@/lib/adminApi";
import {
  archiveLabel,
  availabilityLabel,
  MenuIcon,
  readableMenuText,
  validateAdvancedMenuJson,
} from "@/app/admin/menu/menuPresentation";

type MenuItem = {
  product_id: string;
  name: string;
  category: string;
  currency: string;
  description?: string;
  available: boolean;
  archived?: boolean;
  price?: number;
  starting_price?: number;
  base_prices?: Record<string, number>;
  requires_customization?: boolean;
  customization_group_ids?: string[];
  upsell_group_ids?: string[];
  tags?: string[];
  search_terms?: string[];
  image_url?: string | null;
  metadata?: Record<string, unknown>;
};

type MenuItemPayload = {
  product_id: string;
  name: string;
  category: string;
  currency: string;
  description: string;
  available: boolean;
  price?: number;
  starting_price: number;
  base_prices: Record<string, number>;
  requires_customization: boolean;
  customization_group_ids: string[];
  upsell_group_ids: string[];
  tags: string[];
  search_terms: string[];
  image_url: string | null;
  metadata: Record<string, unknown>;
};

type ItemForm = {
  product_id: string;
  name: string;
  category: string;
  currency: string;
  description: string;
  available: boolean;
  starting_price: string;
  tags: string;
};

type CategoryForm = {
  category_id: string;
  name: string;
  sort_order: string;
};

type Feedback = {
  tone: "success" | "warning" | "error";
  message: string;
};

type RefreshFailure = {
  attemptedAt: Date;
  hadPreviousData: boolean;
};

type RefreshSource = "initial" | "manual" | "background" | "mutation";
type FormMode = "closed" | "add" | "edit";
type AdvancedMode = "option" | "upsell";
type ItemField = "product_id" | "name" | "category" | "currency" | "starting_price";
type CategoryField = "category_id" | "name" | "sort_order";
type FieldErrors<T extends string> = Partial<Record<T, string>>;
type FieldTouched<T extends string> = Record<T, boolean>;

const emptyItemForm: ItemForm = {
  product_id: "",
  name: "",
  category: "",
  currency: "PKR",
  description: "",
  available: true,
  starting_price: "0",
  tags: "",
};

const emptyCategoryForm: CategoryForm = {
  category_id: "",
  name: "",
  sort_order: "999",
};

const emptyItemTouched: FieldTouched<ItemField> = {
  product_id: false,
  name: false,
  category: false,
  currency: false,
  starting_price: false,
};

const emptyCategoryTouched: FieldTouched<CategoryField> = {
  category_id: false,
  name: false,
  sort_order: false,
};

const defaultOptionGroupJson = '{\n  "option_group_id": "",\n  "name": "",\n  "type": "single_select",\n  "question": "",\n  "options": []\n}';
const defaultUpsellGroupJson = '{\n  "upsell_group_id": "",\n  "question": "",\n  "items": []\n}';
const unavailableValue = "\u2014";

function formatTime(value: Date) {
  return value.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function itemPrice(item: MenuItem) {
  return item.starting_price ?? item.price;
}

function tagsToText(tags?: string[]) {
  return (tags ?? []).join(", ");
}

function tagsFromText(value: string) {
  return value.split(",").map((tag) => tag.trim()).filter(Boolean);
}

function itemToForm(item: MenuItem): ItemForm {
  return {
    product_id: item.product_id,
    name: item.name,
    category: item.category,
    currency: item.currency,
    description: item.description ?? "",
    available: item.available,
    starting_price: String(itemPrice(item) ?? 0),
    tags: tagsToText(item.tags),
  };
}

function itemValidationErrors(form: ItemForm, editing: boolean): FieldErrors<ItemField> {
  const errors: FieldErrors<ItemField> = {};
  if (!editing && !form.product_id.trim()) {
    errors.product_id = "Product ID is required.";
  }
  if (!form.name.trim()) {
    errors.name = "Name is required.";
  }
  if (!form.category.trim()) {
    errors.category = "Category ID is required.";
  }
  if (!form.currency.trim()) {
    errors.currency = "Currency is required.";
  }
  const price = Number(form.starting_price);
  if (!Number.isFinite(price) || price < 0) {
    errors.starting_price = "Starting price must be zero or greater.";
  }
  return errors;
}

function categoryValidationErrors(form: CategoryForm): FieldErrors<CategoryField> {
  const errors: FieldErrors<CategoryField> = {};
  if (!form.category_id.trim()) {
    errors.category_id = "Category ID is required.";
  }
  if (!form.name.trim()) {
    errors.name = "Category name is required.";
  }
  if (!Number.isFinite(Number(form.sort_order))) {
    errors.sort_order = "Sort order must be a number.";
  }
  return errors;
}

function visibleErrors<T extends string>(
  errors: FieldErrors<T>,
  touched: FieldTouched<T>,
  submitted: boolean,
): FieldErrors<T> {
  return Object.fromEntries(
    Object.entries(errors).filter(([field]) => submitted || touched[field as T]),
  ) as FieldErrors<T>;
}

function hasErrors<T extends string>(errors: FieldErrors<T>) {
  return Object.keys(errors).length > 0;
}

function buildPayload(form: ItemForm, original: MenuItem | null): MenuItemPayload {
  const price = Number(form.starting_price);
  return {
    product_id: form.product_id.trim(),
    name: form.name.trim(),
    category: form.category.trim(),
    currency: form.currency.trim(),
    description: form.description.trim(),
    available: form.available,
    price: original?.price,
    starting_price: price,
    base_prices: original?.base_prices ?? {},
    requires_customization: original?.requires_customization ?? false,
    customization_group_ids: original?.customization_group_ids ?? [],
    upsell_group_ids: original?.upsell_group_ids ?? [],
    tags: tagsFromText(form.tags),
    search_terms: original?.search_terms ?? [],
    image_url: original?.image_url ?? null,
    metadata: original?.metadata ?? {},
  };
}

function SummaryCard({ label, value, description, loading }: { label: string; value: number | string; description: string; loading: boolean }) {
  return (
    <article className="admin-menu-summary-card">
      <span>{label}</span>
      {loading ? <strong className="admin-skeleton admin-skeleton-value" /> : <strong>{value}</strong>}
      <p>{description}</p>
    </article>
  );
}

export default function AdminMenuPage() {
  const [items, setItems] = useState<MenuItem[]>([]);
  const [hasSuccessfulResponse, setHasSuccessfulResponse] = useState(false);
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const [manualRefreshing, setManualRefreshing] = useState(false);
  const [backgroundRefreshing, setBackgroundRefreshing] = useState(false);
  const [lastSuccessfulRefresh, setLastSuccessfulRefresh] = useState<Date | null>(null);
  const [lastFailedRefresh, setLastFailedRefresh] = useState<RefreshFailure | null>(null);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [availabilityFilter, setAvailabilityFilter] = useState("");
  const [archiveFilter, setArchiveFilter] = useState("active");
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [formMode, setFormMode] = useState<FormMode>("closed");
  const [form, setForm] = useState<ItemForm>(emptyItemForm);
  const [itemTouched, setItemTouched] = useState<FieldTouched<ItemField>>(emptyItemTouched);
  const [itemSubmitAttempted, setItemSubmitAttempted] = useState(false);
  const [editingItem, setEditingItem] = useState<MenuItem | null>(null);
  const [savingItem, setSavingItem] = useState(false);
  const [rowActionId, setRowActionId] = useState<string | null>(null);
  const [archiveCandidate, setArchiveCandidate] = useState<MenuItem | null>(null);
  const [categoryForm, setCategoryForm] = useState<CategoryForm>(emptyCategoryForm);
  const [categoryTouched, setCategoryTouched] = useState<FieldTouched<CategoryField>>(emptyCategoryTouched);
  const [categorySubmitAttempted, setCategorySubmitAttempted] = useState(false);
  const [savingCategory, setSavingCategory] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [advancedMode, setAdvancedMode] = useState<AdvancedMode>("option");
  const [optionGroupJson, setOptionGroupJson] = useState(defaultOptionGroupJson);
  const [upsellGroupJson, setUpsellGroupJson] = useState(defaultUpsellGroupJson);
  const [savingAdvanced, setSavingAdvanced] = useState<AdvancedMode | null>(null);
  const requestInFlight = useRef(false);
  const mounted = useRef(false);
  const hasSuccessfulResponseRef = useRef(false);
  const manualFailureLogged = useRef(false);
  const productIdRef = useRef<HTMLInputElement>(null);
  const itemNameRef = useRef<HTMLInputElement>(null);
  const itemCategoryRef = useRef<HTMLInputElement>(null);
  const itemCurrencyRef = useRef<HTMLInputElement>(null);
  const itemPriceRef = useRef<HTMLInputElement>(null);
  const categoryIdRef = useRef<HTMLInputElement>(null);
  const categoryNameRef = useRef<HTMLInputElement>(null);
  const categorySortOrderRef = useRef<HTMLInputElement>(null);

  const loadItems = useCallback(async (source: RefreshSource) => {
    if (requestInFlight.current) {
      return;
    }
    requestInFlight.current = true;
    const refreshKind = source === "manual" ? "manual" : hasSuccessfulResponseRef.current ? "background" : "initial";
    if (refreshKind === "manual") {
      setManualRefreshing(true);
    } else if (refreshKind === "background") {
      setBackgroundRefreshing(true);
    }
    try {
      const result = await adminGet<{ items: MenuItem[] }>("/api/admin/menu/entities?type=menu_item");
      if (!mounted.current) {
        return;
      }
      setItems(result.items);
      setHasSuccessfulResponse(true);
      hasSuccessfulResponseRef.current = true;
      setLastSuccessfulRefresh(new Date());
      setLastFailedRefresh(null);
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      if (!mounted.current) {
        return;
      }
      setLastFailedRefresh({ attemptedAt: new Date(), hadPreviousData: hasSuccessfulResponseRef.current });
      if (source === "manual" && !manualFailureLogged.current) {
        console.warn("Admin menu refresh failed", exc);
        manualFailureLogged.current = true;
      }
    } finally {
      if (mounted.current) {
        setIsInitialLoading(false);
        setManualRefreshing(false);
        setBackgroundRefreshing(false);
      }
      requestInFlight.current = false;
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void loadItems("initial");
    const timer = window.setInterval(() => {
      void loadItems("background");
    }, 60000);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
    };
  }, [loadItems]);

  const categories = useMemo(() => {
    return Array.from(new Set(items.map((item) => item.category).filter(Boolean))).sort((a, b) => a.localeCompare(b));
  }, [items]);

  const filteredItems = useMemo(() => {
    if (!hasSuccessfulResponse) {
      return [];
    }
    const normalized = search.trim().toLowerCase();
    return items.filter((item) => {
      const matchesSearch = !normalized
        || item.name.toLowerCase().includes(normalized)
        || item.product_id.toLowerCase().includes(normalized);
      const matchesCategory = !categoryFilter || item.category === categoryFilter;
      const matchesAvailability = !availabilityFilter
        || (availabilityFilter === "available" ? item.available : !item.available);
      const matchesArchive = archiveFilter === "all"
        || (archiveFilter === "archived" ? Boolean(item.archived) : !item.archived);
      return matchesSearch && matchesCategory && matchesAvailability && matchesArchive;
    });
  }, [archiveFilter, availabilityFilter, categoryFilter, hasSuccessfulResponse, items, search]);

  const summary = useMemo(() => ({
    total: items.length,
    available: items.filter((item) => item.available && !item.archived).length,
    unavailable: items.filter((item) => !item.available && !item.archived).length,
    archived: items.filter((item) => item.archived).length,
  }), [items]);

  const hasInitialFailure = !hasSuccessfulResponse && lastFailedRefresh !== null;
  const hasStaleWarning = hasSuccessfulResponse && lastFailedRefresh !== null && lastFailedRefresh.hadPreviousData;
  const showSkeleton = isInitialLoading && !hasSuccessfulResponse && !lastFailedRefresh;
  const summaryUnavailable = !hasSuccessfulResponse && lastFailedRefresh !== null;
  const hasActiveFilters = Boolean(search.trim() || categoryFilter || availabilityFilter || archiveFilter !== "active");
  const showEmptyMenu = hasSuccessfulResponse && items.length === 0;
  const showFilteredEmpty = hasSuccessfulResponse && items.length > 0 && filteredItems.length === 0;
  const itemErrors = formMode === "closed" ? {} : itemValidationErrors(form, formMode === "edit");
  const visibleItemErrors = visibleErrors(itemErrors, itemTouched, itemSubmitAttempted);
  const categoryErrors = categoryValidationErrors(categoryForm);
  const visibleCategoryErrors = visibleErrors(categoryErrors, categoryTouched, categorySubmitAttempted);
  const currentAdvancedJson = advancedMode === "option" ? optionGroupJson : upsellGroupJson;
  const advancedValidation = validateAdvancedMenuJson(currentAdvancedJson, advancedMode);

  const clearFilters = () => {
    setSearch("");
    setCategoryFilter("");
    setAvailabilityFilter("");
    setArchiveFilter("active");
  };

  const closeItemForm = () => {
    setFormMode("closed");
    setForm(emptyItemForm);
    setItemTouched(emptyItemTouched);
    setItemSubmitAttempted(false);
    setEditingItem(null);
  };

  const openAddForm = () => {
    setFeedback(null);
    setForm(emptyItemForm);
    setItemTouched(emptyItemTouched);
    setItemSubmitAttempted(false);
    setEditingItem(null);
    setFormMode("add");
  };

  const openEditForm = (item: MenuItem) => {
    setFeedback(null);
    setArchiveCandidate(null);
    setEditingItem(item);
    setForm(itemToForm(item));
    setItemTouched(emptyItemTouched);
    setItemSubmitAttempted(false);
    setFormMode("edit");
  };

  const markItemTouched = (field: ItemField) => {
    setItemTouched((current) => ({ ...current, [field]: true }));
  };

  const markCategoryTouched = (field: CategoryField) => {
    setCategoryTouched((current) => ({ ...current, [field]: true }));
  };

  const focusFirstInvalidItemField = (errors: FieldErrors<ItemField>) => {
    const refs: Record<ItemField, RefObject<HTMLInputElement | null>> = {
      product_id: productIdRef,
      name: itemNameRef,
      category: itemCategoryRef,
      currency: itemCurrencyRef,
      starting_price: itemPriceRef,
    };
    const firstField = (["product_id", "name", "category", "currency", "starting_price"] as ItemField[]).find((field) => errors[field]);
    if (firstField) {
      refs[firstField].current?.focus();
    }
  };

  const focusFirstInvalidCategoryField = (errors: FieldErrors<CategoryField>) => {
    const refs: Record<CategoryField, RefObject<HTMLInputElement | null>> = {
      category_id: categoryIdRef,
      name: categoryNameRef,
      sort_order: categorySortOrderRef,
    };
    const firstField = (["category_id", "name", "sort_order"] as CategoryField[]).find((field) => errors[field]);
    if (firstField) {
      refs[firstField].current?.focus();
    }
  };

  const saveItem = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const errors = itemValidationErrors(form, formMode === "edit");
    if (hasErrors(errors)) {
      setItemSubmitAttempted(true);
      setFeedback({ tone: "error", message: "Check the highlighted menu item fields and retry." });
      focusFirstInvalidItemField(errors);
      return;
    }
    setSavingItem(true);
    setFeedback(null);
    try {
      const payload = buildPayload(form, editingItem);
      const result = formMode === "edit"
        ? await adminPut<{ item: MenuItem }>(`/api/admin/menu/items/${payload.product_id}`, payload)
        : await adminPost<{ item: MenuItem }>("/api/admin/menu/items", payload);
      if (!mounted.current) {
        return;
      }
      setItems((current) => {
        const others = current.filter((item) => item.product_id !== result.item.product_id);
        return [...others, result.item].sort((a, b) => a.name.localeCompare(b.name));
      });
      setHasSuccessfulResponse(true);
      hasSuccessfulResponseRef.current = true;
      setFeedback({ tone: "success", message: `${result.item.name} saved.` });
      closeItemForm();
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      setFeedback({ tone: "error", message: "Menu item could not be saved. Check the form and retry." });
      console.warn("Admin menu item save failed", exc);
    } finally {
      if (mounted.current) {
        setSavingItem(false);
      }
    }
  };

  const toggleAvailability = async (item: MenuItem) => {
    setRowActionId(item.product_id);
    setFeedback(null);
    try {
      const result = await adminPatch<{ item: MenuItem }>(`/api/admin/menu/items/${item.product_id}/availability`, { available: !item.available });
      if (!mounted.current) {
        return;
      }
      setItems((current) => current.map((entry) => entry.product_id === result.item.product_id ? result.item : entry));
      setFeedback({ tone: "success", message: `${result.item.name} is now ${availabilityLabel(result.item.available).toLowerCase()}.` });
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      setFeedback({ tone: "error", message: `${item.name} availability could not be updated.` });
      console.warn("Admin menu availability update failed", exc);
    } finally {
      if (mounted.current) {
        setRowActionId(null);
      }
    }
  };

  const confirmArchive = async () => {
    if (!archiveCandidate) {
      return;
    }
    const item = archiveCandidate;
    setRowActionId(item.product_id);
    setFeedback(null);
    try {
      const result = await adminPatch<{ item: MenuItem }>(`/api/admin/menu/items/${item.product_id}/archive`);
      if (!mounted.current) {
        return;
      }
      setItems((current) => current.map((entry) => entry.product_id === result.item.product_id ? result.item : entry));
      setArchiveCandidate(null);
      setFeedback({ tone: "success", message: `${result.item.name} archived.` });
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      setFeedback({ tone: "error", message: `${item.name} could not be archived.` });
      console.warn("Admin menu archive failed", exc);
    } finally {
      if (mounted.current) {
        setRowActionId(null);
      }
    }
  };

  const saveCategory = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const errors = categoryValidationErrors(categoryForm);
    if (hasErrors(errors)) {
      setCategorySubmitAttempted(true);
      setFeedback({ tone: "error", message: "Check the highlighted category fields and retry." });
      focusFirstInvalidCategoryField(errors);
      return;
    }
    setSavingCategory(true);
    setFeedback(null);
    try {
      await adminPost("/api/admin/menu/categories", {
        category_id: categoryForm.category_id.trim(),
        name: categoryForm.name.trim(),
        sort_order: Number(categoryForm.sort_order),
      });
      if (!mounted.current) {
        return;
      }
      setCategoryForm(emptyCategoryForm);
      setCategoryTouched(emptyCategoryTouched);
      setCategorySubmitAttempted(false);
      setFeedback({ tone: "success", message: "Category saved." });
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      setFeedback({ tone: "error", message: "Category could not be saved. Check the fields and retry." });
      console.warn("Admin menu category save failed", exc);
    } finally {
      if (mounted.current) {
        setSavingCategory(false);
      }
    }
  };

  const saveAdvanced = async () => {
    if (!advancedValidation.ok) {
      return;
    }
    setSavingAdvanced(advancedMode);
    setFeedback(null);
    const path = advancedMode === "option" ? "/api/admin/menu/option-groups" : "/api/admin/menu/upsell-groups";
    try {
      await adminPost(path, advancedValidation.data);
      if (!mounted.current) {
        return;
      }
      setFeedback({ tone: "success", message: `${advancedMode === "option" ? "Option group" : "Upsell group"} saved.` });
      if (advancedMode === "option") {
        setOptionGroupJson(defaultOptionGroupJson);
      } else {
        setUpsellGroupJson(defaultUpsellGroupJson);
      }
    } catch (exc) {
      if (!(exc instanceof Error)) {
        throw exc;
      }
      setFeedback({ tone: "error", message: `${advancedMode === "option" ? "Option group" : "Upsell group"} could not be saved.` });
      console.warn("Admin menu advanced save failed", exc);
    } finally {
      if (mounted.current) {
        setSavingAdvanced(null);
      }
    }
  };

  const actions = (
    <div className="admin-dashboard-actions">
      <span aria-live="polite">
        {lastFailedRefresh && !lastSuccessfulRefresh
          ? `Refresh failed at ${formatTime(lastFailedRefresh.attemptedAt)}`
          : lastFailedRefresh && lastSuccessfulRefresh
            ? `Update failed at ${formatTime(lastFailedRefresh.attemptedAt)} \u00b7 Showing previous menu data`
            : lastSuccessfulRefresh
              ? `Updated ${formatTime(lastSuccessfulRefresh)}`
              : "Not refreshed yet"}
      </span>
      <button className="admin-refresh-button" disabled={manualRefreshing || backgroundRefreshing || showSkeleton} onClick={() => void loadItems("manual")} type="button">
        <MenuIcon name="refresh" />
        {manualRefreshing ? "Refreshing..." : "Refresh"}
      </button>
      <button className="primary admin-inline-action" onClick={openAddForm} type="button">
        <MenuIcon name="plus" />
        Add menu item
      </button>
    </div>
  );

  return (
    <AdminShell
      actions={actions}
      subtitle="Manage products, availability, categories, and ordering options"
      title="Menu Management"
    >
      <div className="admin-menu-page">
        {feedback && (
          <div className={feedback.tone === "success" ? "admin-success" : feedback.tone === "warning" ? "admin-warning-panel" : "admin-error-panel"} role={feedback.tone === "error" ? "alert" : "status"}>
            <strong>{feedback.message}</strong>
          </div>
        )}

        {hasInitialFailure && (
          <section className="admin-error-panel" role="alert">
            <div>
              <strong>Menu unavailable</strong>
              <p>Menu data could not be loaded. Retry when the service is reachable.</p>
            </div>
            <button className="secondary" disabled={manualRefreshing || backgroundRefreshing} onClick={() => void loadItems("manual")} type="button">Retry</button>
          </section>
        )}
        {hasStaleWarning && (
          <section className="admin-warning-panel" role="status">
            <div>
              <strong>Latest refresh failed</strong>
              <p>Latest refresh failed. Showing previously loaded menu data.</p>
            </div>
            <button className="secondary" disabled={manualRefreshing || backgroundRefreshing} onClick={() => void loadItems("manual")} type="button">Retry</button>
          </section>
        )}

        <section className="admin-menu-summary" aria-label="Summary of currently loaded menu">
          <SummaryCard description={summaryUnavailable ? "Unavailable until menu reconnects." : "Items in the currently loaded menu."} label="Total items" loading={showSkeleton} value={summaryUnavailable ? unavailableValue : summary.total} />
          <SummaryCard description={summaryUnavailable ? "Unavailable until menu reconnects." : "Active items available to customers."} label="Available" loading={showSkeleton} value={summaryUnavailable ? unavailableValue : summary.available} />
          <SummaryCard description={summaryUnavailable ? "Unavailable until menu reconnects." : "Active items currently disabled."} label="Unavailable" loading={showSkeleton} value={summaryUnavailable ? unavailableValue : summary.unavailable} />
          <SummaryCard description={summaryUnavailable ? "Unavailable until menu reconnects." : "Items archived out of active operations."} label="Archived" loading={showSkeleton} value={summaryUnavailable ? unavailableValue : summary.archived} />
        </section>

        <section className="admin-menu-toolbar" aria-label="Menu filters">
          <label className="admin-search-control">
            <span>Search menu</span>
            <span>
              <MenuIcon name="search" />
              <input disabled={showSkeleton} onChange={(event) => setSearch(event.target.value)} placeholder="Product name or ID" value={search} />
            </span>
          </label>
          <label>
            <span>Category</span>
            <select disabled={showSkeleton || !hasSuccessfulResponse} onChange={(event) => setCategoryFilter(event.target.value)} value={categoryFilter}>
              <option value="">All categories</option>
              {categories.map((category) => <option key={category} value={category}>{readableMenuText(category)}</option>)}
            </select>
          </label>
          <label>
            <span>Availability</span>
            <select disabled={showSkeleton || !hasSuccessfulResponse} onChange={(event) => setAvailabilityFilter(event.target.value)} value={availabilityFilter}>
              <option value="">All</option>
              <option value="available">Available</option>
              <option value="unavailable">Unavailable</option>
            </select>
          </label>
          <label>
            <span>Archive state</span>
            <select disabled={showSkeleton || !hasSuccessfulResponse} onChange={(event) => setArchiveFilter(event.target.value)} value={archiveFilter}>
              <option value="active">Active items</option>
              <option value="archived">Archived items</option>
              <option value="all">All items</option>
            </select>
          </label>
          <div className="admin-toolbar-status">
            <span>Background refresh every 60s</span>
            {backgroundRefreshing && <strong>Checking menu...</strong>}
          </div>
          {hasActiveFilters && <button className="secondary" onClick={clearFilters} type="button">Clear filters</button>}
        </section>

        {formMode !== "closed" && (
          <section className="admin-panel admin-menu-form-panel" aria-labelledby="menu-item-form-heading">
            <div className="admin-section-heading">
              <div>
                <h2 id="menu-item-form-heading">{formMode === "edit" ? "Edit menu item" : "Add menu item"}</h2>
                <p>Basic fields are editable here. Existing advanced fields are preserved during edits.</p>
              </div>
            </div>
            <form className="admin-menu-form" onSubmit={saveItem}>
              <label>
                Product ID
                <input
                  aria-describedby={visibleItemErrors.product_id ? "menu-product-id-error" : undefined}
                  aria-invalid={visibleItemErrors.product_id ? true : undefined}
                  disabled={formMode === "edit"}
                  ref={productIdRef}
                  value={form.product_id}
                  onBlur={() => markItemTouched("product_id")}
                  onChange={(event) => setForm({ ...form, product_id: event.target.value })}
                />
                {visibleItemErrors.product_id && <p className="admin-form-error" id="menu-product-id-error">{visibleItemErrors.product_id}</p>}
              </label>
              <label>
                Name
                <input
                  aria-describedby={visibleItemErrors.name ? "menu-item-name-error" : undefined}
                  aria-invalid={visibleItemErrors.name ? true : undefined}
                  ref={itemNameRef}
                  value={form.name}
                  onBlur={() => markItemTouched("name")}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
                {visibleItemErrors.name && <p className="admin-form-error" id="menu-item-name-error">{visibleItemErrors.name}</p>}
              </label>
              <label>
                Category ID
                <input
                  aria-describedby={visibleItemErrors.category ? "menu-item-category-error" : undefined}
                  aria-invalid={visibleItemErrors.category ? true : undefined}
                  ref={itemCategoryRef}
                  value={form.category}
                  onBlur={() => markItemTouched("category")}
                  onChange={(event) => setForm({ ...form, category: event.target.value })}
                />
                {visibleItemErrors.category && <p className="admin-form-error" id="menu-item-category-error">{visibleItemErrors.category}</p>}
              </label>
              <label>
                Currency
                <input
                  aria-describedby={visibleItemErrors.currency ? "menu-item-currency-error" : undefined}
                  aria-invalid={visibleItemErrors.currency ? true : undefined}
                  ref={itemCurrencyRef}
                  value={form.currency}
                  onBlur={() => markItemTouched("currency")}
                  onChange={(event) => setForm({ ...form, currency: event.target.value })}
                />
                {visibleItemErrors.currency && <p className="admin-form-error" id="menu-item-currency-error">{visibleItemErrors.currency}</p>}
              </label>
              <label>
                Starting price
                <input
                  aria-describedby={visibleItemErrors.starting_price ? "menu-item-price-error" : undefined}
                  aria-invalid={visibleItemErrors.starting_price ? true : undefined}
                  min="0"
                  ref={itemPriceRef}
                  type="number"
                  value={form.starting_price}
                  onBlur={() => markItemTouched("starting_price")}
                  onChange={(event) => setForm({ ...form, starting_price: event.target.value })}
                />
                {visibleItemErrors.starting_price && <p className="admin-form-error" id="menu-item-price-error">{visibleItemErrors.starting_price}</p>}
              </label>
              <label>
                Tags
                <input value={form.tags} onChange={(event) => setForm({ ...form, tags: event.target.value })} />
              </label>
              <label className="admin-menu-form-wide">
                Description
                <textarea value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
              </label>
              <label className="admin-checkbox-row">
                <input checked={form.available} type="checkbox" onChange={(event) => setForm({ ...form, available: event.target.checked })} />
                Available
              </label>
              <div className="admin-actions admin-menu-form-wide">
                <button className="primary" disabled={savingItem} type="submit">{savingItem ? "Saving..." : "Save item"}</button>
                <button className="secondary" disabled={savingItem} onClick={closeItemForm} type="button">Cancel</button>
              </div>
            </form>
          </section>
        )}

        <section className="admin-panel admin-menu-catalogue">
          <div className="admin-section-heading">
            <div>
              <h2>Menu item catalogue</h2>
              <p>Operational view of the currently loaded menu item response.</p>
            </div>
          </div>
          {hasInitialFailure ? (
            <div className="admin-empty-state">
              <strong>Menu data is unavailable.</strong>
              <p>Retry when the service is reachable.</p>
              <button className="secondary" disabled={manualRefreshing || backgroundRefreshing} onClick={() => void loadItems("manual")} type="button">Retry</button>
            </div>
          ) : (
            <div className="admin-menu-table-wrap">
              <table className="admin-table admin-menu-table">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Product ID</th>
                    <th>Category</th>
                    <th>Starting price</th>
                    <th>Availability</th>
                    <th>Archive state</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {showSkeleton && [0, 1, 2, 3].map((row) => (
                    <tr key={row}>
                      <td><span className="admin-skeleton admin-skeleton-line" /></td>
                      <td><span className="admin-skeleton admin-skeleton-line" /></td>
                      <td><span className="admin-skeleton admin-skeleton-pill" /></td>
                      <td><span className="admin-skeleton admin-skeleton-line" /></td>
                      <td><span className="admin-skeleton admin-skeleton-pill" /></td>
                      <td><span className="admin-skeleton admin-skeleton-pill" /></td>
                      <td><span className="admin-skeleton admin-skeleton-line" /></td>
                    </tr>
                  ))}
                  {!showSkeleton && filteredItems.map((item) => (
                    <tr className={item.archived ? "is-archived" : ""} key={item.product_id}>
                      <td data-label="Item">
                        <div className="admin-menu-item-cell">
                          <strong>{item.name}</strong>
                          {item.description && <p>{item.description}</p>}
                          {(item.tags ?? []).length > 0 && (
                            <div className="admin-menu-tag-row">
                              {(item.tags ?? []).map((tag) => <span key={`${item.product_id}-${tag}`}>{tag}</span>)}
                            </div>
                          )}
                        </div>
                      </td>
                      <td data-label="Product ID"><code>{item.product_id}</code></td>
                      <td data-label="Category">{readableMenuText(item.category)}</td>
                      <td data-label="Starting price">{money(itemPrice(item), item.currency)}</td>
                      <td data-label="Availability"><span className={`admin-menu-badge ${item.available ? "is-available" : "is-unavailable"}`}>{availabilityLabel(item.available)}</span></td>
                      <td data-label="Archive state"><span className={`admin-menu-badge ${item.archived ? "is-archived" : "is-active"}`}>{archiveLabel(item.archived)}</span></td>
                      <td data-label="Actions">
                        <div className="admin-row-actions">
                          <button className="secondary" disabled={rowActionId === item.product_id} onClick={() => openEditForm(item)} type="button"><MenuIcon name="edit" /> Edit</button>
                          <button className="secondary" disabled={rowActionId === item.product_id || Boolean(item.archived)} onClick={() => void toggleAvailability(item)} type="button">
                            {rowActionId === item.product_id ? "Updating..." : item.available ? "Disable" : "Enable"}
                          </button>
                          {!item.archived && (
                            <button className="secondary danger" disabled={rowActionId === item.product_id} onClick={() => setArchiveCandidate(item)} type="button"><MenuIcon name="archive" /> Archive</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {showEmptyMenu && (
            <div className="admin-empty-state">
              <strong>No menu items are currently available.</strong>
              <p>Add a menu item to begin managing products.</p>
            </div>
          )}
          {showFilteredEmpty && (
            <div className="admin-empty-state">
              <strong>No menu items match the selected filters.</strong>
              <p>Clear filters or search for a different product name or ID.</p>
              <button className="secondary" onClick={clearFilters} type="button">Clear filters</button>
            </div>
          )}
        </section>

        {archiveCandidate && (
          <section className="admin-danger-confirmation" aria-labelledby="archive-confirm-heading">
            <h2 id="archive-confirm-heading">Archive {archiveCandidate.name}</h2>
            <p>Archived items will no longer be treated as active menu products.</p>
            <div className="admin-actions">
              <button className="secondary danger" disabled={rowActionId === archiveCandidate.product_id} onClick={() => void confirmArchive()} type="button">
                {rowActionId === archiveCandidate.product_id ? "Archiving..." : "Confirm archive"}
              </button>
              <button className="secondary" disabled={rowActionId === archiveCandidate.product_id} onClick={() => setArchiveCandidate(null)} type="button">Cancel</button>
            </div>
          </section>
        )}

        <section className="admin-panel admin-menu-categories">
          <div className="admin-section-heading">
            <div>
              <h2>Categories</h2>
              <p>Create categories used to organise menu items.</p>
            </div>
          </div>
          <form className="admin-menu-form" onSubmit={saveCategory}>
            <label>
              Category ID
              <input
                aria-describedby={visibleCategoryErrors.category_id ? "category-id-error" : undefined}
                aria-invalid={visibleCategoryErrors.category_id ? true : undefined}
                ref={categoryIdRef}
                value={categoryForm.category_id}
                onBlur={() => markCategoryTouched("category_id")}
                onChange={(event) => setCategoryForm({ ...categoryForm, category_id: event.target.value })}
              />
              {visibleCategoryErrors.category_id && <p className="admin-form-error" id="category-id-error">{visibleCategoryErrors.category_id}</p>}
            </label>
            <label>
              Name
              <input
                aria-describedby={visibleCategoryErrors.name ? "category-name-error" : undefined}
                aria-invalid={visibleCategoryErrors.name ? true : undefined}
                ref={categoryNameRef}
                value={categoryForm.name}
                onBlur={() => markCategoryTouched("name")}
                onChange={(event) => setCategoryForm({ ...categoryForm, name: event.target.value })}
              />
              {visibleCategoryErrors.name && <p className="admin-form-error" id="category-name-error">{visibleCategoryErrors.name}</p>}
            </label>
            <label>
              Sort order
              <input
                aria-describedby={visibleCategoryErrors.sort_order ? "category-sort-order-error" : undefined}
                aria-invalid={visibleCategoryErrors.sort_order ? true : undefined}
                ref={categorySortOrderRef}
                type="number"
                value={categoryForm.sort_order}
                onBlur={() => markCategoryTouched("sort_order")}
                onChange={(event) => setCategoryForm({ ...categoryForm, sort_order: event.target.value })}
              />
              {visibleCategoryErrors.sort_order && <p className="admin-form-error" id="category-sort-order-error">{visibleCategoryErrors.sort_order}</p>}
            </label>
            <div className="admin-actions admin-menu-form-wide">
              <button className="primary" disabled={savingCategory} type="submit">{savingCategory ? "Saving..." : "Save category"}</button>
            </div>
          </form>
        </section>

        <section className="admin-panel admin-menu-advanced">
          <button aria-expanded={advancedOpen} className="admin-menu-advanced-toggle" onClick={() => setAdvancedOpen((open) => !open)} type="button">
            <span><MenuIcon name="settings" /> Advanced configuration</span>
            <strong>{advancedOpen ? "Hide" : "Show"}</strong>
          </button>
          {advancedOpen && (
            <div className="admin-menu-advanced-body">
              <div className="admin-warning-panel" role="note">
                <strong>These tools modify menu configuration structures.</strong>
                <p>Use validated JSON that matches the backend schema.</p>
              </div>
              <div className="admin-menu-tabs" role="tablist" aria-label="Advanced menu configuration type">
                <button aria-selected={advancedMode === "option"} className={advancedMode === "option" ? "is-active" : ""} onClick={() => setAdvancedMode("option")} role="tab" type="button">Option group</button>
                <button aria-selected={advancedMode === "upsell"} className={advancedMode === "upsell" ? "is-active" : ""} onClick={() => setAdvancedMode("upsell")} role="tab" type="button">Upsell group</button>
              </div>
              <label className="admin-json-editor">
                {advancedMode === "option" ? "Option group JSON" : "Upsell group JSON"}
                <textarea
                  spellCheck={false}
                  value={currentAdvancedJson}
                  onChange={(event) => {
                    if (advancedMode === "option") {
                      setOptionGroupJson(event.target.value);
                    } else {
                      setUpsellGroupJson(event.target.value);
                    }
                  }}
                />
              </label>
              <div className={advancedValidation.ok ? "admin-success" : "admin-error-panel"} role="status">
                <strong>{advancedValidation.ok ? "JSON is valid." : advancedValidation.error}</strong>
              </div>
              {advancedValidation.ok && (
                <pre className="admin-json-preview">{JSON.stringify(advancedValidation.data, null, 2)}</pre>
              )}
              <div className="admin-actions">
                <button className="primary" disabled={!advancedValidation.ok || Boolean(savingAdvanced)} onClick={() => void saveAdvanced()} type="button">
                  {savingAdvanced === advancedMode ? "Saving..." : `Save ${advancedMode === "option" ? "option group" : "upsell group"}`}
                </button>
              </div>
            </div>
          )}
        </section>
      </div>
    </AdminShell>
  );
}
