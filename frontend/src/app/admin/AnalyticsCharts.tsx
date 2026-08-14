"use client";

import { useId, useState } from "react";
import type { ReactNode } from "react";
import type { Analytics } from "@/app/admin/OperationsOverview";
import { statusLabel } from "@/app/admin/orders/orderPresentation";

type WindowDays = 7 | 30;
type SeriesEntry = { key: string; label: string; value: number; color: string };
type TooltipData = {
  key: string;
  title: string;
  lines: string[];
  meta?: string;
  left: number;
  top: number;
};

const BLUE = "#1769e0";
const GREEN = "#168438";
const CHART_COLORS = ["#168438", "#7b4bc4", "#c97800", "#07847c", "#cf2834"];
const STATUS_FALLBACK_COLORS = ["#5c6bc0", "#8b5e34", "#64748b", "#6d4c91", "#287c71"];

function compactNumber(value: number) {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function currencyLabel(currency: string) {
  return currency === "PKR" ? "Rs" : currency;
}

function exactNumber(value: number) {
  return new Intl.NumberFormat("en", { maximumFractionDigits: 2 }).format(value);
}

function formattedRevenue(value: number, currency: string) {
  return `${currencyLabel(currency)} ${exactNumber(value)}`;
}

function shortDate(value: string) {
  const [, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(2020, month - 1, day)));
}

function hourLabel(hour: number) {
  const normalized = hour % 24;
  if (normalized === 0) return "12 AM";
  if (normalized === 12) return "12 PM";
  return `${normalized > 12 ? normalized - 12 : normalized} ${normalized >= 12 ? "PM" : "AM"}`;
}

function hourRangeLabel(hour: number) {
  return `${hourLabel(hour)} \u2013 ${hourLabel((hour + 1) % 24)}`;
}

function tooltipPosition(value: number) {
  return Math.min(88, Math.max(12, value));
}

function ChartTooltip({ id, tooltip }: { id: string; tooltip: TooltipData | null }) {
  if (!tooltip) return null;
  const alignment = tooltip.left <= 20 ? "is-start" : tooltip.left >= 80 ? "is-end" : "is-center";
  return (
    <div
      className={`ops-chart-tooltip ${alignment}`}
      id={id}
      role="tooltip"
      style={{ left: `${tooltip.left}%`, top: `${tooltip.top}%` }}
    >
      <strong>{tooltip.title}</strong>
      {tooltip.lines.map((line) => <span key={line}>{line}</span>)}
      {tooltip.meta && <small>{tooltip.meta}</small>}
    </div>
  );
}

function statusColor(status: string, index: number) {
  const known: Record<string, string> = {
    submitted_to_restaurant: "#1769e0",
    accepted: "#244f86",
    preparing: "#d97706",
    ready_for_pickup: "#07847c",
    out_for_delivery: "#0891b2",
    completed: "#168438",
    delivered: "#0f6b36",
    awaiting_fulfillment_method: "#e57a16",
    pending_confirmation: "#c69214",
    awaiting_delivery_address: "#9a6700",
    awaiting_customer_name: "#7b4bc4",
    rejected: "#991f2d",
    cancelled: "#d13b45",
    failed: "#c1123f",
    unknown: "#687682",
  };
  return known[status] ?? STATUS_FALLBACK_COLORS[index % STATUS_FALLBACK_COLORS.length];
}

function ChartCard({ title, subtitle, children, className = "" }: {
  title: string;
  subtitle: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <article className={`admin-panel ops-chart-card ${className}`.trim()}>
      <div className="ops-card-heading"><h3>{title}</h3><p>{subtitle}</p></div>
      {children}
    </article>
  );
}

function ChartSkeleton() {
  return (
    <div className="ops-chart-skeleton" aria-label="Loading analytics chart">
      {[0, 1, 2, 3].map((row) => <span className="admin-skeleton admin-skeleton-line" key={row} />)}
    </div>
  );
}

function ChartEmpty({ unavailable = false }: { unavailable?: boolean }) {
  return (
    <div className="ops-chart-empty">
      {unavailable ? "Analytics unavailable." : "No order activity in this period."}
    </div>
  );
}

function Donut({ entries, total, label }: { entries: SeriesEntry[]; total: number; label: string }) {
  let offset = 0;
  return (
    <div className="ops-donut-layout">
      <svg className="ops-donut" role="img" aria-label={`${label}: ${entries.map((entry) => `${entry.label} ${entry.value}`).join(", ")}`} viewBox="0 0 120 120">
        <title>{label}</title>
        <circle cx="60" cy="60" fill="none" r="43" stroke="#edf0f3" strokeWidth="18" />
        {entries.map((entry) => {
          const percentage = total > 0 ? (entry.value / total) * 100 : 0;
          const dashOffset = -offset;
          offset += percentage;
          return (
            <circle
              cx="60"
              cy="60"
              data-color={entry.color}
              data-series-key={entry.key}
              fill="none"
              key={entry.key}
              pathLength="100"
              r="43"
              stroke={entry.color}
              strokeDasharray={`${percentage} ${100 - percentage}`}
              strokeDashoffset={dashOffset}
              strokeWidth="18"
              transform="rotate(-90 60 60)"
            />
          );
        })}
        <text className="ops-donut-total" textAnchor="middle" x="60" y="58">{total}</text>
        <text className="ops-donut-caption" textAnchor="middle" x="60" y="73">orders</text>
      </svg>
      <ul className="ops-chart-legend">
        {entries.map((entry) => (
          <li key={entry.key}><i data-color={entry.color} data-series-key={entry.key} style={{ backgroundColor: entry.color }} /><span>{entry.label}</span><strong>{entry.value}</strong></li>
        ))}
      </ul>
    </div>
  );
}

function HorizontalBars({ entries, label, tooltipValueLabel }: {
  entries: SeriesEntry[];
  label: string;
  tooltipValueLabel?: string;
}) {
  const maximum = Math.max(...entries.map((entry) => entry.value), 1);
  const tooltipId = useId();
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);
  const hideTooltip = (key: string) => {
    setTooltip((current) => current?.key === key ? null : current);
  };
  return (
    <div className="ops-horizontal-bars" role={tooltipValueLabel ? "group" : "img"} aria-label={`${label}: ${entries.map((entry) => `${entry.label} ${entry.value}`).join(", ")}`}>
      {entries.map((entry, index) => (
        <div
          aria-describedby={tooltip?.key === entry.key ? tooltipId : undefined}
          aria-label={tooltipValueLabel ? `${entry.label}: ${tooltipValueLabel} ${exactNumber(entry.value)}` : undefined}
          className={`ops-horizontal-bar${tooltipValueLabel ? " is-interactive" : ""}`}
          key={entry.key}
          onBlur={() => hideTooltip(entry.key)}
          onFocus={() => tooltipValueLabel && setTooltip({
            key: entry.key,
            title: entry.label,
            lines: [`${tooltipValueLabel}: ${exactNumber(entry.value)}`],
            left: 50,
            top: tooltipPosition(((index + 0.5) / entries.length) * 100),
          })}
          onMouseEnter={() => tooltipValueLabel && setTooltip({
            key: entry.key,
            title: entry.label,
            lines: [`${tooltipValueLabel}: ${exactNumber(entry.value)}`],
            left: 50,
            top: tooltipPosition(((index + 0.5) / entries.length) * 100),
          })}
          onMouseLeave={() => hideTooltip(entry.key)}
          role={tooltipValueLabel ? "img" : undefined}
          tabIndex={tooltipValueLabel ? 0 : undefined}
        >
          <div><span>{entry.label}</span><strong>{entry.value}</strong></div>
          <span className="ops-horizontal-track"><i style={{ backgroundColor: entry.color, width: `${(entry.value / maximum) * 100}%` }} /></span>
        </div>
      ))}
      <ChartTooltip id={tooltipId} tooltip={tooltip} />
    </div>
  );
}

function TrendChart({ analytics }: { analytics: Analytics }) {
  const tooltipId = useId();
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);
  const data = analytics.orders_revenue_trend;
  const totalOrders = data.reduce((sum, point) => sum + point.order_count, 0);
  if (totalOrders === 0) return <ChartEmpty />;

  const currencies = Array.from(new Set(data.flatMap((point) => Object.keys(point.revenue_by_currency)))).sort();
  const width = 720;
  const height = 245;
  const plot = { left: 42, right: 42, top: 16, bottom: 40 };
  const plotWidth = width - plot.left - plot.right;
  const plotHeight = height - plot.top - plot.bottom;
  const maximumOrders = Math.max(...data.map((point) => point.order_count), 1);
  const maximumRevenue = Math.max(...data.flatMap((point) => Object.values(point.revenue_by_currency)), 1);
  const step = plotWidth / data.length;
  const barWidth = Math.max(3, Math.min(18, step * 0.45));
  const labelStep = data.length > 10 ? 5 : 1;
  const accessibility = data.map((point) => {
    const revenue = Object.entries(point.revenue_by_currency)
      .map(([currency, value]) => `${currency} ${value}`).join(" ");
    return `${point.date} ${point.order_count} orders${revenue ? ` ${revenue}` : ""}`;
  }).join(", ");
  const hideTooltip = (key: string) => {
    setTooltip((current) => current?.key === key ? null : current);
  };

  return (
    <>
      <div className="ops-chart-key">
        <span><i style={{ backgroundColor: BLUE }} />Orders</span>
        {currencies.map((currency, index) => <span key={currency}><i style={{ backgroundColor: CHART_COLORS[index % CHART_COLORS.length] }} />Revenue ({currencyLabel(currency)})</span>)}
      </div>
      <div className="ops-svg-chart-wrap">
        <svg className="ops-svg-chart" role="group" aria-label={`Orders and revenue trend: ${accessibility}`} viewBox={`0 0 ${width} ${height}`}>
          {[0, 0.5, 1].map((ratio) => {
            const y = plot.top + plotHeight * ratio;
            return <line key={ratio} stroke="#e5e9ed" x1={plot.left} x2={width - plot.right} y1={y} y2={y} />;
          })}
          {data.map((point, index) => {
            const x = plot.left + step * index + step / 2;
            const barHeight = (point.order_count / maximumOrders) * plotHeight;
            const key = `orders:${point.date}`;
            const label = `${shortDate(point.date)}: ${point.order_count} ${point.order_count === 1 ? "order" : "orders"}`;
            return (
              <g key={point.date}>
                <rect aria-hidden="true" fill={BLUE} height={barHeight} opacity="0.82" rx="2" width={barWidth} x={x - barWidth / 2} y={plot.top + plotHeight - barHeight} />
                <rect
                  aria-describedby={tooltip?.key === key ? tooltipId : undefined}
                  aria-label={label}
                  className="ops-svg-hit-target"
                  height={plotHeight}
                  onBlur={() => hideTooltip(key)}
                  onFocus={() => setTooltip({
                    key,
                    title: shortDate(point.date),
                    lines: [`Orders: ${exactNumber(point.order_count)}`],
                    left: tooltipPosition((x / width) * 100),
                    top: tooltipPosition(((plot.top + plotHeight - barHeight) / height) * 100),
                  })}
                  onMouseEnter={() => setTooltip({
                    key,
                    title: shortDate(point.date),
                    lines: [`Orders: ${exactNumber(point.order_count)}`],
                    left: tooltipPosition((x / width) * 100),
                    top: tooltipPosition(((plot.top + plotHeight - barHeight) / height) * 100),
                  })}
                  onMouseLeave={() => hideTooltip(key)}
                  role="img"
                  tabIndex={0}
                  width={Math.max(barWidth, step * 0.72)}
                  x={x - Math.max(barWidth, step * 0.72) / 2}
                  y={plot.top}
                />
                {(index % labelStep === 0 || index === data.length - 1) && <text className="ops-chart-axis-label" textAnchor="middle" x={x} y={height - 14}>{shortDate(point.date)}</text>}
              </g>
            );
          })}
          {currencies.map((currency, currencyIndex) => {
            const points = data.map((point, index) => {
              const x = plot.left + step * index + step / 2;
              const value = point.revenue_by_currency[currency] ?? 0;
              const y = plot.top + plotHeight - (value / maximumRevenue) * plotHeight;
              return `${x},${y}`;
            }).join(" ");
            const color = CHART_COLORS[currencyIndex % CHART_COLORS.length];
            return (
              <g key={currency}>
                <polyline aria-hidden="true" fill="none" points={points} stroke={color} strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" />
                {data.map((point, index) => {
                  const value = point.revenue_by_currency[currency];
                  if (value === undefined) return null;
                  const x = plot.left + step * index + step / 2;
                  const y = plot.top + plotHeight - (value / maximumRevenue) * plotHeight;
                  const key = `revenue:${currency}:${point.date}`;
                  const displayValue = formattedRevenue(value, currency);
                  return (
                    <g key={key}>
                      <circle aria-hidden="true" cx={x} cy={y} fill={color} r="3.5" stroke="#fff" strokeWidth="1.5" />
                      <circle
                        aria-describedby={tooltip?.key === key ? tooltipId : undefined}
                        aria-label={`${shortDate(point.date)}: revenue ${displayValue}`}
                        className="ops-svg-point-target"
                        cx={x}
                        cy={y}
                        onBlur={() => hideTooltip(key)}
                        onFocus={() => setTooltip({
                          key,
                          title: shortDate(point.date),
                          lines: [`Revenue: ${displayValue}`],
                          left: tooltipPosition((x / width) * 100),
                          top: tooltipPosition((y / height) * 100),
                        })}
                        onMouseEnter={() => setTooltip({
                          key,
                          title: shortDate(point.date),
                          lines: [`Revenue: ${displayValue}`],
                          left: tooltipPosition((x / width) * 100),
                          top: tooltipPosition((y / height) * 100),
                        })}
                        onMouseLeave={() => hideTooltip(key)}
                        r="9"
                        role="img"
                        tabIndex={0}
                      />
                    </g>
                  );
                })}
              </g>
            );
          })}
          <text className="ops-chart-axis-title" x="2" y="12">Orders</text>
          <text className="ops-chart-axis-title" textAnchor="end" x={width - 2} y="12">Revenue</text>
          <text className="ops-chart-axis-value" x="8" y={plot.top + 5}>{maximumOrders}</text>
          <text className="ops-chart-axis-value" textAnchor="end" x={width - 5} y={plot.top + 5}>{compactNumber(maximumRevenue)}</text>
        </svg>
        <ChartTooltip id={tooltipId} tooltip={tooltip} />
      </div>
    </>
  );
}

function HourlyChart({ analytics }: { analytics: Analytics }) {
  const tooltipId = useId();
  const [tooltip, setTooltip] = useState<TooltipData | null>(null);
  const data = analytics.orders_by_hour;
  const total = data.reduce((sum, point) => sum + point.order_count, 0);
  if (total === 0) return <ChartEmpty />;
  const maximum = Math.max(...data.map((point) => point.order_count), 1);
  const peak = data.reduce((best, point) => point.order_count > best.order_count ? point : best, data[0]);
  const width = 520;
  const height = 205;
  const plotTop = 12;
  const plotBottom = 32;
  const plotHeight = height - plotTop - plotBottom;
  const step = width / 24;
  const hideTooltip = (key: string) => {
    setTooltip((current) => current?.key === key ? null : current);
  };
  return (
    <>
      <div className="ops-peak-label"><span>Peak Hour</span><strong>{hourRangeLabel(peak.hour)}</strong><b>{peak.order_count} {peak.order_count === 1 ? "order" : "orders"}</b></div>
      <div className="ops-svg-chart-wrap">
        <svg className="ops-svg-chart ops-hour-chart" role="group" aria-label={`Orders by hour: ${data.map((point) => `${hourLabel(point.hour)} ${point.order_count}`).join(", ")}`} viewBox={`0 0 ${width} ${height}`}>
          <line stroke="#dfe4e9" x1="0" x2={width} y1={plotTop + plotHeight} y2={plotTop + plotHeight} />
          {data.map((point) => {
            const barHeight = (point.order_count / maximum) * plotHeight;
            const key = `hour:${point.hour}`;
            return (
              <g key={point.hour}>
                <rect aria-hidden="true" fill={point.hour === peak.hour ? "#0f55b8" : BLUE} height={barHeight} opacity={point.hour === peak.hour ? 1 : 0.72} rx="2" width={Math.max(5, step - 6)} x={point.hour * step + 3} y={plotTop + plotHeight - barHeight} />
                <rect
                  aria-describedby={tooltip?.key === key ? tooltipId : undefined}
                  aria-label={`${hourLabel(point.hour)} to ${hourLabel((point.hour + 1) % 24)}: ${point.order_count} ${point.order_count === 1 ? "order" : "orders"} (UTC)`}
                  className="ops-svg-hit-target"
                  height={plotHeight}
                  onBlur={() => hideTooltip(key)}
                  onFocus={() => setTooltip({
                    key,
                    title: hourRangeLabel(point.hour),
                    lines: [`Orders: ${exactNumber(point.order_count)}`],
                    meta: "UTC",
                    left: tooltipPosition(((point.hour * step + step / 2) / width) * 100),
                    top: tooltipPosition(((plotTop + plotHeight - barHeight) / height) * 100),
                  })}
                  onMouseEnter={() => setTooltip({
                    key,
                    title: hourRangeLabel(point.hour),
                    lines: [`Orders: ${exactNumber(point.order_count)}`],
                    meta: "UTC",
                    left: tooltipPosition(((point.hour * step + step / 2) / width) * 100),
                    top: tooltipPosition(((plotTop + plotHeight - barHeight) / height) * 100),
                  })}
                  onMouseLeave={() => hideTooltip(key)}
                  role="img"
                  tabIndex={0}
                  width={step}
                  x={point.hour * step}
                  y={plotTop}
                />
                {point.hour % 3 === 0 && <text className="ops-chart-axis-label" textAnchor="middle" x={point.hour * step + step / 2} y={height - 10}>{hourLabel(point.hour).replace(" ", "")}</text>}
              </g>
            );
          })}
        </svg>
        <ChartTooltip id={tooltipId} tooltip={tooltip} />
      </div>
    </>
  );
}

function StatusDistribution({ analytics }: { analytics: Analytics }) {
  const entries = Object.entries(analytics.status_distribution)
    .filter(([, value]) => value > 0)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([status, value], index) => ({ key: status, label: statusLabel(status), value, color: statusColor(status, index) }));
  const total = entries.reduce((sum, entry) => sum + entry.value, 0);
  if (total === 0) return <ChartEmpty />;
  return entries.length <= 7
    ? <Donut entries={entries} label="Order status distribution" total={total} />
    : <HorizontalBars entries={entries} label="Order status distribution" />;
}

function TopItems({ analytics }: { analytics: Analytics }) {
  const entries = analytics.top_selling_items.map((item) => ({
    key: item.item_id ?? `name:${item.name}`,
    label: item.name,
    value: item.quantity,
    color: BLUE,
  }));
  return entries.length > 0
    ? <HorizontalBars entries={entries} label="Top selling menu items" tooltipValueLabel="Quantity" />
    : <ChartEmpty />;
}

function FulfillmentChart({ analytics }: { analytics: Analytics }) {
  const source = analytics.by_fulfillment;
  const entries: SeriesEntry[] = [
    { key: "delivery", label: "Delivery", value: source.delivery, color: BLUE },
    { key: "takeaway", label: "Takeaway", value: source.takeaway, color: GREEN },
    { key: "unspecified", label: "Unspecified", value: source.unspecified, color: "#8a96a1" },
  ].filter((entry) => entry.value > 0);
  const total = entries.reduce((sum, entry) => sum + entry.value, 0);
  return total > 0 ? <Donut entries={entries} label="Delivery versus takeaway" total={total} /> : <ChartEmpty />;
}

export function AnalyticsDashboard({ analytics, loading, windowDays, onWindowChange }: {
  analytics: Analytics | null;
  loading: boolean;
  windowDays: WindowDays;
  onWindowChange: (days: WindowDays) => void;
}) {
  const content = (render: (value: Analytics) => ReactNode) => {
    if (loading) return <ChartSkeleton />;
    if (!analytics) return <ChartEmpty unavailable />;
    return render(analytics);
  };
  return (
    <section className="ops-analytics-section" aria-labelledby="ops-analytics-heading">
      <header className="ops-analytics-header">
        <div><h2 id="ops-analytics-heading">Analytics</h2><p>Order and revenue patterns from persisted order activity.</p></div>
        <div className="ops-range-control" aria-label="Analytics date range">
          {([7, 30] as const).map((days) => (
            <button aria-pressed={windowDays === days} key={days} onClick={() => onWindowChange(days)} type="button">{days} Days</button>
          ))}
        </div>
      </header>
      <div className="ops-analytics-primary-grid">
        <ChartCard className="ops-trend-card" subtitle={`${windowDays}-day UTC view; revenue remains separated by currency.`} title="Orders & Revenue Trend">
          {content((value) => <TrendChart analytics={value} />)}
        </ChartCard>
        <ChartCard subtitle="Exact order statuses created in the selected period." title="Order Status Distribution">
          {content((value) => <StatusDistribution analytics={value} />)}
        </ChartCard>
      </div>
      <div className="ops-analytics-secondary-grid">
        <ChartCard subtitle="Order creation activity across each UTC hour." title="Orders by Hour">
          {content((value) => <HourlyChart analytics={value} />)}
        </ChartCard>
        <ChartCard subtitle="Quantity sold, excluding rejected, cancelled, and failed orders." title="Top Selling Menu Items">
          {content((value) => <TopItems analytics={value} />)}
        </ChartCard>
        <ChartCard subtitle="Fulfillment methods for orders in this period." title="Delivery vs Takeaway">
          {content((value) => <FulfillmentChart analytics={value} />)}
        </ChartCard>
      </div>
    </section>
  );
}
