CXX ?= g++
OUT ?= build
CXXFLAGS ?= -O2 -std=c++17 -Wall -Wextra -Werror
GIO_CFLAGS ?= $(shell pkg-config --cflags gio-2.0 gio-unix-2.0)
GIO_LIBS ?= $(shell pkg-config --libs gio-2.0 gio-unix-2.0)
JSON_CFLAGS ?= $(shell pkg-config --cflags nlohmann_json)
PROGRAMS = $(OUT)/houji-steam-power $(OUT)/houji-x2s-session $(OUT)/houji-gamescope-orientation
PROGRAMS += $(OUT)/houji-gyro $(OUT)/houji-thermal $(OUT)/houji-license-relay
PROGRAMS += $(OUT)/houji-charge-policy
PROGRAMS += $(OUT)/houji-cellular-control
.PHONY: all
all: $(PROGRAMS)
$(OUT):
	mkdir -p $@
$(OUT)/houji-steam-power: steam-power.cpp common.hpp bus.hpp | $(OUT)
	$(CXX) $(CXXFLAGS) $(GIO_CFLAGS) $< $(LDFLAGS) $(GIO_LIBS) -o $@
$(OUT)/houji-gamescope-orientation: orientation.cpp common.hpp bus.hpp | $(OUT)
	$(CXX) $(CXXFLAGS) $(GIO_CFLAGS) $< $(LDFLAGS) $(GIO_LIBS) -o $@
$(OUT)/houji-x2s-session: x2s-session.cpp common.hpp | $(OUT)
	$(CXX) $(CXXFLAGS) $< $(LDFLAGS) -o $@
$(OUT)/houji-gyro: gyro.cpp common.hpp bus.hpp | $(OUT)
	$(CXX) $(CXXFLAGS) $(GIO_CFLAGS) $< $(LDFLAGS) $(GIO_LIBS) -ldl -o $@
$(OUT)/houji-thermal: thermal.cpp common.hpp board-thermal.hpp | $(OUT)
	$(CXX) $(CXXFLAGS) $< $(LDFLAGS) -o $@
$(OUT)/houji-license-relay: license-relay.cpp common.hpp | $(OUT)
	$(CXX) $(CXXFLAGS) $< $(LDFLAGS) -ldl -o $@
$(OUT)/houji-charge-policy: charge-policy.cpp charge-model.hpp stock-battery-state.hpp board-thermal.hpp common.hpp | $(OUT)
	$(CXX) $(CXXFLAGS) $< $(LDFLAGS) -o $@
$(OUT)/houji-cellular-control: cellular-control.cpp capture.hpp common.hpp bus.hpp | $(OUT)
	$(CXX) $(CXXFLAGS) $(GIO_CFLAGS) $(JSON_CFLAGS) $< $(LDFLAGS) $(GIO_LIBS) -pthread -o $@
