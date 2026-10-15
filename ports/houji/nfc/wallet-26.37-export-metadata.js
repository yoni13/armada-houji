// Frida script for Wallet 26.37.985574370, after signing in normally.
// Invokes only the transaction-metadata read through Wallet's live RPC client.
// Authentication stays inside Wallet; only the decoded bmht response is saved.
// See README.md, "iPASS transaction names". Obfuscated names are version-specific.
Java.perform(function () {
  const Response = Java.use('bmht');
  const Request = Java.use('bmhr');
  function field(type, object, name) {
    const f = type.class.getDeclaredField(name);
    f.setAccessible(true);
    return f.get(object);
  }
  const app = Java.use('android.app.ActivityThread').currentApplication();
  const Callback = Java.registerClass({
    name: 'org.houji.MetadataResult',
    implements: [Java.use('vly')],
    methods: {
      a: function (error) {
        const cause = error.getCause();
        console.log('Metadata request failed:', cause ? cause.getClass().getName() : error.getClass().getName());
      },
      b: function (message) {
        const response = Java.cast(message, Response);
        const bytes = response.g();
        const out = app.openFileOutput('houji-ipass-metadata-response.pb', 0);
        try { out.write(bytes); } finally { out.close(); }
        const entries = Java.cast(field(Response, response, 'b'), Java.use('java.util.List'));
        console.log('Metadata response saved:', entries.size(), 'entries;', bytes.length, 'bytes');
      }
    }
  });
  let found = false;
  Java.choose('acqm', {
    onMatch: function (repository) {
      if (found) return 'stop';
      found = true;
      const rpc = Java.cast(field(Java.use('acqm'), repository, 'a'), Java.use('vmh'));
      rpc.c('g/ipassmifare/gettransactionmetadata', Java.cast(field(Request, null, 'a'), Request),
            Java.cast(field(Response, null, 'a'), Response), Callback.$new());
      console.log('Requested iPASS name table through Wallet');
      return 'stop';
    },
    onComplete: function () { if (!found) console.log('No live iPASS repository found'); }
  });
});
