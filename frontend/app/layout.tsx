import type { Metadata } from "next";
import { Providers } from "@/components/providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "BiblioFlow",
  description: "Self-hosted academic paper manager & AI research assistant",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja" suppressHydrationWarning>
      <body className="min-h-screen">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
