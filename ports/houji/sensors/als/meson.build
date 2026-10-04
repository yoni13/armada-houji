project('houji-als', 'c', default_options: ['warning_level=2', 'werror=true'])

executable('houji-als', 'houji-als.c',
  c_args: ['-Wno-unused-parameter'],
  dependencies: [
    dependency('glib-2.0'),
    dependency('gio-unix-2.0'),
    dependency('libssc'),
    meson.get_compiler('c').find_library('m'),
  ],
  install: true,
  install_dir: get_option('libexecdir') / 'armada')
