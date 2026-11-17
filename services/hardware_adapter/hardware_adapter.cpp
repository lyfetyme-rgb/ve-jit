// vendarix-hw-adapter — isolated hardware front door for the Vendarix edge.
//
// Sits between native vendor SDKs (NCR Voyix, Toshiba TCGS, Diebold Nixdorf,
// scanner-scale) and the Python edge. Its ONLY jobs:
//
//   1. Accept newline-delimited JSON commands from SDK-side bridges on a
//      Unix domain socket (default /run/vendarix/hw-adapter.sock).
//   2. Forward hardware events to the Python edge's UDS listener
//      (default /run/vendarix/edge.sock) at /api/v1/edge/ingest.
//   3. Forward device heartbeats at /api/v1/edge/devices/heartbeat.
//
// It is a STATELESS sidecar: no database handles, no ORM, no policy logic,
// no credentials. All contracts, policy, audit and persistence live in the
// Python edge. Normalization and policy evaluation are NOT duplicated here.
//
// Wire protocol (newline-delimited JSON, both directions):
//
//   {"cmd":"ping"}
//     -> {"ok":true,"daemon":"vendarix-hw-adapter","version":"1.0.0"}
//
//   {"cmd":"event","vendor":"ncr-voyix","device_id":"ncr-lane-1","source_seq":1,
//    "tenant_id":"tenant_001","store_id":"store_001","raw":{...}}
//     -> {"ok":true,"forwarded":true,"status":201,"body":{...edge response...}}
//
//   {"cmd":"heartbeat","device_id":"ncr-lane-1","vendor":"ncr-voyix",
//    "interface":"ncr-voyix/pos-v1","healthy":true}
//     -> {"ok":true,"forwarded":true,"status":201,"body":{...edge response...}}

#include <atomic>
#include <cerrno>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <nlohmann/json.hpp>
#include <sstream>
#include <string>
#include <thread>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

using json = nlohmann::json;

constexpr const char* kVersion = "1.0.0";
constexpr const char* kDefaultListen = "/run/vendarix/hw-adapter.sock";
constexpr const char* kDefaultEdgeUds = "/run/vendarix/edge.sock";
constexpr int kMaxLine = 4 * 1024 * 1024;   // 4 MiB per command line
constexpr int kRecvTimeoutSeconds = 5;
constexpr size_t kMaxConnections = 16;

struct Config {
    std::string listen = kDefaultListen;
    std::string edge_uds = kDefaultEdgeUds;
    size_t max_connections = kMaxConnections;
};

std::atomic<bool> g_running{true};

void handle_signal(int) { g_running = false; }

struct HttpResponse {
    int status = 0;
    std::string body;
    bool transport_ok = false;
    std::string error;
};

// Minimal HTTP/1.1 POST over a Unix domain socket. Enough for the edge's
// small JSON API; not a general-purpose client.
HttpResponse http_post_uds(const std::string& sock_path,
                           const std::string& path,
                           const std::string& body,
                           int timeout_seconds = 10) {
    HttpResponse out;
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) {
        out.error = "socket: " + std::string(std::strerror(errno));
        return out;
    }
    timeval tv{};
    tv.tv_sec = timeout_seconds;
    tv.tv_usec = 0;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (sock_path.size() >= sizeof(addr.sun_path)) {
        close(fd);
        out.error = "edge socket path too long";
        return out;
    }
    std::strncpy(addr.sun_path, sock_path.c_str(), sizeof(addr.sun_path) - 1);
    if (connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        out.error = std::string("connect edge socket: ") + std::strerror(errno);
        close(fd);
        return out;
    }

    std::ostringstream req;
    req << "POST " << path << " HTTP/1.1\r\n"
        << "Host: edge\r\n"
        << "Content-Type: application/json\r\n"
        << "Content-Length: " << body.size() << "\r\n"
        << "Accept: application/json\r\n"
        << "Connection: close\r\n\r\n"
        << body;
    const std::string request = req.str();

    size_t sent = 0;
    while (sent < request.size()) {
        ssize_t n = send(fd, request.data() + sent, request.size() - sent, MSG_NOSIGNAL);
        if (n <= 0) {
            out.error = "send to edge failed";
            close(fd);
            return out;
        }
        sent += static_cast<size_t>(n);
    }

    std::string response;
    char buf[4096];
    while (true) {
        ssize_t n = recv(fd, buf, sizeof(buf), 0);
        if (n < 0) {
            if (errno == EINTR) continue;
            out.error = "recv from edge failed";
            close(fd);
            return out;
        }
        if (n == 0) break;  // server closed (Connection: close)
        response.append(buf, static_cast<size_t>(n));
    }
    close(fd);

    const auto line_end = response.find("\r\n");
    const auto sep = response.find("\r\n\r\n");
    std::istringstream status_line(line_end == std::string::npos ? response : response.substr(0, line_end));
    std::string http_version;
    status_line >> http_version >> out.status;
    if (sep != std::string::npos) {
        out.body = response.substr(sep + 4);
    }
    out.transport_ok = out.status > 0;
    return out;
}

json forward_to_edge(const Config& cfg, const std::string& path, const json& payload) {
    HttpResponse resp = http_post_uds(cfg.edge_uds, path, payload.dump());
    json out;
    if (!resp.transport_ok) {
        out["ok"] = false;
        out["forwarded"] = false;
        out["error"] = resp.error;
        return out;
    }
    out["ok"] = true;
    out["forwarded"] = true;
    out["status"] = resp.status;
    try {
        out["body"] = json::parse(resp.body);
    } catch (const json::exception&) {
        out["body_raw"] = resp.body;
    }
    return out;
}

json handle_command(const Config& cfg, const json& command) {
    const std::string cmd = command.value("cmd", "");

    if (cmd == "ping") {
        return {{"ok", true}, {"daemon", "vendarix-hw-adapter"}, {"version", kVersion}};
    }

    if (cmd == "event") {
        for (const char* key : {"vendor", "device_id", "source_seq", "raw"}) {
            if (!command.contains(key)) {
                return {{"ok", false}, {"error", std::string("missing required key: ") + key}};
            }
        }
        if (!command["raw"].is_object()) {
            return {{"ok", false}, {"error", "raw must be a JSON object"}};
        }
        json payload;
        payload["vendor"] = command["vendor"];
        payload["device_id"] = command["device_id"];
        payload["source_seq"] = command["source_seq"];
        payload["raw"] = command["raw"];
        if (command.contains("tenant_id")) payload["tenant_id"] = command["tenant_id"];
        if (command.contains("store_id")) payload["store_id"] = command["store_id"];
        if (command.contains("occurred_at")) payload["occurred_at"] = command["occurred_at"];
        if (command.contains("correlation_id")) payload["correlation_id"] = command["correlation_id"];
        if (command.contains("causation_id")) payload["causation_id"] = command["causation_id"];
        if (command.contains("action_class")) payload["action_class"] = command["action_class"];
        return forward_to_edge(cfg, "/api/v1/edge/ingest", payload);
    }

    if (cmd == "heartbeat") {
        for (const char* key : {"device_id", "vendor", "interface"}) {
            if (!command.contains(key)) {
                return {{"ok", false}, {"error", std::string("missing required key: ") + key}};
            }
        }
        json payload;
        payload["device_id"] = command["device_id"];
        payload["vendor"] = command["vendor"];
        payload["interface"] = command["interface"];
        payload["healthy"] = command.value("healthy", true);
        if (command.contains("detail")) payload["detail"] = command["detail"];
        return forward_to_edge(cfg, "/api/v1/edge/devices/heartbeat", payload);
    }

    return {{"ok", false}, {"error", "unknown cmd (expected: ping, event, heartbeat)"}};
}

void handle_connection(int client_fd, const Config& cfg) {
    timeval tv{};
    tv.tv_sec = kRecvTimeoutSeconds;
    tv.tv_usec = 0;
    setsockopt(client_fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    std::string buffer;
    buffer.reserve(8192);
    char chunk[8192];

    auto flush_line = [&](const std::string& line) {
        if (line.empty()) return;
        json response;
        try {
            response = handle_command(cfg, json::parse(line));
        } catch (const json::exception& e) {
            response = {{"ok", false}, {"error", std::string("invalid JSON: ") + e.what()}};
        }
        std::string out = response.dump() + "\n";
        size_t sent = 0;
        while (sent < out.size()) {
            ssize_t n = send(client_fd, out.data() + sent, out.size() - sent, MSG_NOSIGNAL);
            if (n <= 0) break;
            sent += static_cast<size_t>(n);
        }
    };

    while (g_running) {
        ssize_t n = recv(client_fd, chunk, sizeof(chunk), 0);
        if (n <= 0) break;
        buffer.append(chunk, static_cast<size_t>(n));
        if (buffer.size() > static_cast<size_t>(kMaxLine)) {
            std::string out = "{\"ok\":false,\"error\":\"command exceeds line limit\"}\n";
            send(client_fd, out.data(), out.size(), MSG_NOSIGNAL);
            break;
        }
        size_t pos;
        while ((pos = buffer.find('\n')) != std::string::npos) {
            std::string line = buffer.substr(0, pos);
            buffer.erase(0, pos + 1);
            // tolerate CRLF wire format
            if (!line.empty() && line.back() == '\r') line.pop_back();
            flush_line(line);
        }
    }
    if (!buffer.empty()) flush_line(buffer);
    close(client_fd);
}

void print_help(const char* program) {
    std::cout << "vendarix-hw-adapter " << kVersion << "\n"
              << "Usage: " << program << " [options]\n"
              << "  --listen PATH        Unix socket to expose to SDK bridges (default " << kDefaultListen << ")\n"
              << "  --edge-uds PATH      Unix socket of the Python edge HTTP listener (default " << kDefaultEdgeUds << ")\n"
              << "  --max-connections N  concurrent client cap (default " << kMaxConnections << ")\n"
              << "  --version            print version and exit\n"
              << "  --help               this help\n";
}

int main(int argc, char** argv) {
    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);
    std::signal(SIGPIPE, SIG_IGN);

    Config cfg;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        auto next = [&]() -> std::string {
            if (i + 1 >= argc) {
                std::cerr << "missing value for " << arg << "\n";
                std::exit(2);
            }
            return argv[++i];
        };
        if (arg == "--listen") cfg.listen = next();
        else if (arg == "--edge-uds") cfg.edge_uds = next();
        else if (arg == "--max-connections") cfg.max_connections = static_cast<size_t>(std::stoul(next()));
        else if (arg == "--version") { std::cout << kVersion << "\n"; return 0; }
        else if (arg == "--help") { print_help(argv[0]); return 0; }
        else { std::cerr << "unknown argument: " << arg << "\n"; print_help(argv[0]); return 2; }
    }

    unlink(cfg.listen.c_str());
    int server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_fd < 0) {
        std::cerr << "socket: " << std::strerror(errno) << "\n";
        return 1;
    }
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (cfg.listen.size() >= sizeof(addr.sun_path)) {
        std::cerr << "listen path too long\n";
        return 1;
    }
    std::strncpy(addr.sun_path, cfg.listen.c_str(), sizeof(addr.sun_path) - 1);
    if (bind(server_fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        std::cerr << "bind " << cfg.listen << ": " << std::strerror(errno) << "\n";
        return 1;
    }
    chmod(cfg.listen.c_str(), S_IRUSR | S_IWUSR | S_IRGRP | S_IWGRP);  // 0660
    if (listen(server_fd, SOMAXCONN) < 0) {
        std::cerr << "listen: " << std::strerror(errno) << "\n";
        return 1;
    }

    std::cout << "vendarix-hw-adapter " << kVersion << " listening on " << cfg.listen
              << " (edge: " << cfg.edge_uds << ")\n";

    std::atomic<size_t> active{0};
    pollfd pfd{server_fd, POLLIN, 0};
    while (g_running) {
        int poll_result = poll(&pfd, 1, 200);
        if (poll_result < 0) {
            if (errno == EINTR) continue;
            std::cerr << "poll: " << std::strerror(errno) << "\n";
            break;
        }
        if (poll_result == 0) continue;  // timeout: re-check g_running

        int client_fd = accept(server_fd, nullptr, nullptr);
        if (client_fd < 0) {
            if (errno == EINTR) continue;
            std::cerr << "accept: " << std::strerror(errno) << "\n";
            break;
        }
        if (active >= cfg.max_connections) {
            std::string out = "{\"ok\":false,\"error\":\"connection limit reached\"}\n";
            send(client_fd, out.data(), out.size(), MSG_NOSIGNAL);
            close(client_fd);
            continue;
        }
        active++;
        std::thread([client_fd, &cfg, &active]() {
            handle_connection(client_fd, cfg);
            active--;
        }).detach();
    }

    close(server_fd);
    unlink(cfg.listen.c_str());
    return 0;
}
