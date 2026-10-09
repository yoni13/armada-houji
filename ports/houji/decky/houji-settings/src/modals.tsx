import { ConfirmModal, DialogButton, Focusable, ModalRoot, TextField, showModal } from "@decky/ui";
import { useState } from "react";
import { changeProfile, downloadProfile, type EsimProfile, type Reply } from "./backend";

type Report = (reply: Promise<Reply<unknown>>) => void;

const CODE_PREFIX = "LPA:1$";

export function DownloadModal({ closeModal, report }: { closeModal?(): void; report: Report }) {
  // The code lives only in this component's state; nothing logs it.
  const [code, setCode] = useState("");
  const trimmed = code.trim();
  const valid = trimmed.startsWith(CODE_PREFIX) && trimmed.length >= 12 && !/\s/.test(trimmed);
  return (
    <ConfirmModal
      strTitle="Add eSIM"
      strDescription={
        "Type the activation code from your carrier. It starts with LPA:1$. " +
        "Downloading needs an internet connection and can take a minute."
      }
      strOKButtonText="Download"
      bOKDisabled={!valid}
      closeModal={closeModal}
      onOK={() => report(downloadProfile(trimmed))}
    >
      <TextField
        label="Activation code"
        value={code}
        onChange={(event) => setCode(event.target.value)}
        focusOnMount
      />
    </ConfirmModal>
  );
}

function RenameModal({ profile, closeModal, report }: { profile: EsimProfile; closeModal?(): void; report: Report }) {
  const [name, setName] = useState(profile.nickname);
  const valid = new TextEncoder().encode(name.trim()).length <= 64;
  return (
    <ConfirmModal
      strTitle="Rename eSIM"
      strDescription="The name is stored on the eSIM. Leave it empty to show the carrier's name."
      strOKButtonText="Save"
      bOKDisabled={!valid}
      closeModal={closeModal}
      onOK={() => report(changeProfile("nickname", profile.handle, name.trim()))}
    >
      <TextField label="Name" value={name} onChange={(event) => setName(event.target.value)} focusOnMount />
    </ConfirmModal>
  );
}

export function ProfileModal({ profile, closeModal, report }: { profile: EsimProfile; closeModal?(): void; report: Report }) {
  const run = (action: "enable" | "disable") => {
    closeModal?.();
    report(changeProfile(action, profile.handle));
  };
  const remove = () => {
    closeModal?.();
    showModal(
      <ConfirmModal
        strTitle={`Delete ${profile.name}?`}
        strDescription="The profile is removed from the eSIM. Getting it back needs a new activation code from the carrier."
        strOKButtonText="Delete"
        bDestructiveWarning
        onOK={() => report(changeProfile("delete", profile.handle))}
      />,
    );
  };
  const rename = () => {
    closeModal?.();
    showModal(<RenameModal profile={profile} report={report} />);
  };
  return (
    <ModalRoot closeModal={closeModal}>
      <h2 style={{ marginTop: 0 }}>{profile.name}</h2>
      <p>
        {profile.provider ? `${profile.provider} · ` : ""}
        {profile.enabled ? "Active" : "Off"}. Changing the active profile restarts the modem and
        briefly disconnects mobile data.
      </p>
      <Focusable style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        <DialogButton onClick={() => run(profile.enabled ? "disable" : "enable")}>
          {profile.enabled ? "Turn off" : "Use this profile"}
        </DialogButton>
        <DialogButton onClick={rename}>Rename</DialogButton>
        <DialogButton onClick={remove} disabled={profile.enabled}>
          Delete
        </DialogButton>
      </Focusable>
    </ModalRoot>
  );
}
