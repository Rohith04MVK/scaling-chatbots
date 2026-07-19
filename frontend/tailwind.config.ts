import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: "#1A1A1A",
        paper: "#F7F5F2",
        accent: "#E85D3F",
        "accent-muted": "#F4A896",
        success: "#2D6A4F",
        error: "#C0392B",
        surface: "#EDEAE6",
        muted: "#6B6560",
        border: "#D4CFC8",
      },
      fontFamily: {
        display: ["var(--font-space-grotesk)", "sans-serif"],
        body: ["var(--font-inter)", "sans-serif"],
        mono: ["var(--font-ibm-plex-mono)", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
