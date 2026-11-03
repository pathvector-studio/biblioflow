"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { I18nProvider } from "@/lib/i18n";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 15_000 } } }),
  );
  return (
    <QueryClientProvider client={client}>
      <I18nProvider>
        {children}
        <Toaster position="bottom-right" richColors closeButton theme="system" />
      </I18nProvider>
    </QueryClientProvider>
  );
}
