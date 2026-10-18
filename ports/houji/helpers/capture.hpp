// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "common.hpp"
#include <poll.h>

namespace houji {
// Captured command data may contain activation codes/profile IDs. Exceptions
// intentionally carry no command arguments or child output.
inline std::string capture(const std::vector<std::string> &args, unsigned seconds,
                           const std::string &input = "",
                           const std::vector<std::string> *environment = nullptr,
                           bool cleanup = false) {
    struct Pipe {
        int read = -1, write = -1;
        Pipe() {
            int fds[2];
            if (pipe2(fds, O_CLOEXEC)) throw std::runtime_error("Cannot create command pipe");
            read = fds[0]; write = fds[1];
        }
        void close_read() { if (read >= 0) { close(read); read = -1; } }
        void close_write() { if (write >= 0) { close(write); write = -1; } }
        ~Pipe() { close_read(); close_write(); }
    } in, out;
    std::vector<char *> argv, envp;
    for (const auto &arg : args) argv.push_back(const_cast<char *>(arg.c_str()));
    argv.push_back(nullptr);
    if (environment) {
        for (const auto &arg : *environment) envp.push_back(const_cast<char *>(arg.c_str()));
        envp.push_back(nullptr);
    }
    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attr;
    if (posix_spawn_file_actions_init(&actions)) throw std::runtime_error("Command setup failed");
    if (posix_spawnattr_init(&attr)) {
        posix_spawn_file_actions_destroy(&actions);
        throw std::runtime_error("Command setup failed");
    }
    int error = posix_spawn_file_actions_adddup2(&actions, in.read, STDIN_FILENO);
    error |= posix_spawn_file_actions_adddup2(&actions, out.write, STDOUT_FILENO);
    error |= posix_spawn_file_actions_addopen(&actions, STDERR_FILENO, "/dev/null", O_WRONLY, 0);
    error |= posix_spawn_file_actions_addclose(&actions, in.write);
    error |= posix_spawn_file_actions_addclose(&actions, out.read);
    error |= posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETPGROUP);
    error |= posix_spawnattr_setpgroup(&attr, 0);
    pid_t child = -1;
    if (!error) error = posix_spawnp(&child, argv[0], &actions, &attr, argv.data(),
                                    environment ? envp.data() : environ);
    posix_spawnattr_destroy(&attr);
    posix_spawn_file_actions_destroy(&actions);
    if (error) throw std::runtime_error("Cannot start command");
    in.close_read(); out.close_write();
    bool reaped = false;
    int status = 0;
    std::string result;
    try {
        if (fcntl(in.write, F_SETFL, O_NONBLOCK) < 0 || fcntl(out.read, F_SETFL, O_NONBLOCK) < 0)
            throw std::runtime_error("Command pipe setup failed");
        size_t sent = 0;
        if (input.empty()) in.close_write();
        const auto deadline = Clock::now() + std::chrono::seconds(seconds);
        while (!reaped || out.read >= 0) {
            if ((!cleanup && stopped) || Clock::now() >= deadline)
                throw std::runtime_error("Command interrupted or timed out");
            pollfd fds[2] = {{out.read, POLLIN, 0}, {in.write, POLLOUT, 0}};
            int rc = poll(fds, 2, 50);
            if (rc < 0 && errno != EINTR) throw std::runtime_error("Command poll failed");
            if (out.read >= 0 && fds[0].revents) {
                char buffer[8192];
                ssize_t n = ::read(out.read, buffer, sizeof(buffer));
                if (n > 0) {
                    if (result.size() + n > (4u << 20)) throw std::runtime_error("Command output too large");
                    result.append(buffer, n);
                } else if (!n) out.close_read();
                else if (errno != EAGAIN && errno != EINTR) throw std::runtime_error("Command read failed");
            }
            if (in.write >= 0 && fds[1].revents) {
                ssize_t n = ::write(in.write, input.data() + sent, input.size() - sent);
                if (n > 0) { sent += n; if (sent == input.size()) in.close_write(); }
                else if (n < 0 && errno != EAGAIN && errno != EINTR) throw std::runtime_error("Command input failed");
            }
            if (!reaped) {
                pid_t done = waitpid(child, &status, WNOHANG);
                if (done == child) { reaped = true; in.close_write(); }
                else if (done < 0 && errno != EINTR) throw std::runtime_error("Command wait failed");
            }
        }
        if (!WIFEXITED(status) || WEXITSTATUS(status)) throw std::runtime_error("Command failed");
    } catch (...) {
        kill(-child, SIGKILL);
        if (!reaped) while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
        throw;
    }
    return result;
}
}
