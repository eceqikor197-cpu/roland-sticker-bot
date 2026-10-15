import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./index.css";

interface ErrorBoundaryProps {
  children: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("Uncaught application error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: "2rem", color: "#f87171", background: "#1a1e27", height: "100vh" }}>
          <h2>Произошла ошибка при загрузке кастомайзера</h2>
          <pre style={{ marginTop: "1rem", color: "#cbd5e1" }}>{this.state.error?.message}</pre>
          <pre style={{ marginTop: "0.5rem", fontSize: "11px", color: "#94a3b8" }}>{this.state.error?.stack}</pre>
          <button
            onClick={() => {
              localStorage.clear();
              window.location.reload();
            }}
            style={{
              marginTop: "1.5rem",
              padding: "0.5rem 1rem",
              background: "#3b82f6",
              color: "#fff",
              borderRadius: "4px",
              cursor: "pointer",
            }}
          >
            Сбросить кэш и перезагрузить
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
