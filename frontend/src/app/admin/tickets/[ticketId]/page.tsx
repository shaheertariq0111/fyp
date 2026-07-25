import { TicketDetailPageClient } from "@/app/admin/tickets/[ticketId]/TicketDetailPageClient";

type TicketDetailPageProps = {
  params: Promise<{ ticketId: string }>;
};

export default async function TicketDetailPage({
  params,
}: TicketDetailPageProps) {
  const { ticketId } = await params;
  return (
    <TicketDetailPageClient
      key={ticketId}
      ticketId={ticketId}
    />
  );
}
