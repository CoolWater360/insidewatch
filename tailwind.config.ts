import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        navy: {
          950: "#080B14",
          900: "#0D1117",
          800: "#111827",
          700: "#1A2035",
          600: "#243050",
        },
        brand: {
          blue:    "#4F8EF7",
          emerald: "#10B981",
        },
        buy:    "#4ADE80",
        sell:   "#F75C4F",
        signal: "#F5B841",
        muted:  "#8A94A6",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
      },
      backgroundImage: {
        "gradient-brand": "linear-gradient(90deg, #4F8EF7 0%, #10B981 100%)",
        "gradient-card":  "linear-gradient(135deg, rgba(79,142,247,0.06) 0%, rgba(16,185,129,0.06) 100%)",
      },
      animation: {
        "fade-in": "fadeIn 0.25s ease-out",
        "fade-up": "fadeUp 0.3s ease-out",
        shimmer:   "shimmer 1.8s infinite linear",
      },
      keyframes: {
        fadeIn: {
          "0%":   { opacity: "0" },
          "100%": { opacity: "1" },
        },
        fadeUp: {
          "0%":   { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%":   { backgroundPosition: "-800px 0" },
          "100%": { backgroundPosition: "800px 0" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
