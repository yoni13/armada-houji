// SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <map>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <fcntl.h>
#include <spawn.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

namespace houji {
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
inline volatile sig_atomic_t stopped = 0;
inline void stop_signal(int) { stopped = 1; }
inline void signals() {
    struct sigaction action{};
    action.sa_handler = stop_signal;
    sigemptyset(&action.sa_mask);
    sigaction(SIGTERM, &action, nullptr);
    sigaction(SIGINT, &action, nullptr);
}
inline std::string read(const fs::path &path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) throw std::runtime_error("Cannot read " + path.string());
    return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}
inline std::string trim(std::string value) {
    auto first = value.find_first_not_of(" \t\r\n");
    return first == std::string::npos ? "" : value.substr(first, value.find_last_not_of(" \t\r\n") - first + 1);
}
inline bool digits(const std::string &s) {
    return !s.empty() && std::all_of(s.begin(), s.end(), [](unsigned char c) { return c >= '0' && c <= '9'; });
}
inline bool argument(const std::string &cmdline, const std::string &wanted) {
    for (size_t pos = 0; pos < cmdline.size();) {
        auto end = cmdline.find('\0', pos);
        if (cmdline.substr(pos, end - pos) == wanted) return true;
        if (end == std::string::npos) break;
        pos = end + 1;
    }
    return false;
}
inline void atomic_write(const fs::path &path, const std::string &contents) {
    try { if (read(path) == contents) return; } catch (const std::exception &) {}
    std::string temporary = path.string() + ".XXXXXX";
    int fd = mkstemp(temporary.data());
    if (fd < 0) throw std::runtime_error("Cannot create " + path.string());
    bool ok = fchmod(fd, 0644) == 0;
    size_t offset = 0;
    while (ok && offset < contents.size()) {
        ssize_t n = write(fd, contents.data() + offset, contents.size() - offset);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { ok = false; break; }
        offset += static_cast<size_t>(n);
    }
    if (close(fd)) ok = false;
    if (ok && rename(temporary.c_str(), path.c_str()) == 0) return;
    unlink(temporary.c_str());
    throw std::runtime_error("Cannot publish " + path.string());
}
inline void pause(unsigned milliseconds) {
    timespec delay{static_cast<time_t>(milliseconds / 1000),
                   static_cast<long>(milliseconds % 1000) * 1000000L};
    // One timer wake per polling interval; SIGTERM interrupts the wait.
    while (!stopped && nanosleep(&delay, &delay) < 0 && errno == EINTR) {}
}
// Timeout applies to the process and its children; never invoke a shell.
inline void run(const std::vector<std::string> &args, unsigned timeout_seconds,
                const char *env_name = nullptr, const char *env_value = nullptr,
                bool interruptible = true) {
    std::vector<char *> argv;
    for (const auto &arg : args) argv.push_back(const_cast<char *>(arg.c_str()));
    argv.push_back(nullptr);
    std::vector<std::string> env;
    for (char **e = environ; *e; ++e)
        if (!env_name || std::string(*e).rfind(std::string(env_name) + '=', 0) != 0) env.emplace_back(*e);
    if (env_name) env.emplace_back(std::string(env_name) + '=' + env_value);
    std::vector<char *> envp;
    for (auto &entry : env) envp.push_back(entry.data());
    envp.push_back(nullptr);
    posix_spawnattr_t attr;
    int error = posix_spawnattr_init(&attr);
    if (error) throw std::runtime_error("Cannot initialize helper spawn");
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP);
    posix_spawnattr_setpgroup(&attr, 0);
    pid_t child;
    error = posix_spawnp(&child, argv[0], nullptr, &attr, argv.data(), envp.data());
    posix_spawnattr_destroy(&attr);
    if (error) throw std::runtime_error("Cannot start helper: " + std::string(strerror(error)));
    auto end = Clock::now() + std::chrono::seconds(timeout_seconds);
    int status = 0;
    while (true) {
        pid_t result = waitpid(child, &status, WNOHANG);
        if (result == child) break;
        if (result < 0 && errno != EINTR) throw std::runtime_error("Cannot wait for helper");
        if ((interruptible && stopped) || Clock::now() >= end) {
            kill(-child, SIGKILL);
            while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
            throw std::runtime_error("Helper interrupted or timed out");
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    if (!WIFEXITED(status) || WEXITSTATUS(status)) throw std::runtime_error("Helper failed");
}
struct Options {
    std::map<std::string, std::string> values;
    bool once = false;
    Options(int argc, char **argv, const std::vector<std::string> &allowed) {
        for (int i = 1; i < argc; ++i) {
            std::string key(argv[i]);
            if (key == "--once") { once = true; continue; }
            if (std::find(allowed.begin(), allowed.end(), key) == allowed.end() || i + 1 == argc)
                throw std::runtime_error("Unknown or incomplete option: " + key);
            values[key] = argv[++i];
        }
    }
    std::string get(const std::string &key, const std::string &fallback) const {
        auto it = values.find(key); return it == values.end() ? fallback : it->second;
    }
    unsigned interval(unsigned fallback) const {
        auto value = get("--interval-ms", std::to_string(fallback));
        if (!digits(value) || std::stoul(value) < 50 || std::stoul(value) > 60000)
            throw std::runtime_error("Invalid polling interval");
        return std::stoul(value);
    }
};
}
