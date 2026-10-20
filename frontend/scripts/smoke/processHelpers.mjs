// Smoke harness module (#1103): child-process, HTTP-wait and fetch helpers.
import { spawn } from "node:child_process";
import { FRONTEND_BASE_URL } from "./ports.mjs";

function spawnProcess(command, args, options = {}) {
  return spawn(command, args, {
    stdio: "inherit",
    ...options,
  });
}

async function runCommand(command, args, options = {}) {
  await new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: "inherit",
      ...options,
    });

    child.on("exit", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`${command} ${args.join(" ")} exited with code ${code}`));
    });
  });
}

async function waitForHttp(url, timeoutMs) {
  const startedAt = Date.now();
  let lastError;

  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
      lastError = new Error(`HTTP ${response.status} from ${url}`);
    } catch (error) {
      lastError = error;
    }

    await sleep(500);
  }

  throw new Error(`Timed out waiting for ${url}: ${lastError}`);
}

async function fetchHtml(pathname) {
  const response = await fetch(`${FRONTEND_BASE_URL}${pathname}`);
  const html = await response.text();
  if (!response.ok) {
    throw new Error(`Expected 200 for ${pathname}, received ${response.status}`);
  }
  return html;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export { spawnProcess, runCommand, waitForHttp, fetchHtml, sleep };
