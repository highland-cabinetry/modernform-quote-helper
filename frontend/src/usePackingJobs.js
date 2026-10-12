import { useRef, useState } from "react";
import { getJson, sendJson } from "./api";

export const packingGeometry = value => {
  const { quote_origin, default_commodity_description, default_nmfc, ...settings } = value.settings || {};
  return JSON.stringify({ ...value, settings, shipping_info: null, shipment_name: "", save_result: false });
};
const geometry = packingGeometry;
export default function usePackingJobs(getRequest) {
  const current = useRef(getRequest); current.current = getRequest;
  const generation = useRef(0);
  const acceptedInput = useRef(null);
  const comparisonEstimate = useRef(null);
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const accept = async (value = job, input = acceptedInput.current) => {
    if (!input || input.uiGeometry !== geometry(current.current())) throw new Error("Inputs changed during search. Rebuild before applying this result.");
    const latest = current.current();
    return sendJson(`/api/packing-jobs/${value.id}/accept`, "POST", { ...input.request,
      shipment_name: latest.shipment_name, shipping_info: latest.shipping_info });
  };
  const search = async (request, refine = false) => {
    const token = ++generation.current;
    comparisonEstimate.current = null;
    const input = { request, uiGeometry: geometry(current.current()) };
    acceptedInput.current = input;
    setRunning(true); setError("");
    try {
      let value = await sendJson(refine ? `/api/packing-jobs/${job.id}/refine` : "/api/packing-jobs", "POST", request);
      setJob(value);
      comparisonEstimate.current = value.comparison_estimate || null;
      while (["queued", "running"].includes(value.state)) {
        await new Promise(resolve => setTimeout(resolve, 400));
        if (token !== generation.current) return null;
        value = await getJson(`/api/packing-jobs/${value.id}`);
        setJob(value);
        comparisonEstimate.current = value.comparison_estimate || null;
      }
      if (token !== generation.current) return null;
      if (geometry(current.current()) !== input.uiGeometry) throw new Error("Inputs changed during search. This result is stale; rebuild the plan.");
      if (!value.candidate) throw new Error(value.message);
      if (value.state === "cancelled" || refine) return null;
      return await accept(value, input);
    } catch (e) { setError(e.message); throw e; }
    finally { if (token === generation.current) setRunning(false); }
  };
  return { job, running, error, search, accept,
    getComparisonEstimate: () => comparisonEstimate.current,
    cancel: async () => { if (job) await sendJson(`/api/packing-jobs/${job.id}/cancel`, "POST"); },
  };
}
