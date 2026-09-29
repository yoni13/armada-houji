// SPDX-License-Identifier: MIT
/* Execute only the authentication entry points of hash-pinned batterysecret.
 * The vendor binary remains unmodified and is supplied from the user's ROM.
 * Sysfs paths and Bionic libc calls are adapted to Linux; cryptographic
 * calculations, challenge comparisons and authentication results are stock.
 */
#define _GNU_SOURCE
#include <elf.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "sha256.h"

#if !defined(__aarch64__) || __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error This adapter executes ARM64 little-endian instructions.
#endif

static const char library_pin[] =
	"4772868c56b49d8e8f8bd29044dba08151b70a7ed2d994cf4296245a481ecf5e";
static unsigned char *image;
static const char *auth_dir;
static bool self_test, verification_started;
static char fd_names[1024][32];

static void die(const char *message)
{
	fprintf(stderr, "stock-auth: %s (errno=%d)\n", message, errno);
	exit(1);
}
static void require(bool condition, const char *message)
{
	if (!condition) die(message);
}
static void put64(void *p, uint64_t v) { memcpy(p, &v, 8); }

static const char *map_path(const char *path, char out[PATH_MAX])
{
	const char *allowed[] = { "authentic", "verify_process", "pd_verifed",
		"verify_digest", "verify_slave_flag", "request_vdm_cmd",
		"current_state", "adapter_id", "adapter_svid", "pdo2" };
	require(!self_test, "self-test attempted device I/O");
	if (*path == '/') path++;
	if (!strncmp(path, "sys/class/qcom-battery/", 23)) {
		const char *name = path + 23;
		for (size_t i = 0; i < sizeof(allowed) / sizeof(*allowed); i++) {
			if (strcmp(name, allowed[i])) continue;
			require(snprintf(out, PATH_MAX, "%s/%s", auth_dir, name) < PATH_MAX,
				"sysfs path too long");
			return out;
		}
	} else if (!strcmp(path, "sys/class/power_supply/usb/online")) {
		return "/sys/class/power_supply/qcom-battmgr-usb/online";
	} else if (!strcmp(path, "sys/class/typec/port0/data_role")) {
		return "/sys/class/typec/port0/data_role";
	}
	die("vendor requested unsupported sysfs path");
	return NULL;
}

static int compat_open(const char *path, int flags)
{
	char target[PATH_MAX];
	require(!(flags & ~(O_ACCMODE | O_CLOEXEC)), "unexpected open flags");
	int fd = open(map_path(path, target), flags | O_CLOEXEC);
	if (fd < 0) {
		fprintf(stderr, "stock-auth: cannot open %s\n", path);
		die("device open failed");
	}
	require(fd < 1024, "file descriptor out of range");
	snprintf(fd_names[fd], sizeof(fd_names[fd]), "%s", strrchr(path, '/') + 1);
	return fd;
}

static ssize_t compat_read(int fd, void *buf, size_t count)
{
	require(fd >= 0 && fd < 1024 && fd_names[fd][0], "unexpected read fd");
	ssize_t n;
	do { n = read(fd, buf, count); } while (n < 0 && errno == EINTR);
	if (n <= 0) {
		fprintf(stderr, "stock-auth: cannot read %s\n", fd_names[fd]);
		die("empty or failed device read");
	}
	return n;
}

static ssize_t compat_write(int fd, const void *buf, size_t count)
{
	require(fd >= 0 && fd < 1024 && fd_names[fd][0], "unexpected write fd");
	/* Android's agent pads some scalar writes with NULs. Linux sysfs needs
	 * only the text value; return its original logical write size on success. */
	size_t len = strnlen(buf, count);
	require(len && len <= 128, "invalid sysfs write length");
	ssize_t n;
	do { n = write(fd, buf, len); } while (n < 0 && errno == EINTR);
	if (n != (ssize_t)len) {
		fprintf(stderr, "stock-auth: cannot write %s\n", fd_names[fd]);
		die("device write failed");
	}
	if (!strcmp(fd_names[fd], "verify_process"))
		verification_started = ((const char *)buf)[0] == '1';
	/* Log only transaction names and result booleans, never challenges/keys. */
	if (!strcmp(fd_names[fd], "authentic") || !strcmp(fd_names[fd], "pd_verifed"))
		fprintf(stderr, "stock-auth: vendor %s result=%c\n", fd_names[fd], ((const char *)buf)[0]);
	if (!strcmp(fd_names[fd], "request_vdm_cmd"))
		fprintf(stderr, "stock-auth: adapter command=%c\n", ((const char *)buf)[0]);
	return count;
}

static int compat_close(int fd)
{
	if (fd >= 0 && fd < 1024) fd_names[fd][0] = 0;
	return close(fd);
}
static ssize_t compat_write_chk(int fd, const void *p, size_t n, size_t capacity)
{
	require(n <= capacity, "fortified write overflow");
	return compat_write(fd, p, n);
}
static int compat_property_get(const char *name, char *value, const char *fallback)
{
	const char *result = !strcmp(name, "ro.product.device") ? "houji" : fallback;
	if (!result) result = "";
	require(strlen(result) < 92, "Android property too long");
	strcpy(value, result);
	return strlen(value);
}
static int compat_log(int level, const char *fmt, ...)
{
	(void)level;
	/* Log only the template, without arguments that may contain digests. */
	if (!self_test && getenv("HOUJI_AUTH_TRACE") &&
	    !strstr(fmt, "random[") && !strstr(fmt, "digest["))
		fprintf(stderr, "stock-auth: trace %.160s\n", fmt);
	return 0;
}
static int *compat_errno(void) { return &errno; }
static size_t compat_strlen(const char *s, size_t limit)
{
	size_t n = strnlen(s, limit);
	require(n < limit, "fortified strlen overflow");
	return n;
}
static size_t compat_strlcat(char *dst, const char *src, size_t n, size_t capacity)
{
	require(n <= capacity, "fortified strlcat overflow");
	size_t d = strnlen(dst, n), s = strlen(src);
	if (d < n) {
		size_t copy = s < n - d - 1 ? s : n - d - 1;
		memcpy(dst + d, src, copy);
		dst[d + copy] = 0;
	}
	return d + s;
}
static int compat_vsnprintf(char *dst, size_t n, int flags, size_t capacity,
			    const char *fmt, va_list args)
{
	(void)flags;
	require(n <= capacity, "fortified snprintf overflow");
	return vsnprintf(dst, n, fmt, args);
}
__attribute__((noreturn)) static void unmodeled_import(void)
{
	die("unmodeled vendor import");
	__builtin_unreachable();
}
#define BIND(n, f) { n, (void *)(f) }
static const struct { const char *name; void *address; } bindings[] = {
	BIND("__open_2", compat_open), BIND("read", compat_read),
	BIND("write", compat_write), BIND("close", compat_close),
	BIND("__write_chk", compat_write_chk), BIND("property_get", compat_property_get),
	BIND("klog_write", compat_log), BIND("__errno", compat_errno),
	BIND("__stack_chk_fail", unmodeled_import), BIND("__strlen_chk", compat_strlen),
	BIND("__strlcat_chk", compat_strlcat), BIND("__vsnprintf_chk", compat_vsnprintf),
	BIND("strerror", strerror), BIND("atoi", atoi), BIND("atol", atol),
	BIND("strlen", strlen), BIND("strncmp", strncmp), BIND("sscanf", sscanf),
	BIND("usleep", usleep), BIND("malloc", malloc), BIND("calloc", calloc),
	BIND("free", free), BIND("memset", memset), BIND("memcpy", memcpy),
	BIND("time", time), BIND("srand", srand), BIND("rand", rand), BIND("lseek", lseek),
};
static uintptr_t resolve(const char *name, unsigned int type)
{
	for (size_t i = 0; i < sizeof(bindings) / sizeof(*bindings); ++i)
		if (!strcmp(name, bindings[i].name)) return (uintptr_t)bindings[i].address;
	require(type == STT_FUNC, "unknown undefined data symbol");
	return (uintptr_t)unmodeled_import;
}

/* ELF loader below shares the bounded relocation approach of stock-core.c. */
static unsigned char *read_file(const char *path, size_t *length, size_t limit)
{
	FILE *f = fopen(path, "rb");
	struct stat st;
	if (!f || fstat(fileno(f), &st))
		die("cannot open input");
	require(S_ISREG(st.st_mode) && st.st_size >= 0 &&
		(uint64_t)st.st_size <= limit, "input is not a bounded regular file");
	*length = (size_t)st.st_size;
	unsigned char *data = calloc(1, *length + 1);
	require(data != NULL, "allocation failed");
	require(fread(data, 1, *length, f) == *length, "short input read");
	fclose(f);
	return data;
}

static void check_pin(const unsigned char *data, size_t length, const char *pin)
{
	unsigned char digest[32];
	char hex[65];
	require(sha256(data, length, digest) == 0, "SHA-256 failed");
	for (size_t i = 0; i < sizeof(digest); ++i)
		snprintf(hex + 2 * i, 3, "%02x", digest[i]);
	require(!strcmp(hex, pin), "unsupported file hash");
}
static void *bounded(unsigned char *file, size_t size, uint64_t offset, uint64_t n)
{
	require(offset <= size && n <= size - offset, "ELF range out of bounds");
	return file + offset;
}

static void load_library(const char *path)
{
	size_t size;
	unsigned char *file = read_file(path, &size, 1024 * 1024);
	check_pin(file, size, library_pin);
	Elf64_Ehdr *eh = bounded(file, size, 0, sizeof(*eh));
	require(!memcmp(eh->e_ident, ELFMAG, SELFMAG) &&
		eh->e_ident[EI_CLASS] == ELFCLASS64 && eh->e_ident[EI_DATA] == ELFDATA2LSB &&
		eh->e_machine == EM_AARCH64 && eh->e_type == ET_DYN, "unsupported ELF");
	require(eh->e_phentsize == sizeof(Elf64_Phdr) &&
		eh->e_shentsize == sizeof(Elf64_Shdr), "unsupported ELF table size");
	Elf64_Phdr *ph = bounded(file, size, eh->e_phoff,
		(size_t)eh->e_phnum * sizeof(*ph));
	Elf64_Shdr *sh = bounded(file, size, eh->e_shoff,
		(size_t)eh->e_shnum * sizeof(*sh));
	require(sysconf(_SC_PAGESIZE) == 4096, "this pinned layout requires 4 KiB pages");
	const size_t image_size = 0xa000;
	image = mmap(NULL, image_size, PROT_READ | PROT_WRITE,
		MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	require(image != MAP_FAILED, "image allocation failed");
	for (unsigned int i = 0; i < eh->e_phnum; ++i) {
		if (ph[i].p_type != PT_LOAD)
			continue;
		require(ph[i].p_filesz <= ph[i].p_memsz, "invalid ELF segment");
		void *dst = bounded(image, image_size, ph[i].p_vaddr, ph[i].p_memsz);
		memcpy(dst, bounded(file, size, ph[i].p_offset, ph[i].p_filesz), ph[i].p_filesz);
	}
	for (unsigned int i = 0; i < eh->e_shnum; ++i) {
		if (sh[i].sh_type != SHT_RELA)
			continue;
		require(sh[i].sh_entsize == sizeof(Elf64_Rela) &&
			sh[i].sh_size % sizeof(Elf64_Rela) == 0 && sh[i].sh_link < eh->e_shnum,
			"invalid relocation table");
		Elf64_Shdr *symsec = &sh[sh[i].sh_link];
		require(symsec->sh_type == SHT_DYNSYM && symsec->sh_link < eh->e_shnum &&
			symsec->sh_entsize == sizeof(Elf64_Sym), "invalid dynamic symbols");
		Elf64_Sym *syms = bounded(file, size, symsec->sh_offset, symsec->sh_size);
		Elf64_Shdr *strsec = &sh[symsec->sh_link];
		char *strings = bounded(file, size, strsec->sh_offset, strsec->sh_size);
		Elf64_Rela *rel = bounded(file, size, sh[i].sh_offset, sh[i].sh_size);
		for (size_t n = 0; n < sh[i].sh_size / sizeof(*rel); ++n) {
			uintptr_t value;
			uint32_t type = ELF64_R_TYPE(rel[n].r_info);
			if (type == R_AARCH64_RELATIVE) {
				value = (uintptr_t)image + rel[n].r_addend;
			} else {
				require(type == R_AARCH64_ABS64 || type == R_AARCH64_GLOB_DAT ||
					type == R_AARCH64_JUMP_SLOT, "unsupported ELF relocation");
				size_t index = ELF64_R_SYM(rel[n].r_info);
				require(index < symsec->sh_size / sizeof(*syms), "invalid symbol index");
				Elf64_Sym *sym = &syms[index];
				require(sym->st_name < strsec->sh_size &&
					memchr(strings + sym->st_name, 0, strsec->sh_size - sym->st_name),
					"invalid symbol name");
				const char *name = strings + sym->st_name;
				/* Stock libc++ guard helpers use Android synchronization objects.
				 * Match the single-thread guard boundary used by offline replay. */
				if (!strcmp(name, "__cxa_guard_acquire") || !strcmp(name, "__cxa_guard_release")) {
					value = resolve(name, STT_FUNC);
				} else if (sym->st_shndx != SHN_UNDEF) {
					require(sym->st_value < image_size, "invalid defined symbol");
					value = (uintptr_t)image + sym->st_value;
				} else {
					require(sym->st_name < strsec->sh_size &&
						memchr(strings + sym->st_name, 0, strsec->sh_size - sym->st_name),
						"invalid symbol name");
					value = resolve(name, ELF64_ST_TYPE(sym->st_info));
				}
				value += rel[n].r_addend;
			}
			put64(bounded(image, image_size, rel[n].r_offset, 8), value);
		}
	}
	for (unsigned int i = 0; i < eh->e_phnum; ++i) {
		if (ph[i].p_type != PT_LOAD)
			continue;
		size_t start = ph[i].p_vaddr & ~(size_t)4095;
		size_t end = (ph[i].p_vaddr + ph[i].p_memsz + 4095) & ~(size_t)4095;
		int prot = (ph[i].p_flags & PF_R ? PROT_READ : 0) |
			(ph[i].p_flags & PF_W ? PROT_WRITE : 0) |
			(ph[i].p_flags & PF_X ? PROT_EXEC : 0);
		require(!mprotect(image + start, end - start, prot), "segment mprotect failed");
	}
	__builtin___clear_cache((char *)image, (char *)image + image_size);
	free(file);
}

#define FUNCTION(offset, type) ((type)(void *)(image + (offset)))
typedef int (*intarg_fn)(int);
typedef void (*noargs_fn)(void);
typedef void *(*hmac_fn)(const void *, int, const void *, size_t, void *, int *);

static void verify_compatibility(void)
{
	/* RFC 4231 case 1 verifies the native vendor hashing code, ELF relocation,
	 * AAPCS64 call boundary and TLS stack-canary access without device I/O. */
	unsigned char key[20], digest[32];
	const unsigned char expected[32] = {
		0xb0,0x34,0x4c,0x61,0xd8,0xdb,0x38,0x53,0x5c,0xa8,0xaf,0xce,0xaf,0x0b,0xf1,0x2b,
		0x88,0x1d,0xc2,0x00,0xc9,0x83,0x3d,0xa7,0x26,0xe9,0x37,0x6c,0x2e,0x32,0xcf,0xf7,
	};
	memset(key, 0x0b, sizeof(key));
	int size = sizeof(digest);
	FUNCTION(0x43e4, hmac_fn)(key, sizeof(key), "Hi There", 8, digest, &size);
	require(size == 32 && !memcmp(digest, expected, 32), "vendor HMAC self-test failed");
	FUNCTION(0x3238, noargs_fn)();
	require(image[0x9748] == 1 && image[0x9749] == 0 && image[0x974a] == 0 &&
		image[0x974c] == 0 && image[0x9750] == 0, "unexpected Houji stock profile");
	fprintf(stderr, "stock-auth: vendor HMAC and Houji profile self-tests passed\n");
}

static long read_number(const char *path)
{
	FILE *f = fopen(path, "r");
	long value;
	require(f && fscanf(f, "%ld", &value) == 1, "preflight telemetry unavailable");
	fclose(f);
	return value;
}

static void finish_verification(void)
{
	if (!verification_started) return;
	char path[PATH_MAX];
	snprintf(path, sizeof(path), "%s/verify_process", auth_dir);
	int fd = open(path, O_WRONLY | O_CLOEXEC);
	if (fd >= 0) {
		if (write(fd, "0\n", 2) != 2)
			fprintf(stderr, "stock-auth: failed to end verification\n");
		close(fd);
	}
}

int main(int argc, char **argv)
{
	require(argc == 3 || argc == 4,
		"usage: stock-auth batterysecret --self-test | --gauge DIR | --adapter DIR");
	self_test = !strcmp(argv[2], "--self-test");
	require(self_test ? argc == 3 : argc == 4, "invalid argument count");
	require(self_test || !strcmp(argv[2], "--gauge") || !strcmp(argv[2], "--adapter"),
		"unknown mode");
	if (!self_test) auth_dir = argv[3];
	load_library(argv[1]);
	verify_compatibility();
	if (self_test) return 0;
	require(!mkdir("/run/houji-charging", 0700) || errno == EEXIST,
		"authentication runtime directory unavailable");
	int lock = open("/run/houji-charging/auth.lock", O_CREAT | O_RDWR | O_CLOEXEC, 0600);
	require(lock >= 0 && !flock(lock, LOCK_EX | LOCK_NB), "authentication already running");
	long cap = read_number("/sys/class/power_supply/qcom-battmgr-bat/constant_charge_current");
	long temp = read_number("/sys/class/power_supply/qcom-battmgr-bat/temp");
	require(cap > 0 && cap <= 500000 && temp >= 100 && temp <= 400,
		"authentication requires conservative current and temperature");
	long usb_online = read_number("/sys/class/power_supply/qcom-battmgr-usb/online");
	require(usb_online == 1 || (!strcmp(argv[2], "--gauge") &&
		read_number("/sys/class/power_supply/qcom-battmgr-wls/online") == 1),
		"authentication requires a connected power source");
	atexit(finish_verification);
	if (!strcmp(argv[2], "--gauge")) {
		int result = FUNCTION(0x4d50, intarg_fn)(0);
		if (result != 1) result = FUNCTION(0x4c60, intarg_fn)(0);
		fprintf(stderr, "stock-auth: battery verification %s\n", result == 1 ? "passed" : "failed");
		return result == 1 ? 0 : 2;
	}
	FILE *f = fopen("/sys/class/power_supply/qcom-battmgr-usb/usb_type", "r");
	char type[256];
	require(f && fgets(type, sizeof(type), f), "USB type unavailable");
	fclose(f);
	require(strstr(type, "[PD]") || strstr(type, "[PD_PPS]"), "adapter is not USB-PD");
	/* Stock enum values 10/11 denote USB_PD/USB_PD_PPS, not Linux usb_type. */
	FUNCTION(0x6564, intarg_fn)(strstr(type, "[PD_PPS]") ? 11 : 10);
	char path[PATH_MAX];
	snprintf(path, sizeof(path), "%s/pd_verifed", auth_dir);
	long result = read_number(path);
	fprintf(stderr, "stock-auth: adapter verification %s\n", result == 1 ? "passed" : "failed");
	return result == 1 ? 0 : 2;
}
