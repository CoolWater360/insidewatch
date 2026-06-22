import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { Footer } from "@/components/Footer";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "InsideWatch",
  description:
    "Publicly disclosed insider share transactions of Italian listed companies (MAR Art. 19).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="flex min-h-screen flex-col bg-navy-950 text-[#E8EDF7] antialiased">
        <header className="header-gradient-border sticky top-0 z-40 bg-navy-900/95 backdrop-blur-sm">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6">
            {/* Logo */}
            <Link href="/" className="flex items-center gap-2">
              <span className="text-base font-semibold tracking-tight text-[#E8EDF7]">
                  <span className="bg-gradient-to-r from-brand-blue to-brand-emerald bg-clip-text text-transparent">
                  InsideWatch
                </span>
              </span>
            </Link>

            {/* Nav */}
            <nav className="flex items-center gap-1 text-sm font-medium">
              <NavLink href="/" label="Transactions" />
              <NavLink href="/signals" label="Signals" />
            </nav>
          </div>
        </header>

        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 animate-fade-in">
          {children}
        </main>

        <Footer />
      </body>
    </html>
  );
}

function NavLink({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      className="rounded-md px-3 py-1.5 text-muted transition-colors hover:text-[#E8EDF7] hover:bg-white/5"
    >
      {label}
    </Link>
  );
}
