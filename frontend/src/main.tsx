import { Component, ReactNode, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed) {
      return (
        <div role="alert" className="mx-auto mt-20 max-w-md rounded-xl border border-red-200 bg-red-50 p-6 text-red-800">
          <p className="font-semibold">Something went wrong in the page.</p>
          <button className="mt-3 underline" onClick={() => window.location.reload()}>
            Reload Vani
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
