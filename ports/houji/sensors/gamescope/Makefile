# Use matching ARM64 libwayland-client and headers from the target sysroot.
CC = aarch64-linux-gnu-gcc
OUT ?= build
WAYLAND_INCLUDE ?= /usr/include
WAYLAND_LIBDIR ?= /usr/aarch64-linux-gnu/lib
CFLAGS ?= -O2 -Wall -Wextra -Werror
all: $(OUT)/houji-gamescope-rotate
$(OUT):
	mkdir -p $@
$(OUT)/gamescope-control-client-protocol.h: gamescope-control.xml | $(OUT)
	wayland-scanner client-header $< $@
$(OUT)/gamescope-control-protocol.c: gamescope-control.xml | $(OUT)
	wayland-scanner private-code $< $@
$(OUT)/houji-gamescope-rotate: rotate.c $(OUT)/gamescope-control-client-protocol.h $(OUT)/gamescope-control-protocol.c
	$(CC) $(CFLAGS) -I$(OUT) -I$(WAYLAND_INCLUDE) rotate.c $(OUT)/gamescope-control-protocol.c -L$(WAYLAND_LIBDIR) $(LDFLAGS) -lwayland-client -o $@
