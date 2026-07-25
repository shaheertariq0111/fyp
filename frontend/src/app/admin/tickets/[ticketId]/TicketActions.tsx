"use client";

import {
  type FormEvent,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  priorityLabel,
  statusLabel,
} from "@/app/admin/tickets/ticketPresentation";
import type {
  AdminTicketDetail,
  AdminTicketNoteCreateRequest,
  AdminTicketPriorityUpdateRequest,
  AdminTicketReopenRequest,
  AdminTicketStatusUpdateRequest,
  TicketPriority,
  TicketStatus,
} from "@/lib/adminTicketTypes";

export type TicketMutationKind = "status" | "priority" | "note" | "reopen";

export type TicketMutation =
  | { kind: "status"; request: AdminTicketStatusUpdateRequest }
  | { kind: "priority"; request: AdminTicketPriorityUpdateRequest }
  | { kind: "note"; request: AdminTicketNoteCreateRequest }
  | { kind: "reopen"; request: AdminTicketReopenRequest };

export type TicketMutationOutcome = "success" | "conflict" | "error";

export type TicketMutationFeedback = {
  kind: TicketMutationKind;
  tone: "error" | "success";
  message: string;
} | null;

type TicketActionsProps = {
  ticket: AdminTicketDetail;
  activeMutation: TicketMutationKind | null;
  conflictGuidance: string | null;
  disabled: boolean;
  feedback: TicketMutationFeedback;
  onMutate: (mutation: TicketMutation) => Promise<TicketMutationOutcome>;
};

const STATUS_TRANSITIONS: Record<TicketStatus, TicketStatus[]> = {
  open: ["in_review", "waiting_for_customer", "resolved", "closed"],
  in_review: ["waiting_for_customer", "resolved", "closed"],
  waiting_for_customer: ["in_review", "resolved", "closed"],
  resolved: ["closed"],
  closed: [],
};

const REQUIRED_STATUS_REASONS = new Set<TicketStatus>([
  "waiting_for_customer",
  "resolved",
  "closed",
]);

const PRIORITIES: TicketPriority[] = ["normal", "high", "urgent"];
const REOPEN_TARGETS: Array<"open" | "in_review"> = ["open", "in_review"];
const MAX_NOTE_LENGTH = 2_000;

function ActionFeedback({
  feedback,
  kind,
}: {
  feedback: TicketMutationFeedback;
  kind: TicketMutationKind;
}) {
  if (!feedback || feedback.kind !== kind) {
    return null;
  }
  if (feedback.tone === "success") {
    return (
      <p
        aria-label="Mutation success"
        className="admin-ticket-action-feedback is-success"
        role="status"
      >
        {feedback.message}
      </p>
    );
  }
  return (
    <p className="admin-ticket-action-feedback is-error" role="alert">
      {feedback.message}
    </p>
  );
}

function LocalError({ id, message }: { id: string; message: string }) {
  if (!message) {
    return null;
  }
  return (
    <p className="admin-ticket-action-validation" id={id} role="alert">
      {message}
    </p>
  );
}

export function TicketActions({
  ticket,
  activeMutation,
  conflictGuidance,
  disabled,
  feedback,
  onMutate,
}: TicketActionsProps) {
  const [statusTarget, setStatusTarget] = useState("");
  const [statusReason, setStatusReason] = useState("");
  const [statusError, setStatusError] = useState("");
  const [priorityTarget, setPriorityTarget] = useState("");
  const [priorityReason, setPriorityReason] = useState("");
  const [priorityError, setPriorityError] = useState("");
  const [noteText, setNoteText] = useState("");
  const [noteError, setNoteError] = useState("");
  const [reopenTarget, setReopenTarget] = useState("");
  const [reopenReason, setReopenReason] = useState("");
  const [reopenError, setReopenError] = useState("");
  const statusTargetRef = useRef<HTMLSelectElement>(null);
  const statusReasonRef = useRef<HTMLTextAreaElement>(null);
  const priorityTargetRef = useRef<HTMLSelectElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const reopenTargetRef = useRef<HTMLSelectElement>(null);
  const reopenReasonRef = useRef<HTMLTextAreaElement>(null);

  const allowedStatuses = useMemo(
    () => STATUS_TRANSITIONS[ticket.status] ?? [],
    [ticket.status],
  );
  const statusTargetIsValid = allowedStatuses.includes(
    statusTarget as TicketStatus,
  );
  const visibleStatusTarget = statusTargetIsValid ? statusTarget : "";
  const statusTargetInvalidated = Boolean(statusTarget) && !statusTargetIsValid;
  const statusReasonRequired = REQUIRED_STATUS_REASONS.has(
    visibleStatusTarget as TicketStatus,
  );
  const terminalTicket = ticket.status === "resolved" || ticket.status === "closed";
  const priorityCanSubmit = Boolean(priorityTarget)
    && priorityTarget !== ticket.priority;
  const mutationDisabled = disabled;

  async function submitStatus(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatusError("");
    if (!visibleStatusTarget) {
      setStatusError("Select an available target status.");
      statusTargetRef.current?.focus();
      return;
    }
    const target = visibleStatusTarget as TicketStatus;
    if (REQUIRED_STATUS_REASONS.has(target) && !statusReason.trim()) {
      setStatusError("A reason is required for this status change.");
      statusReasonRef.current?.focus();
      return;
    }
    const outcome = await onMutate({
      kind: "status",
      request: {
        status: target,
        reason: statusReason,
        expected_version: ticket.version,
      },
    });
    if (outcome === "success") {
      setStatusTarget("");
      setStatusReason("");
    }
  }

  async function submitPriority(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPriorityError("");
    if (!priorityCanSubmit) {
      setPriorityError("Choose a priority different from the current priority.");
      priorityTargetRef.current?.focus();
      return;
    }
    const outcome = await onMutate({
      kind: "priority",
      request: {
        priority: priorityTarget as TicketPriority,
        reason: priorityReason,
        expected_version: ticket.version,
      },
    });
    if (outcome === "success") {
      setPriorityTarget("");
      setPriorityReason("");
    }
  }

  async function submitNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNoteError("");
    if (!noteText.trim()) {
      setNoteError("An internal note is required.");
      noteRef.current?.focus();
      return;
    }
    if (noteText.length > MAX_NOTE_LENGTH) {
      setNoteError("The internal note cannot exceed 2,000 characters.");
      noteRef.current?.focus();
      return;
    }
    const outcome = await onMutate({
      kind: "note",
      request: {
        text: noteText,
        expected_version: ticket.version,
      },
    });
    if (outcome === "success") {
      setNoteText("");
    }
  }

  async function submitReopen(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setReopenError("");
    if (!REOPEN_TARGETS.includes(reopenTarget as "open" | "in_review")) {
      setReopenError("Select a valid reopen status.");
      reopenTargetRef.current?.focus();
      return;
    }
    if (!reopenReason.trim()) {
      setReopenError("A reason is required to reopen this ticket.");
      reopenReasonRef.current?.focus();
      return;
    }
    const outcome = await onMutate({
      kind: "reopen",
      request: {
        target_status: reopenTarget as "open" | "in_review",
        reason: reopenReason,
        expected_version: ticket.version,
      },
    });
    if (outcome === "success") {
      setReopenTarget("");
      setReopenReason("");
    }
  }

  return (
    <section
      aria-label="Administrator actions"
      className="admin-ticket-detail-panel admin-ticket-detail-wide admin-ticket-actions"
    >
      <div className="admin-ticket-actions-heading">
        <div>
          <h2>Administrator actions</h2>
          <p>Changes use the current ticket version and are recorded by the server.</p>
        </div>
      </div>
      {!terminalTicket && (
        <ActionFeedback feedback={feedback} kind="reopen" />
      )}
      {conflictGuidance && (
        <p
          aria-label="Conflict guidance"
          className="admin-ticket-action-feedback is-error"
          role="status"
        >
          {conflictGuidance}
        </p>
      )}
      <div className="admin-ticket-actions-grid">
        <form
          aria-label="Update ticket status"
          className="admin-ticket-action-form"
          onSubmit={(event) => void submitStatus(event)}
        >
          <h3>Status</h3>
          <p className="admin-ticket-action-current">
            Current: <strong>{statusLabel(ticket.status)}</strong>
          </p>
          <label htmlFor="admin-ticket-status-target">Target status</label>
          <select
            aria-describedby={statusError ? "admin-ticket-status-error" : undefined}
            disabled={mutationDisabled}
            id="admin-ticket-status-target"
            onChange={(event) => {
              setStatusTarget(event.target.value);
              setStatusError("");
            }}
            ref={statusTargetRef}
            value={visibleStatusTarget}
          >
            <option value="">Select status</option>
            {allowedStatuses.map((status) => (
              <option key={status} value={status}>{statusLabel(status)}</option>
            ))}
          </select>
          {statusTargetInvalidated && (
            <p className="admin-ticket-action-advisory">
              This status target is no longer available.
            </p>
          )}
          <label htmlFor="admin-ticket-status-reason">
            Status reason
            {statusReasonRequired
              ? " (required)"
              : " (optional)"}
          </label>
          <textarea
            aria-describedby={statusError ? "admin-ticket-status-error" : undefined}
            aria-invalid={Boolean(statusError)}
            aria-required={statusReasonRequired || undefined}
            disabled={mutationDisabled}
            id="admin-ticket-status-reason"
            onChange={(event) => {
              setStatusReason(event.target.value);
              setStatusError("");
            }}
            ref={statusReasonRef}
            value={statusReason}
          />
          <LocalError id="admin-ticket-status-error" message={statusError} />
          <ActionFeedback feedback={feedback} kind="status" />
          <div className="admin-ticket-action-buttons">
            <button
              className="admin-primary-button"
              disabled={mutationDisabled || !visibleStatusTarget}
              type="submit"
            >
              {activeMutation === "status" ? "Updating status..." : "Save status"}
            </button>
            <button
              className="admin-secondary-button"
              disabled={mutationDisabled || (!statusTarget && !statusReason)}
              onClick={() => {
                setStatusTarget("");
                setStatusReason("");
                setStatusError("");
              }}
              type="button"
            >
              Reset status
            </button>
          </div>
        </form>

        <form
          aria-label="Update ticket priority"
          className="admin-ticket-action-form"
          onSubmit={(event) => void submitPriority(event)}
        >
          <h3>Priority</h3>
          <p className="admin-ticket-action-current">
            Current: <strong>{priorityLabel(ticket.priority)}</strong>
          </p>
          <label htmlFor="admin-ticket-priority-target">New priority</label>
          <select
            aria-describedby={priorityError ? "admin-ticket-priority-error" : undefined}
            disabled={mutationDisabled}
            id="admin-ticket-priority-target"
            onChange={(event) => {
              setPriorityTarget(event.target.value);
              setPriorityError("");
            }}
            ref={priorityTargetRef}
            value={priorityTarget}
          >
            <option value="">Select priority</option>
            {PRIORITIES.map((priority) => (
              <option key={priority} value={priority}>{priorityLabel(priority)}</option>
            ))}
          </select>
          <label htmlFor="admin-ticket-priority-reason">Priority reason (optional)</label>
          <textarea
            aria-label="Priority reason"
            disabled={mutationDisabled}
            id="admin-ticket-priority-reason"
            onChange={(event) => setPriorityReason(event.target.value)}
            value={priorityReason}
          />
          <LocalError id="admin-ticket-priority-error" message={priorityError} />
          <ActionFeedback feedback={feedback} kind="priority" />
          <button
            className="admin-primary-button"
            disabled={mutationDisabled || !priorityCanSubmit}
            type="submit"
          >
            {activeMutation === "priority" ? "Updating priority..." : "Save priority"}
          </button>
        </form>

        <form
          aria-label="Add internal note"
          className="admin-ticket-action-form"
          onSubmit={(event) => void submitNote(event)}
        >
          <h3>Internal note</h3>
          <label htmlFor="admin-ticket-note">Internal note</label>
          <textarea
            aria-describedby={noteError
              ? "admin-ticket-note-count admin-ticket-note-error"
              : "admin-ticket-note-count"}
            disabled={mutationDisabled}
            id="admin-ticket-note"
            maxLength={MAX_NOTE_LENGTH}
            onChange={(event) => {
              setNoteText(event.target.value);
              setNoteError("");
            }}
            ref={noteRef}
            value={noteText}
          />
          <span
            className="admin-ticket-note-count"
            id="admin-ticket-note-count"
          >
            {noteText.length} / {MAX_NOTE_LENGTH}
          </span>
          <LocalError id="admin-ticket-note-error" message={noteError} />
          <ActionFeedback feedback={feedback} kind="note" />
          <button
            className="admin-primary-button"
            disabled={mutationDisabled || !noteText.trim()}
            type="submit"
          >
            {activeMutation === "note" ? "Adding note..." : "Add internal note"}
          </button>
        </form>

        {terminalTicket && (
          <section
            aria-label="Reopen ticket"
            className="admin-ticket-action-reopen"
          >
            <form
              aria-label="Reopen ticket form"
              className="admin-ticket-action-form"
              onSubmit={(event) => void submitReopen(event)}
            >
              <h3>Reopen</h3>
              <label htmlFor="admin-ticket-reopen-target">Reopen as</label>
              <select
                aria-describedby={reopenError ? "admin-ticket-reopen-error" : undefined}
                disabled={mutationDisabled}
                id="admin-ticket-reopen-target"
                onChange={(event) => {
                  setReopenTarget(event.target.value);
                  setReopenError("");
                }}
                ref={reopenTargetRef}
                value={reopenTarget}
              >
                <option value="">Select status</option>
                {REOPEN_TARGETS.map((status) => (
                  <option key={status} value={status}>{statusLabel(status)}</option>
                ))}
              </select>
              <label htmlFor="admin-ticket-reopen-reason">Reopen reason (required)</label>
              <textarea
                aria-describedby={reopenError ? "admin-ticket-reopen-error" : undefined}
                aria-invalid={Boolean(reopenError)}
                aria-required="true"
                disabled={mutationDisabled}
                id="admin-ticket-reopen-reason"
                onChange={(event) => {
                  setReopenReason(event.target.value);
                  setReopenError("");
                }}
                ref={reopenReasonRef}
                value={reopenReason}
              />
              <LocalError id="admin-ticket-reopen-error" message={reopenError} />
              <ActionFeedback feedback={feedback} kind="reopen" />
              <button
                className="admin-primary-button"
                disabled={mutationDisabled || !reopenTarget}
                type="submit"
              >
                {activeMutation === "reopen" ? "Reopening ticket..." : "Reopen ticket"}
              </button>
            </form>
          </section>
        )}
      </div>
    </section>
  );
}
