// Published listener for the LabTether web console in the Home Assistant add-on.
//
// The Hub always marks its session cookie Secure, and browsers on another
// machine keep such a cookie only over HTTPS. This listener serves port 3000
// with the Hub's own certificate and forwards plain HTTP to the console on
// 127.0.0.1:3001. It replaces every X-Forwarded-* header, so the console (run
// with LABTETHER_TRUST_PROXY_HOPS=1) checks the browser's real origin and
// address and a client cannot choose them.
//
// Without a certificate (tls_mode disabled) it serves plain HTTP. Sign-in then
// needs an HTTPS proxy run by the user, so the scheme and host that proxy
// reports are kept. Browsers cannot add these headers to cross-site requests
// without a CORS preflight the console never grants, so the console's origin
// check still holds. The client address is always the one this listener sees.
import { readFileSync } from "node:fs";
import { createServer as createHttpServer, request } from "node:http";
import { createServer as createHttpsServer } from "node:https";

const LISTEN_HOST = "0.0.0.0";
const LISTEN_PORT = 3000;
const UPSTREAM_HOST = "127.0.0.1";
const UPSTREAM_PORT = 3001;
const MAX_CONNECTIONS = 512;
const CERT_RELOAD_MS = 60_000;
const SHUTDOWN_TIMEOUT_MS = 5_000;
const HOP_BY_HOP_HEADERS = [
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
];

const certFile = process.env.CONSOLE_TLS_CERT ?? "";
const keyFile = process.env.CONSOLE_TLS_KEY ?? "";
const proto = certFile ? "https" : "http";
const keepClientForwarding = !certFile;

function blockedHeaders(headers) {
  const blocked = new Set(HOP_BY_HOP_HEADERS);
  for (const name of String(headers.connection ?? "").split(",")) {
    const trimmed = name.trim().toLowerCase();
    if (trimmed) blocked.add(trimmed);
  }
  return blocked;
}

function firstHeaderValue(value) {
  return String(Array.isArray(value) ? value[0] : value ?? "").split(",")[0].trim();
}

function forwardedScheme(req) {
  if (keepClientForwarding) {
    const scheme = firstHeaderValue(req.headers["x-forwarded-proto"]).toLowerCase();
    if (scheme === "http" || scheme === "https") return scheme;
  }
  return proto;
}

function forwardedHost(req) {
  if (keepClientForwarding) {
    const host = firstHeaderValue(req.headers["x-forwarded-host"]);
    if (host && !/[\s/\\@?#]/.test(host)) return host;
  }
  return req.headers.host ?? "";
}

function browserPort(scheme, host) {
  try {
    const { port } = new URL(`${scheme}://${host}`);
    if (port) return port;
  } catch {
    // Fall back to the scheme's default port.
  }
  return scheme === "https" ? "443" : "80";
}

function upstreamHeaders(req, isWebSocket) {
  const blocked = blockedHeaders(req.headers);
  const headers = {};
  for (const [name, value] of Object.entries(req.headers)) {
    if (
      value == null
      || blocked.has(name)
      || name === "forwarded"
      || name === "x-real-ip"
      || name.startsWith("x-forwarded-")
    ) {
      continue;
    }
    headers[name] = value;
  }
  const scheme = forwardedScheme(req);
  const host = forwardedHost(req);
  headers["x-forwarded-proto"] = scheme;
  if (host) {
    headers["x-forwarded-host"] = host;
    headers["x-forwarded-port"] = browserPort(scheme, host);
  }
  if (req.socket.remoteAddress) headers["x-forwarded-for"] = req.socket.remoteAddress;
  if (isWebSocket) {
    headers.connection = "Upgrade";
    headers.upgrade = "websocket";
  }
  return headers;
}

function upstreamOptions(req, isWebSocket) {
  return {
    host: UPSTREAM_HOST,
    port: UPSTREAM_PORT,
    method: req.method,
    path: req.url,
    headers: upstreamHeaders(req, isWebSocket),
  };
}

function isOriginFormTarget(target) {
  return typeof target === "string" && target.startsWith("/") && !target.startsWith("//");
}

function sendStatus(res, statusCode, message) {
  res.writeHead(statusCode, { "content-type": "text/plain; charset=utf-8", connection: "close" });
  res.end(message);
}

function endRawSocket(socket, statusLine) {
  if (!socket.destroyed) socket.end(`HTTP/1.1 ${statusLine}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
}

function handleRequest(req, res) {
  if (!isOriginFormTarget(req.url)) {
    sendStatus(res, 400, "Bad Request");
    return;
  }
  const upstream = request(upstreamOptions(req, false), (upstreamRes) => {
    const blocked = blockedHeaders(upstreamRes.headers);
    const headers = {};
    for (const [name, value] of Object.entries(upstreamRes.headers)) {
      if (value != null && !blocked.has(name)) headers[name] = value;
    }
    res.writeHead(upstreamRes.statusCode ?? 502, upstreamRes.statusMessage, headers);
    upstreamRes.on("error", () => res.destroy());
    upstreamRes.pipe(res);
  });
  upstream.on("error", () => {
    if (res.headersSent) res.destroy();
    else sendStatus(res, 502, "Bad Gateway");
  });
  res.on("close", () => {
    if (!res.writableFinished) upstream.destroy();
  });
  req.pipe(upstream);
}

function rawResponseHead(upstreamRes) {
  let head = `HTTP/1.1 ${upstreamRes.statusCode} ${upstreamRes.statusMessage}\r\n`;
  for (let index = 0; index < upstreamRes.rawHeaders.length; index += 2) {
    head += `${upstreamRes.rawHeaders[index]}: ${upstreamRes.rawHeaders[index + 1]}\r\n`;
  }
  return `${head}\r\n`;
}

function handleUpgrade(req, socket, head) {
  socket.on("error", () => socket.destroy());
  if (!isOriginFormTarget(req.url) || String(req.headers.upgrade ?? "").toLowerCase() !== "websocket") {
    endRawSocket(socket, "400 Bad Request");
    return;
  }
  let upgraded = false;
  const upstream = request(upstreamOptions(req, true));
  upstream.on("upgrade", (upstreamRes, upstreamSocket, upstreamHead) => {
    upgraded = true;
    socket.write(rawResponseHead(upstreamRes));
    if (upstreamHead.length > 0) socket.write(upstreamHead);
    if (head.length > 0) upstreamSocket.write(head);
    upstreamSocket.on("error", () => socket.destroy());
    upstreamSocket.on("close", () => socket.destroy());
    socket.on("close", () => upstreamSocket.destroy());
    socket.pipe(upstreamSocket).pipe(socket);
  });
  // A refused upgrade carries no data the browser needs; send the status only.
  upstream.on("response", (upstreamRes) => {
    upstreamRes.resume();
    endRawSocket(socket, `${upstreamRes.statusCode} ${upstreamRes.statusMessage}`);
  });
  upstream.on("error", () => {
    if (!upgraded) endRawSocket(socket, "502 Bad Gateway");
  });
  socket.on("close", () => {
    if (!upgraded) upstream.destroy();
  });
  upstream.end();
}

function readKeyPair() {
  return { cert: readFileSync(certFile), key: readFileSync(keyFile), minVersion: "TLSv1.2" };
}

function createListener() {
  if (!certFile) return createHttpServer(handleRequest);

  let current = readKeyPair();
  const server = createHttpsServer(current, handleRequest);
  // The Hub renews its certificate in place. Pick up new files without a
  // restart; a half-written pair fails to load and is retried on the next tick.
  setInterval(() => {
    try {
      const next = readKeyPair();
      if (next.cert.equals(current.cert) && next.key.equals(current.key)) return;
      server.setSecureContext(next);
      current = next;
    } catch {
      // Keep serving the last valid pair.
    }
  }, CERT_RELOAD_MS).unref();
  return server;
}

const server = createListener();
server.maxConnections = MAX_CONNECTIONS;
server.on("upgrade", handleUpgrade);
server.once("error", (error) => {
  const code = typeof error?.code === "string" && /^[A-Z0-9_]+$/.test(error.code) ? error.code : "LISTEN_ERROR";
  console.error(`console-front: listener failed (${code})`);
  process.exit(1);
});
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => {
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), SHUTDOWN_TIMEOUT_MS).unref();
  });
}
server.listen({ host: LISTEN_HOST, port: LISTEN_PORT }, () => {
  console.log(`console-front: serving the web console over ${proto} on port ${LISTEN_PORT}`);
});
