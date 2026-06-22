import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { Footer } from "@/components/Footer";
import { LanguageProvider } from "@/components/LanguageProvider";
import { NavLinks } from "@/components/NavLinks";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "InsideWatch",
  description:
    "Operazioni di Internal Dealing di soggetti rilevanti di società quotate italiane (MAR Art. 19).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="it" className={inter.variable}>
      <body className="flex min-h-screen flex-col bg-navy-950 text-[#E8EDF7] antialiased">
        <LanguageProvider>
          <header className="header-gradient-border sticky top-0 z-40 bg-navy-900/95 backdrop-blur-sm">
            <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6">
              <Link href="/" className="flex items-center gap-2">
                <span className="text-base font-semibold tracking-tight text-[#E8EDF7]">
                  <span className="bg-gradient-to-r from-brand-blue to-brand-emerald bg-clip-text text-transparent">
                    InsideWatch
                  </span>
                </span>
              </Link>

              <NavLinks />
            </div>
          </header>

          <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 animate-fade-in">
            {children}
          </main>

          <Footer />
        </LanguageProvider>
      </body>
    </html>
  );
}

