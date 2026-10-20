// Smoke harness module (#1103): free ports and base URLs.
import net from "node:net";

// Pick ports dynamically. Fixed 3001/3100 collided with this project's
// aiw-grafana / aiw-loki containers and caused the smoke to lock onto the
// wrong server via fetch keep-alive.
async function pickFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close((err) => (err ? reject(err) : resolve(port)));
    });
  });
}

const MOCK_API_PORT = await pickFreePort();
const FRONTEND_PORT = await pickFreePort();
const MOCK_API_BASE_URL = `http://127.0.0.1:${MOCK_API_PORT}`;
const FRONTEND_BASE_URL = `http://127.0.0.1:${FRONTEND_PORT}`;

export { MOCK_API_PORT, FRONTEND_PORT, MOCK_API_BASE_URL, FRONTEND_BASE_URL };
