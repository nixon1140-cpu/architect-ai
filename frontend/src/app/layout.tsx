import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ArchitectAI",
  description: "AIシステム設計アシスタント",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ja">
      <body className="antialiased">{children}</body>
    </html>
  );
}
