#include "../ports/houji/sensors/gamescope/frame-wake.h"
#include <cassert>

int main() {
    HoujiFrameWake wake;
    using Result = HoujiFrameWake::Result;
    assert(wake.Update(1, 10) == Result::Waiting);
    wake.Arm(100, 10);
    assert(wake.Update(200, 10) == Result::Waiting); // Saved scanout is stale.
    assert(wake.Update(300, 0) == Result::Waiting); // Lost focus is not a frame.
    assert(wake.Update(400, 11) == Result::Fresh);
    assert(wake.Update(500, 12) == Result::Waiting); // Release only once.

    wake.Arm(1000, 11);
    assert(wake.Update(500'000'999, 11) == Result::Waiting);
    assert(wake.Update(500'001'000, 11) == Result::Timeout);
    assert(wake.Update(600'001'000, 11) == Result::Waiting);

    wake.Arm(0, 11);
    wake.Arm(400'000'000, 12); // A new request replaces the old deadline/frame.
    assert(wake.Update(600'000'000, 12) == Result::Waiting);
    assert(wake.Update(700'000'000, 13) == Result::Fresh);

    // A second sleep must not be undone by the preceding wake's deadline.
    assert(wake.Arm(0, 13) == 500'000'000);
    wake.Cancel();
    assert(wake.Update(100, 14) == Result::Waiting);
    assert(wake.Update(600'000'000, 14) == Result::Waiting);
}
