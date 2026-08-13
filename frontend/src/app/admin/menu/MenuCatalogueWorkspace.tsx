"use client";

import { money } from "@/app/admin/AdminShell";
import {
  archiveLabel,
  availabilityLabel,
  MenuIcon,
  readableMenuText,
} from "@/app/admin/menu/menuPresentation";
import type { MenuItem } from "@/app/admin/menu/menuTypes";

function itemPrice(item: MenuItem) {
  return item.starting_price ?? item.price;
}

function MenuItemTags({ item, complete = false }: { item: MenuItem; complete?: boolean }) {
  const tags = item.tags ?? [];
  const visibleTags = complete ? tags : tags.slice(0, 3);
  const hiddenTags = tags.slice(visibleTags.length);
  if (!tags.length) return <span className="menu-no-tags">No tags</span>;
  return (
    <div className="menu-card-tags">
      {visibleTags.map((tag) => <span key={`${item.product_id}-${tag}`}>{tag}</span>)}
      {hiddenTags.length > 0 && <span title={hiddenTags.join(", ")}>+{hiddenTags.length}</span>}
    </div>
  );
}

function MenuItemActions({
  item,
  busy,
  strong = false,
  onArchive,
  onEdit,
  onToggleAvailability,
}: {
  item: MenuItem;
  busy: boolean;
  strong?: boolean;
  onArchive: (item: MenuItem) => void;
  onEdit: (item: MenuItem) => void;
  onToggleAvailability: (item: MenuItem) => void;
}) {
  return (
    <div className={strong ? "menu-detail-actions" : "menu-card-actions"}>
      <button disabled={busy} onClick={() => onEdit(item)} type="button"><MenuIcon name="edit" />Edit</button>
      <button className="is-warning" disabled={busy || Boolean(item.archived)} onClick={() => onToggleAvailability(item)} type="button">
        <MenuIcon name={item.available ? "disable" : "available"} />
        {busy ? "Updating..." : item.available ? "Disable" : "Enable"}
      </button>
      {!item.archived && (
        <button className="is-danger" disabled={busy} onClick={() => onArchive(item)} type="button"><MenuIcon name="archive" />Archive</button>
      )}
    </div>
  );
}

function MenuItemCard({
  index,
  item,
  selected,
  busy,
  onArchive,
  onEdit,
  onSelect,
  onToggleAvailability,
}: {
  index: number;
  item: MenuItem;
  selected: boolean;
  busy: boolean;
  onArchive: (item: MenuItem) => void;
  onEdit: (item: MenuItem) => void;
  onSelect: (item: MenuItem) => void;
  onToggleAvailability: (item: MenuItem) => void;
}) {
  return (
    <article className={`menu-item-card${selected ? " is-selected" : ""}${item.archived ? " is-archived" : ""}`}>
      <button
        aria-current={selected ? "true" : undefined}
        className="menu-item-card-select"
        onClick={() => onSelect(item)}
        type="button"
      >
        <span className="menu-card-heading"><span>{index + 1}</span><strong>{item.name}</strong></span>
        <span className="menu-card-description">{item.description || "No description provided."}</span>
        <MenuItemTags item={item} />
        <span className="menu-card-metadata">
          <span><small>ID</small><code title={item.product_id}>{item.product_id}</code></span>
          <span><small>Category</small><strong>{readableMenuText(item.category)}</strong></span>
          <strong>{money(itemPrice(item), item.currency)}</strong>
        </span>
        <span className="menu-card-state">
          <span className={`admin-menu-badge ${item.available ? "is-available" : "is-unavailable"}`}>{availabilityLabel(item.available)}</span>
          <span className={`admin-menu-badge ${item.archived ? "is-archived" : "is-active"}`}>{archiveLabel(item.archived)}</span>
        </span>
      </button>
      <MenuItemActions
        busy={busy}
        item={item}
        onArchive={onArchive}
        onEdit={onEdit}
        onToggleAvailability={onToggleAvailability}
      />
    </article>
  );
}

function SelectedMenuItemPanel({
  item,
  busy,
  onArchive,
  onEdit,
  onToggleAvailability,
}: {
  item: MenuItem | null;
  busy: boolean;
  onArchive: (item: MenuItem) => void;
  onEdit: (item: MenuItem) => void;
  onToggleAvailability: (item: MenuItem) => void;
}) {
  if (!item) {
    return (
      <aside className="admin-panel selected-menu-panel" aria-label="Selected menu item">
        <div className="admin-empty-state"><strong>Select a menu item</strong><p>Choose a catalogue card to inspect its complete details.</p></div>
      </aside>
    );
  }
  return (
    <aside className="admin-panel selected-menu-panel" aria-label="Selected menu item">
      <div className="selected-menu-body">
        <h2>{item.name}</h2>
        <dl className="selected-menu-fields">
          <div><dt>Product ID</dt><dd><code>{item.product_id}</code></dd></div>
          <div className="is-wide"><dt>Description</dt><dd>{item.description || "No description provided."}</dd></div>
          <div><dt>Category</dt><dd>{readableMenuText(item.category)}</dd></div>
          <div><dt>Starting price</dt><dd><strong>{money(itemPrice(item), item.currency)}</strong></dd></div>
          <div className="is-wide"><dt>Tags</dt><dd><MenuItemTags complete item={item} /></dd></div>
          <div><dt>Availability</dt><dd><span className={`admin-menu-badge ${item.available ? "is-available" : "is-unavailable"}`}>{availabilityLabel(item.available)}</span></dd></div>
          <div><dt>Archive state</dt><dd><span className={`admin-menu-badge ${item.archived ? "is-archived" : "is-active"}`}>{archiveLabel(item.archived)}</span></dd></div>
        </dl>
      </div>
      <MenuItemActions
        busy={busy}
        item={item}
        onArchive={onArchive}
        onEdit={onEdit}
        onToggleAvailability={onToggleAvailability}
        strong
      />
    </aside>
  );
}

export function MenuCatalogueWorkspace({
  items,
  selectedProductId,
  rowActionId,
  showSkeleton,
  onArchive,
  onEdit,
  onSelect,
  onToggleAvailability,
}: {
  items: MenuItem[];
  selectedProductId: string | null;
  rowActionId: string | null;
  showSkeleton: boolean;
  onArchive: (item: MenuItem) => void;
  onEdit: (item: MenuItem) => void;
  onSelect: (item: MenuItem) => void;
  onToggleAvailability: (item: MenuItem) => void;
}) {
  const selected = items.find((item) => item.product_id === selectedProductId) ?? null;
  return (
    <div className="menu-catalogue-workspace">
      <section className="admin-panel admin-menu-catalogue">
        <div className="menu-catalogue-heading">
          <h2>Menu item catalogue</h2>
          <p>Operational view of the currently loaded menu item response.</p>
        </div>
        <div className="menu-catalogue-scroll">
          {showSkeleton ? (
            <div className="menu-card-grid" aria-label="Loading menu items">
              {[0, 1, 2, 3, 4, 5].map((row) => <div className="menu-item-card menu-item-card-skeleton" key={row}><span className="admin-skeleton admin-skeleton-line" /><span className="admin-skeleton admin-skeleton-line" /><span className="admin-skeleton admin-skeleton-pill" /></div>)}
            </div>
          ) : (
            <div className="menu-card-grid">
              {items.map((item, index) => (
                <MenuItemCard
                  busy={rowActionId === item.product_id}
                  index={index}
                  item={item}
                  key={item.product_id}
                  onArchive={onArchive}
                  onEdit={onEdit}
                  onSelect={onSelect}
                  onToggleAvailability={onToggleAvailability}
                  selected={item.product_id === selectedProductId}
                />
              ))}
            </div>
          )}
        </div>
      </section>
      <SelectedMenuItemPanel
        busy={Boolean(selected && rowActionId === selected.product_id)}
        item={selected}
        onArchive={onArchive}
        onEdit={onEdit}
        onToggleAvailability={onToggleAvailability}
      />
    </div>
  );
}
