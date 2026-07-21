/* Map DaisyUI v4 theme tokens (oklch CSS vars) onto Tailwind color utilities so
 * classes like `bg-base-200` and `text-base-content/60` work with the Play CDN.
 * Loaded after tailwind.js; DaisyUI itself ships as plain CSS in vendor/. */
tailwind.config = {
  theme: {
    extend: {
      colors: {
        primary: "oklch(var(--p) / <alpha-value>)",
        "primary-content": "oklch(var(--pc) / <alpha-value>)",
        secondary: "oklch(var(--s) / <alpha-value>)",
        "secondary-content": "oklch(var(--sc) / <alpha-value>)",
        accent: "oklch(var(--a) / <alpha-value>)",
        "accent-content": "oklch(var(--ac) / <alpha-value>)",
        neutral: "oklch(var(--n) / <alpha-value>)",
        "neutral-content": "oklch(var(--nc) / <alpha-value>)",
        "base-100": "oklch(var(--b1) / <alpha-value>)",
        "base-200": "oklch(var(--b2) / <alpha-value>)",
        "base-300": "oklch(var(--b3) / <alpha-value>)",
        "base-content": "oklch(var(--bc) / <alpha-value>)",
        info: "oklch(var(--in) / <alpha-value>)",
        "info-content": "oklch(var(--inc) / <alpha-value>)",
        success: "oklch(var(--su) / <alpha-value>)",
        "success-content": "oklch(var(--suc) / <alpha-value>)",
        warning: "oklch(var(--wa) / <alpha-value>)",
        "warning-content": "oklch(var(--wac) / <alpha-value>)",
        error: "oklch(var(--er) / <alpha-value>)",
        "error-content": "oklch(var(--erc) / <alpha-value>)",
      },
    },
  },
};
