import {
  ButtonItem, DialogButton, DropdownItem, Field, Focusable, ModalRoot,
  PanelSection, PanelSectionRow, TextField, ToggleField, showModal,
} from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import {
  getNfcTag, nameIpassPlace, nfcAction, readIpassCard, setNfcMode, setNfcNotifications, updateNfcTag,
  type IpassCard, type IpassPlace, type IpassTrip, type NfcStatus, type NfcTag, type Reply,
} from "./backend";

type Report = (reply: Promise<Reply<unknown>>) => void;

const TRIP_LABELS: Record<IpassTrip["kind"], string> = {
  entry: "Entry",
  exit: "Exit",
  payment: "Payment",
  "top-up": "Top-up",
  other: "Transaction",
};

function tripAmount(trip: IpassTrip): string {
  if (trip.kind === "entry" && trip.amount === 0) return "";
  return `${trip.kind === "top-up" ? "+" : "−"}NT$${trip.amount} · `;
}

const placeLabel = (trip: IpassTrip) => trip.place ?? `Place ${trip.system}-${trip.station}`;

// Imported Wallet metadata supplies automatic names. A local name can override
// the older two-byte place code without changing the downloaded table.
function PlaceModal({ trip, named, closeModal }: {
  trip: IpassTrip; named(place: IpassPlace): void; closeModal?(): void;
}) {
  const [name, setName] = useState(trip.custom_place ? (trip.place ?? "") : "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (value: string) => {
    setBusy(true);
    setError(null);
    try {
      const reply = await nameIpassPlace(trip.system, trip.station, value);
      if (!reply.ok) { setError(reply.error); return; }
      named(reply.result);
      closeModal?.();
    } catch {
      setError("Could not save the name.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <ModalRoot closeModal={busy ? undefined : closeModal} onCancel={() => { if (!busy) closeModal?.(); }}>
      <h2 style={{ marginTop: 0 }}>Name this place</h2>
      <p>Code {trip.system}-{trip.station}. A custom name applies to trips with this code; some operators share codes across locations.</p>
      {trip.automatic_place && <p>Wallet name: {trip.automatic_place}</p>}
      <Focusable style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
        <TextField label="Name" description={`${name.length}/40`} value={name} disabled={busy}
          onChange={(event) => setName(event.target.value)} />
        <DialogButton disabled={busy || !name.trim() || name.length > 40} onClick={() => save(name)}>Save</DialogButton>
        {trip.custom_place && <DialogButton disabled={busy} onClick={() => save("")}>Use automatic name</DialogButton>}
      </Focusable>
      {error && <p role="alert">{error}</p>}
    </ModalRoot>
  );
}

// Touch scrolls the list; with a controller, focus moves row by row and Steam
// scrolls the focused row into view. Steam hides scrollbars, so the list draws
// its own position indicator.
function TripList({ trips, named }: { trips: IpassTrip[]; named(place: IpassPlace): void }) {
  const list = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState<{ top: number; size: number } | null>(null);
  const measure = () => {
    const element = list.current;
    if (!element || element.scrollHeight <= element.clientHeight + 1) {
      setThumb(null);
      return;
    }
    setThumb({ top: (element.scrollTop / element.scrollHeight) * 100,
               size: (element.clientHeight / element.scrollHeight) * 100 });
  };
  useEffect(() => {
    measure();
    const observer = new ResizeObserver(measure);
    if (list.current) {
      observer.observe(list.current);
      if (list.current.firstElementChild) observer.observe(list.current.firstElementChild);
    }
    return () => observer.disconnect();
  }, [trips]);

  return (
    <div style={{ position: "relative" }}>
      <div ref={list} onScroll={measure} style={{ maxHeight: "45vh", overflowY: "auto", paddingRight: "14px" }}>
        <Focusable>
          {trips.map((trip) => (
            <Field
              key={`${trip.time}-${trip.kind}-${trip.balance}`}
              focusable
              label={`${TRIP_LABELS[trip.kind]} · ${placeLabel(trip)}`}
              description={`${trip.time} · ${tripAmount(trip)}balance NT$${trip.balance}`}
              onActivate={() => showModal(<PlaceModal trip={trip} named={named} />)}
            />
          ))}
        </Focusable>
      </div>
      {thumb && (
        <div style={{ position: "absolute", top: 0, bottom: 0, right: "2px", width: "4px",
                      borderRadius: "2px", background: "rgba(255, 255, 255, 0.12)" }}>
          <div style={{ position: "absolute", left: 0, right: 0, top: `${thumb.top}%`, height: `${thumb.size}%`,
                        borderRadius: "2px", background: "rgba(255, 255, 255, 0.6)" }} />
        </div>
      )}
    </div>
  );
}

export function IpassCardModal({ closeModal }: { closeModal?(): void }) {
  const [card, setCard] = useState<IpassCard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const read = () => {
    setBusy(true);
    setError(null);
    readIpassCard()
      .then((reply) => {
        if (reply.ok) setCard(reply.result);
        else setError(reply.error);
      })
      .catch(() => setError("Could not read the iPASS card."))
      .finally(() => setBusy(false));
  };
  useEffect(read, []);

  return (
    <ModalRoot closeModal={busy ? undefined : closeModal} onCancel={() => { if (!busy) closeModal?.(); }}>
      <h2 style={{ marginTop: 0 }}>iPASS card</h2>
      {busy && <p>Reading the card… IPASS pauses for a few seconds.</p>}
      {error && <p role="alert">{error}</p>}
      {card && (
        <>
          <p style={{ fontSize: "28px", margin: "8px 0" }}>NT${card.balance}</p>
          {card.trips.length ? (
            <>
              <div style={{ fontSize: "12px", opacity: 0.7 }}>
                {card.trips.length} recent record{card.trips.length === 1 ? "" : "s"}
              </div>
              <TripList trips={card.trips} named={(place) => setCard((current) => current && {
                ...current,
                trips: current.trips.map((trip) => trip.system === place.system && trip.station === place.station
                  ? { ...trip, place: place.place ?? trip.automatic_place, custom_place: place.place !== null } : trip),
              })} />
            </>
          ) : <p>No recent trips on the card.</p>}
          <p style={{ fontSize: "12px", opacity: 0.7 }}>
            Names use an imported Google Wallet lookup table. Trip types are inferred from the card's records. Select a trip to set a custom name.
          </p>
        </>
      )}
      <Focusable style={{ display: "flex", gap: "12px" }}>
        <DialogButton disabled={busy} onClick={read}>Read again</DialogButton>
        <DialogButton disabled={busy} onClick={() => closeModal?.()}>Close</DialogButton>
      </Focusable>
    </ModalRoot>
  );
}

export function NfcTagModal({ closeModal, changed }: { closeModal?(): void; changed(): void }) {
  const [tag, setTag] = useState<NfcTag | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let mounted = true;
    getNfcTag().then((reply) => {
      if (!mounted) return;
      if (reply.ok) setTag(reply.result);
      else setError(reply.error);
    }).catch(() => { if (mounted) setError("Could not load the saved tag."); });
    return () => { mounted = false; };
  }, []);

  const bytes = new TextEncoder().encode(tag?.text ?? "").length;
  const serialValid = !tag?.custom_serial || /^[0-9a-fA-F]{8}$/.test(tag.serial.replace(/[: -]/g, ""));
  const valid = !!tag?.text.trim() && !tag.text.includes("\0") && bytes <= 200 && serialValid;
  const submit = async (action: "save" | "start") => {
    if (!tag || busy || !valid) return;
    setBusy(true);
    setError(null);
    try {
      // Automatic mode ignores an unfinished custom serial in the editor.
      const reply = await updateNfcTag(action, { ...tag, serial: tag.custom_serial ? tag.serial : "" });
      if (!reply.ok) { setError(reply.error); return; }
      changed();
      closeModal?.();
    } catch {
      setError("Could not update the NFC tag. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <ModalRoot closeModal={busy ? undefined : closeModal} onCancel={() => { if (!busy) closeModal?.(); }}>
      <h2 style={{ marginTop: 0 }}>Emulate an NFC text tag</h2>
      <p>Another phone can read this as a read-only NFC Forum Type 4 text tag.
        Emulation continues after closing this panel and after reboot until you stop it.</p>
      {tag && (
        <Focusable style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <TextField
            label="Tag text"
            description={`${bytes}/200 UTF-8 bytes`}
            value={tag.text}
            disabled={busy}
            onChange={(event) => setTag({ ...tag, text: event.target.value })}
          />
          <ToggleField
            label="Custom serial"
            description="Use a four-byte NFC-A identifier; otherwise let the controller choose."
            checked={tag.custom_serial}
            disabled={busy}
            onChange={(custom_serial) => setTag({ ...tag, custom_serial })}
          />
          {tag.custom_serial && (
            <TextField
              label="Serial (hexadecimal)"
              description={serialValid ? "Four bytes, for example 12:34:56:78" : "Enter exactly four hexadecimal bytes."}
              value={tag.serial}
              disabled={busy}
              onChange={(event) => setTag({ ...tag, serial: event.target.value })}
            />
          )}
          <DialogButton disabled={!valid || busy} onClick={() => submit("start")}>
            Save and start emulation
          </DialogButton>
          <DialogButton disabled={!valid || busy} onClick={() => submit("save")}>
            Save settings
          </DialogButton>
        </Focusable>
      )}
      {!tag && !error && <p>Loading saved tag…</p>}
      {error && <p role="alert">{error}</p>}
      <p style={{ fontSize: "12px", opacity: 0.7 }}>
        Emulates the text tag configured here. A serial alone does not reproduce a card’s applications or authentication.
      </p>
    </ModalRoot>
  );
}

export function NfcPanel({ status, pending, report, refresh }: {
  status: NfcStatus; pending: boolean; report: Report; refresh(): void;
}) {
  const emulationWorker = status.mode === "emulating" || status.mode === "ipass";
  // The emulation worker remains busy while listening. Off/Reader/Stop must
  // still work then; starting/stopping and eSE sessions must finish first.
  const changing = pending || (status.busy && !emulationWorker);
  const chooseMode = (mode: NfcStatus["desired_mode"]) => {
    if (mode === status.desired_mode) return;
    report(setNfcMode(mode));
  };
  return (
    <PanelSection title="NFC">
      <PanelSectionRow>
        <DropdownItem
          label="Mode"
          selectedOption={status.desired_mode}
          rgOptions={[
            { data: "off", label: "Off" },
            { data: "reader", label: "Read nearby tags" },
            { data: "emulation", label: "Emulate text tag" },
            { data: "ipass", label: "IPASS" },
          ]}
          disabled={changing}
          onChange={(option) => chooseMode(option.data)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="Status" description={status.message ?? "NFC service unavailable"} />
      </PanelSectionRow>
      {status.desired_mode === "ipass" && (
        <>
          <PanelSectionRow>
            <Field label="IPASS" description="Uses the iPASS card on the embedded secure element. The card must first be provisioned in Android; this mode cannot add one." />
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="RF activity" description={`${status.ipass_field_on} fields · ${status.ipass_ese_actions} eSE actions (not transaction confirmations)`} />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              description="IPASS pauses for a few seconds while the card is read."
              disabled={pending || status.mode !== "ipass"}
              onClick={() => showModal(<IpassCardModal />)}
            >
              Balance and recent trips
            </ButtonItem>
          </PanelSectionRow>
          {status.mode === "error" && (
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={pending || status.busy} onClick={() => report(setNfcMode("ipass"))}>
                Retry IPASS
              </ButtonItem>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={pending} onClick={() => report(setNfcMode("off"))}>
              Stop IPASS and restore eSE state
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
      {status.desired_mode === "reader" && (
        <>
          <PanelSectionRow>
            <ToggleField
              label="Notify when a tag is found"
              description="Show a Steam notification for each new scan, even with Quick Access closed."
              checked={status.notify_on_scan}
              disabled={pending}
              onChange={(enabled) => report(setNfcNotifications(enabled))}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Tags detected" description={`${status.tags}${status.polling ? " · scanning" : ""}`} />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={pending || status.busy} onClick={() => report(nfcAction("scan"))}>
              Scan again
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          description={emulationWorker ? "Stop emulation to edit the tag." : "Text and optional four-byte serial, shared with NFC Manager."}
          disabled={pending || status.busy}
          onClick={() => showModal(<NfcTagModal changed={refresh} />)}
        >
          Configure emulated tag
        </ButtonItem>
      </PanelSectionRow>
      {status.emulating && (
        <>
          <PanelSectionRow>
            <Field label="Text read requests" description={String(status.reads)} />
          </PanelSectionRow>
          {status.mode === "error" && (
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={pending || status.busy} onClick={() => report(nfcAction("start_saved"))}>
                Retry emulation
              </ButtonItem>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={pending} onClick={() => report(nfcAction("stop"))}>
              Stop emulation
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
}
