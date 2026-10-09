import { callable } from "@decky/api";

export type Reply<T> = { ok: true; result: T } | { ok: false; error: string };

export type Orientation = "normal" | "left" | "right" | "upsidedown";
export type SimMode = "auto" | "physical1" | "physical2" | "esim";

export interface RotationStatus {
  lock: Orientation | null;
  current: Orientation | null;
}

export interface ChargingStatus {
  limit: number | null;
  holding: boolean;
  capacity: number | null;
  status: string | null;
}

export interface NfcStatus {
  enabled: boolean;
  emulating: boolean;
  busy: boolean;
  message: string | null;
}

export interface EsimProfile {
  handle: string;
  name: string;
  nickname: string;
  provider: string | null;
  enabled: boolean;
}

export interface ModemStatus {
  state: string | null;
  reason: string | null;
  signal: number | null;
  technology: string | null;
  operator: string | null;
  registered: boolean;
  roaming: boolean;
}

export interface DataStatus {
  modem: boolean;
  ready: boolean;
  profile: boolean;
  enabled: boolean;
  connected: boolean;
  roaming_allowed: boolean | null;
}

export interface CellularStatus {
  enabled: boolean;
  selection: SimMode;
  service: boolean;
  busy: boolean;
  message: string | null;
  profiles: EsimProfile[];
  modem: ModemStatus | null;
  data: DataStatus | null;
}

export interface Status {
  rotation: RotationStatus;
  charging: ChargingStatus;
  cellular: CellularStatus;
  nfc: NfcStatus;
}

export const getStatus = callable<[], Reply<Status>>("status");
export const setRotation = callable<[mode: Orientation | "auto"], Reply<RotationStatus>>("set_rotation");
export const setChargeLimit = callable<[limit: number | null], Reply<ChargingStatus>>("set_charge_limit");
export const setNfc = callable<[enabled: boolean], Reply<NfcStatus>>("set_nfc");
export const enableCellular = callable<[], Reply<CellularStatus>>("enable_cellular");
export const selectSim = callable<[mode: SimMode], Reply<CellularStatus>>("select_sim");
export const refreshProfiles = callable<[], Reply<CellularStatus>>("refresh_profiles");
export const changeProfile = callable<
  [action: "enable" | "disable" | "delete" | "nickname", handle: string, nickname?: string],
  Reply<CellularStatus>
>("change_profile");
export const setData = callable<[enabled: boolean], Reply<DataStatus>>("set_data");
export const setRoaming = callable<[allowed: boolean], Reply<DataStatus>>("set_roaming");

const beginEsimDownload = callable<[], { url: string; token: string }>("begin_esim_download");

// Decky logs every call's arguments in Steam's JS log, so the activation code
// is posted straight to the plugin's loopback listener instead of passed to a call.
export async function downloadProfile(code: string): Promise<Reply<CellularStatus>> {
  const { url, token } = await beginEsimDownload();
  const response = await fetch(url, { method: "POST", body: JSON.stringify({ token, code }) });
  return (await response.json()) as Reply<CellularStatus>;
}
