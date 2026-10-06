// SPDX-License-Identifier: GPL-2.0-or-later
#include <KQuickConfigModule>
#include <KPluginFactory>
#include <QDBusConnection>
#include <QDBusInterface>
#include <QDBusMessage>
#include <QDBusPendingCallWatcher>
#include <QDBusPendingReply>
#include <QJsonDocument>
#include <QProcess>
#include <QTimer>

class SimSettings : public KQuickConfigModule
{
    Q_OBJECT
    Q_PROPERTY(QVariantMap status READ status NOTIFY statusChanged)
public:
    SimSettings(QObject *parent, const KPluginMetaData &metadata)
        : KQuickConfigModule(parent, metadata)
    {
        setButtons({});
        connect(&m_timer, &QTimer::timeout, this, &SimSettings::refresh);
        m_timer.start(1500);
        refresh();
    }
    QVariantMap status() const { return m_status; }
    Q_INVOKABLE void run(const QString &operation, const QString &value = {})
    {
        auto message = QDBusMessage::createMethodCall(QStringLiteral("org.armada.Cellular"),
            QStringLiteral("/org/armada/Cellular"), QStringLiteral("org.armada.Cellular1"), QStringLiteral("Run"));
        message.setArguments({operation, value});
        auto *watcher = new QDBusPendingCallWatcher(QDBusConnection::systemBus().asyncCall(message, 5000), this);
        connect(watcher, &QDBusPendingCallWatcher::finished, this, [this, watcher] {
            QDBusPendingReply<> reply = *watcher;
            if (reply.isError()) {
                m_status.insert(QStringLiteral("message"), QStringLiteral("SIM service unavailable or operation already running."));
                Q_EMIT statusChanged();
            } else {
                refresh();
            }
            watcher->deleteLater();
        });
    }
    Q_INVOKABLE void openDataSettings()
    {
        QProcess::startDetached(QStringLiteral("plasma-open-settings"), {QStringLiteral("kcm_cellular_network")});
    }
    Q_INVOKABLE void refresh()
    {
        if (m_pending)
            return;
        m_pending = true;
        auto message = QDBusMessage::createMethodCall(QStringLiteral("org.armada.Cellular"),
            QStringLiteral("/org/armada/Cellular"), QStringLiteral("org.armada.Cellular1"), QStringLiteral("Status"));
        auto *watcher = new QDBusPendingCallWatcher(QDBusConnection::systemBus().asyncCall(message, 5000), this);
        connect(watcher, &QDBusPendingCallWatcher::finished, this, [this, watcher] {
            QDBusPendingReply<QString> reply = *watcher;
            if (!reply.isError()) {
                m_status = QJsonDocument::fromJson(reply.value().toUtf8()).toVariant().toMap();
            } else {
                m_status = {{QStringLiteral("busy"), false},
                            {QStringLiteral("message"), QStringLiteral("SIM service unavailable. Use an active local session.")}};
            }
            m_pending = false;
            Q_EMIT statusChanged();
            watcher->deleteLater();
        });
    }
Q_SIGNALS:
    void statusChanged();
private:
    QVariantMap m_status;
    QTimer m_timer;
    bool m_pending = false;
};

K_PLUGIN_CLASS_WITH_JSON(SimSettings, "kcm_houji_sim.json")
#include "settings.moc"
