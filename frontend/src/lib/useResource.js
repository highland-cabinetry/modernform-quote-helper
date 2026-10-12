import { useCallback, useEffect, useRef, useState } from "react";
import { getJson } from "../api";

// Load JSON from the API with loading/error state, stale-response protection and reload().
export default function useResource(path, { enabled = true, initial = null } = {}) {
  const [data, setData] = useState(initial);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(Boolean(path && enabled));
  const generation = useRef(0);

  const load = useCallback(async ({ quiet = false } = {}) => {
    if (!path || !enabled) return null;
    const token = ++generation.current;
    if (!quiet) setLoading(true);
    setError("");
    try {
      const value = await getJson(path);
      if (token === generation.current) setData(value);
      return value;
    } catch (requestError) {
      if (token === generation.current) setError(requestError.message);
      return null;
    } finally {
      if (token === generation.current) setLoading(false);
    }
  }, [path, enabled]);

  useEffect(() => { load(); }, [load]);

  return { data, setData, error, loading, reload: load };
}

export function useDebounced(value, delay = 250) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}
