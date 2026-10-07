/*
 * Run the battery percentage logic from kernel patch 0028 on the host.
 *
 * native-tests.sh extracts houji_capacity() from the patch itself into
 * HOUJI_CAPACITY_INC, so this tests the code that ships. The stubs stand in for
 * the few kernel names the function uses. The gauge is charge_counter over the
 * last full capacity, rounded up; "reported" is the firmware's linearized figure.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

typedef uint64_t u64;

#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, value) ((x) = (value))
#define DIV_ROUND_UP_ULL(n, d) (((n) + (d) - 1) / (d))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define min_t(type, a, b) ((type)(a) < (type)(b) ? (type)(a) : (type)(b))

enum {
	POWER_SUPPLY_PROP_STATUS,
	POWER_SUPPLY_PROP_CHARGE_COUNTER,
	POWER_SUPPLY_PROP_CHARGE_FULL,
	PROPS,
};

enum {
	POWER_SUPPLY_STATUS_UNKNOWN,
	POWER_SUPPLY_STATUS_CHARGING,
	POWER_SUPPLY_STATUS_DISCHARGING,
	POWER_SUPPLY_STATUS_NOT_CHARGING,
	POWER_SUPPLY_STATUS_FULL,
};

struct qcom_battmgr {
	int houji_capacity_floor;
	struct {
		unsigned int status;
		unsigned int capacity;
	} status;
	struct {
		unsigned int last_full_capacity;
	} info;
	bool fail[PROPS];
};

static int qcom_battmgr_bat_sm8350_update(struct qcom_battmgr *battmgr, int prop)
{
	return battmgr->fail[prop] ? -1 : 0;
}

#include HOUJI_CAPACITY_INC

#define FULL_UAH 3950000u
#define CHARGING POWER_SUPPLY_STATUS_CHARGING
#define DISCHARGING POWER_SUPPLY_STATUS_DISCHARGING

static struct qcom_battmgr battery;
static int failures;

static void state(unsigned int status, unsigned int counter_uah)
{
	battery.status.status = status;
	battery.status.capacity = counter_uah;
	battery.info.last_full_capacity = FULL_UAH;
}

static void expect(const char *what, int reported, int want)
{
	int got = houji_capacity(&battery, reported);

	if (got != want) {
		printf("FAIL %s: firmware %d, got %d, want %d\n", what, reported, got, want);
		failures++;
	} else {
		printf("ok   %s: firmware %d -> %d\n", what, reported, got);
	}
}

static void expect_floor(const char *what, int want)
{
	if (battery.houji_capacity_floor != want) {
		printf("FAIL %s: floor %d, want %d\n", what, battery.houji_capacity_floor, want);
		failures++;
	}
}

int main(void)
{
	/* 880000 uAh of 3950000 is 22.3%, so the gauge reads 23. */
	state(DISCHARGING, 880000);
	expect("discharging, stale firmware figure", 54, 23);
	expect_floor("discharging sets the floor", 23);
	state(DISCHARGING, 930000);
	expect("discharging, gauge recovers at rest", 25, 23);

	state(CHARGING, 880000);
	expect("plugging in, stale firmware figure", 54, 26);
	expect_floor("charging re-arms the floor", 0);
	expect("charging, firmware above the margin", 28, 26);
	expect("charging, firmware within the margin", 25, 25);
	expect("charging, firmware below the gauge", 22, 22);

	state(DISCHARGING, 1200000);
	expect("unplugged again, floor starts over", 40, 31);
	expect_floor("floor follows the gauge", 31);

	state(CHARGING, 3871000);
	expect("completed charge reads 100", 100, 100);
	state(CHARGING, 3950000);
	expect("full counter reads 100", 100, 100);
	state(POWER_SUPPLY_STATUS_FULL, 3871000);
	expect("full status reads 100", 100, 100);
	state(POWER_SUPPLY_STATUS_FULL, 3750000);
	expect("full status reads 100 even with the counter short", 100, 100);
	state(CHARGING, 3750000);
	expect("counter 5 points short is capped", 100, 98);
	state(CHARGING, 4100000);
	expect("counter above full capacity", 100, 100);

	state(POWER_SUPPLY_STATUS_NOT_CHARGING, 2765000);
	expect("charge limit holds like charging", 80, 73);

	battery.houji_capacity_floor = 23;
	battery.fail[POWER_SUPPLY_PROP_STATUS] = true;
	expect("status unreadable passes the figure through", 54, 54);
	expect_floor("status unreadable clears the floor", 0);
	battery.fail[POWER_SUPPLY_PROP_STATUS] = false;

	state(CHARGING, 880000);
	battery.fail[POWER_SUPPLY_PROP_CHARGE_COUNTER] = true;
	expect("charging, counter unreadable", 54, 54);
	state(DISCHARGING, 880000);
	battery.houji_capacity_floor = 23;
	expect("discharging, counter unreadable keeps the floor", 40, 23);
	battery.houji_capacity_floor = 0;
	expect("discharging, counter unreadable, no floor", 40, 40);
	battery.fail[POWER_SUPPLY_PROP_CHARGE_COUNTER] = false;

	battery.fail[POWER_SUPPLY_PROP_CHARGE_FULL] = true;
	state(CHARGING, 880000);
	expect("charging, full capacity unreadable", 54, 54);
	battery.fail[POWER_SUPPLY_PROP_CHARGE_FULL] = false;

	state(CHARGING, 880000);
	battery.info.last_full_capacity = 0;
	expect("charging, no full capacity yet", 54, 54);
	state(DISCHARGING, 880000);
	battery.info.last_full_capacity = 0;
	battery.houji_capacity_floor = 0;
	expect("discharging, no full capacity yet", 54, 54);

	if (failures) {
		printf("%d battery capacity checks failed\n", failures);
		return 1;
	}
	puts("PASS: battery capacity follows the gauge while discharging and charging");
	return 0;
}
