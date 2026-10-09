import {
  ButtonItem, DialogButton, DropdownItem, Field, Focusable, ModalRoot,
  PanelSection, PanelSectionRow, TextField, ToggleField, showModal,
} from "@decky/ui";
import { useEffect, useState } from "react";
import {
  getNfcTag, nfcAction, setNfc, setNfcNotifications, updateNfcTag, type NfcStatus, type NfcTag, type Reply,
} from "./backend";

type Report = (reply: Promise<Reply<unknown>>) => void;

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
  const emulationWorker = status.mode === "emulating";
  // The emulation worker remains busy while listening. Off/Reader/Stop must
  // still work then; starting/stopping and eSE sessions must finish first.
  const changing = pending || (status.busy && !emulationWorker);
  const chooseMode = (mode: NfcStatus["desired_mode"]) => {
    if (mode === status.desired_mode) return;
    report(mode === "emulation" ? nfcAction("start_saved") : setNfc(mode === "reader"));
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
          ]}
          disabled={changing}
          onChange={(option) => chooseMode(option.data)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <Field label="Status" description={status.message ?? "NFC service unavailable"} />
      </PanelSectionRow>
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
