import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ChatPanel } from "./components/ChatPanel";
import { SummaryPanel } from "./components/SummaryPanel";
import "./styles.css";

function App() {
  return (
    <div className="app">
      <header>
        <h1>Spend Intelligence Assistant</h1>
        <span className="muted small">Prototype · 2025 invoices · Procurement policy FIN-POL-014</span>
      </header>
      <main>
        <SummaryPanel />
        <ChatPanel />
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
