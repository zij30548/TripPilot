import RestoredTripPage from "@/components/trip/restored-trip";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RestoredTripPage key={id} recordId={id} />;
}
