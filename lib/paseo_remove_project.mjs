// Remove Paseo projects (and their workspaces) through the daemon API, like `paseo project delete`.
// Usage: node paseo_remove_project.mjs <projectId> [<projectId> ...]   (one argv item per id)
// Client dist dir: env SLP_PASEO_CLI_DIST (set by slp_paseo.resolve_dist: PASEO_CLI -> fork -> homebrew);
// standalone fallback order is fork first, then homebrew.
import { existsSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const workspaceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const siblingPaseoDirs = (() => {
  try {
    return readdirSync(workspaceRoot)
      .filter((name) => name.startsWith("paseo") && name !== "paseo-slp")
      .sort()
      .map((name) => path.join(workspaceRoot, name, "packages/cli/dist"));
  } catch {
    return [];
  }
})();
const forkDistCandidates = [
  path.join(workspaceRoot, "paseo/packages/cli/dist"),
  ...siblingPaseoDirs,
];
const FORK_DIST = forkDistCandidates.find((d) => existsSync(`${d}/index.js`)) ?? forkDistCandidates[0];
const dist =
  process.env.SLP_PASEO_CLI_DIST ??
  (existsSync(`${FORK_DIST}/index.js`) ? FORK_DIST : "/opt/homebrew/lib/node_modules/@getpaseo/cli/dist");
const { connectToDaemon } = await import(pathToFileURL(`${dist}/utils/client.js`).href);
const { selectDaemonTarget } = await import(pathToFileURL(`${dist}/utils/daemon-target.js`).href);

const ids = process.argv.slice(2);
if (ids.length === 0) {
  console.error("usage: paseo_remove_project.mjs <projectId> [<projectId> ...]");
  process.exit(2);
}

const client = await connectToDaemon({ target: selectDaemonTarget({}) });
let failed = false;
try {
  for (const id of ids) {
    try {
      const res = await client.removeProject(id);
      console.log(`removed ${id} (workspaces: ${res.removedWorkspaceIds.length})`);
    } catch (err) {
      failed = true;
      console.error(`failed ${id}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
} finally {
  await client.close().catch(() => undefined);
}
process.exit(failed ? 1 : 0);
