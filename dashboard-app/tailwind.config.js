/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                'resonance-deep': '#0f172a',
                'resonance-purple': '#a855f7',
                'resonance-cyan': '#06b6d4',
            }
        },
    },
    plugins: [],
}
