import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            {
              name: "react-vendor",
              test: /node_modules[\\/]react($|[\\/])|node_modules[\\/]react-dom($|[\\/])|node_modules[\\/]scheduler($|[\\/])/,
              priority: 20,
            },
            {
              name: "katex-vendor",
              test: /node_modules[\\/]katex/,
              priority: 18,
            },
            {
              name: "markdown-vendor",
              test: /node_modules[\\/](react-markdown|remark|rehype|mdast|micromark|unified|hast)/,
              priority: 15,
            },
            {
              name: "icons-vendor",
              test: /node_modules[\\/]lucide-react/,
              priority: 5,
            },
          ],
        },
      },
    },
  },
  server: {
    port: 4173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  preview: {
    port: 4173,
    strictPort: true,
  },
});
