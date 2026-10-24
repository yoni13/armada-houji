import ctypes,sys,time
x=ctypes.CDLL('libX11.so.6');t=ctypes.CDLL('libXtst.so.6')
x.XOpenDisplay.argtypes=[ctypes.c_char_p];x.XOpenDisplay.restype=ctypes.c_void_p
x.XStringToKeysym.argtypes=[ctypes.c_char_p];x.XStringToKeysym.restype=ctypes.c_ulong
x.XKeysymToKeycode.argtypes=[ctypes.c_void_p,ctypes.c_ulong];x.XKeysymToKeycode.restype=ctypes.c_ubyte
x.XFlush.argtypes=[ctypes.c_void_p];x.XCloseDisplay.argtypes=[ctypes.c_void_p]
x.XSetInputFocus.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
t.XTestFakeKeyEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
t.XTestFakeMotionEvent.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_ulong]
t.XTestFakeButtonEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
d=x.XOpenDisplay(sys.argv[1].encode());assert d
try:
 if sys.argv[2]=='focus':
  x.XSetInputFocus(d,int(sys.argv[3],0),2,0);x.XFlush(d);sys.exit(0)
 if sys.argv[2]=='click':
  t.XTestFakeMotionEvent(d,-1,int(sys.argv[3]),int(sys.argv[4]),0);x.XFlush(d);time.sleep(.1)
  t.XTestFakeButtonEvent(d,1,1,0);t.XTestFakeButtonEvent(d,1,0,80)
 else:
  code=x.XKeysymToKeycode(d,x.XStringToKeysym(sys.argv[2].encode()));assert code
  t.XTestFakeKeyEvent(d,code,1,0);x.XFlush(d)
  time.sleep(min(float(sys.argv[3]) if len(sys.argv)>3 else .1,10))
  t.XTestFakeKeyEvent(d,code,0,0)
 x.XFlush(d)
finally:x.XCloseDisplay(d)
