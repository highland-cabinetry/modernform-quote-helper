import { build } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";

const output = process.env.BUILD2QUOTE_OUT_DIR || "dist";
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await Promise.all([
  build({ entryPoints: ["src/background.ts"], outfile: `${output}/background.js`, bundle: true, format: "iife", target: "chrome120" }),
  build({ entryPoints: ["src/content.ts"], outfile: `${output}/content.js`, bundle: true, format: "iife", target: "chrome120" }),
]);
await cp("manifest.json", `${output}/manifest.json`);
