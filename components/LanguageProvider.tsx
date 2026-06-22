"use client";

import { createContext, useContext, useState, useEffect } from "react";

type Lang = "it" | "en";

interface LangContext {
  lang: Lang;
  toggle: () => void;
}

const Ctx = createContext<LangContext>({ lang: "it", toggle: () => {} });

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLang] = useState<Lang>("it");

  useEffect(() => {
    const m = document.cookie.match(/(?:^|;\s*)insidewatch_lang=([^;]+)/);
    if (m?.[1] === "en") setLang("en");
  }, []);

  function toggle() {
    const next: Lang = lang === "it" ? "en" : "it";
    document.cookie = `insidewatch_lang=${next};path=/;max-age=31536000;SameSite=Lax`;
    setLang(next);
  }

  return <Ctx.Provider value={{ lang, toggle }}>{children}</Ctx.Provider>;
}

export function useLanguage() {
  return useContext(Ctx);
}

/** Inline translation helper: t("testo italiano", "english text") */
export function useT() {
  const { lang } = useContext(Ctx);
  return (it: string, en: string) => (lang === "it" ? it : en);
}
