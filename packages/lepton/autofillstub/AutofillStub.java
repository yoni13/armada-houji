import android.os.Binder;
import android.os.IBinder;
import android.os.Looper;
import android.os.Parcel;
import android.os.RemoteException;

// Lepton's Android 11 starts no autofill service but still declares the autofill feature. When a text field
// that holds text becomes visible without focus, AutofillManager calls IAutoFillManager.addClient on its
// null service and the app dies (Discord's two-factor step). This answers every call as "autofill is off".
//
// All 23 IAutoFillManager calls are one-way. The thirteen that carry a trailing IResultReceiver are
// answered through it; the other ten need no answer.
public class AutofillStub extends Binder {
    private static final String DESCRIPTOR = "android.view.autofill.IAutoFillManager";
    private static final String RECEIVER = "com.android.internal.os.IResultReceiver";
    private static final int LAST_CALL = 23;
    private static final int START_SESSION = 3;
    private static final int NO_SESSION = Integer.MAX_VALUE;
    private static final int SEND = 1; // IResultReceiver.send(int resultCode, Bundle resultData)
    // A flattened binder is 24 bytes on a 64-bit kernel, followed on Android 10 and later by a 4-byte
    // stability marker. The receiver is the last argument, so it ends the parcel.
    private static final int[] RECEIVER_TAIL = {28, 24, 32, 20, 36, 40};

    @Override
    protected boolean onTransact(int code, Parcel data, Parcel reply, int flags) throws RemoteException {
        if (code == INTERFACE_TRANSACTION) {
            reply.writeString(DESCRIPTOR);
            return true;
        }
        if (code < FIRST_CALL_TRANSACTION || code > LAST_CALL) {
            return super.onTransact(code, data, reply, flags);
        }
        data.enforceInterface(DESCRIPTOR);
        if (hasReceiver(code)) {
            answer(data, code == START_SESSION ? NO_SESSION : 0);
        }
        return true;
    }

    // addClient, startSession, getFillEventHistory, restoreSession, isServiceSupported, isServiceEnabled,
    // getUserData, getUserDataId, isFieldClassificationEnabled, getAutofillServiceComponentName,
    // getAvailableFieldClassificationAlgorithms, getDefaultFieldClassificationAlgorithm and
    // setAugmentedAutofillWhitelist.
    static boolean hasReceiver(int code) {
        switch (code) {
            case 1: case 3: case 4: case 5: case 13: case 14: case 16: case 17: case 19: case 20: case 21:
            case 22: case 23:
                return true;
            default:
                return false;
        }
    }

    // addClient's result is the enabled flags, 0 meaning disabled; startSession's is the session id, and
    // NO_SESSION means none was started; everything else is a boolean or an object, and 0 with no
    // bundle reads as false or null (SyncResultReceiver returns null for a missing bundle).
    private static void answer(Parcel data, int result) {
        IBinder receiver = receiver(data);
        if (receiver == null) {
            return;
        }
        Parcel message = Parcel.obtain();
        try {
            message.writeInterfaceToken(RECEIVER);
            message.writeInt(result);
            message.writeInt(0); // no Bundle
            receiver.transact(SEND, message, null, IBinder.FLAG_ONEWAY);
        } catch (RemoteException e) {
            // The app is gone; there is nobody to answer.
        } finally {
            message.recycle();
        }
    }

    private static IBinder receiver(Parcel data) {
        int end = data.dataSize();
        int position = data.dataPosition();
        try {
            for (int tail : RECEIVER_TAIL) {
                if (end < tail) {
                    continue;
                }
                data.setDataPosition(end - tail);
                IBinder binder = data.readStrongBinder();
                if (binder != null && RECEIVER.equals(binder.getInterfaceDescriptor())) {
                    return binder;
                }
            }
        } catch (RemoteException | RuntimeException e) {
            // Not the receiver, or the app is gone.
        } finally {
            data.setDataPosition(position);
        }
        return null;
    }

    public static void main(String[] args) throws Exception {
        Looper.prepareMainLooper();
        Class<?> sm = Class.forName("android.os.ServiceManager");
        sm.getMethod("addService", String.class, IBinder.class).invoke(null, "autofill", new AutofillStub());
        System.out.println("autofillstub: registered");
        Looper.loop();
    }
}
