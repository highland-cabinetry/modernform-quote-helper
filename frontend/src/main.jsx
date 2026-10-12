import React from "react";
import ReactDOM from "react-dom/client";
import AppShell from "./shell/AppShell";
import "./styles.css";
import "./shell.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AppShell />
  </React.StrictMode>,
);
