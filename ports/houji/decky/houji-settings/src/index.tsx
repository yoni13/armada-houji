import { addEventListener, definePlugin, removeEventListener, toaster } from "@decky/api";
import { Content } from "./Content";

export default definePlugin(() => {
  const scanListener = addEventListener("nfc_tag_found", () => {
    toaster.toast({ title: "NFC tag found", body: "A new tag was detected. Open Houji Settings → NFC for scanner controls." });
  });
  return {
  name: "Houji Settings",
  content: <Content />,
  onDismount() { removeEventListener("nfc_tag_found", scanListener); },
  icon: (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="6" y="2" width="12" height="20" rx="2" />
      <path d="M11 18h2" />
    </svg>
  ),
  };
});
