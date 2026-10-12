import { useCallback, useEffect, useState } from "react";

// Tiny hash router: "#/shipments/abc?tab=costs" -> { path: "/shipments/abc", parts: ["shipments","abc"], query }.
// Hash routing keeps deep links working when the app is served from any local port.

export function parseHash(hash = globalThis.location?.hash || "") {
  const raw = hash.replace(/^#/, "") || "/dashboard";
  const [pathPart, queryPart = ""] = raw.split("?");
  const path = pathPart.startsWith("/") ? pathPart : `/${pathPart}`;
  const query = Object.fromEntries(new URLSearchParams(queryPart));
  return { path, parts: path.split("/").filter(Boolean), query };
}

export function href(path, query = {}) {
  const params = new URLSearchParams(Object.entries(query).filter(([, value]) => value !== undefined && value !== null && value !== ""));
  const suffix = params.toString();
  return `#${path}${suffix ? `?${suffix}` : ""}`;
}

export function navigate(path, query = {}, { replace = false } = {}) {
  const target = href(path, query);
  if (replace) globalThis.history.replaceState(null, "", target);
  else globalThis.location.hash = target.slice(1);
  if (replace) globalThis.dispatchEvent(new HashChangeEvent("hashchange"));
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseHash());
  useEffect(() => {
    const update = () => setRoute(parseHash());
    globalThis.addEventListener("hashchange", update);
    return () => globalThis.removeEventListener("hashchange", update);
  }, []);
  const go = useCallback((path, query, options) => navigate(path, query, options), []);
  return [route, go];
}
