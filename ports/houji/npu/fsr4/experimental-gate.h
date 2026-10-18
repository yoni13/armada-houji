/* Parked experiment: old per-feature launch flags alone must not activate it. */
#ifndef HOUJI_FSR4_EXPERIMENTAL_GATE_H
#define HOUJI_FSR4_EXPERIMENTAL_GATE_H
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#endif
static inline int houji_fsr4_experimental_enabled(void)
{
#ifdef _WIN32
    char value[8] = {0};
    GetEnvironmentVariableA("HOUJI_FSR4_EXPERIMENTAL", value, sizeof(value));
    return strcmp(value, "1") == 0;
#else
    const char *value = getenv("HOUJI_FSR4_EXPERIMENTAL");
    return value && strcmp(value, "1") == 0;
#endif
}
#endif
