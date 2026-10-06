// SPDX-License-Identifier: GPL-2.0-or-later
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM
import org.kde.kirigamiaddons.formcard as FormCard

KCM.SimpleKCM {
    id: root
    title: i18n("SIM Cards and eSIM")
    property bool busy: kcm.status.busy || false
    property string selected: kcm.status.selection || "auto"
    ColumnLayout {
        width: root.width
        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: text.length > 0
            text: kcm.status.message || ""
            type: Kirigami.MessageType.Information
        }
        Controls.BusyIndicator { running: root.busy; visible: running; Layout.alignment: Qt.AlignHCenter }
        FormCard.FormCard {
            FormCard.FormButtonDelegate {
                text: i18n("Enable cellular services")
                enabled: !root.busy
                onClicked: kcm.run("enable")
            }
            FormCard.FormButtonDelegate {
                text: i18n("Mobile data, APNs and networks")
                description: i18n("Open the standard Cellular Network settings.")
                onClicked: kcm.openDataSettings()
            }
        }
        FormCard.FormHeader { title: i18n("Primary data SIM") }
        FormCard.FormCard {
            Repeater {
                model: [
                    { label: i18n("Automatic"), value: "auto" },
                    { label: i18n("Physical SIM — slot 1"), value: "physical1" },
                    { label: i18n("Physical SIM — slot 2"), value: "physical2" },
                    { label: i18n("eSIM — shared slot 2"), value: "esim" }
                ]
                FormCard.FormRadioDelegate {
                    required property var modelData
                    text: modelData.label
                    checked: root.selected === modelData.value
                    enabled: !root.busy
                    onClicked: kcm.run("select", modelData.value)
                }
            }
            FormCard.FormTextDelegate {
                text: i18n("One active data subscription")
                description: i18n("Switching disconnects mobile data. The second physical SIM and eSIM share one interface.")
            }
        }
        FormCard.FormHeader { title: i18n("eSIM profiles") }
        FormCard.FormCard {
            FormCard.FormButtonDelegate {
                text: i18n("Read eSIM profiles")
                description: i18n("Selects the eSIM and briefly reconnects the modem service.")
                enabled: !root.busy
                onClicked: kcm.run("list")
            }
            Repeater {
                model: kcm.status.profiles || []
                FormCard.AbstractFormDelegate {
                    required property var modelData
                    contentItem: ColumnLayout {
                        Controls.Label {
                            text: modelData.profileName || modelData.serviceProviderName
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                        Controls.Label {
                            text: modelData.profileState + " · …" + modelData.iccid.slice(-4)
                            textFormat: Text.PlainText
                        }
                        RowLayout {
                            Controls.Button {
                                text: modelData.profileState === "enabled" ? i18n("Disable") : i18n("Enable")
                                enabled: !root.busy
                                onClicked: kcm.run(modelData.profileState === "enabled" ? "profile-disable" : "profile-enable", modelData.iccid)
                            }
                            Controls.Button {
                                text: i18n("Delete")
                                enabled: !root.busy && modelData.profileState !== "enabled"
                                onClicked: {
                                    removeDialog.iccid = modelData.iccid;
                                    removeDialog.open();
                                }
                            }
                        }
                    }
                }
            }
        }
        FormCard.FormHeader { title: i18n("Download a profile") }
        FormCard.FormCard {
            FormCard.AbstractFormDelegate {
                contentItem: ColumnLayout {
                    Controls.Label {
                        text: i18n("Use Wi-Fi. Paste the carrier's LPA activation code.")
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                    Controls.TextField {
                        id: activation
                        Layout.fillWidth: true
                        placeholderText: "LPA:1$…"
                        echoMode: TextInput.Password
                        inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText
                        enabled: !root.busy
                    }
                    Controls.Button {
                        text: i18n("Download")
                        enabled: !root.busy && activation.text.startsWith("LPA:1$")
                        onClicked: {
                            kcm.run("download", activation.text);
                            activation.clear();
                        }
                    }
                }
            }
        }
    }
    Controls.Dialog {
        id: removeDialog
        property string iccid
        title: i18n("Delete this eSIM profile?")
        modal: true
        standardButtons: Controls.Dialog.Cancel | Controls.Dialog.Ok
        onAccepted: kcm.run("profile-delete", iccid)
        contentItem: Controls.Label { text: i18n("The carrier may require a new activation code to reinstall it."); wrapMode: Text.Wrap }
    }
}
