CC = aarch64-linux-gnu-gcc
OUT ?= .
SOURCE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
CFLAGS ?= -O2 -g -Wall -Wextra -Werror -fno-strict-aliasing

$(OUT)/houji-stock-core: $(SOURCE_DIR)/stock-core.c $(SOURCE_DIR)/sha256.c $(SOURCE_DIR)/sha256.h
	mkdir -p $(OUT)
	$(CC) $(CFLAGS) -static -o $@ $(SOURCE_DIR)/stock-core.c $(SOURCE_DIR)/sha256.c -lm

.PHONY: all
all: $(OUT)/houji-stock-core
