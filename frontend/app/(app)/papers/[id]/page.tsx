"use client";

import { useParams } from "next/navigation";
import { PaperView } from "@/components/paper/paper-view";

export default function PaperPage() {
  const { id } = useParams<{ id: string }>();
  return <PaperView paperId={id} />;
}
