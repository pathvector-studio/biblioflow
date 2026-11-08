"use client";

import { Suspense } from "react";
import { useParams } from "next/navigation";
import { PaperView } from "@/components/paper/paper-view";

export default function PaperPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <Suspense fallback={null}>
      <PaperView paperId={id} />
    </Suspense>
  );
}
