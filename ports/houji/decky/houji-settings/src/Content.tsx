import { toaster } from "@decky/api";
import {
  ButtonItem,
  ConfirmModal,
  DropdownItem,
  Field,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
  showModal,
} from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  enableCellular,
  getStatus,
  refreshProfiles,
  selectSim,
  setChargeLimit,
  setData,
  setNfc,
  setRoaming,
  setRotation,
  type CellularStatus,
  type Orientation,
  type Reply,
  type SimMode,
  type Status,
} from "./backend";
import { DownloadModal, ProfileModal } from "./modals";

const ORIENTATIONS: { data: Orientation; label: string }[] = [
  { data: "normal", label: "Portrait" },
  { data: "left", label: "Landscape, USB port on the left" },
  { data: "right", label: "Landscape, USB port on the right" },
  { data: "upsidedown", label: "Portrait, upside down" },
];

const SIMS: { data: SimMode; label: string }[] = [
  { data: "auto", label: "Automatic" },
  { data: "physical1", label: "SIM 1" },
  { data: "physical2", label: "SIM 2" },
  { data: "esim", label: "eSIM" },
];

const DEFAULT_LIMIT = 80;
const RESUME_MARGIN = 5;

const orientationLabel = (value: Orientation | null) =>
  ORIENTATIONS.find((option) => option.data === value)?.label ?? "unknown";

function networkSummary(cellular: CellularStatus): string {
  const modem = cellular.modem;
  if (!modem) return "Modem not reachable";
  if (modem.state === "failed") {
    return modem.reason === "sim-missing" ? "No SIM detected in the selected slot" : "Modem not ready";
  }
  if (!modem.registered) return modem.state === "searching" ? "Searching for a network" : "Not registered";
  return [modem.operator, modem.technology?.toUpperCase(), modem.signal !== null ? `${modem.signal}%` : null,
    modem.roaming ? "roaming" : null].filter(Boolean).join(" · ");
}

function chargingSummary(status: Status["charging"]): string {
  if (status.limit === null) return "Charges to 100%";
  if (status.holding) {
    return `Holding at ${status.capacity ?? "?"}%; charging resumes at ${status.limit - RESUME_MARGIN}%`;
  }
  return `Charging stops at ${status.limit}%`;
}

export function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [limitDraft, setLimitDraft] = useState<number | null>(null);
  const limitTimer = useRef<number | undefined>(undefined);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const reply = await getStatus();
      if (!mounted.current) return;
      if (reply.ok) {
        setStatus(reply.result);
        setError(null);
      } else {
        setError(reply.error);
      }
    } catch {
      if (mounted.current) setError("Houji Settings is not responding.");
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => {
      mounted.current = false;
      window.clearInterval(timer);
      window.clearTimeout(limitTimer.current);
    };
  }, [refresh]);

  const report = useCallback(
    (reply: Promise<Reply<unknown>>) => {
      setPending(true);
      reply
        .then((result) => {
          if (!result.ok) toaster.toast({ title: "Houji Settings", body: result.error });
        })
        .catch(() => toaster.toast({ title: "Houji Settings", body: "The change did not finish." }))
        .finally(() => {
          if (!mounted.current) return;
          setPending(false);
          refresh();
        });
    },
    [refresh],
  );

  if (!status) {
    return (
      <PanelSection>
        <PanelSectionRow>
          <Field label={error ?? "Loading…"} />
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const { rotation, charging, cellular, nfc } = status;
  const cellularBusy = pending || cellular.busy;
  const data = cellular.data;
  const limit = limitDraft ?? charging.limit ?? DEFAULT_LIMIT;

  const changeLimit = (value: number) => {
    setLimitDraft(value);
    window.clearTimeout(limitTimer.current);
    limitTimer.current = window.setTimeout(() => {
      setLimitDraft(null);
      report(setChargeLimit(value));
    }, 600);
  };

  const chooseSim = (mode: SimMode) => {
    if (mode === cellular.selection) return;
    showModal(
      <ConfirmModal
        strTitle="Switch data SIM?"
        strDescription="The modem restarts, so mobile data disconnects for up to a minute."
        strOKButtonText="Switch"
        onOK={() => report(selectSim(mode))}
      />,
    );
  };

  return (
    <>
      <PanelSection title="Display">
        <PanelSectionRow>
          <ToggleField
            label="Rotation lock"
            description={
              rotation.lock
                ? `Locked: ${orientationLabel(rotation.lock)}`
                : rotation.current
                  ? `Follows the phone (now ${orientationLabel(rotation.current)})`
                  : "Follows the phone"
            }
            checked={rotation.lock !== null}
            disabled={pending}
            onChange={(on) => report(setRotation(on ? (rotation.current ?? "normal") : "auto"))}
          />
        </PanelSectionRow>
        {rotation.lock && (
          <PanelSectionRow>
            <DropdownItem
              label="Orientation"
              rgOptions={ORIENTATIONS}
              selectedOption={rotation.lock}
              disabled={pending}
              onChange={(option) => report(setRotation(option.data))}
            />
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Battery">
        <PanelSectionRow>
          <ToggleField
            label="Charge limit"
            description={chargingSummary(charging)}
            checked={charging.limit !== null}
            disabled={pending}
            onChange={(on) => report(setChargeLimit(on ? DEFAULT_LIMIT : null))}
          />
        </PanelSectionRow>
        {charging.limit !== null && (
          <PanelSectionRow>
            <SliderField
              label="Stop charging at"
              value={limit}
              min={50}
              max={95}
              step={5}
              notchCount={10}
              showValue
              valueSuffix="%"
              onChange={changeLimit}
            />
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Mobile network">
        {!cellular.enabled ? (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              description="Starts the modem services on this and later boots."
              disabled={pending}
              onClick={() => report(enableCellular())}
            >
              Turn on cellular
            </ButtonItem>
          </PanelSectionRow>
        ) : (
          <>
            <PanelSectionRow>
              <Field
                label="Network"
                description={cellular.busy ? (cellular.message ?? "Working…") : networkSummary(cellular)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <DropdownItem
                label="Data SIM"
                rgOptions={SIMS}
                selectedOption={cellular.selection}
                disabled={cellularBusy}
                onChange={(option) => chooseSim(option.data)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label="Mobile data"
                description={
                  !data?.profile
                    ? "Add this SIM's APN in Desktop Mode: Settings, Cellular Network"
                    : data.connected
                      ? "Connected"
                      : data.enabled
                        ? "On, not connected"
                        : "Off"
                }
                checked={!!data?.enabled}
                disabled={cellularBusy || !data?.profile}
                onChange={(on) => report(setData(on))}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label="Allow roaming"
                description="Use mobile data on other carriers' networks. Charges may apply."
                checked={!!data?.roaming_allowed}
                disabled={cellularBusy || data?.roaming_allowed == null}
                onChange={(on) => report(setRoaming(on))}
              />
            </PanelSectionRow>
            {!cellular.busy && cellular.message && cellular.message !== "Done" && (
              <PanelSectionRow>
                <Field description={cellular.message} />
              </PanelSectionRow>
            )}
          </>
        )}
      </PanelSection>

      {cellular.enabled && cellular.selection === "esim" && (
        <PanelSection title="eSIM profiles">
          {cellular.profiles.map((profile) => (
            <PanelSectionRow key={profile.handle}>
              <ButtonItem
                layout="below"
                label={profile.name}
                description={[profile.provider, profile.enabled ? "Active" : "Off"].filter(Boolean).join(" · ")}
                disabled={cellularBusy}
                onClick={() => showModal(<ProfileModal profile={profile} report={report} />)}
              >
                Manage
              </ButtonItem>
            </PanelSectionRow>
          ))}
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              description={cellular.profiles.length ? undefined : "Read the profiles stored on the eSIM."}
              disabled={cellularBusy}
              onClick={() => report(refreshProfiles())}
            >
              {cellular.profiles.length ? "Refresh profiles" : "Show profiles"}
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={cellularBusy}
              onClick={() => showModal(<DownloadModal report={report} />)}
            >
              Add eSIM
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}

      <PanelSection title="NFC">
        <PanelSectionRow>
          <ToggleField
            label="NFC"
            description={nfc.emulating ? "Tag emulation is on; turning NFC off stops it." : (nfc.message ?? undefined)}
            checked={nfc.enabled}
            disabled={pending || nfc.busy}
            onChange={(on) => report(setNfc(on))}
          />
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}
