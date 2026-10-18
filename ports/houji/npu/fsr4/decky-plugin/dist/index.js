const manifest = { name: "FSR 4 NPU" };
const API_VERSION = 2;
const internalAPIConnection = window.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit;
if (!internalAPIConnection) {
    throw new Error('[@decky/api]: Failed to connect to loader');
}
let api;
try {
    api = internalAPIConnection.connect(API_VERSION, manifest.name);
} catch {
    api = internalAPIConnection.connect(1, manifest.name);
}
const callable = api.callable;
const toaster = api.toaster;
const definePlugin = (fn) => (...args) => fn(...args);

const getStatus = callable("get_status");
const setEnabled = callable("set_enabled");
const restartService = callable("restart_service");

function Content() {
    const [status, setStatus] = SP_REACT.useState(null);
    const [pending, setPending] = SP_REACT.useState(false);

    const refresh = SP_REACT.useCallback(async () => {
        try {
            const res = await getStatus();
            setStatus(res);
        } catch (e) {
            console.error("FSR 4 NPU getStatus failed:", e);
        }
    }, []);

    SP_REACT.useEffect(() => {
        refresh();
        const interval = setInterval(refresh, 2000);
        return () => clearInterval(interval);
    }, [refresh]);

    const handleToggle = async (val) => {
        setPending(true);
        try {
            const res = await setEnabled(val);
            setStatus(res);
            toaster.toast({
                title: "FSR 4 NPU",
                body: res.available === false ? res.disabled_reason : (res.enabled ? "Upscaler enabled (Hexagon NPU active)" : "Upscaler disabled (Native game rendering)")
            });
        } catch (e) {
            toaster.toast({ title: "FSR 4 NPU", body: "Failed to update state" });
        } finally {
            setPending(false);
            refresh();
        }
    };

    const handleRestart = async () => {
        setPending(true);
        try {
            const res = await restartService();
            setStatus(res);
            toaster.toast({ title: "FSR 4 NPU", body: "Upscaler service restarted" });
        } catch (e) {
            toaster.toast({ title: "FSR 4 NPU", body: "Failed to restart service" });
        } finally {
            setPending(false);
            refresh();
        }
    };

    if (!status) {
        return SP_JSX.jsx(DFL.PanelSection, {
            children: SP_JSX.jsx(DFL.PanelSectionRow, {
                children: SP_JSX.jsx(DFL.Field, { label: "Loading FSR 4 NPU status..." })
            })
        });
    }

    let description = status.available === false ? status.disabled_reason : "Disabled (Game uses native rendering / TAA)";
    if (status.enabled) {
        if (status.npu_active) {
            description = `Active: ${status.render_res} → ${status.output_res} (${status.preset})`;
        } else if (status.game_running) {
            description = "Connecting to game...";
        } else {
            description = "Ready (Waiting for supported game)";
        }
    }

    return SP_JSX.jsxs(SP_JSX.Fragment, {
        children: [
            SP_JSX.jsxs(DFL.PanelSection, {
                title: "Upscaler",
                children: [
                    SP_JSX.jsx(DFL.PanelSectionRow, {
                        children: SP_JSX.jsx(DFL.ToggleField, {
                            label: "Enable FSR 4 NPU",
                            description: description,
                            checked: status.enabled,
                            disabled: pending || status.available === false,
                            onChange: handleToggle
                        })
                    }),
                    status.enabled && status.available !== false && SP_JSX.jsx(DFL.PanelSectionRow, {
                        children: SP_JSX.jsx(DFL.ButtonItem, {
                            layout: "below",
                            disabled: pending,
                            onClick: handleRestart,
                            children: "Restart Upscaler"
                        })
                    })
                ]
            }),
            status.enabled && SP_JSX.jsxs(DFL.PanelSection, {
                title: "Hardware & Pipeline",
                children: [
                    SP_JSX.jsx(DFL.PanelSectionRow, {
                        children: SP_JSX.jsx(DFL.Field, {
                            label: "Processor",
                            description: status.htp_device
                        })
                    }),
                    SP_JSX.jsx(DFL.PanelSectionRow, {
                        children: SP_JSX.jsx(DFL.Field, {
                            label: "Model",
                            description: status.model
                        })
                    }),
                    SP_JSX.jsx(DFL.PanelSectionRow, {
                        children: SP_JSX.jsx(DFL.Field, {
                            label: "NPU Inference Latency",
                            description: status.npu_latency
                        })
                    }),
                    SP_JSX.jsx(DFL.PanelSectionRow, {
                        children: SP_JSX.jsx(DFL.Field, {
                            label: "Pipeline Pre/Post",
                            description: "3.5 ms (High priority GPU queue)"
                        })
                    }),
                    SP_JSX.jsx(DFL.PanelSectionRow, {
                        children: SP_JSX.jsx(DFL.Field, {
                            label: "Status",
                            description: status.npu_active ? "Inference Active" : (status.game_running ? "Connecting" : "Idle")
                        })
                    })
                ]
            })
        ]
    });
}

const Icon = SP_JSX.jsxs("svg", {
    xmlns: "http://www.w3.org/2000/svg",
    width: "24",
    height: "24",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round",
    children: [
        SP_JSX.jsx("rect", { x: "4", y: "4", width: "16", height: "16", rx: "2" }),
        SP_JSX.jsx("rect", { x: "9", y: "9", width: "6", height: "6" }),
        SP_JSX.jsx("line", { x1: "9", y1: "1", x2: "9", y2: "4" }),
        SP_JSX.jsx("line", { x1: "15", y1: "1", x2: "15", y2: "4" }),
        SP_JSX.jsx("line", { x1: "9", y1: "20", x2: "9", y2: "23" }),
        SP_JSX.jsx("line", { x1: "15", y1: "20", x2: "15", y2: "23" }),
        SP_JSX.jsx("line", { x1: "20", y1: "9", x2: "23", y2: "9" }),
        SP_JSX.jsx("line", { x1: "20", y1: "15", x2: "23", y2: "15" }),
        SP_JSX.jsx("line", { x1: "1", y1: "9", x2: "4", y2: "9" }),
        SP_JSX.jsx("line", { x1: "1", y1: "15", x2: "4", y2: "15" })
    ]
});

var index = definePlugin(() => {
    return {
        name: "FSR 4 NPU",
        content: SP_JSX.jsx(Content, {}),
        icon: Icon,
        onDismount() {}
    };
});

export { index as default };
