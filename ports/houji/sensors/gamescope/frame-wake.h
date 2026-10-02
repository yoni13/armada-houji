// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>

// Called only on the compositor thread, after completed client fences have
// updated the focused held commit. An idle client must not leave the panel off.
class HoujiFrameWake {
public:
    enum class Result { Waiting, Fresh, Timeout };

    uint64_t Arm(uint64_t now, uint64_t commit) {
        pending = true;
        previous = commit;
        deadline = now + 500'000'000;
        return deadline;
    }

    void Cancel() {
        pending = false;
    }

    Result Update(uint64_t now, uint64_t commit) {
        if (!pending)
            return Result::Waiting;
        if (commit && commit != previous) {
            pending = false;
            return Result::Fresh;
        }
        if (now >= deadline) {
            pending = false;
            return Result::Timeout;
        }
        return Result::Waiting;
    }

private:
    bool pending = false;
    uint64_t previous = 0;
    uint64_t deadline = 0;
};
