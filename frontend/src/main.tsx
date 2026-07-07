import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "@/App";
import { AuthProvider } from "@/auth/AuthProvider";
import { loadUiFonts } from "@/theme/fonts";
import "@/theme/fonts.css";
import "@/index.css";

const rootElement = document.getElementById("root");
if (rootElement === null) {
  throw new Error("Missing #root element");
}

// Fonts must be resolved before the first render: Pixi rasterizes text with
// whatever face is loaded at draw time, so rendering early would bake the
// system fallback into the panel chrome. loadUiFonts never rejects (a missing
// face logs and degrades), so this cannot wedge startup. (.then instead of
// top-level await: TLA needs a raised Vite build target.)
void loadUiFonts().then(() => {
  createRoot(rootElement).render(
    <StrictMode>
      <AuthProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AuthProvider>
    </StrictMode>,
  );
});
