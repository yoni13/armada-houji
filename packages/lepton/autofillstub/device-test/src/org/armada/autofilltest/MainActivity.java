package org.armada.autofilltest;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.View;
import android.view.autofill.AutofillManager;
import android.widget.EditText;
import android.widget.LinearLayout;

// The shape of Discord's two-factor screen: an unfocused text field that already holds text becomes visible.
// On an Android 11 without an autofill service that kills the app inside AutofillManager.
public class MainActivity extends Activity {
    private static final String TAG = "AutofillTest";

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        EditText first = new EditText(this);
        EditText code = new EditText(this);
        code.setText("123456");
        code.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_YES);
        code.setVisibility(View.INVISIBLE);
        root.addView(first);
        root.addView(code);
        setContentView(root);
        first.requestFocus();

        final AutofillManager manager = getSystemService(AutofillManager.class);
        Log.i(TAG, "manager=" + (manager != null));
        Handler handler = new Handler(Looper.getMainLooper());
        handler.postDelayed(() -> {
            Log.i(TAG, "showing the text field");
            code.setVisibility(View.VISIBLE);
        }, 1500);
        handler.postDelayed(() -> Log.i(TAG, "survived: enabled=" + manager.isEnabled()
                + " supported=" + manager.isAutofillSupported()
                + " hasEnabledServices=" + manager.hasEnabledAutofillServices()), 4000);
    }
}
