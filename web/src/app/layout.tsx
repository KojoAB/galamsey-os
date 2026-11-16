import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Galamsey Watch",
  description: "Daily satellite monitoring of illegal mining in Ghana.",
  robots: { index: false, follow: false }, // the analyst dashboard is private; the public map comes in M11
};

export const viewport: Viewport = { themeColor: "#0a0f0d", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
