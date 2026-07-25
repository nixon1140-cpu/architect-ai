import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      // 実体はglobals.cssのCSS変数側に置き、ここでは名前だけを与える。
      // <alpha-value> を通すことで bg-ok/10 のような不透明度修飾子も効く。
      colors: {
        ground: "rgb(var(--ground) / <alpha-value>)",
        panel: "rgb(var(--panel) / <alpha-value>)",
        well: "rgb(var(--well) / <alpha-value>)",
        rule: "rgb(var(--rule) / <alpha-value>)",
        "rule-strong": "rgb(var(--rule-strong) / <alpha-value>)",
        ink: "rgb(var(--text) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        "accent-dim": "rgb(var(--accent-dim) / <alpha-value>)",
        danger: "rgb(var(--danger) / <alpha-value>)",
        ok: "rgb(var(--ok) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["var(--font-ui)"],
        mono: ["var(--font-mono)"],
      },
      // 製図の精度感に合わせ、既定の角丸を控えめにする。
      borderRadius: {
        DEFAULT: "3px",
        sm: "2px",
      },
    },
  },
  plugins: [],
};

export default config;
