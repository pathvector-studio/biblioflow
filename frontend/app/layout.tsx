import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BiblioFlow",
  description: "Self-hosted academic paper manager & AI research assistant",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">{children}</body>
    </html>
  );
}
