import { Suspense } from "react";
import { TicketsPageClient } from "@/app/admin/tickets/TicketsPageClient";
import { TicketListSkeleton } from "@/app/admin/tickets/TicketListTable";

export default function AdminTicketsPage() {
  return (
    <Suspense fallback={<TicketListSkeleton />}>
      <TicketsPageClient />
    </Suspense>
  );
}
