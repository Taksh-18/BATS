// Kept in its own file (not inline) so the page's content-security policy can forbid inline scripts.
tailwind.config = {
    theme: { extend: {
        colors: { medical: { 50: '#fef2f2', 100: '#fee2e2', 400: '#f87171', 500: '#ef4444', 600: '#dc2626', 700: '#b91c1c', 800: '#991b1b', 900: '#7f1d1d' } }
    } }
};
